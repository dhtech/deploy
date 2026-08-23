#!/usr/bin/env python3
# Accept inventory data from iPXE and look up the hostname to use.
#
# Identity contract (provisiond3): every VM the provisioner creates is
# published as 'vm-<manager>-<smbios-uuid-lowercase>' in Redis; physical
# machines are keyed 'install-<serial>'. Lookup order is UUID first, then
# serial - no SMBIOS manufacturer sniffing (the old contract only matched
# VMs whose manufacturer contained 'vmware').
#
# Note: Needs /etc/deploy.yaml to contain redis information

import json
import os
import urllib.parse

import redis
import yaml


def connection():
    with open('/etc/deploy.yaml') as f:
        config = yaml.safe_load(f)
    return redis.Redis(**config['redis'])


def handle(contents):
    r = connection()

    uuid = contents.get('uuid', [''])[0].lower()
    serial = contents.get('serial', [''])[0].strip()

    request_json = None
    if uuid:
        keys = r.keys('vm-*-' + uuid)
        if keys:
            request_json = r.get(keys[0])
    if request_json is None and serial:
        request_json = r.get('install-' + serial)

    if 'hostname' in contents:
        return contents['hostname'][0].lower()
    if request_json:
        return json.loads(request_json)['name']
    return None


query_string = urllib.parse.parse_qs(os.environ.get('QUERY_STRING', ''))
hostname = handle(query_string)

print('')
print('#!ipxe')
if hostname:
    print('set hostname %s' % hostname)
else:
    print('echo No hostname found, please enter hostname (FQDN):')
    print('read hostname')

print('echo I am ${hostname}')
