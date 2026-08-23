"""Backend registry. Imports are lazy so unused SDKs are never loaded."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from provisiond.backends.base import HwProvisioner, Provisioner
    from provisiond.config import ManagerConfig
    from provisiond.secrets import Secrets


def create_backend(
    manager_config: ManagerConfig, secrets: Secrets
) -> Provisioner | HwProvisioner:
    kind = manager_config.type
    if kind == "proxmox":
        from provisiond.backends.proxmox import ProxmoxBackend

        return ProxmoxBackend(manager_config, secrets)
    if kind == "vmware":
        from provisiond.backends.vmware import VmwareBackend

        return VmwareBackend(manager_config, secrets)
    if kind == "ocp":
        from provisiond.backends.ocp import OcpBackend

        return OcpBackend(manager_config, secrets)
    raise ValueError(f"unknown manager type: {kind}")
