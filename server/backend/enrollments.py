#!/usr/bin/env python3
# List hostnames with an active enrollment token (freshly installed
# machines whose puppet cert has not been signed yet), with the token's
# age in seconds. Used by the puppetserver to clean stale certs before
# re-enrollment: only a cert OLDER than the token can be stale — a
# younger one was signed by the current install and must be left alone
# (learned the hard way: the cleaner revoked freshly-signed certs).
# Names and ages only - tokens are never exposed.

from lib import metadata

TOKEN_TTL = 86400  # matches the setex in the debian late script

r = metadata.connection()
print('Content-Type: text/plain')
print('')
for key in sorted(r.keys('enroll-*')):
  ttl = r.ttl(key)
  age = max(0, TOKEN_TTL - ttl) if ttl and ttl > 0 else 0
  print('%s %d' % (key.decode()[len('enroll-'):], age))
