"""Backend interface. Backends never touch Redis — daemon.py owns that."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Flag, auto

from provisiond.orders import CreateOrder


class Capability(Flag):
    CREATE = auto()
    PROVISION = auto()
    DELETE = auto()
    VCENTER_DEPLOY = auto()


@dataclass(frozen=True)
class VmInfo:
    """A VM as seen by a backend.

    ``backend_ref`` is backend-specific: "node/vmid" for Proxmox, the
    inventory path for VMware. ``uuid`` is the SMBIOS UUID, lowercase,
    already normalized (VMware byte-swap handled inside that backend).
    """

    name: str
    uuid: str
    backend_ref: str


class ProvisionerError(Exception):
    """Raised by backends for operational failures worth reporting."""


class Provisioner(ABC):
    """One hypervisor manager endpoint."""

    name: str
    capabilities: Capability

    @abstractmethod
    def list_vms(self) -> list[VmInfo]:
        """Return all VMs on this manager (drives the vm-<mgr>-<uuid> inventory keys)."""

    @abstractmethod
    def create_vm(self, order: CreateOrder, deploy_vlan: int) -> VmInfo:
        """Create the VM on the deployment VLAN, EFI, netboot-first, powered on."""

    @abstractmethod
    def provision_vm(self, vm: VmInfo, vlan: int, datacenter: str | None = None) -> None:
        """Move the VM's primary NIC to the production VLAN, preserving its MAC."""

    @abstractmethod
    def delete_vm(self, vm: VmInfo) -> None:
        """Destroy the VM (used by integration-test teardown)."""

    def close(self) -> None:
        """Release any connection state; called between cycles if needed."""

    # -- optional (Capability.VCENTER_DEPLOY backends only) --------------

    def ensure_setup(self) -> None:
        """First-contact setup of the managed endpoint; default no-op."""

    def deploy_vcenter(self, order: CreateOrder) -> None:
        """Deploy a vCenter appliance for an os == 'vcenter' order."""
        raise ProvisionerError(f"manager {self.name} cannot deploy vcenter")

    def configure_vcenter(self, request: dict[str, object]) -> None:
        """Handle a configure-vcenter-* request."""
        raise ProvisionerError(f"manager {self.name} cannot configure vcenter")
