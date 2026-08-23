#!/usr/bin/env python3
# Dynamic late script for preseed/late_command (gen-3, replaces the
# static gen-2 post-install). Emits a per-host shell script that runs in
# the d-i environment with /target mounted. Identity via hack_ip.

import os
import secrets
import urllib.parse

from lib import metadata

query_string = urllib.parse.parse_qs(
    os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
ip = os.environ['REMOTE_ADDR']
if 'hack_ip' in query_string:
  ip = query_string['hack_ip'][0]

client, cm = metadata.find(ip)
config = metadata.config()
base = metadata.base_url()

jumpgates = config.get('jumpgates', [])
puppet_server = config.get('puppet_server', 'puppet.tech.dreamhack.se')
resolvers = config.get('resolvers', ['8.8.8.8'])
ssh_port = int(config.get('ssh_port', 22))

jump_set = ', '.join(jumpgates) if jumpgates else '127.0.0.1'
resolv_lines = '\\n'.join('nameserver %s' % r for r in resolvers)

print('')
print('#!/bin/sh')
print('# Dreamhack gen-3 late script for %s' % client.hostname)
print('set -x')
print('exec > /target/var/tmp/late.log 2>&1')

# --- production network config (immutable, from ipplan) ---
print('ifs=$(ls /sys/class/net/ | grep -v lo | sort | tr "\\n" "," )')
print('wget -q -O /target/etc/network/interfaces '
      '"%s/interfaces.py?ifs=${ifs}&hack_ip=%s"' % (base, ip))
print('in-target chattr +i /etc/network/interfaces || true')
print('printf "%s\\n" > /target/etc/resolv.conf' % resolv_lines)

# --- nftables baseline: default deny until puppet takes over ---
print('cat > /target/etc/nftables.conf <<\'__NFT__\'')
print('''#!/usr/sbin/nft -f
# Dreamhack install-time baseline - replaced by puppet on first run.
flush ruleset

table inet filter {
  chain input {
    type filter hook input priority filter; policy drop;
    ct state established,related accept
    ct state invalid drop
    iif "lo" accept
    ip protocol icmp accept
    meta l4proto ipv6-icmp accept
    tcp dport %d ip saddr { %s } accept
  }
  chain forward { type filter hook forward priority filter; policy drop; }
  chain output { type filter hook output priority filter; policy accept; }
}''' % (ssh_port, jump_set))
print('__NFT__')
print('in-target systemctl enable nftables')

# --- ssh: jumpgate key access; custom port if configured ---
print('mkdir -p /target/root/.ssh')
print('wget -q -O /target/root/.ssh/authorized_keys "%s/data/authorized_keys"'
      % base)
print('chmod 700 /target/root/.ssh; chmod 600 /target/root/.ssh/authorized_keys')
if ssh_port != 22:
  print('sed -i "s/^#\\?Port 22/Port %d/" /target/etc/ssh/sshd_config' % ssh_port)

# --- puppet agent: configured, enabled, never run during install ---
# One-time enrollment token: puppet1's autosign policy validates it
# against us (autosign.py) on the first agent run.
enroll_token = secrets.token_hex(16)
metadata.connection().setex('enroll-' + client.hostname, 86400, enroll_token)
print('mkdir -p /target/etc/puppet')
print('cat > /target/etc/puppet/csr_attributes.yaml <<__EOF__')
print('custom_attributes:')
print("  1.2.840.113549.1.9.7: '%s'" % enroll_token)
print('__EOF__')
print('chmod 600 /target/etc/puppet/csr_attributes.yaml')
print('cat > /target/etc/puppet/puppet.conf <<__EOF__')
print('''[main]
server=%s

[agent]
runinterval=10m''' % puppet_server)
print('__EOF__')
print('in-target systemctl enable puppet || true')

# --- misc goodies (gen-2 parity) ---
print('echo "APT::Install-Recommends \\"0\\";" '
      '> /target/etc/apt/apt.conf.d/70debconf')
print('printf "precedence ::ffff:0:0/96  100\\n" > /target/etc/gai.conf')
print('echo "net.ipv6.conf.all.autoconf=0" >> /target/etc/sysctl.conf')
print('rm -f /target/etc/apt/apt.conf')
print('sed -i \'s/^GRUB_CMDLINE_LINUX=.*/GRUB_CMDLINE_LINUX="console=tty0 '
      'console=ttyS0,115200"/\' /target/etc/default/grub')
print('sed -i "/^GRUB_TERMINAL/d" /target/etc/default/grub')
print('echo \'GRUB_TERMINAL="console serial"\' >> /target/etc/default/grub')
print('in-target update-grub')
# EFI fallback path (\EFI\BOOT\BOOTX64.EFI): OVMF NVRAM boot entries can
# go missing (seen once: VM stranded in the UEFI shell after install);
# the removable-media fallback boots regardless. debconf keeps it across
# grub package upgrades.
print('echo "grub-efi-amd64 grub2/force_efi_extra_removable boolean true"'
      ' | in-target debconf-set-selections')
print('in-target grub-install --force-extra-removable')
print('wget -q -O /target/root/.vimrc "%s/data/vimrc" || true' % base)

# --- LUKS auto-unlock (EVENT machines; passphrase from early.py) ---
print('''if [ -f /tmp/crypto.pass ]; then
  PASSPHRASE=$(cat /tmp/crypto.pass)
  cat > /target/usr/local/sbin/dh-unlock-disk <<__EOF__
#!/bin/sh
echo -n "$PASSPHRASE"
__EOF__
  chmod 0500 /target/usr/local/sbin/dh-unlock-disk
  echo "$PASSPHRASE" > /target/root/.dh-luks-pw
  chmod 0400 /target/root/.dh-luks-pw
  sed -i "s/none luks/none luks,keyscript=\\/usr\\/local\\/sbin\\/dh-unlock-disk/" \\
    /target/etc/crypttab
  in-target update-initramfs -k all -u
fi''')

# --- admin user: operator access (CIS hardening denies root SSH) ---
admin_pw = metadata.vault_read(metadata.vault_login_path(client)).get(
    'dhtech_password', '')
print('in-target useradd -m -s /bin/bash -G sudo dhtech || true')
print('mkdir -p /target/home/dhtech/.ssh')
print('wget -q -O /target/home/dhtech/.ssh/authorized_keys '
      '"%s/data/authorized_keys"' % base)
print('in-target chown -R dhtech:dhtech /home/dhtech/.ssh')
print('chmod 700 /target/home/dhtech/.ssh')
print('chmod 600 /target/home/dhtech/.ssh/authorized_keys')
if admin_pw:
  # in-target does not pass stdin through (log-output wrapper), so the
  # redirect must happen inside the chroot.
  print('printf "dhtech:%%s" "%s" > /target/tmp/.dh-pw' % admin_pw)
  print("in-target sh -c 'chpasswd < /tmp/.dh-pw'")
  print('rm -f /target/tmp/.dh-pw')

# --- CIS hardening (production post-install, versioned in the repo) ---
print('wget -q -O /target/tmp/dh-hardening "%s/data/post-install-hardening"'
      % base)
print('in-target bash /tmp/dh-hardening '
      '> /target/var/tmp/hardening.log 2>&1 || true')
print('rm -f /target/tmp/dh-hardening')

# --- application disk: one disk, vgapp VG, one ext4 LV per package ---
appdisks = metadata.get_appdisks(client.hostname)
if appdisks:
  # LVM runs in the OUTER d-i environment (like partman does): inside
  # the chroot lvcreate hangs forever waiting for udev sync.
  # LVM_SUPPRESS_FD_WARNINGS silences harmless fd-leak noise.
  print('export LVM_SUPPRESS_FD_WARNINGS=1')
  print('if [ -b /dev/sdb ] && ! pvs /dev/sdb >/dev/null 2>&1; then')
  print('  pvcreate /dev/sdb')
  print('  vgcreate vgapp /dev/sdb')
  print('fi')
  for disk in appdisks:
    mib = max(4, int(disk['size']) // 1024**2)
    print('lvcreate -y -L %dM -n %s vgapp' % (mib, disk['lv']))
    print('mkfs.ext4 -q /dev/vgapp/%s' % disk['lv'])
    print('mkdir -p /target%s' % disk['mountpoint'])
    print('echo "/dev/vgapp/%s %s ext4 %s 0 2" >> /target/etc/fstab'
          % (disk['lv'], disk['mountpoint'], disk['options']))

# --- signal finish: provisiond moves us to the production VLAN ---
print('wget -q -O /dev/null "%s/provision.py?hack_ip=%s" || true' % (base, ip))
