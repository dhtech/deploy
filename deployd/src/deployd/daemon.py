"""Per-manager provisioning loops.

All Redis choreography lives here (ported from the gen-2 daemon):

- scrape:    publish ``vm-<manager>-<uuid>`` inventory keys (600s TTL)
- create:    consume ``create-vm-*`` orders for this manager
- provision: watch ``host-*`` records, flip NIC to the production VLAN
             once ``installed`` is true (loop-guarded via ``provisioned``)

Backends implement the Provisioner ABC and never touch Redis.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import cast

import redis

from deployd.backends.base import Capability, HwProvisioner, Provisioner, VmInfo
from deployd.orders import CreateOrder, HostRecord, write_error
from deployd import ipplan

RUN_INTERVAL = 7
INVENTORY_TTL = 600
CREATE_COOLDOWN = 600  # seconds before retrying a create for the same name

log = logging.getLogger(__name__)


class HwManagerLoop:
    """Drives one hardware backend (OCP): bays inventory + one-shot netboot."""

    def __init__(
        self,
        backend: HwProvisioner,
        conn: redis.Redis,
        interval: int = RUN_INTERVAL,
    ) -> None:
        self.backend = backend
        self.redis = conn
        self.interval = interval
        self.thread = threading.Thread(
            target=self.run, name=f"manager-{backend.name}", daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        while True:
            try:
                self.iterate()
            except Exception:
                log.exception("[%s] exception in manager loop", self.backend.name)
            time.sleep(self.interval)

    def iterate(self) -> None:
        bays = self.backend.scrape_bays()
        self.redis.setex(f"bays-{self.backend.name}", INVENTORY_TTL, json.dumps(bays))

        for bay_id, info in bays.items():
            if not info or not info.get("serial"):
                continue
            key = f"install-{info['serial']}"
            raw = cast("bytes | None", self.redis.get(key))
            if raw is None:
                continue
            install = json.loads(raw)
            if install.get("initialized", True):
                continue
            self.backend.initialize(bay_id, info, install)
            install["initialized"] = True
            self.redis.setex(key, 3600, json.dumps(install))
            log.info("[%s] initialized netboot for %s", self.backend.name, bay_id)


class VmManagerLoop:
    """Drives one VM backend (Proxmox or VMware)."""

    def __init__(
        self,
        backend: Provisioner,
        conn: redis.Redis,
        deploy_vlan: int,
        fqdn: str | None = None,
        interval: int = RUN_INTERVAL,
    ) -> None:
        self.backend = backend
        self.redis = conn
        self.deploy_vlan = deploy_vlan
        self.fqdn = fqdn
        self.interval = interval
        self._inventory: dict[str, VmInfo] | None = None  # backend_ref -> VmInfo
        # Names we recently attempted to create: a backend error AFTER the
        # VM came into existence keeps the order key, and the fresh VM may
        # not show up in the inventory for a few scrapes — without this
        # guard one order can storm into several VMs (seen live: three).
        self._create_attempts: dict[str, float] = {}
        self.thread = threading.Thread(
            target=self.run, name=f"manager-{backend.name}", daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        while True:
            try:
                self.iterate()
            except Exception:
                log.exception("[%s] exception in manager loop", self.backend.name)
            time.sleep(self.interval)

    def iterate(self) -> None:
        self.backend.ensure_setup()
        self.scrape()
        self.create()
        self.configure()
        self.provision()

    # -- scrape ----------------------------------------------------------

    def scrape(self) -> None:
        """Publish inventory keys for every VM this manager owns."""
        vms = self.backend.list_vms()
        previous = self._inventory if self._inventory is not None else {}
        current: dict[str, VmInfo] = {}
        for vm in vms:
            current[vm.backend_ref] = vm
            if vm.backend_ref not in previous:
                log.info("[%s] found new VM %s (%s)", self.backend.name, vm.name, vm.uuid)
            metadata = {"name": vm.name, "manager": self.backend.name, "fqdn": self.fqdn}
            self.redis.setex(
                f"vm-{self.backend.name}-{vm.uuid.lower()}",
                INVENTORY_TTL,
                json.dumps(metadata),
            )
        for ref in set(previous) - set(current):
            log.info("[%s] forgot VM %s", self.backend.name, previous[ref].name)
        self._inventory = current

    # -- create ----------------------------------------------------------

    def create(self) -> None:
        """Create new VMs if we have orders to do so."""
        if self._inventory is None:
            # Never act before the first successful scrape.
            return
        for rawkey in cast(list[bytes], self.redis.keys("create-vm-*")):
            key = rawkey.decode() if isinstance(rawkey, bytes) else rawkey
            raw = cast("bytes | None", self.redis.get(key))
            if raw is None:
                continue
            data = json.loads(raw)
            if data.get("manager") != self.backend.name:
                continue

            try:
                order = CreateOrder.from_json(key, raw)
                known_names = {vm.name for vm in self._inventory.values()}
                attempted = self._create_attempts.get(order.name, 0.0)
                if order.name in known_names:
                    log.error(
                        "[%s] tried to create already existing VM %s",
                        self.backend.name,
                        order.name,
                    )
                elif time.monotonic() - attempted < CREATE_COOLDOWN:
                    # Recent attempt with a lingering order key: do NOT
                    # retry blindly — the VM may exist even though the
                    # attempt errored. Keep the key for the operator.
                    log.warning(
                        "[%s] create of %s attempted %.0fs ago; holding off",
                        self.backend.name,
                        order.name,
                        time.monotonic() - attempted,
                    )
                    continue
                elif order.os == "vcenter":
                    # Raises for backends without VCENTER_DEPLOY capability.
                    self.backend.deploy_vcenter(order)
                else:
                    log.info(
                        "[%s] creating VM %s (cpus=%d memory=%d disk=%d os=%s)",
                        self.backend.name,
                        order.name,
                        order.cpus,
                        order.memory,
                        order.disk,
                        order.os,
                    )
                    self._create_attempts[order.name] = time.monotonic()
                    vm = self.backend.create_vm(order, self.deploy_vlan)
                    log.info("[%s] created VM %s (%s)", self.backend.name, vm.name, vm.uuid)
            except Exception as e:
                write_error(self.redis, key, data, e)
                raise
            # Delete the order since we are done; failures above keep the
            # key (with the error field) so the operator can see and retry.
            self.redis.delete(key)

    # -- configure (vCenter management requests) -------------------------

    def configure(self) -> None:
        """Handle configure-vcenter-* requests (VMware managers only)."""
        if Capability.VCENTER_DEPLOY not in self.backend.capabilities:
            return
        for rawkey in cast(list[bytes], self.redis.keys("configure-vcenter-*")):
            key = rawkey.decode() if isinstance(rawkey, bytes) else rawkey
            raw = cast("bytes | None", self.redis.get(key))
            if raw is None:
                continue
            request = json.loads(raw)
            if request.get("manager") != self.backend.name:
                continue
            self.backend.configure_vcenter(request)
            # Delete the request since we are done; a raise above keeps the
            # key so it is retried next cycle (parity with gen-2).
            self.redis.delete(key)

    # -- provision -------------------------------------------------------

    def provision(self) -> None:
        """Move installed hosts to their production VLAN."""
        if self._inventory is None:
            return
        by_uuid = {vm.uuid.lower(): vm for vm in self._inventory.values()}
        for rawkey in cast(list[bytes], self.redis.keys("host-*")):
            key = rawkey.decode() if isinstance(rawkey, bytes) else rawkey
            raw = cast("bytes | None", self.redis.get(key))
            if raw is None:
                continue
            try:
                host = HostRecord.from_json(key, raw)
            except (ValueError, TypeError, KeyError):
                # Ignore malformed entries (same behavior as gen-2)
                log.debug("[%s] ignoring malformed record %s", self.backend.name, key)
                continue
            if not host.installed or host.provisioned or not host.uuid:
                continue
            vm = by_uuid.get(host.uuid.lower())
            if vm is None:
                # Not our VM
                continue

            # To avoid loops, consider the VM provisioned even though we
            # are not done yet.
            host.provisioned = True
            self.redis.setex(key, 3600, host.to_json())

            if not host.network:
                log.error("[%s] VM %s lacking network config", self.backend.name, vm.name)
                continue
            vlan = int(host.network["vlan"])
            datacenter = None
            if host.client and host.client.get("domain"):
                datacenter = str(host.client["domain"]).lower()

            if ipplan.is_router(vm.name):
                # trunk set at create time - a tag move would tear the
                # trunk down; production leg is a subinterface already
                log.info("[%s] router %s: trunk NIC, no VLAN move", self.backend.name, vm.name)
                continue

            try:
                self.backend.provision_vm(vm, vlan, datacenter)
                log.info("[%s] provisioned VLAN %d on VM %s", self.backend.name, vlan, vm.name)
            except Exception as e:
                write_error(self.redis, key, host.raw | {"provisioned": True}, e)
                log.error("[%s] failed to provision VM %s: %s", self.backend.name, vm.name, e)
                raise
