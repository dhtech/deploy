#!/usr/bin/env python3
# Dynamic early script for preseed/early_command (gen-3).
# Generates the per-host root password, stores it in the secret store
# (OpenBao/Vault KV v1, gen-2 path contract), and preseeds partitioning.
# Identity comes from fqdn= (validated against ipplan).

import os
import sys
import secrets
import string
import urllib.parse

from lib import metadata

query_string = urllib.parse.parse_qs(
    os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
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
config = metadata.config()

# The installer runs on the deployment VLAN with a DHCP address; record
# the mapping so the syslog receiver can identify it by source IP.
remote = os.environ.get('REMOTE_ADDR', '')
if remote and remote != ip:
    metadata.connection().setex('install-ip-' + remote, 3600, client.hostname)

# No look-alike characters (gen-2 parity)
alphabet = ''.join(c for c in string.ascii_letters + string.digits
                   if c not in '01liIoO')
root_pw = ''.join(secrets.choice(alphabet) for _ in range(16))

is_event = client.domain == 'EVENT'
vault_path = metadata.vault_login_path(client)
admin_pw = ''.join(secrets.choice(alphabet) for _ in range(16))
metadata.vault_write(vault_path, root_password=root_pw,
                     dhtech_password=admin_pw)

passphrase = None
if is_event:
    luks_path = vault_path.replace('/login:', '/luks:')
    passphrase = ''.join(secrets.choice(alphabet) for _ in range(32))
    metadata.vault_write(luks_path, passphrase=passphrase)

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
