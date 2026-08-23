#!/usr/bin/env python3
# List hostnames with an active enrollment token (freshly installed
# machines whose puppet cert has not been signed yet). Used by the
# puppetserver to clean stale certs before re-enrollment. Names only -
# tokens are never exposed.

from lib import metadata

r = metadata.connection()
print('Content-Type: text/plain')
print('')
for key in sorted(r.keys('enroll-*')):
  print(key.decode()[len('enroll-'):])
