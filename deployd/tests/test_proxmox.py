from unittest.mock import MagicMock

import pytest

from deployd.backends.base import ProvisionerError, VmInfo
from deployd.backends.proxmox import ProxmoxBackend
from deployd.config import ManagerConfig
from deployd.orders import CreateOrder

CFG = ManagerConfig(
    name="coloc",
    type="proxmox",
    api_url="https://10.10.10.1:8006",
    token_id="provisioner@pve!dev",
    token_secret="s3cret",
    bridge="vmbr0",
    deploy_vlan=100,
)

ORDER = CreateOrder(
    key="create-vm-1",
    manager="coloc",
    name="web1.test",
    cpus=2,
    memory=2 * 1024**3,
    disk=20 * 1024**3,
    os="debian",
)


@pytest.fixture
def backend():
    api = MagicMock()
    # cold-start waiter sees the VM already stopped
    api.nodes.return_value.qemu.return_value.status.current.get.return_value = {
        "status": "stopped"
    }
    # every task finishes OK immediately
    api.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    b = ProxmoxBackend(CFG, secrets=None, api=api)
    return b, api


def test_list_vms_parses_smbios_uuid(backend):
    b, api = backend
    api.cluster.resources.get.return_value = [
        {"node": "pve-test", "vmid": 100, "name": "provision1", "status": "running"},
    ]
    api.nodes.return_value.qemu.return_value.config.get.return_value = {
        "smbios1": "uuid=ABCD-EF,base64=0"
    }
    vms = b.list_vms()
    assert vms == [VmInfo(name="provision1", uuid="abcd-ef", backend_ref="pve-test/100")]
    # second call served from cache: config.get not called again
    api.nodes.return_value.qemu.return_value.config.get.reset_mock()
    b.list_vms()
    api.nodes.return_value.qemu.return_value.config.get.assert_not_called()


def test_list_vms_skips_templates(backend):
    b, api = backend
    api.cluster.resources.get.return_value = [
        {"node": "pve-test", "vmid": 900, "name": "tpl", "template": 1},
    ]
    assert b.list_vms() == []


def test_create_vm_payload(backend):
    b, api = backend
    api.cluster.resources.get.return_value = [
        {"node": "pve-test", "status": "online", "maxmem": 12 * 1024**3, "mem": 2 * 1024**3},
    ]
    api.nodes.return_value.storage.get.return_value = [
        {"storage": "local-lvm", "active": 1, "avail": 100 * 1024**3},
        {"storage": "small", "active": 1, "avail": 1 * 1024**3},
    ]
    api.cluster.nextid.get.return_value = "101"

    vm = b.create_vm(ORDER, deploy_vlan=100)

    create_kwargs = api.nodes.return_value.qemu.create.call_args.kwargs
    assert create_kwargs["vmid"] == 101
    assert create_kwargs["name"] == "web1.test"
    assert create_kwargs["cores"] == 2
    assert create_kwargs["memory"] == 2048
    assert create_kwargs["bios"] == "ovmf"
    assert create_kwargs["efidisk0"] == "local-lvm:1,pre-enrolled-keys=0"
    assert create_kwargs["machine"] == "q35"
    assert create_kwargs["ostype"] == "l26"
    assert create_kwargs["scsihw"] == "virtio-scsi-single"
    assert create_kwargs["scsi0"] == "local-lvm:20"
    assert create_kwargs["net0"] == "virtio,bridge=vmbr0,tag=100"
    assert create_kwargs["boot"] == "order=scsi0;net0"
    assert create_kwargs["smbios1"] == f"uuid={vm.uuid}"
    assert vm.backend_ref == "pve-test/101"
    # started
    api.nodes.return_value.qemu.return_value.status.start.post.assert_called_once()


def test_create_vm_untagged_deploy_vlan(backend):
    b, api = backend
    api.cluster.resources.get.return_value = [
        {"node": "pve-test", "status": "online", "maxmem": 4, "mem": 1},
    ]
    api.nodes.return_value.storage.get.return_value = [
        {"storage": "local-lvm", "active": 1, "avail": 10},
    ]
    api.cluster.nextid.get.return_value = "102"
    b.create_vm(ORDER, deploy_vlan=0)
    assert api.nodes.return_value.qemu.create.call_args.kwargs["net0"] == "virtio,bridge=vmbr0"


def test_create_vm_honors_explicit_datastore(backend):
    b, api = backend
    api.cluster.resources.get.return_value = [
        {"node": "pve-test", "status": "online", "maxmem": 4, "mem": 1},
    ]
    api.cluster.nextid.get.return_value = "103"
    order = CreateOrder(**{**ORDER.__dict__, "datastore": "fastpool"})
    b.create_vm(order, deploy_vlan=100)
    kwargs = api.nodes.return_value.qemu.create.call_args.kwargs
    assert kwargs["scsi0"] == "fastpool:20"
    api.nodes.return_value.storage.get.assert_not_called()


def test_create_vm_failed_task_raises(backend):
    b, api = backend
    api.cluster.resources.get.return_value = [
        {"node": "pve-test", "status": "online", "maxmem": 4, "mem": 1},
    ]
    api.nodes.return_value.storage.get.return_value = [
        {"storage": "local-lvm", "active": 1, "avail": 10},
    ]
    api.cluster.nextid.get.return_value = "104"
    api.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "storage full",
    }
    with pytest.raises(ProvisionerError):
        b.create_vm(ORDER, deploy_vlan=100)


def test_provision_vm_flips_tag_preserving_mac(backend):
    b, api = backend
    api.nodes.return_value.qemu.return_value.config.get.return_value = {
        "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=100"
    }
    vm = VmInfo(name="web1.test", uuid="u", backend_ref="pve-test/101")
    b.provision_vm(vm, vlan=200)
    api.nodes.return_value.qemu.return_value.config.put.assert_called_once_with(
        net0="virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=200"
    )


def test_provision_vm_untagged_removes_tag(backend):
    b, api = backend
    api.nodes.return_value.qemu.return_value.config.get.return_value = {
        "net0": "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=100"
    }
    vm = VmInfo(name="web1.test", uuid="u", backend_ref="pve-test/101")
    b.provision_vm(vm, vlan=0)
    api.nodes.return_value.qemu.return_value.config.put.assert_called_once_with(
        net0="virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0"
    )


def test_delete_vm_stops_running_vm_first(backend):
    b, api = backend
    api.nodes.return_value.qemu.return_value.status.current.get.return_value = {
        "status": "running"
    }
    vm = VmInfo(name="x", uuid="u", backend_ref="pve-test/101")
    b.delete_vm(vm)
    api.nodes.return_value.qemu.return_value.status.stop.post.assert_called_once()
    api.nodes.return_value.qemu.return_value.delete.assert_called_once_with(purge=1)
