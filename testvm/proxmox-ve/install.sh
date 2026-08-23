#!/bin/sh

# One-time unattended installation of Proxmox VE from the prepared ISO.
# Opens a QEMU window so the installer can be watched; the VM powers off
# when the installation is done (-no-reboot). Then use ./start.sh.

set -eu
cd "$(dirname "$0")"

for f in pve-auto.iso pve-test.qcow2; do
  if [ ! -f "$f" ]; then
    echo "Missing $f; run ./provision.sh first" >&2
    exit 1
  fi
done

exec qemu-system-x86_64 \
  -name proxmox-ve-install \
  -enable-kvm \
  -machine q35 \
  -cpu host \
  -m "${MEMORY:-12G}" \
  -smp "${CPUS:-4}" \
  -drive file=pve-test.qcow2,format=qcow2,if=virtio \
  -cdrom pve-auto.iso \
  -boot d \
  -no-reboot \
  -monitor unix:install-mon.sock,server,nowait \
  -nic user,model=virtio-net-pci
