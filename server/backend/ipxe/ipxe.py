#!/usr/bin/env python2

import os
import urlparse
from lib import metadata 

query_string = urlparse.parse_qs(os.environ['QUERY_STRING'])
# HACK(bluecmd): Since bnx2 iPXE doesn't like VLAN, we need to provide a way to
# override IP in order to not screw the whole design up.
ip = os.environ['REMOTE_ADDR']
if 'hack_ip' in query_string:
  ip = query_string['hack_ip'][0]

client, cm = metadata.find(ip)

mac = query_string['mac'][0]
# Force unknown VMware MACs to use VGA installer
is_vga = mac.startswith('00:0c:29:')

def debian(label, vga=False, debug=False, serial='ttyS0', variant='debian'):
  path = 'https://deploy.tech.dreamhack.se/{variant}-installer/amd64'.format(
          variant=variant)
  print ':' + label
  print 'kernel {path}/linux'.format(path=path)
  print 'initrd {path}/initrd.gz'.format(path=path)

  args = [
      'imgargs', 'linux', 'vga=normal', 'fb=false', 'auto=true', 'console=tty0',
      'priority=high', 'locale=en_US', 'console-keymaps-at/keymap=se-latin1' ]

  if variant == 'ubuntu':
    # Ubuntu has better netcfg so we don't have to do a lot of hacks
    # Skip straight to start-preinstall
    args.append('preseed/early_command="/start-preinstall deploy.tech.dreamhack.se"')
    args.append('preseed/url=https://deploy.tech.dreamhack.se/debian/preseed-ubuntu')
    args.append('netcfg/choose_interface=auto')
    args.append('netcfg/get_hostname=${shortname}')
    args.append('netcfg/hostname=${shortname}')
    args.append('netcfg/get_domain=${dns_domain}')
  else:
    args.append('preseed/early_command="/early-launch deploy.tech.dreamhack.se"')
    args.append('preseed/url=https://deploy.tech.dreamhack.se/preseed')
    args.append('netcfg/get_hostname=${hostname}')
    args.append('netcfg/hostname=${hostname}')
    args.append('netcfg/get_domain=unassigned-domain')

  args.append('netcfg/disable_dhcp=true')
  args.append('netcfg/confirm_static=true')
  args.append('netcfg/get_ipaddress=${v4_address}')
  args.append('netcfg/get_netmask=${v4_netmask}')
  args.append('netcfg/get_gateway=${v4_gateway}')
  args.append('netcfg/get_nameservers=8.8.8.8')
  args.append('netcfg/vlan_id=${vlan}')

  if not vga:
    args.append('console={serial},9600n8'.format(serial=serial))

  if debug:
    args.append('--')
    args.append('DEBCONF_DEBUG=5')

  print ' '.join(args)
  print 'boot'

print """
#!ipxe

imgfree

:menu
menu Dreamhack Deploy System (host: {hostname})
item autoinstall Autoinstall ({os}) {auto_suffix}
item autoinstallvga Autoinstall ({os}) (Force VGA)
item esxi ESXi install
item --key s shell Drop to iPXE (s)hell
item --key x exit E(x)it and continue BIOS boot order
""".format(
  hostname=client.hostname,
  os=client.os_human if client and client.os_human else 'Autodetect',
  auto_suffix='(VGA)' if client and client.virtual or is_vga else '(Serial)')

if cm and cm['installed']:
  default = 'exit' if cm and cm['installed'] else 'autoinstall'
else:
  default = 'autoinstall'

print ('choose --timeout 15000 --default %s selected && goto ${selected} '
       '|| goto %s' % (default, default))

print """
goto menu

:shell
shell
goto menu

:exit
exit

:esxi
  kernel https://deploy.tech.dreamhack.se/esxi/mboot.c32 -c https://deploy.tech.dreamhack.se/esxi-boot.py?ip=%s
  boot

""" % (ip)

if not client or not client.os or client.os == 'debian':
  # NOTE(bluecmd): Default *must* be serial port installation, ttyS0
  # as long as we're using iLO 2 servers. The VSP in iLO 2 is slow and doesn't
  # seem to react to navigation in the menus, so let's use ttyS0 as the default.
  debian('autoinstall', vga=client.virtual if client else is_vga)
  debian('autoinstallvga', vga=True)
elif client.os == 'ubuntu':
  debian('autoinstall', vga=client.virtual if client else is_vga,
          variant='ubuntu')
  debian('autoinstallvga', vga=True, variant='ubuntu')
else:
  print ':autoinstallvga'
  print ':autoinstall'
  if client.os == 'openbsd':
    print 'initrd https://deploy.tech.dreamhack.se/dh-obsd-5.8-amd64.iso'
    print 'chain https://deploy.tech.dreamhack.se/memdisk iso raw'
  elif client.os == 'esxi':
    print 'kernel https://deploy.tech.dreamhack.se/esxi/mboot.c32 -c https://deploy.tech.dreamhack.se/esxi-boot.py?ip=%s' % ip
  elif client.os == 'cdrom':
    print 'exit'
  elif client.os == 'coreos':
    print 'kernel https://deploy.tech.dreamhack.se/coreos/coreos_production_pxe.vmlinuz coreos.first_boot=1 coreos.config.url=oem:///install.ign coreos.autologin=tty1'
    print 'initrd https://deploy.tech.dreamhack.se/coreos/coreos_production_pxe_image.cpio.gz'
    print 'initrd https://deploy.tech.dreamhack.se/coreos/oem.cpio.py?hostname={hostname}'.format(hostname=client.hostname)
  elif client.os == 'tectonic':
    print 'chain http://provision-esx.event.dreamhack.se:8080/boot.ipxe'
  print 'boot'
