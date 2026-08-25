"""Read-only helpers against the ipplan SQLite database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

DB_FILE = "/etc/ipplan.db"


def host_to_ip(hostname: str, db_file: str = DB_FILE) -> str | None:
    conn = sqlite3.connect(db_file)
    try:
        c = conn.execute("SELECT ipv4_addr_txt FROM host WHERE name = ?", (hostname,))
        res = c.fetchone()
        return str(res[0]) if res else None
    finally:
        conn.close()


def get_vlan(hostname: str, db_file: str = DB_FILE) -> tuple[str | None, int | None]:
    conn = sqlite3.connect(db_file)
    try:
        c = conn.execute(
            "SELECT n.name, vlan FROM host h, network n "
            "WHERE h.network_id = n.node_id AND h.name = ?",
            (hostname,),
        )
        res = c.fetchone()
        return (str(res[0]), int(res[1])) if res else (None, None)
    finally:
        conn.close()


def all_vlans_in_same_domain(
    hostname: str, db_file: str = DB_FILE
) -> Iterator[tuple[str, int]]:
    """Yield (network, vlan) for every VLAN in the host's domain."""
    my_net, _ = get_vlan(hostname, db_file)
    if my_net is None:
        return
    my_domain, _, _ = my_net.partition("@")
    conn = sqlite3.connect(db_file)
    try:
        for network, vlan in conn.execute("SELECT name, vlan FROM network ORDER BY vlan"):
            if vlan == 0:
                continue
            if str(network).startswith(my_domain + "@"):
                yield str(network), int(vlan)
    finally:
        conn.close()
