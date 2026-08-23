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
     provision1 DNAT (nftables, keyed on destination port)
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
  resolution comes from dnsmasq on provision1. Names will move into
  the ipplan file when the generated-ipplan work lands.
- **Ports**: the service VM always uses standard `443`; the workstation
  side uses unprivileged `84xx` forwards (no root/sysctl needed locally).

## Current forwards (start.sh)

| Workstation | Backend | Service | Status |
|---|---|---|---|
| `127.0.0.1:4454` | pve:22 | pve-test SSH | live |
| `127.0.0.1:8006` | pve:8006 | Proxmox web UI | live |
| `127.0.0.1:4455` | provision1:22 | provision1 SSH | live |
| `127.0.0.1:8768` | provision1:8080 | deploy status page | live |
| `127.0.0.1:8200` | → vault1:8200 (DNAT) | OpenBao API/UI (puppet CA) | live |
| `127.0.0.1:8443` | → vault1:443 (DNAT) | **vault website** (nginx + LE) | live |
| `127.0.0.1:8444` | provision1:444 → future `fusion` VM | FusionDirectory/LDAP UI | reserved |
| `127.0.0.1:8445` | provision1:445 → future `doc1` VM | Trac + SVN (doc) | reserved |

Live URLs:

- Deploy status: <http://localhost:8768>
- Proxmox: <https://localhost:8006> (root / Linux PAM)
- OpenBao (puppet-CA listener): <https://localhost:8200/ui>
- **Vault website**: <https://vault.dh.notproduction.net:8443/ui>
  (real Let's Encrypt certificate — green padlock)

Each service gets its **own dedicated VM**, deployed through the stack
like everything else: FusionDirectory (web UI) on `fusion`, with the directory itself
(slapd) on its own **internal** VM `ldap1` (puppet-CA TLS, not a
website); Trac **and** SVN together on `doc1`
(their appdisks become separate LVs on its vgapp: `/srv/trac` + `/srv/svn`).

## Hosts in ipplan (test environment)

The authoritative source is the ipplan seed in `seed.sh` (production:
the real ipplan file). Current inventory:

| Host | IP | pkgs | Role | Appdisk LVs (vgapp) | Status |
|---|---|---|---|---|---|
| `provision1.colo.notproduction.net` | 10.200.0.2 | — | provision + deploy server, router/DNS/DHCP for the lab VLANs | — | live (hand-built dev box) |
| `web1.colo.notproduction.net` | 10.200.0.60 | `base`, `web(port=80)` | reference/test machine | `/srv/www` 10G | live |
| `vault1.colo.notproduction.net` | 10.200.0.61 | `vault` | OpenBao + vault website (nginx/LE) | `/var/lib/openbao` 20G (future redeploys) | live (pre-appdisk build) |
| `puppet1.colo.notproduction.net` | 10.200.0.62 | `puppetserver` | puppetserver, ENC client, ACME issuer | — | live |
| `fusion.colo.notproduction.net` | 10.200.0.63 | `fusiondirectory` | FusionDirectory web UI | — | planned |
| `doc1.colo.notproduction.net` | 10.200.0.64 | `trac`, `svn` | Trac + SVN (doc server) | `/srv/trac` 15G + `/srv/svn` 20G | planned |
| `ldap1-master.colo.notproduction.net` | 10.200.0.65 | `ldap` | directory master A (writable, mirror mode, seeds DIT) | `/var/lib/ldap` 10G | live |
| `ldap2-master.colo.notproduction.net` | 10.200.0.66 | `ldap` | directory master B (writable, mirror mode) | `/var/lib/ldap` 10G | live |
| `ldap1.colo.notproduction.net` | 10.200.0.67 | `ldap` | site slave (read-only consumer) | `/var/lib/ldap` 10G | live |
| `ldap2.colo.notproduction.net` | 10.200.0.68 | `ldap` | site slave (read-only consumer) | `/var/lib/ldap` 10G | planned |

Directory topology: two mirror-mode **masters** in colo take all writes
(FusionDirectory, apps, admin); **every site — colo included — runs a
pair of read-only slaves** (syncrepl from both masters over ldaps,
puppet-CA verified) for resilient local reads. ldaps 636 only, no
plaintext 389. Suffixes `dc=tech,dc=dreamhack,dc=se` (permanent) and
`dc=event,dc=dreamhack,dc=se` (permanent, flat). Secrets live in
OpenBao on the dedicated `services-ldap` mount, readable **only** by
the ldap servers (cert-auth role `ldap`, bound to their certnames);
other machines' `deploy` policy cannot see it.

Network plan: VLAN 10 mgmt (10.10.10.0/24), VLAN 100 deployment
(10.100.0.0/24, DHCP/PXE), VLAN 200 colo production (10.200.0.0/24),
VLAN 300 event production (10.201.0.0/24, reserved — no hosts yet).
Machines live under `colo.notproduction.net` / `event.notproduction.net`;
public websites stay under `dh.notproduction.net`.

## Adding a new public website (checklist)

Using the reserved FusionDirectory slot as the example:

1. **ipplan/manifest**: add the host (e.g. `fusion.colo.notproduction.net`,
   `10.200.0.63`) with its pkg; give the pkg `puppet: classes:
   [dhfirewall, 'dhacme::cert', 'dhnginx::<service>']` in the manifest
   (classification only — no params there).
2. **Deploy**: `deploy-vm fusion.colo.notproduction.net coloc` (~4 min).
3. **Puppet repo** (`~/repos/dh/local/puppet`):
   - hiera `data/common.yaml`: append the new name to
     `dhacme::issuer::domains` — the issuer runs one lego order and one
     publish per domain (single-name certs).
   - hiera `data/nodes/fusion.colo.notproduction.net.yaml`: `dhfirewall::open_tcp:
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
