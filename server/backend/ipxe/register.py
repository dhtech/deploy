#!/usr/bin/env python2
# Register metadata about the calling server

import json
import os
import syslog
import urlparse

from lib import metadata


def handle(ip, contents):
  r = metadata.connection()

  # Initialize state fields.
  # These will be updated by our provisiond slaves all around the world.

  # VMware machines will need to be provisioned (i.e. change network)
  # when they have been installed. Physical machines not.
  will_provision = 'vmware' in contents['manufacturer'][0].lower()

  data = {
      'installed': False,
      'provisioned': not will_provision,
      'uuid': contents['uuid'][0],
      'manufacturer': contents['manufacturer'][0],
      'serial': contents['serial'][0],
      'product': contents['product'][0]
     }

  hostname = metadata.lookup_ip(ip)
  data_str = json.dumps(data)
  syslog.syslog(syslog.LOG_INFO,
      'Registered metadata for %s: %s' % (hostname, data_str))
  r.setex('host-' + hostname, 3600, data_str)
  r.delete('last-log-' + hostname)
  return hostname

ip = os.environ['REMOTE_ADDR']
query_string = urlparse.parse_qs(os.environ['QUERY_STRING'])
# HACK(bluecmd): Since bnx2 iPXE doesn't like VLAN, we need to provide a way to
# override IP in order to not screw the whole design up.
if 'hack_ip' in query_string:
  ip = query_string['hack_ip'][0]

handle(ip, query_string)

# We need to present a dummy iPXE script to continue the boot process
print ''
print '#!ipxe'
