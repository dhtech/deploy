#!/bin/sh

set -eu
cd "$(dirname "$0")"

if [ ! -f pve-test.qcow2 ]; then
  echo "Missing pve-test.qcow2; run ./provision.sh and ./install.sh first" >&2
  exit 1
fi

exec qemu-system-x86_64 \
  -name proxmox-ve \
  -enable-kvm \
  -machine q35 \
  -cpu host \
  -m "${MEMORY:-12G}" \
  -smp "${CPUS:-4}" \
  -drive file=pve-test.qcow2,format=qcow2,if=virtio \
  -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:4454-:22,hostfwd=tcp:127.0.0.1:8006-:8006,hostfwd=tcp:127.0.0.1:4455-10.0.2.16:22,hostfwd=tcp:127.0.0.1:8768-10.0.2.16:8080,hostfwd=tcp:127.0.0.1:8200-10.0.2.16:8200,hostfwd=tcp:127.0.0.1:8443-10.0.2.16:443,hostfwd=tcp:127.0.0.1:8444-10.0.2.16:444 \
  -nographic
