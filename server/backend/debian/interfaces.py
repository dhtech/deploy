#!/usr/bin/env python2
# Generate /etc/network/interfaces

import os
import urlparse

from lib import metadata

first_if = None
if 'QUERY_STRING' in os.environ:
  query_string = urlparse.parse_qs(os.environ['QUERY_STRING'])
  ifs = query_string['ifs'][0].split(',')
  first_if = ifs[0]

client, cm = metadata.find(os.environ['REMOTE_ADDR'], first_if)
network = metadata.network(client, cm, )
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
	address {v4_address}
	netmask {v4_netmask}
	gateway {v4_gateway}

iface {vlan_interface} inet6 static
	address {v6_address}
	netmask {v6_netmask}
	gateway {v6_gateway}
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

template = (vars_template if 'vars' in os.environ['QUERY_STRING']
            else if_template)
print ''
print template.format(
    v4_address=network.v4_address, v4_netmask=network.v4_netmask,
    v4_gateway=network.v4_gateway, v6_address=network.v6_address,
    v6_netmask=network.v6_netmask, v6_gateway=network.v6_gateway,
    interface=network.interface, vlan_interface=network.vlan_interface,
    vlan=network.vlan)

