"""vCenter Server Appliance deployment via the vcsa-deploy CLI (os == 'vcenter').

Kept functionally identical to gen-2: mounts $VMWARE_VCENTER_ISO, renders
the 2.13.0 installer JSON, runs vcsa-cli-installer/lin64/vcsa-deploy in a
worker thread (it runs for a long time), and stores the generated
credentials in Vault.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from provisiond.backends.vmware import VmwareBackend
    from provisiond.orders import CreateOrder
    from provisiond.secrets import Secrets

log = logging.getLogger(__name__)


def generate_vcenter_install_config(
    backend: VmwareBackend,
    host: str,
    vlan: int,
    ip: str,
    prefix: str,
    gateway: str,
    password: str,
    datastore: str | None,
    domain: str,
    datacenter: str | None = None,
) -> str:
    dc = backend._get_datacenter(datacenter)
    cluster = backend._get_active_cluster(dc)
    real_datastore = backend.find_datastore(cluster, datastore)
    network = backend._vlan_to_network(vlan, dc).name
    install_data = {
        "__version": "2.13.0",
        "new_vcsa": {
            "esxi": {
                "hostname": backend.get_server_ip(),
                "username": backend._username,
                "password": backend._password,
                "deployment_network": network,
                "datastore": real_datastore,
            },
            "appliance": {
                "thin_disk_mode": True,
                "deployment_option": "tiny",
                "name": host,
            },
            "network": {
                "ip_family": "ipv4",
                "mode": "static",
                "ip": ip,
                "dns_servers": ["8.8.8.8", "8.8.4.4"],
                "prefix": prefix,
                "gateway": gateway,
                "system_name": host,
            },
            "os": {
                "password": password,
                "ntp_servers": "ntp1.sp.se",
                "ssh_enable": True,
            },
            "sso": {
                "password": password,
                "domain_name": domain,
            },
        },
        "ceip": {"settings": {"ceip_enabled": False}},
    }
    return json.dumps(install_data, indent=2)


def vcenter_deploy(backend: VmwareBackend, secrets: Secrets, order: CreateOrder) -> None:
    """Deploy a VCSA for this order in a background thread."""
    assert order.ipv4 is not None
    domain = ".".join(order.name.split(".")[1:])
    password = (
        subprocess.check_output(
            ["/usr/bin/apg", "-M", "SNCL", "-n", "1", "-m", "16", "-x", "20"]
        )
        .decode()
        .strip()
    )
    config = generate_vcenter_install_config(
        backend,
        order.name,
        order.ipv4.vlan,
        order.ipv4.address,
        str(order.ipv4.prefix),
        order.ipv4.gateway,
        password,
        order.datastore,
        domain,
    )

    log.info("starting vCenter installation VM %s", order.name)
    mount = tempfile.mkdtemp()
    subprocess.check_call(
        ["/bin/mount", "-o", "loop", os.environ["VMWARE_VCENTER_ISO"], mount]
    )

    def deploy() -> None:
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as f:
                f.write(config)
                f.flush()
                exe = os.path.join(mount, "vcsa-cli-installer", "lin64", "vcsa-deploy")
                subprocess.check_call(
                    [exe, "install", "--no-esx-ssl-verify", "--accept-eula", f.name]
                )
            secrets.write(
                "login:" + order.name,
                username=f"administrator@{domain}",
                password=password,
            )
            log.info("created new vCenter VM %s", order.name)
        except Exception:
            log.exception("failed to create vCenter VM %s", order.name)
        finally:
            subprocess.check_call(["/bin/umount", mount])

    threading.Thread(target=deploy, name=f"vcsa-{order.name}", daemon=True).start()


def setup_vcenter(backend: Any, fqdn: str, defaults: dict[str, str]) -> None:
    """First-contact setup of a fresh vCenter: DC, cluster, DVS, portgroups.

    ``defaults`` carries datacenter/cluster/dvs names; VLAN portgroups come
    from ipplan for the manager's domain.
    """
    from provisiond import ipplan

    datacenter = backend.get_or_create_datacenter(defaults["datacenter"])
    backend.get_or_create_cluster(datacenter, defaults["cluster"])
    backend.create_dvswitch(datacenter, defaults["dvs"])
    vlan_map: dict[str, int | None] = {"0: Untagged Deploy": 0}
    for network, vlan in ipplan.all_vlans_in_same_domain(fqdn):
        vlan_map[f"{vlan}: {network.split('@', 1)[1]}"] = vlan
    backend.create_dvs_portgroup(datacenter, defaults["dvs"], vlan_map)
    log.info("setup for vCenter done")
