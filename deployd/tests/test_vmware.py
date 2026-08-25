from types import SimpleNamespace

import pytest
from pyVmomi import vim

from deployd.backends.base import ProvisionerError
from deployd.backends.vmware import (
    SYSTEM_CONFIGURATION_MAP,
    VmwareBackend,
    make_ssl_context,
    swap_uuid,
)
from deployd.config import ManagerConfig
from deployd.orders import CreateOrder, Ipv4Config

CFG = ManagerConfig(
    name="event",
    type="vmware",
    host="vcenter.example",
    username="u",
    password="p",
    deploy_vlan=509,
)

ORDER = CreateOrder(
    key="create-vm-1",
    manager="event",
    name="web1.event",
    cpus=2,
    memory=2 * 1024**3,
    disk=20 * 1024**3,
    os="debian",
    ipv4=Ipv4Config(vlan=922, address="1.2.3.4", prefix=27, gateway="1.2.3.1"),
)


def _backend():
    return VmwareBackend(CFG, secrets=None, si=object())


def test_swap_uuid_byte_order():
    # AABBCCDD -> DDCCBBAA on the first three groups (gen-2 parity)
    assert (
        swap_uuid("564D5DB0-2E0A-DE7C-BA6D-2F1F84F45A0C")
        == "b05d4d56-0a2e-7cde-ba6d-2f1f84f45a0c"
    )


def test_swap_uuid_is_involution():
    u = "564d5db0-2e0a-de7c-ba6d-2f1f84f45a0c"
    assert swap_uuid(swap_uuid(u)) == u


def test_make_ssl_context_disabled():
    ctx = make_ssl_context(False)
    assert ctx.check_hostname is False


def test_make_ssl_context_default_verifies():
    ctx = make_ssl_context(True)
    assert ctx.check_hostname is True


def _fake_cluster(stores):
    datastores = [
        SimpleNamespace(
            name=name,
            summary=SimpleNamespace(freeSpace=free, accessible=accessible),
        )
        for name, free, accessible in stores
    ]
    return SimpleNamespace(datastore=datastores)


def test_find_datastore_picks_most_free():
    cluster = _fake_cluster([("small", 10, True), ("big", 1000, True), ("off", 9999, False)])
    assert VmwareBackend.find_datastore(cluster, None) == "big"


def test_find_datastore_explicit():
    cluster = _fake_cluster([("small", 10, True), ("big", 1000, True)])
    assert VmwareBackend.find_datastore(cluster, "small") == "small"


def test_find_datastore_missing_explicit_raises():
    cluster = _fake_cluster([("small", 10, True)])
    with pytest.raises(ProvisionerError):
        VmwareBackend.find_datastore(cluster, "nope")


def test_build_config_spec_debian():
    b = _backend()
    backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(deviceName="net")
    spec = b.build_config_spec(ORDER, "ds1", backing)
    assert spec.name == "web1.event"
    assert spec.version == "vmx-13"
    assert spec.guestId == "debian10_64Guest"
    assert spec.firmware == "efi"
    assert spec.numCPUs == 2
    assert spec.memoryMB == 2048
    assert spec.files.vmPathName == "[ds1]"
    scsi, disk, nic = spec.deviceChange
    assert isinstance(scsi.device, vim.vm.device.ParaVirtualSCSIController)
    assert scsi.device.sharedBus == "noSharing"
    assert isinstance(disk.device, vim.vm.device.VirtualDisk)
    assert disk.device.capacityInKB == 20 * 1024**3 // 1024
    assert disk.fileOperation == "create"
    assert isinstance(nic.device, vim.vm.device.VirtualVmxnet3)
    assert nic.device.addressType == "generated"


def test_build_config_spec_openbsd_uses_lsi_sas():
    b = _backend()
    order = CreateOrder(**{**ORDER.__dict__, "os": "openbsd"})
    backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(deviceName="net")
    spec = b.build_config_spec(order, "ds1", backing)
    assert spec.guestId == "otherGuest64"
    assert isinstance(
        spec.deviceChange[0].device, vim.vm.device.VirtualLsiLogicSASController
    )


def test_build_config_spec_unknown_os_raises():
    b = _backend()
    order = CreateOrder(**{**ORDER.__dict__, "os": "plan9"})
    with pytest.raises(ProvisionerError):
        b.build_config_spec(order, "ds1", None)


def test_system_configuration_map_parity():
    # Exact parity with gen-2 esxi.py
    assert SYSTEM_CONFIGURATION_MAP == {
        "debian": ("debian10_64Guest", "paravirtual"),
        "ubuntu": ("ubuntu64Guest", "paravirtual"),
        "openbsd": ("otherGuest64", "lsi_sas"),
        "coreos": ("otherGuest64", "paravirtual"),
    }


def test_vcsa_install_config():
    from deployd.backends import vcsa

    b = _backend()
    b._username = "root"
    b._password = "pw"
    b._get_datacenter = lambda name: "dc"
    b._get_active_cluster = lambda dc: SimpleNamespace(host=[1])
    b.find_datastore = lambda cluster, ds: "datastore1"
    b._vlan_to_network = lambda vlan, dc: SimpleNamespace(name="923: Deploy")
    b.get_server_ip = lambda: "172.16.0.79"

    import json

    config = json.loads(
        vcsa.generate_vcenter_install_config(
            b, "vc.event", 923, "1.2.3.4", "27", "1.2.3.1", "pw2", None, "event.se"
        )
    )
    assert config["__version"] == "2.13.0"
    assert config["new_vcsa"]["esxi"]["hostname"] == "172.16.0.79"
    assert config["new_vcsa"]["esxi"]["deployment_network"] == "923: Deploy"
    assert config["new_vcsa"]["esxi"]["datastore"] == "datastore1"
    assert config["new_vcsa"]["appliance"]["name"] == "vc.event"
    assert config["new_vcsa"]["network"]["ip"] == "1.2.3.4"
    assert config["new_vcsa"]["os"]["ntp_servers"] == "ntp1.sp.se"
    assert config["new_vcsa"]["sso"]["domain_name"] == "event.se"
    assert config["ceip"]["settings"]["ceip_enabled"] is False


def test_ssl_thumbprint_format():
    b = _backend()
    import base64

    cert_der = b"fake-certificate-bytes"
    pem = (
        b"-----BEGIN CERTIFICATE-----\n"
        + base64.b64encode(cert_der)
        + b"\n-----END CERTIFICATE-----"
    )
    host = SimpleNamespace(
        config=SimpleNamespace(certificate=list(pem))
    )
    b._view_hosts = lambda dc: [host]
    b._get_datacenter = lambda name: None
    thumb = b.get_ssl_thumbprint()
    import hashlib

    expected = hashlib.sha1(cert_der).hexdigest().upper()
    assert thumb == ":".join(expected[i : i + 2] for i in range(0, len(expected), 2))
    assert thumb.isupper()
