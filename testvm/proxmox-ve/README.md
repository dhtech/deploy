# Proxmox VE test VM

Proxmox VE 9.2 test server installed from the **official Proxmox ISO**
(`proxmox-ve_9.2-1.iso`) using Proxmox's automated installer
(`proxmox-auto-install-assistant` with an embedded `answer.toml`).

## Specs

| | |
|---|---|
| System disk | 200 GiB sparse qcow2 (`/dev/vda`, ext4, LVM by installer) |
| Default resources | 12 GiB RAM, 4 vCPUs (`MEMORY`/`CPUS` env overrides) |
| SSH | `127.0.0.1:4454` (key-only, `~/.ssh/id_ecdsa`) |
| Web UI | `https://127.0.0.1:8006/` (self-signed cert) |
| Hostname / FQDN | `pve-test` / `pve-test.lan` |
| Root password | `pve-test` (web UI login, realm **Linux PAM**) |
| Locale | country `se`, keyboard `se`, `Europe/Stockholm` |
| Network | QEMU user-mode NAT (guest IP 10.0.2.15), ports forwarded on 127.0.0.1 only |
| Nested virt | Host has nested KVM enabled, so guests inside Proxmox get KVM |

## Setup (already automated)

```sh
./provision.sh   # idempotent: downloads + verifies everything, builds pve-auto.iso
./install.sh     # one-time unattended install, QEMU window, powers off when done
./start.sh       # normal start, serial console in the terminal
```

Everything the provisioner downloads is checksum-pinned:

- `proxmox-ve_9.2-1.iso` from enterprise.proxmox.com (sha256 verified)
- `proxmox-auto-install-assistant` 9.2.8, extracted from the Debian
  package into `./tools/` — nothing is installed on the host. Debian's
  `libcrypt.so.1` is extracted next to it because Fedora only ships
  `libcrypt.so.2`; the binary is run with `LD_LIBRARY_PATH` pointing there.

`provision.sh` then writes:

- `answer.toml` — the unattended-install answers (locale above, DHCP
  network, ext4 on `vda`, root password + your SSH public key). Holds the
  password and key, so it is chmod 600.
- `first-boot.sh` — baked into the ISO via `--on-first-boot`; runs once in
  the guest after installation and enables the serial console
  (`serial-getty@ttyS0` + `console=ttyS0` in GRUB) so `./start.sh` can use
  `-nographic` like the other test VMs.
- `pve-auto.iso` — the prepared ISO with the answer file embedded.

`install.sh` boots the prepared ISO with `-boot d -no-reboot`: the install
runs unattended in a QEMU window and the VM powers off at the end instead
of rebooting back into the installer.

## Daily use

Start (console attached to the terminal):

```sh
./start.sh
```

SSH:

```sh
ssh -i ~/.ssh/id_ecdsa -p 4454 root@127.0.0.1
```

Web UI: open <https://127.0.0.1:8006/>, accept the self-signed
certificate, log in as `root` / `pve-test`, realm **Linux PAM**.

Temporary resource override:

```sh
MEMORY=8G CPUS=2 ./start.sh
```

Shut down cleanly from inside (`poweroff`) or via the web UI.

## Guests

### deploy (VMID 100)

Debian 13 provision server for development, running as a guest inside
this Proxmox instance. Built from the official `debian-13-nocloud-amd64`
image (no cloud-init), customized offline on the pve host with
`virt-customize` (guestfs-tools):

- 2 GiB RAM, 2 vCPUs, 20 GiB disk (`local-lvm:vm-100-disk-0`), root
  partition grown to the full disk with `growpart`/`resize2fs`
- Static IP `10.0.2.16/24`, gw `10.0.2.2`, DNS `10.0.2.3`
  (static on purpose: slirp DHCP hands out `10.0.2.15` and collides
  with the pve host itself — this actually happened; see gotchas)
- Hostname `deploy`; `openssh-server` installed into the image,
  root SSH with the usual key; root password `pve-test` (console)
- Timezone `Europe/Stockholm`
- Serial console: `qm terminal 100` on the pve host
- Starts automatically with the pve host (`onboot 1`)

#### Management VLAN + API access

`vmbr0` on the pve host is VLAN-aware (`bridge-vlan-aware yes`,
`bridge-vids 2-4094`); management lives on **tagged VLAN 10**:

- pve host: `vmbr0.10` = `10.10.10.1/24`
  (config in `/etc/network/interfaces.d/mgmt-vlan`)
- the deploy server: second NIC `net1` (`bridge=vmbr0,tag=10`) = `ens19`,
  static `10.10.10.2/24` via `/etc/systemd/network/00-mgmt.network`

the deploy server talks to the Proxmox API over this VLAN as
`provisioner@pve` (role Administrator, token `provisioner@pve!dev`,
privsep off). Credentials on the deploy server in
`/root/.config/proxmox-api.env` (mode 600); token JSON also on the pve
host in `/root/provisioner-token.json`. Example:

```sh
set -a; . /root/.config/proxmox-api.env; set +a
curl -ks -H "Authorization: PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}" \
  "$PVE_API_URL/version"
```

`curl` and `jq` are installed on the deploy server.

Gotchas hit while building this (relevant for future nocloud guests):

- The nocloud image ships **netplan**, which generates a DHCP-on-`en*`
  networkd config named `10-netplan-all-en.network`. A custom
  `/etc/systemd/network/` file must sort **before** it to win — hence
  `00-static.network`, not `10-static.network`. With DHCP active, slirp
  gave the guest `10.0.2.15`, hijacking the pve host's IP via ARP and
  knocking pve's SSH/web UI offline until the static config took over.
- The nocloud image runs an interactive `systemd-firstboot` wizard on the
  serial console (timezone, root password) which **blocks boot** until
  answered. Pre-seed timezone/root-password with `virt-customize`
  (`--timezone`, `--root-password`) to avoid it, or answer once via the
  serial socket.
- No cloud-init means no automatic root-fs grow; run
  `growpart /dev/sda 1 && resize2fs /dev/sda1` after resizing the disk.

SSH from the workstation, jumping through pve-test:

```sh
ssh -o ProxyCommand="ssh -i ~/.ssh/id_ecdsa -p 4454 -W %h:%p root@127.0.0.1" \
    -i ~/.ssh/id_ecdsa root@10.0.2.16
```

After the next pve-test restart, directly (start.sh forwards
`127.0.0.1:4455` to `10.0.2.16:22`):

```sh
ssh -i ~/.ssh/id_ecdsa -p 4455 root@127.0.0.1
```

The the deploy server machine also runs the deploy stack under development:
ISC dhcpd on the deploy VLAN, a local redis, provisiond3 (from
`/root/src/provisiond3`, venv `/opt/provisiond`), and the HTML5 status
frontend on port 8080 (forwarded as `http://127.0.0.1:8768/` after the
next pve-test restart).

## Notes

- Port allocation follows the ~/vms convention: SSH ports count up from
  4444 (this VM has 4454); 8006 is forwarded 1:1 because it is the
  standard Proxmox web port.
- The VM uses the `pve-no-subscription` repository defaults from the ISO.
  Expect the "no valid subscription" dialog at web UI login.
- To reinstall from scratch: `rm pve-test.qcow2 pve-auto.iso`, then re-run
  `./provision.sh` and `./install.sh`. The ISO download and tools are kept.
- ISO version, checksums, and every other pinned value live at the top of
  `provision.sh`.

Reserved forwards: `127.0.0.1:8443` → vault website (nginx/LE on
vault1:443 via the deploy server DNAT 443); `127.0.0.1:8444` →
the deploy server:444, reserved for the future FusionDirectory/LDAP web UI
(DNAT to be added when ldap1 is deployed); `127.0.0.1:8445` →
the deploy server:445, reserved for the future Trac web UI.

## Web service names (workstation /etc/hosts)

The public zone (Route 53) points these at 127.0.0.1 already; the local
hosts entries make them work offline too. Paste into `/etc/hosts`:

```
# dhtech lab web services
127.0.0.1 vault.dh.notproduction.net
127.0.0.1 pve1.dh.notproduction.net
127.0.0.1 pve2.dh.notproduction.net
127.0.0.1 directory.dh.notproduction.net
127.0.0.1 doc.dh.notproduction.net
127.0.0.1 deploy.dh.notproduction.net
```

| URL | Service |
|---|---|
| <https://vault.dh.notproduction.net:8443/ui> | OpenBao website (Let's Encrypt) |
| <https://pve1.dh.notproduction.net:8006> | Proxmox web UI (Let's Encrypt) |
| <https://directory.dh.notproduction.net:8444> | Directory UI — LAM (planned) |
| <https://doc.dh.notproduction.net:8445> | Trac + SVN (planned) |
| <https://deploy.dh.notproduction.net:8446> | Deploy status (after next restart) |

Machine names (`*.colo.notproduction.net`) resolve only inside the lab
(dnsmasq on the deploy server); machine TLS is the puppet CA
(`Puppet CA: puppet1.colo.notproduction.net`) — import
`puppet1:/etc/puppet/puppetserver/ca/ca_crt.pem` into the browser if
you also want the OpenBao `:8200` listener trusted.
