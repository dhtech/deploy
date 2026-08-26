#!/usr/bin/env python3
# Generate /etc/network/interfaces
#
# Normal hosts: one primary interface on their ipplan network (VMs get
# the plain iface - the hypervisor tags the VLAN; physicals get
# iface.vlan). ROUTER-shaped hosts (any host with addr= legs): a
# trunk on ifs[0] - the native-flagged network untagged, every other
# vlan'd leg as a subinterface - plus dedicated NICs for the legs in
# un-vlan'd networks (the OUTSIDE slirp net), where the default route
# lives. A vlan'd othernet (MGMT) is a trunk subinterface like any
# other vlan leg.

import ipaddress
import os
import sqlite3
import sys
import urllib.parse

from lib import metadata

HEADER = (
    """# This file describes the network interfaces available on your system
# and how to activate them. For more information, see interfaces(5).

source-directory interfaces.d

# The loopback network interface
auto lo
iface lo inet loopback
""")


def router_legs(hostname):
    """The router's legs: (address, netmask, vlan, native, gateway)
    per network the host has an address in - the host row's primary
    plus every addr= option, mapped to its enclosing network."""
    conn = sqlite3.connect(metadata.DB_FILE)
    c = conn.cursor()
    addrs = [ip for (ip,) in c.execute(
        'SELECT ipv4_addr_txt FROM host WHERE name = ?', (hostname,))]
    addrs += [v for (v,) in c.execute(
        'SELECT o.value FROM option o, host h WHERE o.node_id = '
        'h.node_id AND h.name = ? AND o.name = "addr"', (hostname,))]
    networks = []
    rows = c.execute(
        'SELECT node_id, name, ipv4_txt, ipv4_netmask_txt, vlan, '
        'ipv4_gateway_txt, ipv6_gateway_txt, ipv6_txt '
        'FROM network WHERE ipv4_txt IS NOT NULL'
    ).fetchall()   # fetch first: the opts query below reuses the cursor
    for node_id, name, cidr, netmask, vlan, gw, gw6, net6 in rows:
        if name.endswith('@DREAMHACK'):
            continue
        opts = {n for (n,) in c.execute(
            'SELECT name FROM option WHERE node_id = ?', (node_id,))}
        try:
            networks.append((ipaddress.ip_network(cidr), netmask,
                             vlan or 0, 'native' in opts,
                             'othernet' in opts, gw, gw6, net6))
        except ValueError:
            continue
    conn.close()
    legs = []
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        for net, netmask, vlan, native, othernet, gw, gw6, net6 in networks:
            if ip in net:
                legs.append({'address': addr, 'netmask': netmask,
                             'vlan': vlan, 'native': native,
                             'othernet': othernet, 'gateway': gw,
                             # the router IS the v6 gateway (::1): its
                             # leg address is the network's gateway
                             'address6': gw6,
                             'prefixlen6': (net6.split('/')[1]
                                            if net6 else None)})
                break
    return legs


def router_config(hostname, ifs):
    """Trunk + outside stanzas. ifs[0] is the trunk (the NIC the
    machine PXE-booted on: its native VLAN is the deployment
    network); further NICs take the un-vlan'd legs in order."""
    trunk = ifs[0]
    extra = list(ifs[1:])
    out = [HEADER]
    for leg in sorted(router_legs(hostname),
                      key=lambda leg: (not leg['native'], leg['vlan'])):
        if leg['native']:
            iface = trunk
        elif leg['vlan']:
            iface = '%s.%d' % (trunk, leg['vlan'])
        else:
            if not extra:
                continue
            iface = extra.pop(0)
        out.append('\nauto %s\niface %s inet static' % (iface, iface))
        out.append('\taddress %s' % leg['address'])
        out.append('\tnetmask %s' % leg['netmask'])
        if '.' in iface:
            out.append('\tvlan-raw-device %s' % trunk)
        # the default route lives on the outside leg only
        if not leg['vlan'] and not leg['native'] and leg['gateway']:
            out.append('\tgateway %s' % leg['gateway'])
        if leg.get('address6'):
            out.append('\niface %s inet6 static' % iface)
            out.append('\taddress %s/%s' % (leg['address6'],
                                             leg['prefixlen6'] or '64'))
    return '\n'.join(out) + '\n'


def host_config(client, network, ifs):
    template = HEADER
    if network.bonded:
        if client.os == 'debian':
            template += """
auto bond0
iface bond0 inet manual
    bond-mode 802.3ad
    slaves eth0 eth1
"""
        elif client.os == 'ubuntu':
            template += """
auto {if0}
iface {if0} inet manual
    bond-master bond0

auto {if1}
iface {if1} inet manual
    bond-master bond0

auto bond0
iface bond0 inet manual
    bond-mode 802.3ad
    bond-slaves none
""".format(if0=ifs[0], if1=ifs[1])

    template += """
# The primary network interface
auto {vlan_interface}
iface {vlan_interface} inet static
\taddress {v4_address}
\tnetmask {v4_netmask}
\tgateway {v4_gateway}
"""
    if network.v6_address:
        template += """
iface {vlan_interface} inet6 static
\taddress {v6_address}
\tnetmask {v6_netmask}
\tgateway {v6_gateway}
"""
    return template


VARS_TEMPLATE = (
    """v4_address={v4_address}
v4_netmask={v4_netmask}
v4_gateway={v4_gateway}
v6_address={v6_address}
v6_netmask={v6_netmask}
v6_gateway={v6_gateway}
interface={interface}
vlan_interface={vlan_interface}
vlan={vlan}""")


def is_router(hostname):
    conn = sqlite3.connect(metadata.DB_FILE)
    c = conn.cursor()
    c.execute('SELECT 1 FROM option o, host h WHERE o.node_id = '
              'h.node_id AND h.name = ? AND o.name = "addr"',
              (hostname,))
    res = c.fetchone()
    conn.close()
    return bool(res)


def main():
    first_if = None
    ifs = []
    query_string = urllib.parse.parse_qs(os.environ.get('QUERY_STRING', ''))
    if 'ifs' in query_string:
        ifs = query_string['ifs'][0].split(',')
        first_if = ifs[0]

    try:
        # identity: fqdn only (beta) - must name a host row in ipplan;
        # anomalies answer 403 and are SEEN in the logs
        hostname = metadata.request_host(query_string)
    except metadata.IdentityError as error:
        print('Status: 403')
        print('')
        print(error)
        sys.exit(0)

    if is_router(hostname) and 'vars' not in query_string:
        print('')
        print(router_config(hostname, ifs or ['eth0', 'eth1']))
        return

    client, cm = metadata.find(hostname, first_if)
    network = metadata.network(client, cm)
    if not network:
        sys.exit(1)

    template = (VARS_TEMPLATE if 'vars' in query_string
                else host_config(client, network, ifs))
    print('')
    print(template.format(
        v4_address=network.v4_address, v4_netmask=network.v4_netmask,
        v4_gateway=network.v4_gateway, v6_address=network.v6_address,
        v6_netmask=network.v6_netmask, v6_gateway=network.v6_gateway,
        interface=network.interface, vlan_interface=network.vlan_interface,
        vlan=network.vlan))


if __name__ == '__main__':
    main()
