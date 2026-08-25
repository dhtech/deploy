import json

import fakeredis

from deployd.orders import CreateOrder, HostRecord, write_error

ORDER = {
    "manager": "coloc",
    "name": "web1.coloc.dreamhack.se",
    "datacenter": "coloc",
    "cpus": 2,
    "disk": 20 * 1024**3,
    "memory": 2 * 1024**3,
    "datastore": None,
    "os": "debian",
    "ipv4": {"vlan": 200, "address": "10.200.0.10", "prefix": 24, "gateway": "10.200.0.1"},
}


def test_create_order_roundtrip():
    order = CreateOrder.from_json("create-vm-x", json.dumps(ORDER))
    assert order.manager == "coloc"
    assert order.name == "web1.coloc.dreamhack.se"
    assert order.cpus == 2
    assert order.disk == 20 * 1024**3
    assert order.memory == 2 * 1024**3
    assert order.os == "debian"
    assert order.datastore is None
    assert order.ipv4 is not None and order.ipv4.vlan == 200


def test_create_order_defaults():
    minimal = {"manager": "m", "name": "n", "cpus": 1, "disk": 1, "memory": 1}
    order = CreateOrder.from_json("k", json.dumps(minimal))
    assert order.os == "debian"
    assert order.ipv4 is None


def test_host_record_roundtrip_preserves_unknown_fields():
    data = {
        "installed": True,
        "provisioned": False,
        "uuid": "AABB-CC",
        "network": {"vlan": 200},
        "client": {"domain": "Coloc"},
        "extra": "kept",
    }
    rec = HostRecord.from_json("host-x", json.dumps(data))
    assert rec.installed and not rec.provisioned
    assert rec.uuid == "AABB-CC"
    rec.provisioned = True
    out = json.loads(rec.to_json())
    assert out["provisioned"] is True
    assert out["extra"] == "kept"


def test_write_error_preserves_ttl():
    r = fakeredis.FakeRedis()
    r.setex("k", 1800, json.dumps({"a": 1}))
    write_error(r, "k", {"a": 1}, ValueError("boom"))
    assert 0 < r.ttl("k") <= 1800
    data = json.loads(r.get("k"))
    assert data["error"] == "ValueError: boom"
    assert data["a"] == 1
