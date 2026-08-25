import json
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from deployd.backends.base import HwProvisioner
from deployd.backends.ocp import OcpBackend
from deployd.config import ManagerConfig, VaultConfig
from deployd.daemon import HwManagerLoop
from deployd.secrets import Secrets

OCP_CFG = ManagerConfig(
    name="ocp-site",
    type="ocp",
    username="admin",
    password="pw",
    machines={
        "node1": {"mac": "aa:bb:cc:dd:ee:01", "ip": "10.1.0.1"},
        "node2": {"mac": "aa:bb:cc:dd:ee:02", "ip": "10.1.0.2"},
    },
)


class FakeHwBackend(HwProvisioner):
    name = "ocp-site"

    def __init__(self):
        self.initialized: list[str] = []

    def scrape_bays(self):
        return {
            "node1": {"mac": "aa:01", "serial": "aa:01", "ip": "10.1.0.1"},
            "empty": None,
        }

    def initialize(self, bay_id, info, install):
        self.initialized.append(bay_id)


def test_hw_loop_publishes_bays():
    backend = FakeHwBackend()
    r = fakeredis.FakeRedis()
    HwManagerLoop(backend, r).iterate()
    bays = json.loads(r.get("bays-ocp-site"))
    assert bays["node1"]["serial"] == "aa:01"
    assert bays["empty"] is None


def test_hw_loop_initializes_pending_install():
    backend = FakeHwBackend()
    r = fakeredis.FakeRedis()
    r.setex("install-aa:01", 3600, json.dumps({"initialized": False, "bay": "node1"}))
    HwManagerLoop(backend, r).iterate()
    assert backend.initialized == ["node1"]
    assert json.loads(r.get("install-aa:01"))["initialized"] is True


def test_hw_loop_skips_initialized_and_missing():
    backend = FakeHwBackend()
    r = fakeredis.FakeRedis()
    r.setex("install-aa:01", 3600, json.dumps({"initialized": True}))
    HwManagerLoop(backend, r).iterate()
    assert backend.initialized == []


def test_ocp_scrape_bays_uses_mac_as_serial():
    b = OcpBackend(OCP_CFG, Secrets(VaultConfig()))
    bays = b.scrape_bays()
    assert bays["node1"] == {
        "mac": "aa:bb:cc:dd:ee:01",
        "serial": "aa:bb:cc:dd:ee:01",
        "ip": "10.1.0.1",
    }


def test_ocp_initialize_sets_netboot_and_power():
    b = OcpBackend(OCP_CFG, Secrets(VaultConfig()))
    with patch("pyghmi.ipmi.command.Command") as cmd:
        b.initialize("node1", {"ip": "10.1.0.1"}, {})
        cmd.assert_called_once_with("10.1.0.1", "admin", "pw")
        cmd.return_value.set_bootdev.assert_called_once_with("network", uefiboot=True)
        cmd.return_value.set_power.assert_called_once_with("boot")


def test_ocp_initialize_wraps_ipmi_errors():
    import pyghmi.exceptions

    b = OcpBackend(OCP_CFG, Secrets(VaultConfig()))
    with patch(
        "pyghmi.ipmi.command.Command",
        MagicMock(side_effect=pyghmi.exceptions.IpmiException("boom")),
    ), pytest.raises(RuntimeError, match="node1"):
        b.initialize("node1", {"ip": "10.1.0.1"}, {})
