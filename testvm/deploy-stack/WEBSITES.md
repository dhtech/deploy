# Web services in the test environment

How browser-facing services are exposed from the lab, and how to add a
new one. Machine-to-machine traffic is not covered here — that stays on
the puppet CA (see `provisiond3/README.md`).

## The pattern

Every public-facing website follows the same chain:

```
browser
  └─ https://<name>.dh.notproduction.net:<fwd-port>
       │  public DNS A record → 127.0.0.1 (Route 53)
       ▼
     QEMU hostfwd on the workstation (start.sh, unprivileged port)
       ▼
     provision-dev DNAT (nftables, keyed on destination port)
       ▼
     nginx on the service VM, port 443
       │  single-name Let's Encrypt certificate (no wildcards)
       ▼
     the application (proxied on localhost)
```

- **Certificates**: issued by `dhacme::issuer` on puppet1 (lego, DNS-01
  via Route 53, credentials from OpenBao at `services/acme:route53`),
  published to `services/certs:<name>`, synced to the service VM by
  `dhacme::cert`, terminated by nginx (`dhnginx::*`). All puppet,
  all config in hiera. Let's Encrypt is for **public-facing websites
  only** — everything internal uses the puppet CA.
- **DNS**: the public zone (`notproduction.net`, Route 53) carries only
  the A records (→ `127.0.0.1`) and transient ACME TXT records. Internal
  resolution comes from dnsmasq on provision-dev. Names will move into
  the ipplan file when the generated-ipplan work lands.
- **Ports**: the service VM always uses standard `443`; the workstation
  side uses unprivileged `84xx` forwards (no root/sysctl needed locally).

## Current forwards (start.sh)

| Workstation | Backend | Service | Status |
|---|---|---|---|
| `127.0.0.1:4454` | pve:22 | pve-test SSH | live |
| `127.0.0.1:8006` | pve:8006 | Proxmox web UI | live |
| `127.0.0.1:4455` | provision-dev:22 | provision-dev SSH | live |
| `127.0.0.1:8768` | provision-dev:8080 | deploy status page | live |
| `127.0.0.1:8200` | → vault1:8200 (DNAT) | OpenBao API/UI (puppet CA) | live |
| `127.0.0.1:8443` | → vault1:443 (DNAT) | **vault website** (nginx + LE) | live |
| `127.0.0.1:8444` | provision-dev:444 | FusionDirectory/LDAP UI | reserved |
| `127.0.0.1:8445` | provision-dev:445 | Trac | reserved |

Live URLs:

- Deploy status: <http://localhost:8768>
- Proxmox: <https://localhost:8006> (root / Linux PAM)
- OpenBao (puppet-CA listener): <https://localhost:8200/ui>
- **Vault website**: <https://vault.dh.notproduction.net:8443/ui>
  (real Let's Encrypt certificate — green padlock)

## Adding a new public website (checklist)

Using the reserved FusionDirectory slot as the example:

1. **ipplan/manifest**: add the host (e.g. `ldap1.test.lan`,
   `10.200.0.63`) with its pkg; give the pkg `puppet: classes:
   [dhfirewall, 'dhacme::cert', 'dhnginx::<service>']` in the manifest
   (classification only — no params there).
2. **Deploy**: `deploy-vm ldap1.test.lan coloc` (~4 min).
3. **Puppet repo** (`~/repos/dh/local/puppet`):
   - hiera `data/common.yaml`: append the new name to
     `dhacme::issuer::domains`? No — single certs: add the domain to the
     issuer by adding a second `dhacme::issuer` cert run, or reuse the
     class per-domain (current issuer handles one cert; extend when the
     second site lands).
   - hiera `data/nodes/ldap1.test.lan.yaml`: `dhfirewall::open_tcp:
     [443, 636]`, `dhacme::cert::cert_name: 'fusion.dh.notproduction.net'`.
   - a `dhnginx::<service>` class (copy `dhnginx/manifests/vault.pp`).
   - `git push puppet1 main` (push-to-deploy).
4. **Network** (deploy side, seed.sh + live):
   - dnsmasq `host-record=fusion.dh.notproduction.net,10.200.0.63`
   - DNAT: `iifname "ens18" tcp dport 444 dnat to 10.200.0.63:443`
     (+ matching `oifname "ens21" ... masquerade` rule)
   - Route 53 A record `fusion.dh.notproduction.net → 127.0.0.1`
5. Browse `https://fusion.dh.notproduction.net:8444/`.

## Operational notes

- OpenBao must be **unsealed** after every vault1 reboot or openbao
  restart (`bao operator unseal` with the key from
  `vault1:/root/vault-init.json`).
- Cert renewal is automatic: daily `dh-acme-issue.timer` on puppet1
  (renews < 30 days), daily `dh-cert-sync.timer` on consumers (reloads
  nginx on change).
- The AWS access key for Route 53 lives in OpenBao
  (`services/acme:route53`) and scoped IAM; it passed through a chat
  transcript once — rotate it.
