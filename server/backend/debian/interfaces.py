#!/usr/bin/env python3
# Generate /etc/network/interfaces

import os
import urllib.parse

from lib import metadata

first_if = None
ifs = []
query_string = urllib.parse.parse_qs(os.environ.get('QUERY_STRING', ''))
if 'ifs' in query_string:
    ifs = query_string['ifs'][0].split(',')
    first_if = ifs[0]

ip = os.environ['REMOTE_ADDR']
# The install runs on the deployment VLAN; hack_ip carries the host's
# production (ipplan) address, which is its identity here.
if 'hack_ip' in query_string:
    ip = query_string['hack_ip'][0]

client, cm = metadata.find(ip, first_if)
network = metadata.network(client, cm)
if not network:
    exit(1)

if_template = (
    """# This file describes the network interfaces available on your system
# and how to activate them. For more information, see interfaces(5).

source-directory interfaces.d

# The loopback network interface
auto lo
iface lo inet loopback
""")

if network.bonded:
    if client.os == 'debian':
        if_template = if_template + """
auto bond0
iface bond0 inet manual
    bond-mode 802.3ad
    slaves eth0 eth1
"""
    elif client.os == 'ubuntu':
        if_template = if_template + """
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

if_template = if_template + """
# The primary network interface
auto {vlan_interface}
iface {vlan_interface} inet static
\taddress {v4_address}
\tnetmask {v4_netmask}
\tgateway {v4_gateway}
"""

if network.v6_address:
    if_template = if_template + """
iface {vlan_interface} inet6 static
\taddress {v6_address}
\tnetmask {v6_netmask}
\tgateway {v6_gateway}
"""

vars_template = (
    """v4_address={v4_address}
v4_netmask={v4_netmask}
v4_gateway={v4_gateway}
v6_address={v6_address}
v6_netmask={v6_netmask}
v6_gateway={v6_gateway}
interface={interface}
vlan_interface={vlan_interface}
vlan={vlan}""")

template = (vars_template if 'vars' in query_string else if_template)
print('')
print(template.format(
    v4_address=network.v4_address, v4_netmask=network.v4_netmask,
    v4_gateway=network.v4_gateway, v6_address=network.v6_address,
    v6_netmask=network.v6_netmask, v6_gateway=network.v6_gateway,
    interface=network.interface, vlan_interface=network.vlan_interface,
    vlan=network.vlan))
