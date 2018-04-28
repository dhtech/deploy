#!/usr/bin/env python2

import json
import os
import redis
from lib import metadata


# We should be using atomic operations here, but we ignore that since
# the updates *should* be linear since it's only one machine accessing its own
# records.
client, cm = metadata.find(os.environ['REMOTE_ADDR'])

if not cm['installed']:
  network = metadata.network(client, cm)

  # This will tell provisiond to provision for the machine if not already done
  # and also tell ipxe.py to boot to disk as default
  cm['installed'] = True
  cm['client'] = client.__dict__
  cm['network'] = network.__dict__ if network else None

  metadata.update(client, cm)

print ''
print 'provisioned:', cm['provisioned']
