#!/bin/sh
# Idempotent setup of the full deploy stack on provision1 (test env).
# Run as root on provision1 with the repo tree at /root/src/deploy.
# Puppet does the equivalent of this in production.

set -eu
repo=/root/src/deploy
stack=$repo/testvm/deploy-stack

export DEBIAN_FRONTEND=noninteractive
apt-get -qq install -y ipxe dnsmasq nftables apt-cacher-ng >/dev/null

# --- deploy backend CGIs + lib -------------------------------------------
mkdir -p /var/www/deploy
cp -r "$repo/server/backend/ipxe" "$repo/server/backend/debian" /var/www/deploy/
cp "$repo/server/backend/finish.py" "$repo/server/backend/enc.py" "$repo/server/backend/autosign.py" "$repo/server/backend/enrollments.py" "$repo/server/backend/report.py" /var/www/deploy/
cp -r "$repo/server/backend/modules" /var/www/deploy/
mkdir -p /var/www/deploy/lib
cp "$repo/server/libdhdeploy/metadata.py" /var/www/deploy/lib/
cp "$repo/server/libdhdeploy/flows.py" /var/www/deploy/lib/
touch /var/www/deploy/lib/__init__.py
chmod -R a+rX /var/www/deploy
find /var/www/deploy -name '*.py' -exec chmod 755 {} +

# --- status frontend ------------------------------------------------------
install -m 644 "$repo/server/frontend/index.html" /var/www/index.html
install -m 755 "$repo/server/frontend/status.json.py" /var/www/status.json.py

# --- operator CLI ---------------------------------------------------------
install -m 755 "$repo/utils/deploy-vm" /usr/local/bin/deploy-vm

# --- config: deploy.yaml, manifest, ipplan.db ----------------------------
# Preserve live-provisioned secret-store settings across reseeds
vault_lines=$(grep -E "^vault_(addr|token|cacert|cert|key):" /etc/deploy.yaml 2>/dev/null || true)
cat > /etc/deploy.yaml <<EOF
redis:
  host: localhost
base_url: http://10.100.0.2:8080
jumpgates: [10.200.0.2]
puppet_server: puppet1.colo.notproduction.net
syslog_host: 10.100.0.2
resolvers: [10.200.0.2]
ssh_port: 22
EOF
[ -n "$vault_lines" ] && printf "%s\n" "$vault_lines" >> /etc/deploy.yaml
chown root:www-data /etc/deploy.yaml; chmod 640 /etc/deploy.yaml


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
c.execute("INSERT INTO network VALUES (1, 'colo@prod', 200, "
          "'10.200.0.2', '255.255.255.0', 24, NULL, NULL)")
# Reserved: event network (no hosts yet). domain=EVENT drives the
# LUKS + services-<event> secret flow in the deploy backend.
c.execute("INSERT INTO network VALUES (2, 'EVENT@prod', 300, "
          "'10.201.0.2', '255.255.255.0', 24, NULL, NULL)")
c.executemany("INSERT INTO host VALUES (?, ?, ?, NULL, 1)", [
    (10, 'web1.colo.notproduction.net', '10.200.0.60'),
    (11, 'vault1.colo.notproduction.net', '10.200.0.61'),
    (12, 'puppet1.colo.notproduction.net', '10.200.0.62'),
    (13, 'provision1.colo.notproduction.net', '10.200.0.2'),
    (14, 'directory1.colo.notproduction.net', '10.200.0.63'),
    (15, 'doc1.colo.notproduction.net', '10.200.0.64'),
    (16, 'ldap1-master.colo.notproduction.net', '10.200.0.65'),
    (17, 'ldap2-master.colo.notproduction.net', '10.200.0.66'),
    (18, 'ldap1.colo.notproduction.net', '10.200.0.67'),
    (19, 'ldap2.colo.notproduction.net', '10.200.0.68'),
    # the hypervisors (mgmt VLAN) - puppet-enrolled by hand, never deployed
    (20, 'pve1.colo.notproduction.net', '10.10.10.1'),
    (21, 'pve2.colo.notproduction.net', '10.10.10.3'),
    # ssh entry point for users - like production
    (22, 'jumpgate1.colo.notproduction.net', '10.200.0.69'),
])
c.executemany("INSERT INTO option VALUES (?, ?, ?)", [
    (10, 'os', 'debian'), (10, 'pkg', 'base'), (10, 'pkg', 'web(port=80)'),
    (13, 'pkg', 'jumpgate'),
    (22, 'os', 'debian'), (22, 'pkg', 'jumpgate'),
    (11, 'os', 'debian'), (11, 'pkg', 'vault'),
    (12, 'os', 'debian'), (12, 'pkg', 'puppetserver'),
    (12, 'webname', 'puppet.dh.notproduction.net'),
    (14, 'os', 'debian'), (14, 'pkg', 'lam'),
    (15, 'os', 'debian'), (15, 'pkg', 'trac'), (15, 'pkg', 'svn'),
    (16, 'os', 'debian'), (16, 'pkg', 'ldap(role=master,id=1)'),
    (17, 'os', 'debian'), (17, 'pkg', 'ldap(role=master,id=2)'),
    (18, 'os', 'debian'), (18, 'pkg', 'ldap'),
    (19, 'os', 'debian'), (19, 'pkg', 'ldap'),
    (20, 'os', 'debian'), (20, 'pkg', 'pve'),
    # public website names (webname): drives certs, nginx server_name,
    # the issuer domain list - single source of truth
    (11, 'webname', 'vault.dh.notproduction.net'),
    (14, 'webname', 'directory.dh.notproduction.net'),
    (15, 'webname', 'doc.dh.notproduction.net'),
    (20, 'webname', 'pve1.dh.notproduction.net'),
    (21, 'os', 'debian'), (21, 'pkg', 'pve'),
    (21, 'webname', 'pve2.dh.notproduction.net'),
    (13, 'webname', 'deploy.dh.notproduction.net'),
])
c.execute("INSERT INTO meta_data VALUES ('current_event', 'test')")
# Operational flags ride with current_event (in prod both come from
# the current-event file in svn -> ipplan db). change_freeze=true
# during events: the appstore stops following upstream releases.
c.execute("INSERT INTO meta_data VALUES ('change_freeze', 'false')")
conn.commit()
conn.close()
print('ipplan.db seeded')
EOF
# compile the manifest into the db - provision reads ONLY ipplan.db
python3 "$repo/utils/ipplan2db" "$stack/manifest.yaml" /etc/ipplan.db
rm -f /etc/manifest  # gen-3: provision reads only the db
chmod 644 /etc/ipplan.db

# --- hosted data ----------------------------------------------------------
mkdir -p /var/www/data/debian-installer/amd64
if [ ! -f /var/www/data/debian-installer/amd64/linux ]; then
  base=https://deb.debian.org/debian/dists/trixie/main/installer-amd64/current/images/netboot/debian-installer/amd64
  curl -sfL -o /var/www/data/debian-installer/amd64/linux "$base/linux"
  curl -sfL -o /var/www/data/debian-installer/amd64/initrd.gz "$base/initrd.gz"
fi
cp "$stack/dhtech.ipxe" "$stack/nftables-baseline.conf" "$stack/vimrc" /var/www/data/
cp "$repo/server/backend/debian/post-install-hardening" /var/www/data/
cp "$stack/preseed" /var/www/data/preseed
# Operator/jumpgate keys only: the provision server must NOT be able to
# log in to deployed machines.
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
# provision1: NAT the deployment VLAN out via the test-only NAT NIC.
flush ruleset
table ip nat {
  chain postrouting {
    type nat hook postrouting priority srcnat;
    oifname "ens18" ip saddr 10.100.0.0/24 masquerade
    oifname "ens18" ip saddr 10.200.0.0/24 masquerade
    oifname "ens21" ip daddr 10.200.0.61 tcp dport { 443, 8200 } masquerade
    oifname "ens21" ip daddr 10.200.0.63 tcp dport 443 masquerade
    oifname "ens21" ip daddr 10.200.0.64 tcp dport 443 masquerade
    oifname "ens21" ip daddr 10.200.0.62 tcp dport 443 masquerade
    oifname "ens21" ip daddr 10.200.0.69 tcp dport 22 masquerade
  }
}
table ip deploynat {
  chain prerouting {
    type nat hook prerouting priority dstnat;
    # Workstation-facing forwards via the NAT NIC (see WEBSITES.md):
    # OpenBao API/UI (puppet-CA listener)
    iifname "ens18" tcp dport 8200 dnat to 10.200.0.61:8200
    # vault website (nginx + Let's Encrypt)
    iifname "ens18" tcp dport 443 dnat to 10.200.0.61:443
    # directory (LAM) and doc1 (Trac+SVN) websites
    iifname "ens18" tcp dport 444 dnat to 10.200.0.63:443
    iifname "ens18" tcp dport 445 dnat to 10.200.0.64:443
    # puppetboard website (puppet1)
    iifname "ens18" tcp dport 447 dnat to 10.200.0.62:443
    # user ssh entry: jumpgate1
    iifname "ens18" tcp dport 2022 dnat to 10.200.0.69:22
  }
}
EOF
systemctl enable --now nftables >/dev/null 2>&1
nft -f /etc/nftables.conf

cat > /etc/dnsmasq.d/deploy.conf <<EOF
# Resolver for the deployment VLAN. Explicit upstream: Debian's
# systemd-resolved integration otherwise leaves dnsmasq with no servers
# (queries get REFUSED).
interface=ens20
interface=ens21
bind-interfaces
no-dhcp-interface=ens20
no-dhcp-interface=ens21
no-resolv
server=10.0.2.3
host-record=provision1.colo.notproduction.net,10.200.0.2
host-record=web1.colo.notproduction.net,10.200.0.60
host-record=vault1.colo.notproduction.net,10.200.0.61
host-record=vault.dh.notproduction.net,10.200.0.61
host-record=puppet1.colo.notproduction.net,10.200.0.62
host-record=directory1.colo.notproduction.net,10.200.0.63
host-record=ldap1-master.colo.notproduction.net,10.200.0.65
host-record=ldap2-master.colo.notproduction.net,10.200.0.66
host-record=ldap1.colo.notproduction.net,10.200.0.67
host-record=ldap2.colo.notproduction.net,10.200.0.68
host-record=puppet.dh.notproduction.net,10.200.0.62
host-record=jumpgate1.colo.notproduction.net,10.200.0.69
host-record=pve1.colo.notproduction.net,10.10.10.1
host-record=pve1.dh.notproduction.net,10.10.10.1
host-record=pve2.dh.notproduction.net,10.10.10.3
host-record=doc1.colo.notproduction.net,10.200.0.64
host-record=directory.dh.notproduction.net,10.200.0.63
host-record=doc.dh.notproduction.net,10.200.0.64
host-record=deploy.dh.notproduction.net,10.200.0.2
EOF
systemctl restart dnsmasq

# --- installer syslog receiver (live status page) -------------------------
install -m 755 "$repo/server/syslog-receiver/syslog-receiver" /usr/local/bin/dh-syslog-receiver
cat > /etc/systemd/system/dh-syslog-receiver.service <<EOF
[Unit]
Description=Deploy installer syslog receiver
After=network-online.target redis-server.service

[Service]
ExecStart=/usr/local/bin/dh-syslog-receiver
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now dh-syslog-receiver

# --- deploy website: nginx + LE cert on 443, proxying the status page ----
# (puppet's dhacme::cert/dhnginx pattern, hand-seeded here because
# provision1 is not puppet-managed in the test env)
apt-get -qq install -y nginx >/dev/null
cat > /usr/local/sbin/dh-cert-sync <<"EOF"
#!/bin/sh
# Fetch the deploy website cert from the secret store (cert-auth with
# the provision server identity in /etc/deploy-ssl).
set -eu
name=deploy.dh.notproduction.net
D=/etc/deploy-ssl
TOKEN=$(curl -sf --cacert "$D/puppet-ca.pem" --cert "$D/node.pem" --key "$D/node.key" \
  -XPOST "https://vault1.colo.notproduction.net:8200/v1/auth/cert/login" | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['auth']['client_token'])")
mkdir -p /etc/dh-certs
umask 077
curl -sf --cacert "$D/puppet-ca.pem" -H "X-Vault-Token: $TOKEN" \
  "https://vault1.colo.notproduction.net:8200/v1/services/certs:$name" | \
  python3 -c "
import json, sys
d = json.load(sys.stdin)['data']
open('/etc/dh-certs/$name.fullchain.pem.new', 'w').write(d['certificate'] + d['chain'])
open('/etc/dh-certs/$name.key.new', 'w').write(d['private_key'])"
if ! cmp -s /etc/dh-certs/$name.fullchain.pem.new /etc/dh-certs/$name.fullchain.pem 2>/dev/null; then
  mv /etc/dh-certs/$name.fullchain.pem.new /etc/dh-certs/$name.fullchain.pem
  mv /etc/dh-certs/$name.key.new /etc/dh-certs/$name.key
  systemctl reload nginx || true
else
  rm -f /etc/dh-certs/$name.fullchain.pem.new /etc/dh-certs/$name.key.new
fi
EOF
chmod 755 /usr/local/sbin/dh-cert-sync
cat > /etc/systemd/system/dh-cert-sync.service <<EOF
[Unit]
Description=Sync deploy website cert from the secret store
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/dh-cert-sync
EOF
cat > /etc/systemd/system/dh-cert-sync.timer <<EOF
[Unit]
Description=Daily deploy website cert sync
[Timer]
OnCalendar=daily
RandomizedDelaySec=2h
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now dh-cert-sync.timer >/dev/null 2>&1

cat > /etc/nginx/sites-available/deploy-web <<EOF
# deploy status website (LE cert); the plain :8080 apache stays for the
# installer CGIs on the deploy VLAN.
server {
  # 446: the workstation-facing port (ens18:443 is DNATed to the vault
  # website, so the deploy site cannot share it); 443 kept for lab-
  # internal access.
  listen 443 ssl;
  listen 446 ssl;
  server_name deploy.dh.notproduction.net;
  ssl_certificate     /etc/dh-certs/deploy.dh.notproduction.net.fullchain.pem;
  ssl_certificate_key /etc/dh-certs/deploy.dh.notproduction.net.key;
  location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host \$host;
  }
}
EOF
ln -sf /etc/nginx/sites-available/deploy-web /etc/nginx/sites-enabled/deploy-web
rm -f /etc/nginx/sites-enabled/default
if [ -f /etc/dh-certs/deploy.dh.notproduction.net.fullchain.pem ]; then
  systemctl reload nginx || systemctl restart nginx
fi

echo "deploy stack seeded"
