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

from ipplanlib import metadata


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


def _deploy_flagged(networks):
    """node_ids of networks carrying the deploy flag: the per-site
    deployment net is site-LOCAL by decree (user, 2026-08-26) -
    routed inside its site, never announced, never reachable
    cross-site."""
    ids = [nid for nid, _, _, _ in networks]
    if not ids:
        return set()
    return {nid for (nid,) in _query(
        'SELECT node_id FROM option WHERE name = "deploy" '
        'AND node_id IN (%s)' % ','.join('?' * len(ids)),
        tuple(ids))}


def _bird(host, params, networks):
    """BIRD from data (P3): pkg=router(asn=N) switches BGP on - no
    asn, no daemon. Announce = every network this router terminates
    (v4 + its derived v6); peers = hosts carrying pkg bgp(asn=N)
    inside those networks (prod's LINK-net convention: the upstream's
    device is an ipplan host with pkg=-default,bgp). The lab declares
    no peer yet - the rendered config is announce-only until one
    appears in the plan."""
    asn = params.get('asn')
    if not asn:
        return None
    # announce only the ROUTABLE networks: deploy-flagged nets are
    # site-local and never leave
    flagged = _deploy_flagged(networks)
    node_ids = [nid for nid, _, _, _ in networks
                if nid not in flagged]
    nets6 = [v6 for (v6,) in _query(
        'SELECT ipv6_txt FROM network WHERE node_id IN (%s) '
        'AND ipv6_txt IS NOT NULL'
        % ','.join('?' * len(node_ids)), tuple(node_ids))]
    cidrs = [ipaddress.ip_network(c) for _, _, c, _ in networks]
    peers = []
    for peer, pparams in metadata.hosts_with_pkg('bgp'):
        if peer == host:
            continue
        ip = metadata.host_ip(peer)
        if not ip or not any(
                ipaddress.ip_address(ip) in c for c in cidrs):
            continue
        peers.append({'ip': ip, 'asn': int(pparams.get('asn', 0))})
    return {
        'asn': int(asn),
        'router_id': metadata.host_ip(host),
        'networks4': sorted(c for nid, _, c, _ in networks
                            if nid not in flagged),
        'networks6': sorted(nets6),
        'peers': sorted(peers, key=lambda p: p['ip']),
    }


def _colovpn(host):
    """The site-to-site WireGuard listener (P5): a site network
    carrying wg=<port> is the tunnel link net - the router terminates
    it as wg0 at the net's computed gateway. Peers = other sites'
    routers declaring uplink=colo; each one's tunnel address is the
    addr= leg that falls inside the link net, and its site networks
    ride along (wg crypto ACL, BGP, firewall). egress=colo on the
    peer means colo also exports a default and masquerades for it."""
    site = metadata.host_site(host)
    link = next((r for r in _query(
        'SELECT n.node_id, n.name, n.ipv4_txt, n.ipv4_gateway_txt, '
        'o.value FROM network n, option o '
        'WHERE o.node_id = n.node_id AND o.name = "wg"')
        if r[1].split('@', 1)[0].lower() == site), None)
    if link is None:
        return None
    link_id, _, cidr, gateway, port = link
    net = ipaddress.ip_network(cidr)
    # wgsrc= (repeatable, /32 or wider, v4 or v6): the listener's
    # DECLARED sources - 0.0.0.0/0 spells open, the compiler refuses
    # a wg= net without any. Peer sites' declared nat=<ip> egress
    # addresses join automatically below (user rule 2026-08-26: if
    # there is a nat, that will be opened); a bare valueless nat
    # declares no address and adds nothing.
    listen_sources = {v for (v,) in _query(
        'SELECT value FROM option WHERE node_id = ? '
        'AND name = "wgsrc"', (link_id,))}
    peers = []
    for peer, pparams in metadata.hosts_with_pkg('router'):
        if peer == host or pparams.get('uplink') != 'colo':
            continue
        tunnel = next(
            (a for (a,) in _query(
                'SELECT o.value FROM option o, host h WHERE '
                'o.node_id = h.node_id AND o.name = "addr" '
                'AND h.name = ?', (peer,))
             if ipaddress.ip_address(a) in net), None)
        if tunnel is None:
            continue
        psite = metadata.host_site(peer)
        label = (metadata.get_current_event() if psite == 'event'
                 else psite)
        # the peer site's ROUTABLE networks: its deploy-flagged nets
        # are just as site-local as ours
        networks = sorted(
            c for nid2, n2, c, vlan in _query(
                'SELECT node_id, name, ipv4_txt, vlan FROM network '
                'WHERE ipv4_txt IS NOT NULL')
            if vlan and n2.split('@', 1)[0].lower() == psite
            and not _query(
                'SELECT 1 FROM option WHERE node_id = ? '
                'AND name = "deploy"', (nid2,)))
        # the peer site's declared nat=<ip> addresses are where its
        # wg packets come from - they open on the listener
        listen_sources.update(
            v for nid2, n2, v in _query(
                'SELECT o.node_id, n.name, o.value FROM option o, '
                'network n WHERE o.node_id = n.node_id '
                'AND o.name = "nat" AND o.value != "1"')
            if n2.split('@', 1)[0].lower() == psite)
        peers.append({
            'site': label, 'asn': int(pparams.get('asn', 0)),
            'tunnel_ip': tunnel,
            'egress': pparams.get('egress', 'local'),
            'networks': networks,
        })
    return {'address': '%s/%d' % (gateway, net.prefixlen),
            'link_net': cidr, 'port': int(port),
            'listen_sources': sorted(listen_sources),
            'peers': sorted(peers, key=lambda p: p['tunnel_ip'])}


def generate(host, params, manifest):
    networks = router_networks(host)
    if not networks:
        return {}
    bird = _bird(host, params, networks)
    vpn = _colovpn(host)
    out = {
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
    if vpn:
        out['dhcolovpn'] = {
            'address': vpn['address'], 'port': vpn['port'],
            'peers': [{'site': p['site'], 'tunnel_ip': p['tunnel_ip'],
                       'networks': p['networks']}
                      for p in vpn['peers']],
        }
        # the wg port's exposure is DECLARED, never implicit (the
        # compiler enforces wgsrc= wherever wg= appears):
        # wgsrc=0.0.0.0/0 or ::/0 spells open (roaming peers, wg
        # authenticates); anything else locks the listener to those
        # sources (/32 or wider, v4 and/or v6). BGP is admitted only
        # on the link net either way.
        if any(s in ('0.0.0.0/0', '::/0', '0.0.0.0')
               for s in vpn['listen_sources']):
            out['dhfirewall']['open_udp'] = [vpn['port']]
        else:
            src4 = [s for s in vpn['listen_sources'] if ':' not in s]
            src6 = [s for s in vpn['listen_sources'] if ':' in s]
            if src4:
                out['dhfirewall']['open_udp_scoped'] = {
                    vpn['port']: src4}
            if src6:
                out['dhfirewall']['open_udp_scoped6'] = {
                    vpn['port']: src6}
        out['dhfirewall']['open_tcp_scoped'] = {179: [vpn['link_net']]}
        # forward permits between this site and the vpn sites (native,
        # no NAT between sites); egress+masquerade only for peers that
        # declared egress=colo
        vpn_nets = sorted({n for p in vpn['peers']
                           for n in p['networks']})
        egress_nets = sorted({n for p in vpn['peers']
                              if p['egress'] == 'colo'
                              for n in p['networks']})
        if vpn_nets:
            out['dhfirewall']['router']['vpn_networks'] = vpn_nets
            # the site side of the crossing: routable nets only -
            # the deploy net never meets another site
            flagged = _deploy_flagged(networks)
            out['dhfirewall']['router']['vpn_site_networks'] = sorted(
                c for nid, _, c, _ in networks if nid not in flagged)
        if egress_nets:
            out['dhfirewall']['router']['vpn_egress_networks'] = \
                egress_nets
    if bird:
        if vpn:
            for p in vpn['peers']:
                bird['peers'].append({
                    'ip': p['tunnel_ip'], 'asn': p['asn'],
                    'export_default': p['egress'] == 'colo'})
            bird['peers'].sort(key=lambda p: p['ip'])
            bird['default_export'] = any(
                p.get('export_default') for p in bird['peers'])
        out['dhbird'] = bird
    return out
