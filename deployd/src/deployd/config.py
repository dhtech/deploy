"""Configuration schema and loader for /etc/deployd/config.yaml.

In production this file is delivered by Puppet; config.yaml.sample in the
repo is the reference for the puppet template. Secrets are indirect:
``{env: NAME}`` reads an environment variable, ``{vault: path}`` reads
from Vault at startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

SecretRef = str | dict[str, str] | None


@dataclass(frozen=True)
class RedisConfig:
    host: str
    port: int = 6379
    db: int = 0
    ssl: bool = False
    password: SecretRef = None


@dataclass(frozen=True)
class VaultConfig:
    url: str | None = None
    mount_env: str = "VAULT_MOUNT"


@dataclass(frozen=True)
class ManagerConfig:
    name: str
    type: str  # proxmox | vmware | ocp
    deploy_vlan: int = 0
    # True = system CAs, path = CA bundle, False = no verification (lab only)
    verify_tls: bool | str = True
    # proxmox
    api_url: str | None = None
    token_id: str | None = None
    token_secret: SecretRef = None
    bridge: str = "vmbr0"
    node: str | None = None
    pool: str | None = None
    # vmware
    host: str | None = None
    fqdn: str | None = None
    username: SecretRef = None
    password: SecretRef = None
    # ocp: name -> {mac, ip}
    machines: dict[str, dict[str, str]] | None = None


@dataclass(frozen=True)
class Config:
    redis: RedisConfig
    vault: VaultConfig
    managers: list[ManagerConfig] = field(default_factory=list)


class ConfigError(Exception):
    pass


def _manager(data: dict[str, Any]) -> ManagerConfig:
    known = {f for f in ManagerConfig.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"manager {data.get('name', '?')}: unknown keys {sorted(unknown)}")
    mgr = ManagerConfig(**data)
    if not mgr.name:
        raise ConfigError("manager without a name")
    if mgr.type == "proxmox":
        if not mgr.api_url or not mgr.token_id or mgr.token_secret is None:
            raise ConfigError(f"manager {mgr.name}: proxmox needs api_url, token_id, token_secret")
    elif mgr.type == "vmware":
        if not mgr.host or mgr.username is None or mgr.password is None:
            raise ConfigError(f"manager {mgr.name}: vmware needs host, username, password")
    elif mgr.type == "ocp":
        if not mgr.machines or mgr.username is None or mgr.password is None:
            raise ConfigError(f"manager {mgr.name}: ocp needs machines, username, password")
    else:
        raise ConfigError(f"manager {mgr.name}: unknown type {mgr.type!r}")
    if mgr.verify_tls is False:
        # Allowed for the lab, but never silently.
        import logging

        logging.getLogger(__name__).warning(
            "manager %s: TLS verification DISABLED - lab use only", mgr.name
        )
    return mgr


def load(path: str) -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "redis" not in data:
        raise ConfigError(f"{path}: missing redis section")
    managers = [_manager(m) for m in data.get("managers") or []]
    names = [m.name for m in managers]
    if len(names) != len(set(names)):
        raise ConfigError("duplicate manager names")
    return Config(
        redis=RedisConfig(**data["redis"]),
        vault=VaultConfig(**(data.get("vault") or {})),
        managers=managers,
    )
