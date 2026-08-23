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

### provision-dev (VMID 100)

Debian 13 provision server for development, running as a guest inside
this Proxmox instance. Built from the official `debian-13-nocloud-amd64`
image (no cloud-init), customized offline on the pve host with
`virt-customize` (guestfs-tools):

- 2 GiB RAM, 2 vCPUs, 20 GiB disk (`local-lvm:vm-100-disk-0`)
- Static IP `10.0.2.16/24`, gw `10.0.2.2`, DNS `10.0.2.3`
  (static on purpose: slirp DHCP could hand out `10.0.2.15` and collide
  with the pve host itself)
- Hostname `provision-dev`; `openssh-server` installed into the image,
  root SSH key-only (same key as everything else)
- Serial console: `qm terminal 100` on the pve host
- Starts automatically with the pve host (`onboot 1`)

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
