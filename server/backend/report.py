#!/usr/bin/env python3
# Puppet report hook: puppetserver POSTs every run report here
# (reports = http, reporturl -> this CGI). The first successful run
# after a host is provisioned stamps puppet_time in its host record -
# the status page shows "Done" only from that moment ("Converging"
# in between). Later reports are ignored so the record's TTL keeps
# aging out naturally.

import json
import re
import sys

import runtime
from ipplanlib import metadata

# Puppet posts Ruby-tagged YAML (not safe_load-able); we only need two
# scalar fields, so pull them out with regexes.
raw = sys.stdin.read()
host_m = re.search(r'^host: "?([^"\n]+)"?$', raw, re.MULTILINE)
status_m = re.search(r'^status: "?(\w+)"?$', raw, re.MULTILINE)

print('Content-Type: text/plain')
print('')

if not host_m or not status_m:
    print('ignored')
    sys.exit(0)

host = host_m.group(1)
status = status_m.group(1)

r = runtime.connection()
key = 'host-' + host
raw_rec = r.get(key)
if raw_rec is None or status not in ('changed', 'unchanged'):
    print('ignored')
    sys.exit(0)

rec = json.loads(raw_rec)
if rec.get('provisioned') and not rec.get('puppet_time'):
    import time
    rec['puppet_time'] = int(time.time())
    ttl = r.ttl(key)
    r.setex(key, ttl if ttl and ttl > 0 else 3600, json.dumps(rec))
    print('marked')
else:
    print('ok')
