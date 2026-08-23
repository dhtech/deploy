#!/bin/sh
# Idempotent setup of the full deploy stack on provision-dev (test env).
# Run as root on provision-dev with the repo tree at /root/src/deploy.
# Puppet does the equivalent of this in production.

set -eu
repo=/root/src/deploy
stack=$repo/testvm/deploy-stack

export DEBIAN_FRONTEND=noninteractive
apt-get -qq install -y ipxe dnsmasq nftables apt-cacher-ng >/dev/null

# --- deploy backend CGIs + lib -------------------------------------------
mkdir -p /var/www/deploy
cp -r "$repo/server/backend/ipxe" "$repo/server/backend/debian" /var/www/deploy/
cp "$repo/server/backend/finish.py" /var/www/deploy/
mkdir -p /var/www/deploy/lib
cp "$repo/server/libdhdeploy/metadata.py" /var/www/deploy/lib/
touch /var/www/deploy/lib/__init__.py
chmod -R a+rX /var/www/deploy
find /var/www/deploy -name '*.py' -exec chmod 755 {} +

# --- operator CLI ---------------------------------------------------------
install -m 755 "$repo/utils/deploy-vm" /usr/local/bin/deploy-vm

# --- config: deploy.yaml, manifest, ipplan.db ----------------------------
cat > /etc/deploy.yaml <<EOF
redis:
  host: localhost
base_url: http://10.100.0.2:8080
EOF

cat > /etc/manifest <<EOF
packages:
  base:
    hardware:
      cpus: 1
      memory: 1G
      disk: 12G
  web:
    hardware:
      cpus: 2
      memory: 2G
      disk: 10G
EOF

python3 - <<'EOF'
import sqlite3

conn = sqlite3.connect('/etc/ipplan.db')
c = conn.cursor()
c.executescript('''
CREATE TABLE IF NOT EXISTS network (
  node_id INTEGER PRIMARY KEY, name TEXT, vlan INTEGER,
  ipv4_gateway_txt TEXT, ipv4_netmask_txt TEXT, ipv4_netmask_dec INTEGER,
  ipv6_gateway_txt TEXT, ipv6_netmask_txt TEXT);
CREATE TABLE IF NOT EXISTS host (
  node_id INTEGER PRIMARY KEY, name TEXT, ipv4_addr_txt TEXT,
  ipv6_addr_txt TEXT, network_id INTEGER);
CREATE TABLE IF NOT EXISTS option (node_id INTEGER, name TEXT, value TEXT);
CREATE TABLE IF NOT EXISTS meta_data (name TEXT, value TEXT);
DELETE FROM network; DELETE FROM host; DELETE FROM option; DELETE FROM meta_data;
''')
c.execute("INSERT INTO network VALUES (1, 'coloc@prod', 200, "
          "'10.200.0.2', '255.255.255.0', 24, NULL, NULL)")
c.execute("INSERT INTO host VALUES (10, 'web1.test.lan', '10.200.0.60', "
          "NULL, 1)")
c.executemany("INSERT INTO option VALUES (?, ?, ?)", [
    (10, 'os', 'debian'),
    (10, 'pkg', 'base'),
    (10, 'pkg', 'web(port=80)'),
])
c.execute("INSERT INTO meta_data VALUES ('current_event', 'test')")
conn.commit()
conn.close()
print('ipplan.db seeded')
EOF
chmod 644 /etc/ipplan.db

# --- hosted data ----------------------------------------------------------
mkdir -p /var/www/data/debian-installer/amd64
if [ ! -f /var/www/data/debian-installer/amd64/linux ]; then
  base=https://deb.debian.org/debian/dists/trixie/main/installer-amd64/current/images/netboot/debian-installer/amd64
  curl -sfL -o /var/www/data/debian-installer/amd64/linux "$base/linux"
  curl -sfL -o /var/www/data/debian-installer/amd64/initrd.gz "$base/initrd.gz"
fi
cp "$stack/dhtech.ipxe" "$stack/nftables-baseline.conf" /var/www/data/
cp "$stack/preseed" /var/www/data/preseed
cp /root/.ssh/authorized_keys /var/www/data/authorized_keys
chmod -R a+rX /var/www/data

# --- TFTP: iPXE EFI binary ------------------------------------------------
cp /usr/lib/ipxe/ipxe.efi /srv/tftp/

# --- Apache ---------------------------------------------------------------
cp "$stack/apache-deploy.conf" /etc/apache2/sites-available/deploy.conf
a2ensite deploy >/dev/null
systemctl reload apache2

# --- dhcpd: iPXE chains to the HTTP script on :8080 -----------------------
sed -i 's#filename "http://10.100.0.2/data/dhtech.ipxe";#filename "http://10.100.0.2:8080/data/dhtech.ipxe";#' /etc/dhcp/dhcpd.conf
systemctl restart isc-dhcp-server

# --- router role: NAT the deployment VLAN, resolve DNS for it -------------
cat > /etc/sysctl.d/90-deploy-forward.conf <<EOF
net.ipv4.ip_forward=1
EOF
sysctl -q -p /etc/sysctl.d/90-deploy-forward.conf

cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
# provision-dev: NAT the deployment VLAN out via the test-only NAT NIC.
flush ruleset
table ip nat {
  chain postrouting {
    type nat hook postrouting priority srcnat;
    oifname "ens18" ip saddr 10.100.0.0/24 masquerade
  }
}
EOF
systemctl enable --now nftables >/dev/null 2>&1
nft -f /etc/nftables.conf

cat > /etc/dnsmasq.d/deploy.conf <<EOF
# Resolver for the deployment VLAN
interface=ens20
bind-interfaces
no-dhcp-interface=ens20
EOF
systemctl restart dnsmasq

echo "deploy stack seeded"
