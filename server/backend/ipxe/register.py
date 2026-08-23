#!/usr/bin/env python3
# Register metadata about the calling server

import json
import os
import syslog
import urllib.parse

from lib import metadata


def handle(ip, contents):
  r = metadata.connection()

  # Initialize state fields.
  # These will be updated by the provisiond daemons.

  # Virtual machines need to be provisioned (i.e. moved to their
  # production VLAN) when they have been installed; physical machines not.
  manufacturer = contents['manufacturer'][0]
  will_provision = ('vmware' in manufacturer.lower()
                    or 'qemu' in manufacturer.lower())

  data = {
      'installed': False,
      'provisioned': not will_provision,
      'uuid': contents['uuid'][0],
      'manufacturer': manufacturer,
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
# keep_blank_values: QEMU VMs have an empty SMBIOS serial
query_string = urllib.parse.parse_qs(
    os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
# The install runs on the deployment VLAN; hack_ip carries the host's
# production (ipplan) address, which is its identity here.
if 'hack_ip' in query_string:
  ip = query_string['hack_ip'][0]

handle(ip, query_string)

# We need to present a dummy iPXE script to continue the boot process
print('')
print('#!ipxe')
