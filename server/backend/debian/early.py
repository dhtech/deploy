#!/usr/bin/env python3
# Dynamic early script for preseed/early_command (gen-3).
# Generates the per-host root password, stores it in the secret store
# (OpenBao/Vault KV v1, gen-2 path contract), and preseeds partitioning.
# Identity comes from hack_ip (the host's production address).

import json
import os
import secrets
import string
import urllib.parse
import urllib.request

from lib import metadata

query_string = urllib.parse.parse_qs(
    os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
ip = os.environ['REMOTE_ADDR']
if 'hack_ip' in query_string:
  ip = query_string['hack_ip'][0]

client, cm = metadata.find(ip)
config = metadata.config()

# No look-alike characters (gen-2 parity)
alphabet = ''.join(c for c in string.ascii_letters + string.digits
                   if c not in '01liIoO')
root_pw = ''.join(secrets.choice(alphabet) for _ in range(16))

is_event = client.domain == 'EVENT'
if is_event:
  event = metadata.get_current_event()
  vault_path = 'services-%s/login:%s' % (event, client.hostname)
  luks_path = 'services-%s/luks:%s' % (event, client.hostname)
else:
  vault_path = 'services/login:%s' % client.hostname
  luks_path = 'services/luks:%s' % client.hostname


def vault_write(path, **data):
  """KV v1 write over the plain HTTP API - no client dependency needed."""
  request = urllib.request.Request(
      '%s/v1/%s' % (config['vault_addr'], path),
      data=json.dumps(data).encode(),
      headers={'X-Vault-Token': config['vault_token']},
      method='PUT')
  urllib.request.urlopen(request, timeout=10)


vault_write(vault_path, root_password=root_pw)

passphrase = None
if is_event:
  passphrase = ''.join(secrets.choice(alphabet) for _ in range(32))
  vault_write(luks_path, passphrase=passphrase)

print('')
print('#!/bin/sh')
print('# Dreamhack gen-3 early script for %s' % client.hostname)
print('cat > conf.input <<__EOF__')
print('d-i passwd/root-password password %s' % root_pw)
print('d-i passwd/root-password-again password %s' % root_pw)
if is_event:
  # Full-disk encryption for event machines; skip the slow disk erase.
  print('d-i partman-auto/method string crypto')
  print('d-i partman-crypto/passphrase string %s' % passphrase)
  print('d-i partman-crypto/passphrase-again string %s' % passphrase)
  print('d-i partman-auto-crypto/erase_disks boolean false')
else:
  print('d-i partman-auto/method string lvm')
print('__EOF__')
print('debconf-set-selections conf.input')
print('rm conf.input')
if is_event:
  print('# Passphrase for the late script (LUKS auto-unlock setup)')
  print('printf "%%s" "%s" > /tmp/crypto.pass' % passphrase)
syslog_host = config.get('syslog_host')
if syslog_host:
  print('# Stream installer syslog to the deploy server (live status page)')
  print('kill $(ps | grep "[s]yslogd" | cut -c 1-5) 2>/dev/null')
  print("echo '::respawn:/sbin/syslogd -n -m 0 -O /var/log/syslog -L -S "
        "-R %s' >> /etc/inittab" % syslog_host)
  print('kill -HUP 1')
print('rm -f $0')
print('exit 0')
