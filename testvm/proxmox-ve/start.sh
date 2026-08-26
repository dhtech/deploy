#!/bin/sh

set -eu
cd "$(dirname "$0")"

if [ ! -f pve-test.qcow2 ]; then
  echo "Missing pve-test.qcow2; run ./provision.sh and ./install.sh first" >&2
  exit 1
fi

# udp 8451 -> router wg 51820 (colovpn listener, P5): in-bench peers
# reach 10.0.2.17:51820 directly; this forward serves outside peers.
exec qemu-system-x86_64 \
  -name proxmox-ve \
  -enable-kvm \
  -machine q35 \
  -cpu host \
  -m "${MEMORY:-20G}" \
  -smp "${CPUS:-6}" \
  -drive file=pve-test.qcow2,format=qcow2,if=virtio \
  -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-10.0.2.17:2022,hostfwd=tcp:127.0.0.1:4454-:22,hostfwd=tcp:127.0.0.1:4455-10.0.2.16:22,hostfwd=tcp:127.0.0.1:8006-:8006,hostfwd=tcp:127.0.0.1:8200-10.0.2.17:8200,hostfwd=tcp:127.0.0.1:8443-10.0.2.17:443,hostfwd=tcp:127.0.0.1:8444-10.0.2.17:444,hostfwd=tcp:127.0.0.1:8445-10.0.2.17:445,hostfwd=tcp:127.0.0.1:8446-10.0.2.16:446,hostfwd=tcp:127.0.0.1:8447-10.0.2.17:447,hostfwd=tcp:127.0.0.1:8448-10.0.2.17:448,hostfwd=tcp:127.0.0.1:8449-10.0.2.17:449,hostfwd=tcp:127.0.0.1:8450-10.0.2.17:450,hostfwd=tcp:127.0.0.1:8451-10.0.2.17:451,hostfwd=tcp:127.0.0.1:8452-10.0.2.17:452,hostfwd=tcp:127.0.0.1:8453-10.0.2.17:453,hostfwd=tcp:127.0.0.1:8454-10.0.2.17:454,hostfwd=tcp:127.0.0.1:8455-10.0.2.17:455,hostfwd=tcp:127.0.0.1:8456-10.0.2.17:456,hostfwd=tcp:127.0.0.1:8457-10.0.2.17:457,hostfwd=tcp:127.0.0.1:8458-10.0.2.17:458,hostfwd=tcp:127.0.0.1:8459-10.0.2.17:459,hostfwd=tcp:127.0.0.1:8460-10.0.2.16:8080,hostfwd=udp:127.0.0.1:8451-10.0.2.17:51820 \
  -nographic
