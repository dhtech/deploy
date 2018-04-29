# provisiond

`provisiond` is the part of the deployment system that talks to various
platforms to actuate the creation of VMs and VM indexing.

You would most likely want to run a `provisiond` per physical site if you
have local management networks.

`provisiond` supports two platform to index and actuate:

 * HP C7000 Chassis
 * VMware ESXi and VMware vCenter

# How it works

`provisiond` communicates through work orders places on Redis server by the
central deployment system. It uses a busy loop to poll for new changes.

`provisiond` also uploads inventories of the VMs it is able to index.

Finally, after a VM has been installed it supports executing actions such
as moving the VM to another network.

# Deployment

By default `provisiond` will deploy a VM to PXE boot from a trunked network.
When the installer starts, it will switch to use VLAN tagged interface and
install from its real production network.

After deployment `provisiond` will move the VM to a untagged VLAN network
for use during the life of the VM.

## vCenter

`provisiond` supports deploying vCenter. For this you need two things:

 * VMware vCenter ISO file location set in `VMWARE_VCENTER_ISO`
 * Permissions for `provisiond` to mount ISO files

When installing, `provisiond` will write the generated password to Vault.
