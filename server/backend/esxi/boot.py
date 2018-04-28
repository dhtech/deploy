#!/usr/bin/env python2

import os
import urlparse

from lib import metadata


query_string = urlparse.parse_qs(os.environ['QUERY_STRING'])
ip = query_string['ip'][0]
#client = metadata.lookup_ip(ip)
client, cm = metadata.find(ip)
network = metadata.network(client, cm)

print ''
with open('esxi/boot.cfg') as file:
  for line in file:
    if line.startswith('kernelopt='):
      print line.rstrip() + " vlanid=%s ip=%s netmask=%s gateway=%s nameserver=8.8.8.8" % (network.vlan, network.v4_address, network.v4_netmask, network.v4_gateway)
    else:
      print line

#print "vlanid=%s" % network.vlan
#print "ip=%s" % network.v4_address
#print "netmask=%s" % network.v4_netmask
#print "gateway=%s" % network.v4_gateway
