#!/usr/bin/env python3
# Render the iPXE boot menu and Debian installer boot line.
#
# Gen-3 flow: the installer runs with DHCP on the deployment VLAN; the
# production network config is written to the installed system and the
# provisioner moves the machine to its production VLAN after the install
# finishes. Legacy ESXi/OpenBSD/CoreOS/Tectonic menu entries are gone.

import os
import urllib.parse

from lib import metadata

query_string = urllib.parse.parse_qs(os.environ.get('QUERY_STRING', ''))
ip = os.environ['REMOTE_ADDR']
# The install runs on the deployment VLAN; hack_ip carries the host's
# production (ipplan) address, which is its identity here.
if 'hack_ip' in query_string:
    ip = query_string['hack_ip'][0]

client, cm = metadata.find(ip)
BASE = metadata.base_url()

mac = query_string.get('mac', [''])[0]
# Force unknown VMware MACs to use VGA installer
is_vga = mac.startswith('00:0c:29:')


def debian(label, vga=False, debug=False, serial='ttyS0', variant='debian'):
    path = '{base}/{variant}-installer/amd64'.format(base=BASE, variant=variant)
    print(':' + label)
    print('kernel {path}/linux'.format(path=path))
    print('initrd {path}/initrd.gz'.format(path=path))

    args = [
        'imgargs', 'linux', 'vga=normal', 'fb=false', 'auto=true',
        'console=tty0', 'priority=high', 'locale=en_US',
        'console-keymaps-at/keymap=se-latin1',
        # Prefer IPv4 during installation: no RA/DHCPv6 waits, no
        # v6-first mirror fetches. The installed system is unaffected.
        'ipv6.disable=1']

    args.append('preseed/url={base}/preseed'.format(base=BASE))
    # Identity for early/late script callbacks: the install runs on the
    # deployment VLAN, so the production address and the deploy base URL
    # ride along on the kernel command line.
    args.append('dh_v4=${v4_address}')
    args.append('dh_base=%s' % BASE)
    args.append('netcfg/choose_interface=auto')
    args.append('netcfg/get_hostname=${shortname}')
    args.append('netcfg/hostname=${shortname}')
    args.append('netcfg/get_domain=${dns_domain}')

    if not vga:
        args.append('console={serial},115200n8'.format(serial=serial))

    if debug:
        args.append('--')
        args.append('DEBCONF_DEBUG=5')

    print(' '.join(args))
    print('boot')


print("""
#!ipxe

imgfree

:menu
menu Dreamhack Deploy System (host: {hostname})
item autoinstall Autoinstall ({os}) {auto_suffix}
item autoinstallvga Autoinstall ({os}) (Force VGA)
item --key s shell Drop to iPXE (s)hell
item --key x exit E(x)it and continue BIOS boot order
""".format(
    hostname=client.hostname if client else 'unknown',
    os=client.os_human if client and client.os_human else 'Autodetect',
    auto_suffix='(VGA)' if (client and client.virtual) or is_vga
    else '(Serial)'))

if cm and cm['installed']:
    default = 'exit'
else:
    default = 'autoinstall'

print('choose --timeout 15000 --default %s selected && goto ${selected} '
      '|| goto %s' % (default, default))

print("""
goto menu

:shell
shell
goto menu

:exit
exit
""")

if not client or not client.os or client.os == 'debian':
    # NOTE: serial installation is the default; our VMs attach their
    # console to a serial port.
    debian('autoinstall', vga=client.virtual if client else is_vga)
    debian('autoinstallvga', vga=True)
elif client.os == 'ubuntu':
    debian('autoinstall', vga=client.virtual if client else is_vga,
           variant='ubuntu')
    debian('autoinstallvga', vga=True, variant='ubuntu')
else:
    print(':autoinstallvga')
    print(':autoinstall')
    print('echo OS %s is not supported by this deploy server' % client.os)
    print('exit')
