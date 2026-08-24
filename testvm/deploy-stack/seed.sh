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
# Bootstrap only on a fresh bench: puppet (dhdeploy::config) owns
# this file once provision1 is enrolled - reseeds never touch it.
if [ ! -f /etc/deploy.yaml ]; then
cat > /etc/deploy.yaml <<EOF
redis:
  host: localhost
base_url: http://10.100.0.2:8080
puppet_server: puppet1.colo.notproduction.net
syslog_host: 10.100.0.2
resolvers: [10.200.0.2]
ssh_port: 22
EOF
chown root:www-data /etc/deploy.yaml; chmod 640 /etc/deploy.yaml
fi


# BOOTSTRAP-ONLY db compile from the vendored snapshot (like
# deploy.yaml): once the pipeline is alive, svn on doc1 is the source
# and puppet (dhipplan) distributes the published build over this.
if [ ! -f /etc/ipplan.db ]; then
python3 "$repo/utils/ipplan2db" --ipplan-root "$stack/ipplan" \
    --manifest "$stack/manifest.yaml" --manifest "$stack/appstore.yaml" \
    /etc/ipplan.db
fi
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
