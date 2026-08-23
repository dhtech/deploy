#!/bin/sh

set -eu
cd "$(dirname "$0")"

iso_name=proxmox-ve_9.2-1.iso
iso_url=${ISO_URL:-"https://enterprise.proxmox.com/iso/$iso_name"}
iso_sha256=4e88fe416df9b527624a175f24c9aa07c714d3332afb1ee3dbf3879573ef2c6c

assistant_deb=proxmox-auto-install-assistant_9.2.8_amd64.deb
assistant_url="http://download.proxmox.com/debian/pve/dists/trixie/pve-no-subscription/binary-amd64/$assistant_deb"
assistant_sha256=94b562ac026bf9a989e0a5834538ad24e38606f8858367af0e665af0bcbdc2e2

# Debian's libcrypt.so.1 for the assistant binary; Fedora only ships .so.2.
libcrypt_deb=libcrypt1_4.4.38-1_amd64.deb
libcrypt_url="https://deb.debian.org/debian/pool/main/libx/libxcrypt/$libcrypt_deb"
libcrypt_sha256=0ebc144d662e3197982d1bf3a7b8b35ca845e54c68811de0328b1f0d7c67585c

key_file=${SSH_PUBLIC_KEY_FILE:-"$HOME/.ssh/id_ecdsa.pub"}
root_password=pve-test

if [ ! -r "$key_file" ]; then
  echo "SSH public key not readable: $key_file" >&2
  exit 1
fi

fetch() {
  # fetch <url> <file> <sha256>
  if [ ! -f "$2" ]; then
    rm -f "$2.part"
    curl -fL --retry 3 --output "$2.part" "$1"
    printf '%s  %s\n' "$3" "$2.part" | sha256sum -c -
    mv "$2.part" "$2"
  else
    printf '%s  %s\n' "$3" "$2" | sha256sum -c -
  fi
}

fetch "$iso_url" "$iso_name" "$iso_sha256"

# Extract the Proxmox auto-install assistant into ./tools (nothing is
# installed on the host).
assistant=tools/usr/bin/proxmox-auto-install-assistant
if [ ! -x "$assistant" ]; then
  mkdir -p tools
  fetch "$assistant_url" "tools/$assistant_deb" "$assistant_sha256"
  fetch "$libcrypt_url" "tools/$libcrypt_deb" "$libcrypt_sha256"
  (cd tools && ar p "$assistant_deb" data.tar.xz | tar -xJ ./usr/bin/proxmox-auto-install-assistant)
  (cd tools && ar p "$libcrypt_deb" data.tar.xz | tar -xJ)
fi
run_assistant() {
  LD_LIBRARY_PATH=$PWD/tools/usr/lib/x86_64-linux-gnu "$assistant" "$@"
}

ssh_key=$(cat "$key_file")

cat >answer.toml <<EOF
[global]
keyboard = "se"
country = "se"
fqdn = "pve-test.lan"
mailto = "root@pve-test.lan"
timezone = "Europe/Stockholm"
root-password = "$root_password"
root-ssh-keys = ["$ssh_key"]

[network]
source = "from-dhcp"

[disk-setup]
filesystem = "ext4"
disk-list = ["vda"]

[first-boot]
source = "from-iso"
EOF
chmod 600 answer.toml

# Runs once inside the guest after installation: enable the serial console
# so ./start.sh can attach it as a normal terminal, like the other test VMs.
cat >first-boot.sh <<'EOF'
#!/bin/sh
set -eu
systemctl enable --now serial-getty@ttyS0.service
sed -i 's/^GRUB_CMDLINE_LINUX=.*/GRUB_CMDLINE_LINUX="console=tty0 console=ttyS0,115200"/' /etc/default/grub
update-grub
EOF
chmod 755 first-boot.sh

if ! command -v xorriso >/dev/null 2>&1; then
  echo "xorriso is required to prepare the ISO (sudo dnf install xorriso)" >&2
  exit 1
fi

run_assistant validate-answer answer.toml

if [ ! -f pve-auto.iso ]; then
  run_assistant prepare-iso "$iso_name" \
    --fetch-from iso \
    --answer-file answer.toml \
    --on-first-boot first-boot.sh \
    --output pve-auto.iso
  [ -f pve-auto.iso ] || { echo "prepare-iso failed: pve-auto.iso not created" >&2; exit 1; }
fi

if [ ! -f pve-test.qcow2 ]; then
  qemu-img create -f qcow2 pve-test.qcow2 300G
fi

chmod 600 pve-test.qcow2
printf 'Provisioned. Run ./install.sh once (unattended, ~5-10 min),\n'
printf 'then start the VM with ./start.sh\n'
