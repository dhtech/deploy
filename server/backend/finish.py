#!/usr/bin/env python3

import os
import time
import urllib.parse

from lib import metadata

# We should be using atomic operations here, but we ignore that since
# the updates *should* be linear since it's only one machine accessing its
# own records.
query_string = urllib.parse.parse_qs(os.environ.get('QUERY_STRING', ''))
ip = os.environ['REMOTE_ADDR']
# The install runs on the deployment VLAN; hack_ip carries the host's
# production (ipplan) address, which is its identity here.
if 'hack_ip' in query_string:
  ip = query_string['hack_ip'][0]

client, cm = metadata.find(ip)

if not cm['installed']:
  network = metadata.network(client, cm)

  # This will tell provisiond to provision the machine if not already done
  # and also tell ipxe.py to boot to disk as default
  cm['installed'] = True
  cm['finished'] = int(time.time())
  cm['client'] = client._asdict()
  cm['network'] = network._asdict() if network else None

  metadata.update(client, cm)

print('')
print('provisioned:', cm['provisioned'])
