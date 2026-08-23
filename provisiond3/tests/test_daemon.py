import json

import fakeredis
import pytest

from provisiond.backends.base import Capability, Provisioner, ProvisionerError, VmInfo
from provisiond.daemon import VmManagerLoop
from provisiond.orders import CreateOrder


class FakeBackend(Provisioner):
    name = "coloc"
    capabilities = Capability.CREATE | Capability.PROVISION | Capability.DELETE

    def __init__(self):
        self.vms: list[VmInfo] = []
        self.created: list[tuple[CreateOrder, int]] = []
        self.provisioned: list[tuple[VmInfo, int, str | None]] = []
        self.fail_create: Exception | None = None
        self.fail_provision: Exception | None = None

    def list_vms(self):
        return list(self.vms)

    def create_vm(self, order, deploy_vlan):
        if self.fail_create:
            raise self.fail_create
        self.created.append((order, deploy_vlan))
        vm = VmInfo(name=order.name, uuid=f"uuid-{order.name}", backend_ref=f"ref/{order.name}")
        self.vms.append(vm)
        return vm

    def provision_vm(self, vm, vlan, datacenter=None):
        if self.fail_provision:
            raise self.fail_provision
        self.provisioned.append((vm, vlan, datacenter))

    def delete_vm(self, vm):
        self.vms = [v for v in self.vms if v.uuid != vm.uuid]


ORDER = {
    "manager": "coloc",
    "name": "web1.test",
    "cpus": 2,
    "disk": 20 * 1024**3,
    "memory": 2 * 1024**3,
    "os": "debian",
    "ipv4": {"vlan": 200, "address": "10.200.0.10", "prefix": 24, "gateway": "10.200.0.1"},
}


@pytest.fixture
def loop():
    backend = FakeBackend()
    r = fakeredis.FakeRedis()
    return VmManagerLoop(backend, r, deploy_vlan=100, fqdn="pve.test"), backend, r


def test_scrape_publishes_inventory_keys(loop):
    mgr, backend, r = loop
    backend.vms = [VmInfo(name="a", uuid="AA-11", backend_ref="ref/a")]
    mgr.scrape()
    raw = r.get("vm-coloc-aa-11")
    assert raw is not None
    meta = json.loads(raw)
    assert meta == {"name": "a", "manager": "coloc", "fqdn": "pve.test"}
    assert 0 < r.ttl("vm-coloc-aa-11") <= 600


def test_create_consumes_order(loop):
    mgr, backend, r = loop
    r.setex("create-vm-1", 3600, json.dumps(ORDER))
    mgr.scrape()
    mgr.create()
    assert len(backend.created) == 1
    order, deploy_vlan = backend.created[0]
    assert order.name == "web1.test"
    assert deploy_vlan == 100
    assert r.get("create-vm-1") is None  # consumed


def test_create_skips_other_manager(loop):
    mgr, backend, r = loop
    other = dict(ORDER, manager="event")
    r.setex("create-vm-1", 3600, json.dumps(other))
    mgr.scrape()
    mgr.create()
    assert backend.created == []
    assert r.get("create-vm-1") is not None  # untouched


def test_create_does_nothing_before_first_scrape(loop):
    mgr, backend, r = loop
    r.setex("create-vm-1", 3600, json.dumps(ORDER))
    mgr.create()
    assert backend.created == []


def test_create_failure_writes_error_and_keeps_key(loop):
    mgr, backend, r = loop
    backend.fail_create = RuntimeError("no space")
    r.setex("create-vm-1", 3600, json.dumps(ORDER))
    mgr.scrape()
    with pytest.raises(RuntimeError):
        mgr.create()
    data = json.loads(r.get("create-vm-1"))
    assert data["error"] == "RuntimeError: no space"
    assert 0 < r.ttl("create-vm-1") <= 3600


def test_create_failure_does_not_storm(loop):
    # A backend error can strike AFTER the VM exists (seen live: pveproxy
    # restart mid-create made one order produce three VMs). The lingering
    # order must not trigger another create within the cooldown.
    mgr, backend, r = loop
    backend.fail_create = RuntimeError("api flaked")
    r.setex("create-vm-1", 3600, json.dumps(ORDER))
    mgr.scrape()
    with pytest.raises(RuntimeError):
        mgr.create()
    backend.fail_create = None
    mgr.scrape()  # fresh VM may not be visible yet
    mgr.create()  # must hold off, not create again
    assert backend.created == []
    assert r.get("create-vm-1") is not None  # kept for the operator


def test_create_existing_name_deletes_order_without_creating(loop):
    mgr, backend, r = loop
    backend.vms = [VmInfo(name="web1.test", uuid="AA", backend_ref="ref/x")]
    r.setex("create-vm-1", 3600, json.dumps(ORDER))
    mgr.scrape()
    mgr.create()
    assert backend.created == []
    assert r.get("create-vm-1") is None


def test_vcenter_order_rejected_without_capability(loop):
    mgr, _backend, r = loop
    r.setex("create-vm-1", 3600, json.dumps(dict(ORDER, os="vcenter")))
    mgr.scrape()
    with pytest.raises(ProvisionerError):
        mgr.create()
    assert "error" in json.loads(r.get("create-vm-1"))


def _host_record(uuid="uuid-x", installed=True, provisioned=False):
    return {
        "installed": installed,
        "provisioned": provisioned,
        "uuid": uuid,
        "network": {"vlan": 200},
        "client": {"domain": "Coloc"},
    }


def test_provision_flips_vlan_and_sets_guard(loop):
    mgr, backend, r = loop
    backend.vms = [VmInfo(name="web1.test", uuid="uuid-x", backend_ref="ref/x")]
    mgr.scrape()
    r.setex("host-web1.test", 3600, json.dumps(_host_record()))
    mgr.provision()
    assert len(backend.provisioned) == 1
    vm, vlan, dc = backend.provisioned[0]
    assert (vm.uuid, vlan, dc) == ("uuid-x", 200, "coloc")
    data = json.loads(r.get("host-web1.test"))
    assert data["provisioned"] is True


def test_provision_skips_foreign_uuid(loop):
    mgr, backend, r = loop
    backend.vms = [VmInfo(name="a", uuid="other", backend_ref="ref/a")]
    mgr.scrape()
    r.setex("host-b", 3600, json.dumps(_host_record(uuid="not-ours")))
    mgr.provision()
    assert backend.provisioned == []
    assert json.loads(r.get("host-b"))["provisioned"] is False


def test_provision_guard_prevents_second_run(loop):
    mgr, backend, r = loop
    backend.vms = [VmInfo(name="a", uuid="uuid-x", backend_ref="ref/a")]
    mgr.scrape()
    r.setex("host-a", 3600, json.dumps(_host_record(provisioned=True)))
    mgr.provision()
    assert backend.provisioned == []


def test_provision_failure_writes_error_with_guard_set(loop):
    mgr, backend, r = loop
    backend.vms = [VmInfo(name="a", uuid="uuid-x", backend_ref="ref/a")]
    backend.fail_provision = RuntimeError("dvs gone")
    mgr.scrape()
    r.setex("host-a", 3600, json.dumps(_host_record()))
    with pytest.raises(RuntimeError):
        mgr.provision()
    data = json.loads(r.get("host-a"))
    assert data["error"] == "RuntimeError: dvs gone"
    assert data["provisioned"] is True  # guard stays, no retry loop


def test_provision_ignores_malformed_records(loop):
    mgr, backend, r = loop
    backend.vms = [VmInfo(name="a", uuid="uuid-x", backend_ref="ref/a")]
    mgr.scrape()
    r.setex("host-broken", 3600, b"not json")
    mgr.provision()  # must not raise
    assert backend.provisioned == []
