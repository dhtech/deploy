# ENC generator for pkg "jumpgate": the ssh entry points (marker pkg
# for the firewall's jumpgate list - base owns dhfirewall). They face
# the internet on ssh, so they get sshd brute-force protection; the
# other jumpgates (and thereby the lab's masquerade source) are never
# banned - that would lock everyone out.

from lib import metadata


def generate(host, params, manifest):
    out = {
        'dhfail2ban': {
            'ignore_ips': sorted(metadata.host_ip(h) for h, _ in
                                 metadata.hosts_with_pkg('jumpgate')),
            'jails': {'sshd': {'backend': 'systemd'}},
        },
    }
    # ssh reaches a jumpgate from OUTSIDE only through the router's
    # 2022 DNAT (user decision 2026-08-26): port 22 accepts just the
    # router's masquerade address (the DNAT'ed entry traffic) - plus
    # the built-in jumpgates rule for admin hops. The deploy server
    # (also jumpgate-pkg) keeps 22 open: its ssh is the slirp-only
    # mgmt door (workstation hostfwd 4455).
    if any(pkg == 'deploy'
           for pkg, _ in metadata.pkgs_with_params(host)):
        out['dhfirewall'] = {'open_tcp': [22]}
    else:
        gateway = _own_gateway(host)
        out['dhfirewall'] = {
            'open_tcp_scoped': {22: [gateway] if gateway else []}}
    return out


def _own_gateway(host):
    import sqlite3
    conn = sqlite3.connect(metadata.DB_FILE)
    row = conn.execute(
        'SELECT n.ipv4_gateway_txt FROM network n, host h '
        'WHERE h.name = ? AND n.node_id = h.network_id',
        (host,)).fetchone()
    conn.close()
    return row[0] if row else None
