#!/usr/bin/env python3
# Autosign policy endpoint: puppet1 asks us whether a CSR may be signed.
# The enrollment token was minted by late.py during the install and is
# one-time use.

import os
import urllib.parse

from lib import metadata

query_string = urllib.parse.parse_qs(
    os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
hostname = query_string.get('hostname', [''])[0]
token = query_string.get('token', [''])[0]

r = metadata.connection()
expected = r.get('enroll-' + hostname)

print('Content-Type: text/plain')
if expected is not None and token and expected.decode() == token:
    r.delete('enroll-' + hostname)  # one-time
    # lifecycle stamp: the host's puppet cert is being signed right now
    raw = r.get('host-' + hostname)
    if raw is not None:
        import json
        import time
        rec = json.loads(raw)
        if not rec.get('puppet_ssl_time'):
            rec['puppet_ssl_time'] = int(time.time())
            ttl = r.ttl('host-' + hostname)
            r.setex('host-' + hostname, ttl if ttl and ttl > 0 else 3600,
                    json.dumps(rec))
    print('Status: 200')
    print('')
    print('ok')
else:
    print('Status: 403')
    print('')
    print('denied')
