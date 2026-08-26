# deploy

The Dreamhack deployment system, generation 3: bare data in, running
fleet out. One text file of addresses (ipplan) and one yaml of roles
(the manifest) drive everything - PXE install, disk layout, secrets,
puppet classification, firewalls, DNS, monitoring, BGP. If a fact is
not in the data, it does not exist on a server.

This tree is developed against a nested Proxmox lab that mirrors the
production colo ("the new colo"); the conventions are prod's
(allevents/colo grammar, package model, flow engine), re-rendered
from data where prod still installs by hand.

## The pipeline

```
svn (doc1)                     puppet master              fleet
ipplan + manifest.yaml   --->  ipplan.db  --->  ENC  ---> catalogs
      |                        (published        |
  pre-commit GATE               per commit)      +-- fileserver:
  compiles the whole                                 ipplan.db, apps
  tree; bad data
  never lands
```

- **ipplan** (`allevents/<site>/.../ipplan`, `<event>/core/ipplan`):
  networks, hosts, and their options in prod's tab-aligned grammar.
  `#@ IPV6-X-NET` masters derive per-vlan v6; `pkg=` grants roles;
  `expose=`, `webname=`, `addr=`, `wg=`, `nat=` are all consumed
  downstream. The svn pre-commit gate ([utils/svn-pre-commit] +
  [utils/ipplan2db]) compiles every commit into a throwaway db and
  rejects anything that would not build - unknown flags, misfiled
  hosts, misaligned columns, overlapping networks, all of it, with
  every error named at once. `force:` in the log message is the
  escape hatch.
- **manifest.yaml**: what a package *means* - puppet classes, flows
  (client/server service pairs), hardware sizing, appdisks, and the
  `monitor:` idiom (one url line = a site-prometheus scrape job plus
  the scoped firewall opening). `appstore.yaml` pins the application
  artifacts the puppet master mirrors for the fleet.
- **The compiler and the gate live in their own repo**,
  [dhtech/ipplan2db](https://github.com/dhtech/ipplan2db) (successor
  to ipplan2sqlite) - clone it as a sibling checkout; the tests and
  seed script load it from there.
- **ipplan.db**: the compiled single source of truth. doc1's
  post-commit publishes it per revision; the puppet master syncs and
  serves it; every consumer reads only the db.
- **ENC** ([server/backend/enc.py] + [server/backend/modules/]): the
  external node classifier. The global enc imposes nothing - each
  package has a generator module that derives its parameters from the
  db (topology, webnames, scrape targets, router config, wg peers).
  Fleet baselines are DEFAULT packages (`node`, `managed`), data like
  everything else.
- **Addresses are never puppet-managed**: the installer renders
  `/etc/network/interfaces` (immutable after); changes are one-shots
  or redeploys.

## Layout

| path | what |
|---|---|
| `server/backend/` | deploy web backend: PXE/preseed flow, finish/report, the ENC and its per-pkg modules |
| `server/libdhdeploy/` | vendored copy of the shared library - its authoritative home is [dhtech/ipplan2db](https://github.com/dhtech/ipplan2db) (byte-parity-guarded) |
| `server/frontend/` | the deploy status site (fleet panel, develop tab) |
| `server/tests/` | pytest suite - includes the prod-ipplan corpus (byte-for-byte parity with the gen-2 engine) and gate-rule proofs |
| `utils/deploy-vm`, `deploy-bay` | provisioning helpers (create VMs on pve, bay handling) |
| `utils/directory-import` | sanitized prod-directory LDIF import |
| `deployd/` | the provisioning daemon (pve API, bao-backed tokens) |
| `debian/`, `ipxe/`, `build-*` | installer assets and their builders; `server/backend/debian/post-install-hardening` is the CIS pass |
| `testvm/deploy-stack/` | the lab seed: vendored ipplan snapshot, preseed, setup scripts (svn on doc1 owns the data once live) |
| `testvm/proxmox-ve/` | the nested-Proxmox bench (start/provision scripts) |

## Running the tests

```
cd server && python3 -m pytest tests/
```

The compiler's own suite (prod-ipplan corpus, gate rules) lives with
it in dhtech/ipplan2db; this repo's tests cover the ENC, flows and
provisioning and load the compiler from the sibling checkout. Tests
run before every commit - no exceptions.

## Conventions worth knowing

- **Per-site singletons**: one `router`, `prometheus`, `grafana` per
  site, number-less. Prometheus scrapes only its own site; grafana
  talks only to its site's prometheus.
- **The router is data**: `pkg=router(asn=N)` renders BIRD 2;
  `wg=`/`wgsrc=` on a link net render the colovpn WireGuard listener
  (exposure always declared, never implicit); event routers are one
  line: `pkg=router(asn=N,uplink=colo,egress=local|colo)`. The
  deployment network is site-local by decree - the `deploy` network
  flag keeps it out of BGP and cross-site paths.
- **Secrets** live in bao (OpenBao): machine secrets seeded
  server-side (bind-secret pattern), never in catalogs or ENC
  output; wg keypairs backed up under `services/colovpn`.
- **Static everything**: no DHCPv6/SLAAC on our nets, no learned
  defaults; v6 policy is one installer-written sysctl file.
