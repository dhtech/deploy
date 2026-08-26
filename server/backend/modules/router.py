# ENC generator for pkg "router": the site router. Everything it does
# is derived from ipplan - a host routes a network iff its address is
# that network's gateway. From those networks come the masquerade list
# (nat option), the DNAT table (expose= on member hosts) and the
# forward permits (precomputed flow pairs that cross between two of
# its networks). The outside interface name is host-level config and
# comes from the manifest params (dhfirewall router_outside), not from
# here. Egress AND intra-site forwarding are permissive by decision
# (2026-08-25): every nat network may initiate outward, and the
# router's own networks talk freely among themselves (the strict
# per-pair forward is emitted but not yet the only permit - unscoped
# services like puppet 8140 have no flow pairs).

import ipaddress
import sqlite3

from lib import metadata


def _query(sql, args=()):
    conn = sqlite3.connect(metadata.DB_FILE)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def router_addrs(host):
    """The router's addresses: its host row plus the addr= option
    (the trunk subinterface legs - one ipplan host line has one
    primary address; the other legs are declared data)."""
    addrs = {ip for (ip,) in _query(
        'SELECT ipv4_addr_txt FROM host WHERE name = ?', (host,))}
    for (extra,) in _query(
            'SELECT o.value FROM option o, host h WHERE '
            'o.node_id = h.node_id AND h.name = ? AND o.name = "addr"',
            (host,)):
        addrs.add(extra)
    return addrs


def router_networks(host):
    """The networks this host routes: its addresses (host row +
    addr= legs) match their gateways.
    Returns [(node_id, name, cidr, has_nat)]."""
    addrs = router_addrs(host)
    out = []
    for node_id, name, cidr, gw in _query(
            'SELECT node_id, name, ipv4_txt, ipv4_gateway_txt '
            'FROM network WHERE ipv4_gateway_txt IS NOT NULL '
            'ORDER BY name'):
        if name.endswith('@DREAMHACK'):
            continue   # the #@ master rows are supernets, not networks
        if gw in addrs:
            nat = bool(_query(
                'SELECT 1 FROM option WHERE node_id = ? '
                'AND name = "nat"', (node_id,)))
            out.append((node_id, name, cidr, nat))
    return out


def _site_networks6():
    import sqlite3
    conn = sqlite3.connect(metadata.DB_FILE)
    rows = conn.execute(
        'SELECT DISTINCT ipv6_txt FROM network '
        'WHERE vlan = 0 AND ipv6_txt IS NOT NULL').fetchall()
    conn.close()
    return sorted(r[0] for r in rows)


def dnat_entries(net_ids):
    """expose=EXT:INT rows on hosts inside the routed networks:
    [{'port': EXT, 'to': 'ip:INT'}], sorted by external port."""
    entries = []
    for ext, ip in _query(
            'SELECT o.value, h.ipv4_addr_txt FROM option o, '
            'host h WHERE o.node_id = h.node_id AND o.name = "expose" '
            'AND h.network_id IN (%s)'
            % ','.join('?' * len(net_ids)), tuple(net_ids)):
        outside, _, inside = ext.partition(':')
        entries.append({'port': int(outside),
                        'to': '%s:%s' % (ip, inside)})
    return sorted(entries, key=lambda e: e['port'])


def forward_rules(host, networks):
    """Flow pairs that cross between two of this router's networks:
    the forward-chain permits, from the same precomputed rules the
    input chains use. {'tcp': {port: [src]}, 'udp': ...} keyed to a
    destination - flattened to rule dicts for the template."""
    nets = {name: ipaddress.ip_network(cidr)
            for _, name, cidr, _ in networks}
    own = router_addrs(host)

    def net_of(ip_txt):
        try:
            addr = ipaddress.ip_network(ip_txt, strict=False)
        except ValueError:
            return None
        for name, net in nets.items():
            if addr.subnet_of(net):
                return name
        return None

    rules = []
    seen = set()
    for src, dst, ports in _query(
            'SELECT from_ipv4, to_ipv4, service_dst_ports '
            'FROM firewall_rule_ip_level WHERE is_ipv4 = 1'):
        if not src or not dst:
            continue
        if src in own or dst in own:
            continue   # traffic to/from the router itself is input/output
        src_net, dst_net = net_of(src), net_of(dst)
        if not src_net or not dst_net or src_net == dst_net:
            continue
        for proto, plist in metadata.ports_by_proto(
                ports.split(',') if ports else []).items():
            for port in plist:
                key = (src, dst, proto, port)
                if key not in seen:
                    seen.add(key)
                    rules.append({'saddr': src, 'daddr': dst,
                                  'proto': proto, 'port': port})
    return sorted(rules, key=lambda r: (r['daddr'], r['proto'],
                                        r['port'], r['saddr']))


def generate(host, params, manifest):
    networks = router_networks(host)
    if not networks:
        return {}
    return {
        'dhfirewall': {
            'router': {
                'nat_networks': sorted(
                    cidr for _, _, cidr, nat in networks if nat),
                # intra-site forward is PERMISSIVE between the
                # router's networks (strictness deferred with the
                # egress decision - unscoped services have no pairs);
                # the flow pairs below are the future strict mode
                'site_networks': sorted(
                    cidr for _, _, cidr, _ in networks),
                # the v6 site supernet (the master /48) - the intra-
                # site permissive forward's v6 mirror; empty until the
                # plan declares IPV6-<SITE>-NET
                'site_networks6': _site_networks6(),
                'dnat': dnat_entries([nid for nid, _, _, _ in networks]),
                'forward': forward_rules(host, networks),
            },
        },
    }
