#!/bin/sh
# One-time OpenBao setup on a freshly deployed vault VM (test env).
# Run as root ON the vault machine. Puppet does the equivalent in prod
# (incl. TLS via the puppet CA; the lab runs tls_disable + token auth).
#
# OpenBao (MPL-2.0, Linux Foundation fork of Vault) - API-compatible with
# the gen-2 Vault contract (KV v1, tokens, TLS cert auth); hvac works
# unchanged.

set -eu

# The installer leaves deploy-VLAN DNS behind; point at the provision
# server's resolver on the production VLAN. (Gen-3 late script TODO.)
echo "nameserver 10.200.0.2" > /etc/resolv.conf

# Open the OpenBao API in the baseline firewall (puppet's job in prod)
if ! grep -q "tcp dport 8200" /etc/nftables.conf; then
  sed -i 's#tcp dport 22 ip saddr .* accept#&\n    tcp dport 8200 ip saddr 10.200.0.0/24 accept#' /etc/nftables.conf
  nft -f /etc/nftables.conf
fi

export DEBIAN_FRONTEND=noninteractive
if ! command -v bao >/dev/null 2>&1; then
  apt-get -qq install -y curl jq >/dev/null
  url=$(curl -sfL https://api.github.com/repos/openbao/openbao/releases/latest \
    | jq -r '.assets[].browser_download_url' \
    | grep -E 'linux_amd64\.deb$' | grep -v hsm | head -1)
  [ -n "$url" ] || { echo "could not find OpenBao deb release" >&2; exit 1; }
  curl -sfL -o /tmp/openbao.deb "$url"
  apt-get -qq install -y /tmp/openbao.deb >/dev/null
  rm -f /tmp/openbao.deb
fi

cat > /etc/openbao/openbao.hcl <<EOF
# Test-env OpenBao: raft single node, TLS disabled (lab only - prod gets
# TLS with the puppet CA and cert auth).
ui = true

storage "raft" {
  path    = "/opt/openbao/data"
  node_id = "vault1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr = "http://10.200.0.61:8200"
cluster_addr = "http://10.200.0.61:8201"
EOF
mkdir -p /opt/openbao/data
chown -R openbao:openbao /opt/openbao /etc/openbao

systemctl enable --now openbao
sleep 3

export BAO_ADDR=https://vault1.test.lan:8200 BAO_CACERT=/etc/openbao/tls/puppet-ca.pem
if bao status 2>/dev/null | grep -q "Initialized.*false"; then
  bao operator init -key-shares=1 -key-threshold=1 -format=json \
    > /root/vault-init.json
  chmod 600 /root/vault-init.json
fi

unseal=$(jq -r '.unseal_keys_b64[0]' /root/vault-init.json)
root_token=$(jq -r '.root_token' /root/vault-init.json)
bao operator unseal "$unseal" >/dev/null

export BAO_TOKEN="$root_token"
# KV v1 mounts matching the gen-2 path contract
bao secrets list | grep -q "^services/" \
  || bao secrets enable -path=services -version=1 kv >/dev/null
bao secrets list | grep -q "^services-test/" \
  || bao secrets enable -path=services-test -version=1 kv >/dev/null

# Machine auth: puppet node certs (short-TTL tokens; gen-2 used ttl=0)
bao auth list | grep -q "^cert/" || bao auth enable cert >/dev/null
bao write auth/cert/certs/puppet \
  display_name="Puppet machines" \
  policies=deploy \
  certificate=@/etc/openbao/tls/puppet-ca.pem \
  token_ttl=1h token_max_ttl=4h >/dev/null

# Scoped token for the deploy stack (legacy bridge; cert auth preferred)
bao policy write deploy - >/dev/null <<EOF
path "services/*" { capabilities = ["create", "read", "update"] }
path "services-test/*" { capabilities = ["create", "read", "update"] }
EOF
bao token create -policy=deploy -orphan -period=768h -format=json \
  > /root/deploy-token.json
chmod 600 /root/deploy-token.json

echo "openbao ready: http://10.200.0.61:8200"
echo "init material: /root/vault-init.json, deploy token: /root/deploy-token.json"
