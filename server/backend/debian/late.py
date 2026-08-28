#!/usr/bin/env python3
# Dynamic late script for preseed/late_command (gen-3, replaces the
# static gen-2 post-install). Emits a per-host shell script that runs in
# the d-i environment with /target mounted. Identity via fqdn=.

import io
import os
import sys
import secrets
import urllib.parse

import runtime
from ipplanlib import metadata

query_string = urllib.parse.parse_qs(
    os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
try:
    # identity: fqdn only (beta) - must name a host row in ipplan;
    # anomalies answer 403 and are SEEN in the logs
    hostname = runtime.request_host(query_string)
except runtime.IdentityError as error:
    print('Status: 403')
    print('')
    print(error)
    sys.exit(0)

client, cm = runtime.find(hostname)
config = runtime.config()
base = runtime.base_url()

# jumpgates come from ipplan.db (pkg jumpgate), the same source the
# steady-state firewall uses - one truth from the first boot onward
jumpgates = [metadata.host_ip(h)
             for h, _ in metadata.hosts_with_pkg('jumpgate')]
puppet_server = config.get('puppet_server', 'puppet.tech.dreamhack.se')
resolvers = config.get('resolvers', ['8.8.8.8'])
ssh_port = int(config.get('ssh_port', 22))

jump_set = ', '.join(jumpgates) if jumpgates else '127.0.0.1'
resolv_lines = '\\n'.join('nameserver %s' % r for r in resolvers)

# Build the whole script in memory before sending a single byte: the
# secret store or redis failing mid-generation must yield a hard CGI
# error (no output -> apache 500 -> the installer's wget fails), never
# a truncated-but-valid script that d-i would happily execute.
_real_stdout = sys.stdout
_response = io.StringIO()
sys.stdout = _response

print('')
print('#!/bin/sh')
print('# Dreamhack gen-3 late script for %s' % client.hostname)
print('set -x')
print('exec > /target/var/tmp/late.log 2>&1')

# --- production network config (immutable, from ipplan) ---
print('ifs=$(ls /sys/class/net/ | grep -v lo | sort | tr "\\n" "," )')
print('wget -q -O /target/etc/network/interfaces '
      '"%s/interfaces.py?ifs=${ifs}&fqdn=%s"' % (base, client.hostname))
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
runtime.connection().setex('enroll-' + client.hostname, 86400, enroll_token)
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
# NB: pipe must live inside the chroot — in-target swallows stdin
# (same trap as the chpasswd fix below).
print('in-target sh -c \'echo "grub-efi-amd64 grub2/force_efi_extra_removable'
      ' boolean true" | debconf-set-selections\'')
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
admin_pw = runtime.vault_read(runtime.vault_login_path(client)).get(
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
    # Kernel device names are nondeterministic with two disks — the appdisk
    # is unit 1 on the SCSI bus (work convention: find disk by SCSI path).
    # virtio-scsi puts the unit in the LUN field (scsi-0:0:0:1), VMware
    # pvscsi in the target field (scsi-0:0:1:0); match both. Fallback:
    # second disk in /sys bus-path order, should by-path be absent.
    print('appdev=""')
    print('for l in /dev/disk/by-path/*scsi-0:0:1:0'
          ' /dev/disk/by-path/*scsi-0:0:0:1; do')
    print('  [ -e "$l" ] && [ -z "$appdev" ] && appdev=$(readlink -f "$l")')
    print('done')
    print('[ -n "$appdev" ] || appdev=/dev/$(for b in /sys/block/sd*'
          ' /sys/block/vd*; do [ -d "$b" ] || continue;'
          ' echo "$(readlink -f "$b") ${b##*/}";'
          ' done | sort | sed -n 2p | sed "s/.* //")')
    print('if [ -n "$appdev" ] && ! pvs "$appdev" >/dev/null 2>&1; then')
    print('  pvcreate "$appdev"')
    print('  vgcreate vgapp "$appdev"')
    print('fi')
    for disk in appdisks:
        mib = max(4, int(disk['size']) // 1024**2)
        print('lvcreate -y -L %dM -n %s vgapp' % (mib, disk['lv']))
        print('mkfs.ext4 -q /dev/vgapp/%s' % disk['lv'])
        print('mkdir -p /target%s' % disk['mountpoint'])
        print('echo "/dev/vgapp/%s %s ext4 %s 0 2" >> /target/etc/fstab'
              % (disk['lv'], disk['mountpoint'], disk['options']))

# --- signal finish: provisiond moves us to the production VLAN.
# (gen-2's provision.py is gone; finish.py flips the installed flag
# provisiond watches)
print('wget -q -O /dev/null "%s/finish.py?fqdn=%s" || true'
      % (base, client.hostname))

# generation complete - emit the buffered response in one piece
sys.stdout = _real_stdout
sys.stdout.write(_response.getvalue())
