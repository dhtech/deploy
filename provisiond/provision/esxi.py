#
# DISCLAIMER: A lot of code here was borrowed from pysphere's mailinglist
# and even more code from ansible-modules-core/.../vsphere_guest.py
#
import base64
import collections
import hashlib
import json
import logging
import pysphere
from pysphere.resources import VimService_services as VI

# WARNING(2014-10-18): If you set this higher than 8 the
# vSphere client will not allow you to modify the VMs
ESXI_HW_VERSION = 'vmx-13'
VCENTER_HW_VERSION = 'vmx-13'

# This value can be overridden by setting an 'os' option in ipplan.
DEFAULT_OS = 'debian'

# For valid 'osid' values, see:
# http://pubs.vmware.com/vsphere-55/topic/com.vmware.wssdk.apiref.doc/vim.vm.GuestOsDescriptor.GuestOsIdentifier.html
# (URL is for vSphere 5.5, more up-to-date information might be available)

SysConf = collections.namedtuple('SysConf', ('osid', 'scsi'))

DistributedSwitchPort = collections.namedtuple('DistributedSwitchPort',
                                              ('uuid', 'portgroup'))

# Map OS -> Hardware configurations
SYSTEM_CONFIGURATION_MAP = {
  'debian': SysConf(osid='debian10_64Guest', scsi='paravirtual'),
  'ubuntu': SysConf(osid='ubuntu64Guest', scsi='paravirtual'),
  'openbsd': SysConf(osid='otherGuest64', scsi='lsi_sas'),
  'coreos': SysConf(osid='otherGuest64', scsi='paravirtual')
}

NET_DEVICE_TYPES = ['VirtualE1000', 'VirtualVmxnet3']


class Error(Exception):
  """Base exception class for this module."""


class DatastoreNotFoundError(Error):
  """Supplied datastore not found, or no datastores available."""


class CreateVmError(Error):
  """An error occurred when creating a VM."""


class ScsiControllerNotFoundError(Error):
  """Supplied SCSI controller type not found."""


class OsNotSupportedError(Error):
  """Supplied OS not supported."""


class ProvisionVmError(Error):
  """An error occurred when provisioning a VM."""


class NicNotFoundError(Error):
  """Could not find any network cards."""


class UnknownVlanError(Error):
  """No matching networks found for a given VLAN."""


class CreateClusterError(Error):
  """An error occurred when creating cluster in vSphere."""


class AddHostToVsphereError(Error):
  """An error occurred when adding host to vSphere."""


class CreateDvSwitchError(Error):
  """An error occurred when creating a new DVS in vSphere."""


class CreateDvPortgroupError(Error):
  """An error occurred when creating a new DV port group in vSphere."""


class SwitchPortNotFoundError(Error):
  """Could not find given distributed switch port group."""


class NoHostsInClusterError(Error):
  """Tried to create VM in cluster without any ESXi servers."""

class NoHostsInDatacenterError(Error):
  """Datacenter has no hosts."""

class DatacenterNotFoundError(Error):
  """The specificed datacenter was not found."""


def _get_datacenter_props(server, datacenter):
  if datacenter is None:
    datacenter = server.get_datacenters().values()[0]
  for k, v in server.get_datacenters().iteritems():
    if v == datacenter:
      return pysphere.VIProperty(server, k)
  raise DatacenterNotFoundError(
          'Found no datacenter named "%s"' % datacenter)


def _vlan_to_network(server, vlan, datacenter):
  """Given a numeric VLAN, resolve to network name."""
  # Fetch VLAN -> network label map
  hosts = server._retrieve_properties_traversal(
      property_names=('name', ), obj_type='HostSystem',
      from_node=datacenter.hostFolder._obj)
  if not hosts:
    raise NoHostsInClusterError('Datacenter %s has no hosts' % datacenter.name)
  host = next(p.Val for p in next(hosts).PropSet if p.Name == 'name')
  prop = pysphere.VIProperty(server, host)

  network_info = prop.configManager.networkSystem.networkInfo

  # This doesn't work if we have the same VLAN in multiple switches
  vlan_map = {pg.spec.vlanId: pg.spec.name for pg in network_info.portgroup}

  if vlan not in vlan_map:
    raise UnknownVlanError('VLAN %d not found in any networks' % vlan)

  return vlan_map[vlan]


def _vlan_to_dvs_portgroup_key(server, vlan, datacenter):
  """Given a numeric VLAN, resolve to portgroup key (dvSwitch specific)."""
  netfolder = datacenter.networkFolder._obj
  dvportgroup_resources = server._retrieve_properties_traversal(
      property_names=['key', 'config'],
      from_node=netfolder, obj_type='DistributedVirtualPortgroup')
  for pg in dvportgroup_resources or []:
    config = next(x.Val for x in pg.PropSet if x.Name == 'config')
    port_config = config.get_element_defaultPortConfig()
    vlanid = port_config.get_element_vlan().get_element_vlanId()
    if 'dvuplinks' in config.get_element_name().lower():
      # Hack to find deploy trunk. We do not want to use the uplink PGs
      # and I found no way of detecting this nicely.
      continue
    # Emulate "vlan id 4095" in dswitch by picking the first trunk
    if (type(vlanid) != int and vlan == 4095) or vlanid == vlan:
      return (next(x.Val for x in pg.PropSet if x.Name == 'key'),
              config.get_element_name())
  else:
    raise SwitchPortNotFoundError(
        'Uses dvSwitch, but could not find a port with VLAN %d' % vlan)


def _portgroup_to_dvswitch(server, dvswitch_resources, portgroup_key):
  # Yeah, this is what we need to do, sadly. Yo dog..
  for dvswitch in dvswitch_resources:
    for p in (x for x in dvswitch.PropSet if x.Name == 'portgroup'):
      for pg in p.Val.ManagedObjectReference:
        key_object = server._get_object_properties(pg, property_names=['key'])
        for key in key_object.PropSet:
          if key.Val == portgroup_key:
            return dvswitch
  raise SwitchPortNotFoundError(
      'Could not find backing switch for port group %s' % portgroup_key)


def _find_dvswitch(server, vlan, datacenter):
  """Try to find a dvSwitch named as a network."""
  netfolder = datacenter.networkFolder._obj
  dvswitch_resources = server._retrieve_properties_traversal(
      property_names=['uuid', 'portgroup'],
      from_node=netfolder, obj_type='DistributedVirtualSwitch')

  if not dvswitch_resources:
    return None

  # Count number of uplinks we can use to make sure that we're not trying to
  # use an un-initialized DVS
  for dvswitch in dvswitch_resources:
    if pysphere.VIProperty(server, dvswitch._obj).summary.numPorts > 0:
      break
  else:
    return None

  # Find the dvPortgroup that contains a portgroup with the correct VLAN
  portgroup_key, portgroup_name = _vlan_to_dvs_portgroup_key(server, vlan, datacenter)

  # Now get the associated dvswitch
  dvswitch = _portgroup_to_dvswitch(server, dvswitch_resources, portgroup_key)
  dvswitch_uuid = next(p.Val for p in dvswitch.PropSet if p.Name == 'uuid')
  dvswitch_name = pysphere.VIProperty(server, dvswitch._obj).summary.name
  print 'Using VLAN %s on PG %s on DVS %s' % (vlan, portgroup_name, dvswitch_name)
  return DistributedSwitchPort(dvswitch_uuid, portgroup_key)


def create_nic_backing(server, vlan, datacenter):
  dvswitch = _find_dvswitch(server, vlan, datacenter)
  if dvswitch:
    nic_backing_port = VI.ns0.DistributedVirtualSwitchPortConnection_Def(
        'nic_backing_port').pyclass()
    nic_backing_port.set_element_switchUuid(dvswitch.uuid)
    nic_backing_port.set_element_portgroupKey(dvswitch.portgroup)

    nic_backing = (
        VI.ns0.VirtualEthernetCardDistributedVirtualPortBackingInfo_Def(
          'nic_backing').pyclass())
    nic_backing.set_element_port(nic_backing_port)
    return nic_backing
  # Default to standard ESXi network
  nic_backing = VI.ns0.VirtualEthernetCardNetworkBackingInfo_Def(
      'nic_backing').pyclass()
  nic_backing.set_element_deviceName(_vlan_to_network(server, vlan, datacenter))
  return nic_backing


def get_ssl_thumbprint(server):
  """Calculate the SSL thumbprint for a given server."""
  # Source: https://groups.google.com/forum/#!topic/pysphere/rio7h8nWDcw
  host = next(server.get_hosts().iterkeys())
  prop = pysphere.VIProperty(server, host)
  # Prop is a "HostSystem"
  # http://pubs.vmware.com/vsphere-60/index.jsp#com.vmware.wssdk.apiref.doc/vim.HostSystem.html
  cert = ''.join(chr(x) for x in prop.config.certificate)
  # Remove --BEGIN CERTIFICATE-- and --END CERTIFICATE--
  cert = ''.join(x for x in cert.split('\n') if not x.startswith('--')).strip()
  cert_decode = base64.decodestring(cert)
  cert_digest = hashlib.sha1(cert_decode).hexdigest()

  # SSL Thumbprint MUST be uppercase for VMware use
  ssl_thumbprint = ':'.join(
      cert_digest[i:i + 2] for i in range(0, len(cert_digest), 2))
  return ssl_thumbprint.upper()


def get_server_ip(server):
  """Extract server's configured IP."""
  host = next(server.get_hosts().iterkeys())
  prop = pysphere.VIProperty(server, host)
  # Prop is a "HostSystem"
  # http://pubs.vmware.com/vsphere-60/index.jsp#com.vmware.wssdk.apiref.doc/vim.HostSystem.html
  return prop.config.network.vnic[0].spec.ip.ipAddress


def get_server_fqdn(server):
  """Extract server's configured fqdn."""
  host = next(server.get_hosts().iterkeys())
  prop = pysphere.VIProperty(server, host)
  # Prop is a "HostSystem"
  # http://pubs.vmware.com/vsphere-60/index.jsp#com.vmware.wssdk.apiref.doc/vim.HostSystem.html
  dnsconfig = prop.config.network.dnsConfig
  return dnsconfig.hostName + '.' + dnsconfig.domainName


def provision_vm(server, vm, vlan):
  # TODO: Set datacenter_prop
  # Set VMs first NIC to the correct label
  hardware = pysphere.VIProperty(server, vm).config.hardware
  nic = next((x._obj for x in hardware.device
              if x._type in NET_DEVICE_TYPES), None)
  if not nic:
    raise NicNotFoundError('No NIC found')

  nic.set_element_backing(create_nic_backing(server, vlan, datacenter_prop))

  # Submit reconfig request
  # Copy/paste from pysphere mailinglist
  request = VI.ReconfigVM_TaskRequestMsg()
  _this = request.new__this(vm)
  _this.set_attribute_type(vm.get_attribute_type())
  request.set_element__this(_this)
  spec = request.new_spec()
  dev_change = spec.new_deviceChange()
  dev_change.set_element_device(nic)
  dev_change.set_element_operation('edit')
  spec.set_element_deviceChange([dev_change])
  request.set_element_spec(spec)

  ret = server._proxy.ReconfigVM_Task(request)._returnval
  task = pysphere.VITask(ret, server)
  status = task.wait_for_state([task.STATE_SUCCESS, task.STATE_ERROR])
  if task.get_state() == task.STATE_ERROR:
    raise ProvisionVmError(task.get_error_message())


def find_datastore(target_config, datastore=None, brackets=True):
  """Find the given datastore.

  If no datastore is given, the one with the most free space is used.
  """
  # Enumerate all datastores with free space.
  datastores = {}
  for d in target_config.Datastore:
    if not d.Datastore.Accessible:
      continue
    logging.info('Considering datastore %s with %s free',
            d.Datastore.Name, d.Datastore.FreeSpace)
    datastores[d.Datastore.Name] = (
            d.Datastore.Datastore, d.Datastore.FreeSpace)

  if datastore:
    if datastore not in datastores:
      raise DatastoreNotFoundError('Datastore %s does not appear to exist' %
               datastore)
    logging.info('Selected datastore %s (user provided)', datastore)
  else:
    # Use the datastore with most free space
    datastore = max(datastores.iteritems(), key=lambda x: int(x[1][1]))[0]
    logging.info('Selected datastore %s (max free)', datastore)

  ds, _ = datastores[datastore]
  return '[%s]' % datastore if brackets else datastore, ds


def add_scsi_controller(server, new_vm_config, scsi_controller_type,
                        bus_num=0, disk_ctrl_key=0):
  """Add a SCSI controller to the configuration."""

  scsi_ctrl_spec = new_vm_config.new_deviceChange()
  scsi_ctrl_spec.set_element_operation('add')

  if scsi_controller_type == 'paravirtual':
    scsi_ctrl = VI.ns0.ParaVirtualSCSIController_Def('scsi_ctrl').pyclass()
  elif scsi_controller_type == 'lsi_sas':
    scsi_ctrl = VI.ns0.VirtualLsiLogicSASController_Def('scsi_ctrl').pyclass()
  else:
    raise ScsiControllerNotFoundError(
      'SCSI controller type %s is not supported' % scsi_controller_type)

  scsi_ctrl.set_element_busNumber(bus_num)
  scsi_ctrl.set_element_key(disk_ctrl_key)
  scsi_ctrl.set_element_sharedBus('noSharing')
  scsi_ctrl_spec.set_element_device(scsi_ctrl)
  return scsi_ctrl_spec


def add_disk(server, new_vm_config, datastore, size, provision='thick',
             disk_ctrl_key=0, unit_number=0, disk_number=0):
  """Add a VMDK disk to the configuration.

  Args:
      server: pysphere.VIServer, the server to talk to
      new_vm_config: CreateVM_TaskRequestMsg.config,
          Configuration to add this new disk to.
      datastore: str, datastore to add the disk to
      size: int, Bytes of the capacity of the new disk
      provision: str, how to provision the disk (thin/thick)
      disk_ctrl_key: int, which disk controller to attach to
      unit_number: int, unit number on the controller
      disk_number: int, disk number in the VM
  """

  disk_spec = new_vm_config.new_deviceChange()
  disk_spec.set_element_fileOperation('create')
  disk_spec.set_element_operation('add')

  disk_ctlr = VI.ns0.VirtualDisk_Def('disk_ctlr').pyclass()
  disk_backing = VI.ns0.VirtualDiskFlatVer2BackingInfo_Def(
    'disk_backing').pyclass()
  disk_backing.set_element_fileName(datastore)
  disk_backing.set_element_diskMode('persistent')
  if provision != 'thick':
    disk_backing.set_element_thinProvisioned(1)
  disk_ctlr.set_element_key(disk_number)
  disk_ctlr.set_element_controllerKey(disk_ctrl_key)
  disk_ctlr.set_element_unitNumber(unit_number)
  disk_ctlr.set_element_backing(disk_backing)
  disk_ctlr.set_element_capacityInKB(size / 1024)
  disk_spec.set_element_device(disk_ctlr)
  return disk_spec


def add_nic(server, new_vm_config, vlan, datacenter, nic_key=0):
  """Adds a VMXnet3 network card to the configuration."""
  nic_spec = new_vm_config.new_deviceChange()
  nic_spec.set_element_operation('add')

  nic_ctlr = VI.ns0.VirtualVmxnet3_Def('nic_ctrl').pyclass()
  nic_ctlr.set_element_addressType('generated')
  nic_ctlr.set_element_backing(create_nic_backing(server, vlan, datacenter))
  nic_ctlr.set_element_key(nic_key)
  nic_spec.set_element_device(nic_ctlr)
  return nic_spec


def _get_first_active_cluster(server, datacenter):
  """Given a server, return the first active cluster."""
  compute_resources = server._retrieve_properties_traversal(
      property_names=('name', 'host'), obj_type='ComputeResource',
      from_node=datacenter.hostFolder._obj)
  for compute_resource in compute_resources:
    compute_resource_props = pysphere.VIProperty(
        server, compute_resource.Obj)
    if compute_resource_props.summary.numEffectiveHosts > 0:
      break
  else:
    raise NoHostsInClusterError(
        'Tried to create VM, but no available clusters could be found')
  return compute_resource_props


def create_vm(server, vm_name, vlan, datastore_name=None,
              disk_size=16*1024*1024*1024, num_cpus=1, memory=1024*1024*1024,
              os=DEFAULT_OS, datacenter=None):

  if os not in SYSTEM_CONFIGURATION_MAP:
    raise OsNotSupportedError('OS %s not supported' % os)

  sysconf = SYSTEM_CONFIGURATION_MAP[os]

  datacenter_props = _get_datacenter_props(server, datacenter)
  compute_resource_props = _get_first_active_cluster(server, datacenter_props)

  if not compute_resource_props.host:
    raise NoHostsInClusterError(
        'Tried to create VM, but no ESXi servers exists in the cluster')
  resource_pool = compute_resource_props.resourcePool._obj

  target_config = _get_target_config(server, compute_resource_props)
  datastore, _ = find_datastore(target_config, datastore_name)

  # Create VM configuration
  hw_version = (
      VCENTER_HW_VERSION if 'vCenter' in server.get_server_type()
      else ESXI_HW_VERSION)

  vmfolder = datacenter_props.vmFolder._obj
  create_vm_request, new_vm_config = _build_create_vm_request(
      target_config, datastore, vm_name, memory, num_cpus, sysconf.osid,
      resource_pool, vmfolder, hw_version)

  devices = [
      add_scsi_controller(server, new_vm_config, sysconf.scsi),
      add_disk(server, new_vm_config, datastore, size=disk_size),
      add_nic(server, new_vm_config, vlan, datacenter_props)]

  new_vm_config.set_element_deviceChange(devices)

  # Create the VM
  taskmor = server._proxy.CreateVM_Task(create_vm_request)._returnval
  task = pysphere.VITask(taskmor, server)
  task.wait_for_state([task.STATE_SUCCESS, task.STATE_ERROR])
  if task.get_state() == task.STATE_ERROR:
    raise CreateVmError(task.get_error_message())
  # Return the new VM MOR
  return task.get_result()._obj


def _get_target_config(server, compute_resource_props):
  """Return the target configuration.

  Read more: http://pubs.vmware.com/vsphere-60/index.jsp?topic=%2Fcom.vmware.wssdk.apiref.doc%2Fvim.vm.ConfigTarget.html
  """
  env_browser = compute_resource_props.environmentBrowser._obj
  request = VI.QueryConfigTargetRequestMsg()
  _this = request.new__this(env_browser)
  _this.set_attribute_type(env_browser.get_attribute_type())
  request.set_element__this(_this)
  return server._proxy.QueryConfigTarget(request)._returnval


def _build_create_vm_request(target_config, datastore, vm_name, memory,
                             num_cpus, osid, resource_pool, vmfolder, version):
  """Creates a new VM create request and populates the config field.

  Return:
    (CreateVM_TaskRequestMsg, CreateVM_TaskRequestMsg.config)
  """
  create_vm_request = VI.CreateVM_TaskRequestMsg()
  config = create_vm_request.new_config()
  config.set_element_version(version)

  vmfiles = config.new_files()
  vmfiles.set_element_vmPathName(datastore)
  config.set_element_files(vmfiles)
  config.set_element_name(vm_name)
  config.set_element_memoryMB(memory / 1024 / 1024)
  config.set_element_numCPUs(num_cpus)
  config.set_element_guestId(osid)

  create_vm_request.set_element_config(config)

  # Reference to what vSphere refers to as a 'Folder' that will handle the call
  mor = create_vm_request.new__this(vmfolder)
  mor.set_attribute_type(vmfolder.get_attribute_type())
  create_vm_request.set_element__this(mor)

  # Set which resource pool to use
  mor = create_vm_request.new_pool(resource_pool)
  mor.set_attribute_type(resource_pool.get_attribute_type())
  create_vm_request.set_element_pool(mor)

  return create_vm_request, config


def add_esxi_to_vcenter(vcenter, esxi, cluster):
  """Add ESXi host to a given vCenter in the given cluster."""
  # Source:
  # https://groups.google.com/forum/#!msg/pysphere/zZZfryCe3zw/ZHx8m7m8cQ4J
  request = VI.AddHost_TaskRequestMsg()
  _this = request.new__this(cluster)
  _this.set_attribute_type(cluster.get_attribute_type())
  request.set_element__this(_this)

  request.AsConnected = True
  spec = request.new_spec()
  spec.Force = True
  spec.HostName = get_server_fqdn(esxi)
  spec.UserName = esxi._VIServer__user
  spec.Password = esxi._VIServer__password
  spec.SslThumbprint = get_ssl_thumbprint(esxi)

  request.set_element_spec(spec)
  task = vcenter._proxy.AddHost_Task(request)._returnval
  vi_task = pysphere.VITask(task, vcenter)
  status = vi_task.wait_for_state([vi_task.STATE_SUCCESS,
                                   vi_task.STATE_ERROR])
  if status == vi_task.STATE_ERROR:
    raise AddHostToVsphereError(vi_task.get_error_message())


def get_or_create_datacenter(vcenter, datacenter):
  """Retrieve (or create) a given datacenter."""
  for folder, name in vcenter.get_datacenters().iteritems():
    if  name == datacenter:
      return folder
  folders = vcenter._get_managed_objects(pysphere.vi_mor.MORTypes.Folder)
  dc_folder = next(
      folder for folder, name in folders.iteritems() if name == 'Datacenters')
  request = VI.CreateDatacenterRequestMsg()
  request.set_element__this(dc_folder)
  request.set_element_name(datacenter)
  return vcenter._proxy.CreateDatacenter(request)._returnval


def get_or_create_cluster(vcenter, datacenter, cluster):
  """Retrieve (or create) a given cluster."""
  for folder, name in vcenter.get_clusters().iteritems():
    if  name == cluster:
      return folder
  cluster_folder = pysphere.VIProperty(vcenter, datacenter).hostFolder._obj
  request = VI.CreateClusterRequestMsg()
  _this = request.new__this(cluster_folder)
  _this.set_attribute_type(cluster_folder.get_attribute_type())
  request.set_element__this(_this)
  request.set_element_name(cluster)
  spec = request.new_spec()
  drsConfig = spec.new_drsConfig()
  drsConfig.Enabled = True
  drsConfig.DefaultVmBehavior = 'fullyAutomated'
  spec.set_element_drsConfig(drsConfig)
  request.set_element_spec(spec)
  return vcenter._proxy.CreateCluster(request)._returnval


def create_dvswitch(vcenter, datacenter, name, uplinks=2):
  """Create a new DVS."""
  switch_folder = pysphere.VIProperty(vcenter, datacenter).networkFolder._obj
  request = VI.CreateDVS_TaskRequestMsg()
  _this = request.new__this(switch_folder)
  _this.set_attribute_type(switch_folder.get_attribute_type())
  request.set_element__this(_this)

  spec = request.new_spec()
  config = spec.new_configSpec()
  config.Name = name

  uplink_policy = (
      VI.ns0.DVSNameArrayUplinkPortPolicy_Def('uplink_policy').pyclass())
  uplink_policy.UplinkPortName = [
      'Uplink%d' % x for x in range(1, uplinks + 1)]
  config.set_element_uplinkPortPolicy(uplink_policy)

  spec.set_element_configSpec(config)
  request.set_element_spec(spec)
  task = vcenter._proxy.CreateDVS_Task(request)._returnval
  vi_task = pysphere.VITask(task, vcenter)
  status = vi_task.wait_for_state([vi_task.STATE_SUCCESS,
                                   vi_task.STATE_ERROR])
  if status == vi_task.STATE_ERROR:
    raise CreateDvSwitchError(vi_task.get_error_message())


def create_dvs_portgroup(server, datacenter, switch, name_vlan_map):
  """Create a new DVS."""
  netfolder = pysphere.VIProperty(server, datacenter).networkFolder._obj
  dvswitch_resources = server._retrieve_properties_traversal(
      property_names=['name'],
      from_node=netfolder, obj_type='DistributedVirtualSwitch')

  for dvswitch in dvswitch_resources:
    name = next(p.Val for p in dvswitch.PropSet if p.Name == 'name')
    if name == switch:
      break
  else:
    raise CreateDvSwitchError('Switch %s not found' % switch)

  request = VI.AddDVPortgroup_TaskRequestMsg()
  _this = request.new__this(dvswitch._obj)
  _this.set_attribute_type(dvswitch._obj.get_attribute_type())
  request.set_element__this(_this)

  specs = []
  for name, vlan_id in name_vlan_map.iteritems():
    if vlan_id is None:
      continue
    spec = request.new_spec()
    spec.Name = name
    spec.Type = 'earlyBinding'
    spec.NumPorts = 0
    vlan = VI.ns0.VmwareDistributedVirtualSwitchVlanIdSpec_Def('vlan').pyclass()
    vlan.Inherited = False
    vlan.VlanId = vlan_id
    port_config = VI.ns0.VMwareDVSPortSetting_Def('port_config').pyclass()
    port_config.Vlan = vlan
    spec.DefaultPortConfig = port_config
    specs.append(spec)

  request.set_element_spec(specs)
  task = server._proxy.AddDVPortgroup_Task(request)._returnval
  vi_task = pysphere.VITask(task, server)
  status = vi_task.wait_for_state([vi_task.STATE_SUCCESS,
                                   vi_task.STATE_ERROR])
  if status == vi_task.STATE_ERROR:
    raise CreateDvPortgroupError(vi_task.get_error_message())


def add_host_to_dvs(server, host, datacenter, switch, interface):
  """Add given interface on a host to a dvSwitch."""
  host_mor = next(m for m, h in server.get_hosts().iteritems() if h == host)

  netfolder = pysphere.VIProperty(server, datacenter).networkFolder._obj
  dvswitch_resources = server._retrieve_properties_traversal(
      property_names=['config', 'name'],
      from_node=netfolder, obj_type='DistributedVirtualSwitch')

  version = None
  for dvswitch in dvswitch_resources:
    name = next(p.Val for p in dvswitch.PropSet if p.Name == 'name')
    config = next(p.Val for p in dvswitch.PropSet if p.Name == 'config')
    if name == switch:
      version = config.get_element_configVersion()
      break
  else:
    raise CreateDvSwitchError('Switch %s not found' % switch)

  request = VI.ReconfigureDvs_TaskRequestMsg()
  _this = request.new__this(dvswitch._obj)
  _this.set_attribute_type(dvswitch._obj.get_attribute_type())
  request.set_element__this(_this)

  spec = VI.ns0.VMwareDVSConfigSpec_Def('spec').pyclass()
  spec.ConfigVersion = version
  host_spec = spec.new_host()

  backing = VI.ns0.DistributedVirtualSwitchHostMemberPnicBacking_Def(
    'backing').pyclass()
  pnic_spec = backing.new_pnicSpec()
  pnic_spec.PnicDevice = interface
  backing.set_element_pnicSpec([pnic_spec])

  host_spec.Backing = backing
  host_spec.Host = host_mor
  host_spec.Operation = 'add'

  spec.set_element_host([host_spec])
  request.set_element_spec(spec)
  task = server._proxy.ReconfigureDvs_Task(request)._returnval
  vi_task = pysphere.VITask(task, server)
  status = vi_task.wait_for_state([vi_task.STATE_SUCCESS,
                                   vi_task.STATE_ERROR])
  if status == vi_task.STATE_ERROR:
    raise CreateDvSwitchError(vi_task.get_error_message())


def get_vm_by_name(server, name):
  """Get VM by name."""
  for path in server.get_registered_vms():
    vm = server.get_vm_by_path(path)
    if vm.properties.name == name:
      return vm._mor
  return None


def get_vm_by_path(server, path):
  """Get VM by path."""
  return server.get_vm_by_path(path)._mor


def power_on(server, vm):
  """Send power on task to given VM."""
  pysphere.vi_virtual_machine.VIVirtualMachine(server, vm).power_on()


def generate_vcenter_install_config(server, host, vlan, ip, prefix, gateway,
                                    password, datastore, domain, datacenter):
  datacenter_props = _get_datacenter_props(server, datacenter)
  compute_resource_props = _get_first_active_cluster(server, datacenter_props)
  if not compute_resource_props.host:
    raise NoHostsInClusterError(
        'Tried to create VM, but no ESXi servers exists in the cluster')
  target_config = _get_target_config(server, compute_resource_props)

  # Resolve datastore, if not specified pick the largest
  real_datastore, _ = find_datastore(target_config, datastore, brackets=False)

  network = _vlan_to_network(server, vlan, datacenter_props)
  install_data = {
      '__version': '2.3.0',
      '__comments': host,
      'new.vcsa': {
        'esxi': {
          'hostname': get_server_ip(server),
          'username': server._VIServer__user,
          'password': server._VIServer__password,
          'deployment.network': network,
          'datastore': real_datastore
        },
        'appliance': {
          'thin.disk.mode': True,
          'deployment.option': 'tiny',
          'name': host
        },
        'network': {
          'ip.family': 'ipv4',
          'mode': 'static',
          'ip': ip,
          'dns.servers': [
            '8.8.8.8',
            '8.8.4.4'
            ],
          'prefix': prefix,
          'gateway': gateway,
          'system.name': host
        },
        'os': {
          'password': password,
          'ssh.enable': True
        },
        'sso': {
          'password': password,
          'domain-name': domain,
          'site-name': 'Dreamhack-vSphere'
        }
      },
      'ceip': {
        'settings': {
          'ceip.enabled': False
        }
      }
    }
  return json.dumps(install_data, indent=2)


if __name__ == '__main__':
  esxi_password = raw_input('ESXi password: ')
  vcenter_password = raw_input('vCenter password: ')
  esxi_config = {
      'host': '172.16.0.79',
      'username': 'root',
      'password': esxi_password,
  }
  vcenter_config = {
      'host': 'vc.event.dreamhack.se',
      'username': 'administrator@event.dreamhack.se',
      'password': vcenter_password
  }
  host_config = {
      'vlan': 921,
      'final-vlan': 922,
      'datastore': None
  }

  print 'Connecting'
  # SSL verification hack until pySphere get their stuff together
  # https://www.python.org/dev/peps/pep-0476/#opting-out
  import ssl

  try:
    _create_unverified_https_context = ssl._create_unverified_context
  except AttributeError:
    # Legacy Python that doesn't verify HTTPS certificates by default
    pass
  else:
    # Handle target environment that doesn't support HTTPS verification
    ssl._create_default_https_context = _create_unverified_https_context
  # End SSL hack

  test_esxi = False
  test_vcenter = True
  test_setup = False

  if test_esxi:
    esxi = pysphere.VIServer()
    esxi.connect(esxi_config['host'], esxi_config['username'],
                 esxi_config['password'])
    # Just test that this works
    generate_vcenter_install_config(
        esxi, vcenter_config['host'], 923, '1.2.3.4', '27', '1.2.3.1',
        'testar', 'datastore1', 'event.dreamhack.se')

  if test_vcenter:
    vcenter = pysphere.VIServer()
    vcenter.connect(vcenter_config['host'], vcenter_config['username'],
                    vcenter_config['password'])
    datacenter = get_or_create_datacenter(vcenter, 'event')
    cluster = get_or_create_cluster(vcenter, datacenter, 'POP')

  if test_setup:
    add_esxi_to_vcenter(vcenter, esxi, cluster)
    create_dvswitch(vcenter, datacenter, 'DVS-POP')
    create_dvs_portgroup(vcenter, datacenter, 'DVS-POP', {
        '123: Test': 123})
    add_host_to_dvs(
        vcenter, esxi_config['host'], datacenter, 'DVS-POP', 'vmnic1')

  if test_vcenter:
    vm = create_vm(
        vcenter, 'test-vcenter', host_config['vlan'], host_config['datastore'],
        disk_size=1024*1024*1024, num_cpus=1, memory=128*1024*1024, os='debian')
    assert vm == get_vm_by_name(vcenter, 'test-vcenter')
    provision_vm(vcenter, vm, host_config['final-vlan'])
    power_on(vcenter, vm)

  if test_esxi:
    vm = create_vm(
        esxi, 'test-esxi', host_config['vlan'], host_config['datastore'],
        disk_size=1024*1024*1024, num_cpus=1, memory=128*1024*1024, os='debian')
    assert vm == get_vm_by_name(vcenter, 'test-esxi')
    provision_vm(esxi, vm, host_config['final-vlan'])
    power_on(esxi, vm)
