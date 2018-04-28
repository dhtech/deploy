#!/usr/bin/env python2
import os
import yaml

from lib import metadata

# Move the secrets out of the directory that is kept in SVN
config = yaml.safe_load(file('/etc/deploy.yaml'))

client, cm = metadata.find(os.environ['REMOTE_ADDR'])
network = metadata.network(client, cm)

if not network:
  # No network info, default to manual installation
  print ''
  exit(0)


def networkname(network, vlan):
    return '%s: %s' % (vlan, network.split('@', 1)[1])


def deploy_vm(hostname):
    network, vlan = metadata.get_vlan(hostname)
    if network is None:
      raise Exception('Unknown host %s' % hostname)
    name = networkname(network, vlan)
    vmx = file('deploy-esx.template').read().format(
            hostname=hostname, network=name)
    # Create provisioning VMX
    print """
mkdir /vmfs/volumes/datastore1/{hostname}/
cat << _EOF_ > /vmfs/volumes/datastore1/{hostname}/vm.vmx
{vmx}
_EOF_
vmkfstools -c 16G /vmfs/volumes/datastore1/{hostname}/disk.vmdk
ID=$(vim-cmd solo/registervm /vmfs/volumes/datastore1/{hostname}/vm.vmx)
vim-cmd vmsvc/power.on $ID
""".format(vmx=vmx, hostname=hostname)

print ''
print """
vmaccepteula
rootpw {rootpw}
install --firstdisk --overwritevmfs
network --bootproto=static --ip={ipaddr} --netmask={netmask} --gateway={gateway} --nameserver=8.8.8.8 --vlanid={vlan} --addvmportgroup=false
reboot
""".format(
        ipaddr=network.v4_address, netmask=network.v4_netmask,
        gateway=network.v4_gateway, vlan=network.vlan,
        rootpw=config['root-password'])

# First-boot script
# TODO(bluecmd): Replace with dhtech CA certs
print """
%firstboot --interpreter=busybox
esxcli network ip interface ipv4 set --interface-name=vmk0 --ipv4={ipaddr} --netmask={netmask} --type=static
esxcli network ip dns server add 8.8.8.8
esxcli network ip dns server add 8.8.4.4
esxcli system hostname set --fqdn={hostname}
esxcli network vswitch standard portgroup set -p="Management Network" -v={vlan}
esxcfg-route -a default {gateway}

esxcli network vswitch standard portgroup add -v=vSwitch0 -p=deploy
esxcli network vswitch standard portgroup set -p=deploy -v=4095
""".format(ipaddr=network.v4_address, netmask=network.v4_netmask,
           gateway=network.v4_gateway, hostname=client.hostname,
           vlan=network.vlan)

deploy_iter = metadata.get_deploy(client.hostname)

# Add all VLANs if we're deploying
if deploy_iter:
  for network, vlan in metadata.all_vlans_in_same_domain(client.hostname):
      name = networkname(network, vlan)
      print """
esxcli network vswitch standard portgroup add -v=vSwitch0 -p="{name}"
esxcli network vswitch standard portgroup set -p="{name}" -v={vlan}
""".format(name=name, vlan=vlan).strip()

# Continuation of first-boot
print """
rm /etc/vmware/ssl/rui.crt
rm /etc/vmware/ssl/rui.key
/sbin/generate-certificates
/sbin/services.sh restart

vim-cmd hostsvc/enable_ssh
vim-cmd hostsvc/start_ssh
vim-cmd hostsvc/enable_esx_shell
vim-cmd hostsvc/start_esx_shell

vim-cmd hostsvc/autostartmanager/enable_autostart true
# Start machines with 20 sec intervals and do not wait for heartbeats
vim-cmd hostsvc/autostartmanager/update_defaults 20 20 no

echo 'vmx.allowNested = "TRUE"' >> /etc/vmware/config
echo 'hv.assumeEnabled = "TRUE"' >> /etc/vmware/config
"""

for host in deploy_iter:
  deploy_vm(host)

# Post-install script
# TODO(bluecmd): This seems to fail first install, but always succeed the second install
# TODO(bluecmd): This probably doesn't like our CA
print """
%post --interpreter=busybox --ignorefailure=true
wget https://deploy.tech.dreamhack.se/provision.py
""".format(hostname=client.hostname)
