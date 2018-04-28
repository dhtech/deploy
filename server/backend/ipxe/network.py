#!/usr/bin/env python2
# Configure iPXE network to production network

import os
import urlparse

from lib import metadata


query_string = urlparse.parse_qs(os.environ['QUERY_STRING'])
hostname = query_string['hostname'][0]
network = metadata.installation_network(hostname)

print ''
print '#!ipxe'
for key, value in network.iteritems():
  print 'set', key, value

if 'noset' not in query_string:
  # Remove DHCP settings
  print 'set net0/ip 0.0.0.0'

  # Apply settings to iPXE
  print 'vcreate --tag ${vlan} net0'
  print 'set net0-${vlan}/ip ${v4_address}'
  print 'set net0-${vlan}/netmask ${v4_netmask}'
  print 'set net0-${vlan}/gateway ${v4_gateway}'
else:
  # HACK(bluecmd): Since bnx2 iPXE doesn't like VLAN, we need to provide a way to
  # override IP in order to not screw the whole design up.
  print 'echo HACK: No-set hack enabled, will not switch over to VLAN'

print 'echo My IP is ${v4_address} on VLAN ${vlan}'
print 'sleep 3'
