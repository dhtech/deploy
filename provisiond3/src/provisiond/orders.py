"""Typed Redis contract.

Keys handled by the daemon (all values JSON, written with 1h setex by
their producers):

- ``create-vm-<uuid>``  order written by utils/deploy-vm
- ``host-<fqdn>``       state record written by the backend CGIs
- ``vm-<manager>-<smbios-uuid>``  inventory keys published by us (600s TTL)
- ``install-<serial>`` / ``bays-<manager>``  hardware backends (C7000/OCP)

Error reporting contract: on failure the ``error`` field is written back
onto the same key, preserving the remaining TTL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import redis


@dataclass(frozen=True)
class Ipv4Config:
    vlan: int
    address: str
    prefix: int
    gateway: str


@dataclass(frozen=True)
class CreateOrder:
    """A create-vm-* order as written by utils/deploy-vm."""

    key: str
    manager: str
    name: str
    cpus: int
    memory: int  # bytes
    disk: int  # bytes
    os: str
    datacenter: str | None = None
    datastore: str | None = None
    ipv4: Ipv4Config | None = None
    # Application disk: {size (bytes), filesystem, mountpoint, options}
    appdisk: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, key: str, raw: str | bytes) -> CreateOrder:
        data = json.loads(raw)
        ipv4 = None
        if data.get("ipv4"):
            ipv4 = Ipv4Config(
                vlan=int(data["ipv4"]["vlan"]),
                address=data["ipv4"]["address"],
                prefix=int(data["ipv4"]["prefix"]),
                gateway=data["ipv4"]["gateway"],
            )
        return cls(
            key=key,
            manager=data["manager"],
            name=data["name"],
            cpus=int(data["cpus"]),
            memory=int(data["memory"]),
            disk=int(data["disk"]),
            os=data.get("os", "debian"),
            datacenter=data.get("datacenter"),
            datastore=data.get("datastore"),
            ipv4=ipv4,
            appdisk=data.get("appdisk") or None,
        )


@dataclass
class HostRecord:
    """A host-<fqdn> record as written by the backend CGIs."""

    key: str
    installed: bool
    provisioned: bool
    uuid: str | None
    network: dict[str, Any] | None
    client: dict[str, Any] | None
    raw: dict[str, Any]

    @classmethod
    def from_json(cls, key: str, raw: str | bytes) -> HostRecord:
        data = json.loads(raw)
        return cls(
            key=key,
            installed=bool(data.get("installed", False)),
            provisioned=bool(data.get("provisioned", False)),
            uuid=(data.get("uuid") or None),
            network=data.get("network") or None,
            client=data.get("client") or None,
            raw=data,
        )

    def to_json(self) -> str:
        data = dict(self.raw)
        data["installed"] = self.installed
        data["provisioned"] = self.provisioned
        return json.dumps(data)


def write_error(conn: redis.Redis, key: str, data: dict[str, Any], exc: Exception) -> None:
    """Write the error back onto the key, preserving the remaining TTL."""
    data = dict(data)
    data["error"] = f"{exc.__class__.__name__}: {exc}"
    ttl = cast(int, conn.ttl(key))
    if ttl <= 0:
        ttl = 3600
    conn.setex(key, ttl, json.dumps(data))
