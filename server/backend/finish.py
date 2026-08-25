#!/usr/bin/env python3

import os
import sys
import time
import urllib.parse

from lib import metadata

# We should be using atomic operations here, but we ignore that since
# the updates *should* be linear since it's only one machine accessing its
# own records.
query_string = urllib.parse.parse_qs(os.environ.get('QUERY_STRING', ''))
try:
    # identity: fqdn only (beta) - must name a host row in ipplan;
    # anomalies answer 403 and are SEEN in the logs
    hostname = metadata.request_host(query_string)
except metadata.IdentityError as error:
    print('Status: 403')
    print('')
    print(error)
    sys.exit(0)

client, cm = metadata.find(hostname)

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
