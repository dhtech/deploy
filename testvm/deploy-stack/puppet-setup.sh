#!/bin/sh
# One-time puppetserver setup on a freshly deployed puppet VM (test env).
# Run as root ON the puppet machine. Debian 13's own puppetserver package.

set -eu

# Open the puppet API in the baseline firewall (self-managed after first run)
if ! grep -q "tcp dport 8140" /etc/nftables.conf; then
  sed -i 's#tcp dport 22 ip saddr .* accept#&\n    tcp dport 8140 ip saddr 10.200.0.0/24 accept#' /etc/nftables.conf
  nft -f /etc/nftables.conf
fi

export DEBIAN_FRONTEND=noninteractive
apt-get -qq install -y puppetserver >/dev/null

# Modest JVM heap: the VM has 3G
sed -i 's/^JAVA_ARGS=.*/JAVA_ARGS="-Xms512m -Xmx1g"/' /etc/default/puppetserver

cat > /etc/puppet/puppet.conf <<EOF
[main]
server=puppet1.test.lan
certname=puppet1.test.lan

[server]
autosign=/etc/puppet/autosign.conf

[agent]
runinterval=10m
EOF

# Test env: autosign every deploy-system host. Prod would use policy
# autosign validated against ipplan.
echo "*.test.lan" > /etc/puppet/autosign.conf

systemctl enable --now puppetserver
# puppetserver takes a while to come up the first time (CA generation)
for i in $(seq 1 60); do
  ss -ltn | grep -q 8140 && break
  sleep 5
done
ss -ltn | grep -q 8140 || { echo "puppetserver never opened 8140" >&2; exit 1; }

echo "puppetserver ready on puppet1.test.lan:8140"
