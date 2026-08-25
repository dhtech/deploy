"""Proxmox VE backend (proxmoxer, REST API, token auth)."""

from __future__ import annotations

import logging
import math
import time
import uuid as uuid_mod
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from deployd.backends.base import Capability, Provisioner, ProvisionerError, VmInfo
from deployd.orders import CreateOrder

if TYPE_CHECKING:
    from deployd.config import ManagerConfig
    from deployd.secrets import Secrets

log = logging.getLogger(__name__)

# order 'os' -> PVE ostype (parallels the old SYSTEM_CONFIGURATION_MAP)
OSTYPE_MAP = {
    "debian": "l26",
    "ubuntu": "l26",
    "coreos": "l26",
    "openbsd": "other",
}

TASK_TIMEOUT = 300


class ProxmoxBackend(Provisioner):
    capabilities = Capability.CREATE | Capability.PROVISION | Capability.DELETE

    def __init__(
        self, config: ManagerConfig, secrets: Secrets, api: Any | None = None
    ) -> None:
        self.name = config.name
        self._config = config
        if api is not None:
            self._api = api
        else:
            from proxmoxer import ProxmoxAPI

            assert config.api_url and config.token_id
            url = urlparse(config.api_url)
            user, token_name = config.token_id.split("!", 1)
            self._api = ProxmoxAPI(
                url.hostname,
                port=url.port or 8006,
                user=user,
                token_name=token_name,
                token_value=secrets.resolve(config.token_secret, field="token_secret"),
                verify_ssl=config.verify_tls,
            )
        # (node, vmid) -> smbios uuid; avoids a config fetch per VM per cycle
        self._uuid_cache: dict[tuple[str, int], str] = {}

    # -- helpers ---------------------------------------------------------

    def _wait_task(self, node: str, upid: str) -> None:
        deadline = time.monotonic() + TASK_TIMEOUT
        while time.monotonic() < deadline:
            status = self._api.nodes(node).tasks(upid).status.get()
            if status.get("status") == "stopped":
                if status.get("exitstatus") != "OK":
                    raise ProvisionerError(
                        f"task {upid} on {node} failed: {status.get('exitstatus')}"
                    )
                return
            time.sleep(1)
        raise ProvisionerError(f"task {upid} on {node} timed out")

    def _pick_node(self) -> str:
        if self._config.node:
            return self._config.node
        nodes = [
            n
            for n in self._api.cluster.resources.get(type="node")
            if n.get("status") == "online"
        ]
        if not nodes:
            raise ProvisionerError("no online nodes")
        best = max(nodes, key=lambda n: n.get("maxmem", 0) - n.get("mem", 0))
        return str(best["node"])

    def _pick_storage(self, node: str, explicit: str | None) -> str:
        if explicit:
            return explicit
        stores = [
            s
            for s in self._api.nodes(node).storage.get(content="images")
            if s.get("active") and s.get("enabled", 1)
        ]
        if not stores:
            raise ProvisionerError(f"no image storage on node {node}")
        best = max(stores, key=lambda s: s.get("avail", 0))
        return str(best["storage"])

    def _get_vm_uuid(self, node: str, vmid: int) -> str:
        cached = self._uuid_cache.get((node, vmid))
        if cached:
            return cached
        config = self._api.nodes(node).qemu(vmid).config.get()
        vm_uuid = ""
        for part in str(config.get("smbios1", "")).split(","):
            if part.startswith("uuid="):
                vm_uuid = part[len("uuid=") :].lower()
                break
        if vm_uuid:
            self._uuid_cache[(node, vmid)] = vm_uuid
        return vm_uuid

    # -- Provisioner -----------------------------------------------------

    def list_vms(self) -> list[VmInfo]:
        vms = []
        for entry in self._api.cluster.resources.get(type="vm"):
            if entry.get("template"):
                continue
            node, vmid = str(entry["node"]), int(entry["vmid"])
            vm_uuid = self._get_vm_uuid(node, vmid)
            if not vm_uuid:
                continue
            vms.append(
                VmInfo(
                    name=str(entry.get("name", vmid)),
                    uuid=vm_uuid,
                    backend_ref=f"{node}/{vmid}",
                )
            )
        return vms

    def create_vm(self, order: CreateOrder, deploy_vlan: int) -> VmInfo:
        node = self._pick_node()
        storage = self._pick_storage(node, order.datastore)
        vmid = int(self._api.cluster.nextid.get())
        vm_uuid = str(uuid_mod.uuid4())

        disk_gib = max(1, math.ceil(order.disk / 1024**3))
        memory_mib = max(128, order.memory // 1024**2)
        net0 = f"virtio,bridge={self._config.bridge}"
        if deploy_vlan:
            net0 += f",tag={deploy_vlan}"

        params: dict[str, Any] = {
            "vmid": vmid,
            "name": order.name,
            "cores": order.cpus,
            "cpu": "host",
            "memory": memory_mib,
            "bios": "ovmf",
            "efidisk0": f"{storage}:1,pre-enrolled-keys=0",
            "machine": "q35",
            "ostype": OSTYPE_MAP.get(order.os, "l26"),
            "scsihw": "virtio-scsi-single",
            # qemu-guest-agent channel (the agent itself is installed by
            # the hardening step): clean shutdowns, IPs in the UI,
            # qm guest exec
            "agent": 1,
            "scsi0": f"{storage}:{disk_gib}",
            "net0": net0,
            # Deployed machines come back when the hypervisor reboots.
            "onboot": 1,
            # Disk first: OVMF skips the blank disk near-instantly and
            # falls through to PXE for the install; once installed, every
            # boot goes straight to disk with no PXE timeouts. VM
            # reinstalls are destroy+redeploy, so menu-driven netboot
            # reinstalls are not needed.
            "boot": "order=scsi0;net0",
            "smbios1": f"uuid={vm_uuid}",
        }
        if order.appdisks:
            total = sum(int(d["size"]) for d in order.appdisks)
            # +1 GiB headroom for LVM metadata/extent rounding
            appdisk_gib = max(1, math.ceil(total / 1024**3)) + 1
            params["scsi1"] = f"{storage}:{appdisk_gib}"
        if self._config.pool:
            params["pool"] = self._config.pool

        upid = self._api.nodes(node).qemu.create(**params)
        self._wait_task(node, upid)
        self._uuid_cache[(node, vmid)] = vm_uuid.lower()

        upid = self._api.nodes(node).qemu(vmid).status.start.post()
        self._wait_task(node, upid)
        log.info("[%s] created and started vmid %d on %s", self.name, vmid, node)
        return VmInfo(name=order.name, uuid=vm_uuid.lower(), backend_ref=f"{node}/{vmid}")

    def provision_vm(self, vm: VmInfo, vlan: int, datacenter: str | None = None) -> None:
        node, vmid = self._split_ref(vm)
        config = self._api.nodes(node).qemu(vmid).config.get()
        net0 = str(config.get("net0", ""))
        if not net0:
            raise ProvisionerError(f"VM {vm.name} has no net0")
        # net0 looks like "virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0,tag=100".
        # Keep model=MAC and everything else, replace only the tag.
        parts = [p for p in net0.split(",") if not p.startswith("tag=")]
        if vlan:
            parts.append(f"tag={vlan}")
        self._api.nodes(node).qemu(vmid).config.put(net0=",".join(parts))

    def delete_vm(self, vm: VmInfo) -> None:
        node, vmid = self._split_ref(vm)
        status = self._api.nodes(node).qemu(vmid).status.current.get()
        if status.get("status") == "running":
            upid = self._api.nodes(node).qemu(vmid).status.stop.post()
            self._wait_task(node, upid)
        upid = self._api.nodes(node).qemu(vmid).delete(purge=1)
        self._wait_task(node, upid)
        self._uuid_cache.pop((node, vmid), None)

    @staticmethod
    def _split_ref(vm: VmInfo) -> tuple[str, int]:
        node, _, vmid = vm.backend_ref.partition("/")
        return node, int(vmid)
