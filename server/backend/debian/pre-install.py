#!/usr/bin/env python2
# This is a python script that produces a shell script, nifty huh? :)

# Terminate headers here
print ''

print """
#!/bin/sh
#
# Dreamhack overrides for Debian Installer
#

set -x

# Make sure Debian is able to find iptables modules.
# This is not necessary on Ubuntu for some reason.
depmod -a

# Load iptables rules early to protect machines. This depends on populate-tftp
# installing xtables-multi and the related kernel modules in the Debian
# installer. We only allow SSH from jumpgate machines.
xtables-multi iptables -P INPUT DROP
xtables-multi iptables -P FORWARD DROP
xtables-multi iptables -A INPUT -i lo -j ACCEPT
xtables-multi iptables -A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
xtables-multi iptables -A INPUT -p icmp -j ACCEPT
xtables-multi iptables -A INPUT -s 77.80.254.132/32 -p tcp -m multiport --dports 2022 -m comment --comment "pre-install v4 ssh from jumpgate1.tech.dreamhack.se" -j ACCEPT
xtables-multi iptables -A INPUT -s 77.80.255.4/32 -p tcp -m multiport --dports 2022 -m comment --comment "pre-install v4 ssh from jumpgate2.tech.dreamhack.se" -j ACCEPT
xtables-multi iptables -A INPUT -s 77.80.231.135/32 -p tcp -m multiport --dports 2022 -m comment --comment "pre-install v4 ssh from jumpgate1.event.dreamhack.se" -j ACCEPT
xtables-multi iptables -A INPUT -s 77.80.231.136/32 -p tcp -m multiport --dports 2022 -m comment --comment "pre-install v4 ssh from jumpgate2.event.dreamhack.se" -j ACCEPT

xtables-multi ip6tables -P INPUT DROP
xtables-multi ip6tables -P FORWARD DROP
xtables-multi ip6tables -A INPUT -i lo -j ACCEPT
xtables-multi ip6tables -A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
xtables-multi ip6tables -A INPUT -p icmpv6 -j ACCEPT
xtables-multi ip6tables -A INPUT -s 2a02:4b00:1337:778::132/128 -p tcp -m multiport --dports 2022 -m comment --comment "pre-install v6 ssh from jumpgate1.tech.dreamhack.se" -j ACCEPT
xtables-multi ip6tables -A INPUT -s 2a05:2240:5000:300::4/128 -p tcp -m multiport --dports 2022 -m comment --comment "pre-install v6 ssh from jumpgate2.tech.dreamhack.se" -j ACCEPT
xtables-multi ip6tables -A INPUT -s 2a05:2242:926::135/128 -p tcp -m multiport --dports 2022 -m comment --comment "pre-install v6 ssh from jumpgate1.event.dreamhack.se" -j ACCEPT
xtables-multi ip6tables -A INPUT -s 2a05:2242:926::136/128 -p tcp -m multiport --dports 2022 -m comment --comment "pre-install v6 ssh from jumpgate2.event.dreamhack.se" -j ACCEPT

sed -i 's/Port 22/Port 2022/' /etc/ssh/sshd_config
# TODO(bluecmd): IPv6 disabled for now due to intermittent connection issues
# Revisit for dhw16.
#echo 1 > /proc/sys/net/ipv6/conf/eth0."$vlan"/autoconf
"""

import base64
import hvac
import os
import socket
import yaml

from lib import metadata


def vault(host):
  fqdn = socket.getfqdn()
  cert = '/var/lib/puppet/ssl/certs/%s.pem' % fqdn
  key = '/var/lib/puppet/ssl/private_keys/%s.pem' % fqdn
  client = hvac.Client(url=host, cert=(cert, key))
  client.auth_tls()
  return client

# Move the secrets out of the directory that is kept in SVN
config = yaml.safe_load(file('/etc/deploy.yaml'))
client, _ = metadata.find(os.environ['REMOTE_ADDR'])

# Only enable crypto disks on event machines
is_event = client.domain == 'EVENT'
crypto = is_event
auto_unlock = True
root_pw = base64.b64encode(os.urandom(8))

if is_event:
  vault_path = 'services-{event}/login:{hostname}'
else:
  vault_path = 'services/login:{hostname}'

vault(config['vault-host']).write(
        vault_path.format(
            hostname=client.hostname,
            event=metadata.get_current_event()),
        root_password=root_pw)

print 'ROOTPW="%s"' % root_pw

# Disable crypto on machines we know are co-location machines
if not crypto:
  print 'DO_CRYPTO=""'
else:
  passphrase = base64.b64encode(os.urandom(32))
  print 'DO_CRYPTO="true"'
  print 'PASSPHRASE="%s"' % passphrase
  if auto_unlock:
    print '# Save the passphrase for later if we want automatic unlock'
    print 'echo -n "$PASSPHRASE" > /tmp/crypto.pass'

print """
echo "d-i passwd/root-password password $ROOTPW" > conf.input
echo "d-i passwd/root-password-again password $ROOTPW" >> conf.input
echo "d-i network-console/password password $ROOTPW" >> conf.input
echo "d-i network-console/password-again password $ROOTPW" >> conf.input
echo "d-i network-console/start note" >> conf.input
echo "network-console network-console/start note" >> conf.input

# Install helper scripts

if [ ! -z $DO_CRYPTO ]; then
  echo "d-i partman-auto/method string crypto" >> conf.input
  echo "d-i partman-crypto/passphrase string $PASSPHRASE" >> conf.input
  echo "d-i partman-crypto/passphrase seen true" >> conf.input
  echo "d-i partman-crypto/passphrase-again string $PASSPHRASE" >> conf.input
  echo "d-i partman-crypto/passphraseagain seen true" >> conf.input
else
  echo "d-i partman-auto/method string lvm" >> conf.input
fi

debconf-set-selections conf.input
rm conf.input

do_crypto() {
  cat << __EOF__ > /detach.sh
#!/bin/sh

# Wait for package installation
while [ ! -f /bin/blockdev-wipe ];
do
  true
done

# Disable drive wipes
cat << _EOF_ > /bin/blockdev-wipe
#!/bin/sh
echo "*"
exit 0
_EOF_
chmod +x /bin/blockdev-wipe

# Wait for package installation
while [ ! -f /bin/blockdev-keygen ];
do
  true
done

# Enable keypharse preseeding
sed -i '/db_set \$templ ""/d' /bin/blockdev-keygen
sed -i '/db_fset \$templ seen false/d' /bin/blockdev-keygen

# We're launched through init so sleep forever to not respawn
while true
do
  sleep 60
done
__EOF__

  chmod +x detach.sh

  # We cannot spawn children, so trick init to spawn our watcher
  echo "::respawn:/detach.sh" >> /etc/inittab
  kill -SIGHUP 1
}

if [ ! -z $DO_CRYPTO ]; then
  do_crypto
fi

# Enable remote syslog to allow operator to follow progress
kill $(ps | grep [s]yslogd | cut -c 1-5)
echo '::respawn:/sbin/syslogd -n -m 0 -O /var/log/syslog -L -S -R deploy.tech.dreamhack.se' >> /etc/inittab
kill -SIGHUP 1

# Cleanup
rm $0

exit 0
"""
