#!/bin/sh
# One-time Vault setup on a freshly deployed vault VM (test env).
# Run as root ON the vault machine. Puppet does the equivalent in prod
# (incl. TLS via the puppet CA; the lab runs tls_disable + token auth).

set -eu

# The installer leaves deploy-VLAN DNS behind; point at the provision
# server's resolver on the production VLAN. (Gen-3 late script TODO.)
echo "nameserver 10.200.0.2" > /etc/resolv.conf

# Open the Vault API in the baseline firewall (puppet's job in prod)
if ! grep -q "tcp dport 8200" /etc/nftables.conf; then
  sed -i 's#tcp dport 22 ip saddr .* accept#&\n    tcp dport 8200 ip saddr 10.200.0.0/24 accept#' /etc/nftables.conf
  nft -f /etc/nftables.conf
fi

export DEBIAN_FRONTEND=noninteractive
if ! command -v vault >/dev/null 2>&1; then
  apt-get -qq install -y curl gnupg >/dev/null
  curl -sfL https://apt.releases.hashicorp.com/gpg \
    | gpg --dearmor > /usr/share/keyrings/hashicorp.gpg
  echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com bookworm main" \
    > /etc/apt/sources.list.d/hashicorp.list
  apt-get -qq update >/dev/null
  apt-get -qq install -y vault >/dev/null
fi

cat > /etc/vault.d/vault.hcl <<EOF
# Test-env Vault: raft single node, TLS disabled (lab only - prod gets
# TLS with the puppet CA and cert auth).
ui = false

storage "raft" {
  path    = "/opt/vault/data"
  node_id = "vault1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr = "http://10.200.0.61:8200"
cluster_addr = "http://10.200.0.61:8201"
EOF
mkdir -p /opt/vault/data
chown -R vault:vault /opt/vault

systemctl enable --now vault
sleep 3

export VAULT_ADDR=http://127.0.0.1:8200
if vault status 2>/dev/null | grep -q "Initialized.*false"; then
  vault operator init -key-shares=1 -key-threshold=1 -format=json \
    > /root/vault-init.json
  chmod 600 /root/vault-init.json
fi

unseal=$(python3 -c "import json;print(json.load(open('/root/vault-init.json'))['unseal_keys_b64'][0])")
root_token=$(python3 -c "import json;print(json.load(open('/root/vault-init.json'))['root_token'])")
vault operator unseal "$unseal" >/dev/null

export VAULT_TOKEN="$root_token"
# KV v1 mounts matching the gen-2 path contract
vault secrets list | grep -q "^services/" \
  || vault secrets enable -path=services -version=1 kv >/dev/null
vault secrets list | grep -q "^services-test/" \
  || vault secrets enable -path=services-test -version=1 kv >/dev/null

# Scoped token for the deploy stack (CGIs + provisiond)
vault policy write deploy - >/dev/null <<EOF
path "services/*" { capabilities = ["create", "read", "update"] }
path "services-test/*" { capabilities = ["create", "read", "update"] }
EOF
vault token create -policy=deploy -orphan -period=768h -format=json \
  > /root/deploy-token.json
chmod 600 /root/deploy-token.json

echo "vault ready: http://10.200.0.61:8200"
echo "init material: /root/vault-init.json, deploy token: /root/deploy-token.json"
