#!/usr/bin/env python2
# Accept inventory data from iPXE and looks up the hostname to use.
# This replaces inventoryd and inventory.py
# TODO(bluecmd): Remove mention of inventoryd and inventory.py
# Note: Needs /etc/deploy.yaml to contain redis information

import json
import os
import redis
import syslog
import urlparse
import yaml

from lib import metadata


def handle(contents):
  r = metadata.connection()

  data = {
      'uuid': contents['uuid'][0],
      'manufacturer': contents['manufacturer'][0],
      'serial': contents['serial'][0]
     }

  request_json = None
  if 'vmware' in data['manufacturer'].lower():
    keys = r.keys('vmware-*-' + data['uuid'].lower())
    if not keys:
      return
    key = keys[0]
    request_json = r.get(key)
  else:
    # If platform not known, use serial number
    request_json = r.get('install-' + data['serial'].strip())

  if 'hostname' in contents:
    hostname = contents['hostname'][0].lower()
  elif request_json:
    request = json.loads(request_json)
    hostname = request['name']
  else:
    return
  return hostname


query_string = urlparse.parse_qs(os.environ['QUERY_STRING'])
hostname = handle(query_string)

print ''
print '#!ipxe'
if hostname:
  print 'set hostname %s' % hostname
else:
  # TODO(bluecmd): Enable this to allow users to enter hostname on
  # non-managed hosts
  print 'echo No hostname found, please enter hostname (FQDN):'
  print 'read hostname'

print 'echo I am ${hostname}'
