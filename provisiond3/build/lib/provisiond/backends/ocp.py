"""OCP node backend: netboot + power via IPMI (pyghmi). Gen-2 parity."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from provisiond.backends.base import HwProvisioner

if TYPE_CHECKING:
    from provisiond.config import ManagerConfig
    from provisiond.secrets import Secrets

log = logging.getLogger(__name__)


class OcpBackend(HwProvisioner):
    def __init__(self, config: ManagerConfig, secrets: Secrets) -> None:
        self.name = config.name
        self._machines = config.machines or {}
        self._username = secrets.resolve(config.username, field="username")
        self._password = secrets.resolve(config.password, field="password")

    def scrape_bays(self) -> dict[str, dict[str, Any] | None]:
        bays: dict[str, dict[str, Any] | None] = {}
        for name, entry in self._machines.items():
            # MAC doubles as serial so deploy-bay can be reused (gen-2 parity).
            mac = entry["mac"]
            bays[name] = {"mac": mac, "serial": mac, "ip": entry["ip"]}
        return bays

    def initialize(
        self, bay_id: str, info: dict[str, Any], install: dict[str, Any]
    ) -> None:
        import pyghmi.exceptions
        import pyghmi.ipmi.command

        try:
            ipmi = pyghmi.ipmi.command.Command(
                str(info["ip"]), self._username, self._password
            )
            ipmi.set_bootdev("network", uefiboot=True)
            ipmi.set_power("boot")
        except pyghmi.exceptions.IpmiException as e:
            raise RuntimeError(f"OCP failed to IPMI node {bay_id}: {e}") from e
