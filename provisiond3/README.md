# provisiond3

Third-generation deploy provisioner: a Python 3 daemon that consumes VM
and hardware install orders from Redis and drives hypervisors/hardware.
Replaces the Python 2 `provisiond` (pysphere) entirely — hard swap, no
parallel run.

## Backends

| type | API | notes |
|---|---|---|
| `proxmox` | proxmoxer (REST, API token) | OVMF/q35, net-first boot, VLAN tag flip |
| `vmware` | pyvmomi (official SDK) | parity port of gen-2 esxi.py incl. DVS ops, VCSA deploy (`os: vcenter`), `configure-vcenter-*` requests |
| `ocp` | pyghmi (IPMI) | one-shot netboot + power for OCP nodes |

C7000 blade support was dropped in the rewrite.

## Redis contract

Orders and state (unchanged from gen-2, produced by `utils/deploy-vm` and
the backend CGIs):

- `create-vm-<uuid>` — VM order `{manager, name, datacenter, cpus, disk,
  memory, datastore, os, ipv4:{vlan,address,prefix,gateway}}`; consumed on
  success, annotated with an `error` field (TTL preserved) on failure.
- `host-<fqdn>` — install state; when `installed` flips true the daemon
  moves the VM's NIC to `network.vlan` and sets `provisioned` (set *before*
  acting, as a loop guard).
- `install-<serial>` / `bays-<manager>` — hardware flow (OCP).
- `configure-vcenter-<...>` — vCenter management requests (VMware only).

**Changed in gen-3 (identity redesign):** inventory keys are
`vm-<manager>-<smbios-uuid-lowercase>` for *all* hypervisors (600 s TTL).
`server/backend/ipxe/inventory.py` resolves PXE clients UUID-first with an
`install-<serial>` fallback — no SMBIOS manufacturer sniffing. Proxmox VMs
carry a daemon-generated SMBIOS UUID; the VMware backend handles the
vmx-12 UUID byte-swap internally.

## Provision flow

1. VM is created on the **deployment VLAN** (tagged, boot order net-first)
   and powered on.
2. It PXE-installs there: DHCP + boot chain from the provision server's
   ISC dhcpd, iPXE identification, Debian preseed. The installer writes
   production network config from ipplan and sets up (but does not run)
   puppet, plus an **nftables baseline** (SSH from jumpgates + puppet
   only).
3. Installer finish flips `installed: true` → daemon moves the NIC to the
   **production VLAN** (MAC preserved) and marks `provisioned`.
4. First boot in production: puppet runs and applies the full role,
   including the production nftables ruleset.

## Deployment

No containers. The daemon runs on the provision server (which sits on
mgmt + deployment + production VLANs and serves dhcpd on the deployment
VLAN) as a systemd service:

```sh
python3 -m venv /opt/provisiond
/opt/provisiond/bin/pip install -r requirements.lock .
useradd -r -s /usr/sbin/nologin provisiond
cp provisiond.service /etc/systemd/system/
systemctl enable --now provisiond
```

Configuration is Puppet-delivered in production; `config.yaml.sample` is
the template reference. Secrets come from `/etc/provision/provisiond.env`
(`EnvironmentFile`) or Vault (`{vault: path}` refs, TLS-cert auth via
`VAULT_CERT`/`VAULT_KEY`). TLS verification is always explicit per
manager (`verify_tls: <ca-path>|true|false`); there are no global
SSL-disable hacks.

## Test environment

pve-test (Proxmox VE in QEMU) + provision-dev guest — see
`testvm/proxmox-ve/README.md`. There is no Puppet in the test env, so
`/etc/provision/config.yaml`, `provisiond.env`, dhcpd.conf and
`/etc/deploy.yaml` are seeded manually on provision-dev.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e .[dev]
.venv/bin/pytest                  # unit tests
.venv/bin/mypy src && .venv/bin/ruff check src tests
.venv/bin/pytest -m integration   # on provision-dev only: live e2e on pve-test
```
