"""End-to-end integration test against the live pve-test environment.

Runs ONLY on provision-dev (needs /root/.config/proxmox-api.env, a local
redis-server, and the deploy/prod VLANs 100/200 on pve-test's vmbr0):

    pytest -m integration
"""

import json
import os
import subprocess
import time
import uuid as uuid_mod

import pytest
import redis as redislib

from provisiond.backends.proxmox import ProxmoxBackend
from provisiond.config import ManagerConfig, VaultConfig
from provisiond.daemon import VmManagerLoop
from provisiond.secrets import Secrets

pytestmark = pytest.mark.integration

DEPLOY_VLAN = 100
PROD_VLAN = 200


@pytest.fixture(scope="module")
def backend():
    if "PVE_API_URL" not in os.environ:
        pytest.skip("PVE_API_URL not set (source /root/.config/proxmox-api.env)")
    cfg = ManagerConfig(
        name="coloc",
        type="proxmox",
        api_url=os.environ["PVE_API_URL"].removesuffix("/api2/json"),
        token_id=os.environ["PVE_TOKEN_ID"],
        token_secret={"env": "PVE_TOKEN_SECRET"},
        verify_tls=False,  # lab: pve-test cert has no SAN for the mgmt VLAN IP
        bridge="vmbr0",
        deploy_vlan=DEPLOY_VLAN,
    )
    return ProxmoxBackend(cfg, secrets=Secrets(VaultConfig()))


def _qemu_config(backend, vm):
    node, _, vmid = vm.backend_ref.partition("/")
    return backend._api.nodes(node).qemu(int(vmid)).config.get()


def test_e2e_create_provision_delete(backend):
    r = redislib.Redis()
    loop = VmManagerLoop(backend, r, deploy_vlan=DEPLOY_VLAN, fqdn="pve-test.lan")
    name = f"e2e-{uuid_mod.uuid4().hex[:8]}.colo.notproduction.net"
    order = {
        "manager": "coloc",
        "name": name,
        "datacenter": "coloc",
        "cpus": 1,
        "memory": 1024**3,
        "disk": 10 * 1024**3,
        "datastore": None,
        "os": "debian",
        "ipv4": {"vlan": PROD_VLAN, "address": "10.200.0.50", "prefix": 24,
                 "gateway": "10.200.0.1"},
    }
    key = f"create-vm-{uuid_mod.uuid4()}"
    r.setex(key, 3600, json.dumps(order))

    vm = None
    try:
        # --- create ---
        loop.iterate()
        assert r.get(key) is None, "order not consumed"
        # /cluster/resources lags a few seconds behind on freshly created
        # VMs (pvestatd cache), so poll for the name to appear.
        deadline = time.monotonic() + 60
        vm = None
        while time.monotonic() < deadline and vm is None:
            vm = {v.name: v for v in backend.list_vms()}.get(name)
            if vm is None:
                time.sleep(3)
        assert vm is not None, "VM not created"

        # inventory keys are published by scrape(), i.e. the next cycle
        loop.scrape()
        assert r.get(f"vm-coloc-{vm.uuid}") is not None, "inventory key missing"

        conf = _qemu_config(backend, vm)
        assert conf["bios"] == "ovmf"
        assert conf["boot"].startswith("order=net0")
        assert f"tag={DEPLOY_VLAN}" in conf["net0"]
        mac = conf["net0"].split(",")[0]

        node, _, vmid = vm.backend_ref.partition("/")
        status = backend._api.nodes(node).qemu(int(vmid)).status.current.get()
        assert status["status"] == "running"

        # --- PXE: the VM should get a DHCP lease from our dhcpd ---
        deadline = time.monotonic() + 120
        saw_dhcp = False
        while time.monotonic() < deadline and not saw_dhcp:
            out = subprocess.run(
                ["journalctl", "-u", "isc-dhcp-server", "--since", "-3min", "-o", "cat"],
                capture_output=True, text=True, check=False,
            ).stdout
            saw_dhcp = mac.split("=")[1].lower() in out.lower()
            if not saw_dhcp:
                time.sleep(5)
        assert saw_dhcp, "no DHCPDISCOVER from the new VM on the deploy VLAN"

        # --- provision (flip to prod VLAN) ---
        r.setex(
            f"host-{name}",
            3600,
            json.dumps({
                "installed": True,
                "provisioned": False,
                "uuid": vm.uuid,
                "network": {"vlan": PROD_VLAN},
                "client": {"domain": "coloc"},
            }),
        )
        loop.iterate()
        conf2 = _qemu_config(backend, vm)
        assert f"tag={PROD_VLAN}" in conf2["net0"], "tag not flipped"
        assert conf2["net0"].split(",")[0] == mac, "MAC changed during flip"
        host = json.loads(r.get(f"host-{name}"))
        assert host["provisioned"] is True
    finally:
        r.delete(key)
        r.delete(f"host-{name}")
        if vm is not None:
            backend.delete_vm(vm)

    assert name not in {v.name for v in backend.list_vms()}, "teardown failed"
