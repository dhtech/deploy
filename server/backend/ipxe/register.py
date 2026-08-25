#!/usr/bin/env python3
# Register metadata about the calling server

import json
import os
import syslog
import time
import urllib.parse

from lib import metadata


def verify_vm_identity(r, hostname, uuid):
    """The fqdn claim is VERIFIABLE for virtual machines: provisiond
    publishes vm-<manager>-<smbios-uuid> records at create time, so a
    claimed name must agree with the presented SMBIOS uuid - in both
    directions. No record (physical hosts, expired window): first-seen,
    as always."""
    claimed = None
    if uuid:
        for key in r.keys('vm-*-' + uuid.lower()):
            claimed = json.loads(r.get(key))['name']
    if claimed is not None and claimed != hostname:
        return 'uuid %s belongs to %s, not %s' % (uuid, claimed, hostname)
    for key in r.keys('vm-*'):
        record = json.loads(r.get(key))
        if (record.get('name') == hostname
                and (not uuid
                     or not key.decode().endswith(uuid.lower()))):
            return '%s is a VM being created under another uuid' % hostname
    return None


def handle(hostname, contents):
    r = metadata.connection()

    denial = verify_vm_identity(r, hostname,
                                contents.get('uuid', [''])[0])
    if denial:
        syslog.syslog(syslog.LOG_ERR, 'registration DENIED: ' + denial)
        print('Status: 403')
        print('')
        print(denial)
        sys.exit(0)

    # Initialize state fields.
    # These will be updated by the provisiond daemons.

    # Virtual machines need to be provisioned (i.e. moved to their
    # production VLAN) when they have been installed; physical machines not.
    manufacturer = contents['manufacturer'][0]
    will_provision = ('vmware' in manufacturer.lower()
                      or 'qemu' in manufacturer.lower())

    data = {
        'installed': False,
        'provisioned': not will_provision,
        'uuid': contents['uuid'][0],
        'manufacturer': manufacturer,
        'serial': contents['serial'][0],
        'product': contents['product'][0],
        'started': int(time.time())
    }

    data_str = json.dumps(data)
    syslog.syslog(syslog.LOG_INFO,
                  'Registered metadata for %s: %s' % (hostname, data_str))
    r.setex('host-' + hostname, 3600, data_str)
    r.delete('last-log-' + hostname)
    return hostname


# keep_blank_values: QEMU VMs have an empty SMBIOS serial
query_string = urllib.parse.parse_qs(
    os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
try:
    # identity: fqdn only (beta) - must name a host row in ipplan
    hostname = metadata.request_host(query_string)
except metadata.IdentityError as error:
    print('Status: 403')
    print('')
    print(error)
    sys.exit(0)

handle(hostname, query_string)

# We need to present a dummy iPXE script to continue the boot process
print('')
print('#!ipxe')
