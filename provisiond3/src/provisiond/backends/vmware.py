"""VMware vSphere backend (pyvmomi), parity port of the pysphere esxi.py.

TLS verification is always explicit: an ``ssl.SSLContext`` built from the
manager's ``verify_tls`` setting (path to a CA bundle, ``True`` for system
CAs, ``False`` for the lab — logged loudly by the config loader).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import ssl
import time
from typing import TYPE_CHECKING, Any

from provisiond.backends.base import Capability, Provisioner, ProvisionerError, VmInfo
from provisiond.orders import CreateOrder

if TYPE_CHECKING:
    from provisiond.config import ManagerConfig
    from provisiond.secrets import Secrets

log = logging.getLogger(__name__)

# WARNING(2014-10-18, gen-2): vSphere client compatibility caps this.
HW_VERSION = "vmx-13"

# Map OS -> (guestId, scsi controller type); parity with gen-2.
SYSTEM_CONFIGURATION_MAP = {
    "debian": ("debian10_64Guest", "paravirtual"),
    "ubuntu": ("ubuntu64Guest", "paravirtual"),
    "openbsd": ("otherGuest64", "lsi_sas"),
    "coreos": ("otherGuest64", "paravirtual"),
}

TASK_TIMEOUT = 600

# First-contact defaults for a freshly deployed vCenter (parity with gen-2).
VCENTER_DEFAULTS = {"datacenter": "event", "cluster": "POP", "dvs": "DVS-POP"}


def swap_uuid(uuid: str) -> str:
    """Normalize a vmx-12+ SMBIOS UUID (endian fix, parity with gen-2).

    The first three groups are byte-reversed in steps of two:
    AABBCCDD -> DDCCBBAA.
    """
    groups = uuid.lower().split("-")
    swapped = [
        "".join([g[i : i + 2] for i in range(0, len(g), 2)][::-1]) for g in groups[0:3]
    ]
    return "-".join(swapped + groups[3:])


def make_ssl_context(verify_tls: bool | str) -> ssl.SSLContext:
    if verify_tls is False:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if isinstance(verify_tls, str):
        return ssl.create_default_context(cafile=verify_tls)
    return ssl.create_default_context()


class VmwareBackend(Provisioner):
    capabilities = (
        Capability.CREATE
        | Capability.PROVISION
        | Capability.DELETE
        | Capability.VCENTER_DEPLOY
    )

    def __init__(
        self, config: ManagerConfig, secrets: Secrets, si: Any | None = None
    ) -> None:
        self.name = config.name
        self._config = config
        self._secrets = secrets
        self._si = si  # injected in tests; lazily connected otherwise
        self._username: str | None = None
        self._password: str | None = None

    # -- connection ------------------------------------------------------

    @property
    def si(self) -> Any:
        if self._si is None:
            from pyVim.connect import SmartConnect

            assert self._config.host
            self._username = self._secrets.resolve(self._config.username, field="username")
            self._password = self._secrets.resolve(self._config.password, field="password")
            self._si = SmartConnect(
                host=self._config.host,
                user=self._username,
                pwd=self._password,
                sslContext=make_ssl_context(self._config.verify_tls),
            )
        return self._si

    def close(self) -> None:
        if self._si is not None:
            from pyVim.connect import Disconnect

            Disconnect(self._si)
            self._si = None

    def _content(self) -> Any:
        return self.si.RetrieveContent()

    def _wait_task(self, task: Any) -> Any:
        from pyVmomi import vim

        deadline = time.monotonic() + TASK_TIMEOUT
        while time.monotonic() < deadline:
            state = task.info.state
            if state == vim.TaskInfo.State.success:
                return task.info.result
            if state == vim.TaskInfo.State.error:
                raise ProvisionerError(str(task.info.error.msg))
            time.sleep(1)
        raise ProvisionerError("vSphere task timed out")

    def _view(self, vimtype: Any, root: Any | None = None) -> list[Any]:
        content = self._content()
        container = content.viewManager.CreateContainerView(
            root or content.rootFolder, [vimtype], True
        )
        try:
            return list(container.view)
        finally:
            container.Destroy()

    # -- lookups ---------------------------------------------------------

    def _get_datacenter(self, name: str | None) -> Any:
        from pyVmomi import vim

        dcs = self._view(vim.Datacenter)
        if not dcs:
            raise ProvisionerError("no datacenters")
        if name is None:
            return dcs[0]
        for dc in dcs:
            if dc.name == name:
                return dc
        raise ProvisionerError(f'found no datacenter named "{name}"')

    def _get_active_cluster(self, datacenter: Any) -> Any:
        from pyVmomi import vim

        for cr in self._view(vim.ComputeResource, root=datacenter.hostFolder):
            if cr.summary.numEffectiveHosts > 0:
                return cr
        raise ProvisionerError("no available clusters with active hosts")

    @staticmethod
    def find_datastore(cluster: Any, datastore: str | None) -> str:
        """Pick a datastore name: explicit, else the one with most free space."""
        stores = {
            ds.name: ds.summary.freeSpace for ds in cluster.datastore if ds.summary.accessible
        }
        if not stores:
            raise ProvisionerError("no accessible datastores")
        if datastore:
            if datastore not in stores:
                raise ProvisionerError(f"datastore {datastore} does not appear to exist")
            return datastore
        return str(max(stores.items(), key=lambda kv: int(kv[1]))[0])

    def _vlan_to_network(self, vlan: int, datacenter: Any) -> Any:
        """Resolve a VLAN to a standard-switch network on the first host."""
        hosts = self._view_hosts(datacenter)
        if not hosts:
            raise ProvisionerError(f"datacenter {datacenter.name} has no hosts")
        vlan_map = {
            pg.spec.vlanId: pg.spec.name
            for pg in hosts[0].configManager.networkSystem.networkInfo.portgroup
            if pg.spec.name != "Management Network"
        }
        if vlan not in vlan_map:
            raise ProvisionerError(f"VLAN {vlan} not found in any networks")
        name = vlan_map[vlan]
        for net in datacenter.network:
            if net.name == name:
                return net
        raise ProvisionerError(f"network {name} not found in datacenter")

    def _view_hosts(self, datacenter: Any) -> list[Any]:
        from pyVmomi import vim

        return self._view(vim.HostSystem, root=datacenter.hostFolder)

    def _find_dvs_portgroup(self, vlan: int, datacenter: Any) -> Any | None:
        """Find a DVS portgroup carrying the VLAN (or first trunk for 4095)."""
        from pyVmomi import vim

        for pg in self._view(vim.dvs.DistributedVirtualPortgroup, root=datacenter.networkFolder):
            if "dvuplinks" in pg.config.name.lower():
                continue
            vlan_spec = pg.config.defaultPortConfig.vlan
            vlan_id = getattr(vlan_spec, "vlanId", None)
            if (not isinstance(vlan_id, int) and vlan == 4095) or vlan_id == vlan:
                dvs = pg.config.distributedVirtualSwitch
                if dvs.summary.numPorts > 0:
                    return pg
        return None

    def _nic_backing(self, vlan: int, datacenter: Any) -> Any:
        from pyVmomi import vim

        pg = self._find_dvs_portgroup(vlan, datacenter)
        if pg is not None:
            port = vim.dvs.PortConnection(
                switchUuid=pg.config.distributedVirtualSwitch.uuid, portgroupKey=pg.key
            )
            return vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo(
                port=port
            )
        network = self._vlan_to_network(vlan, datacenter)
        return vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(
            deviceName=network.name, network=network
        )

    # -- spec building ---------------------------------------------------

    def build_config_spec(
        self, order: CreateOrder, datastore: str, nic_backing: Any
    ) -> Any:
        from pyVmomi import vim

        if order.os not in SYSTEM_CONFIGURATION_MAP:
            raise ProvisionerError(f"OS {order.os} not supported")
        guest_id, scsi_type = SYSTEM_CONFIGURATION_MAP[order.os]

        if scsi_type == "paravirtual":
            scsi_ctrl: Any = vim.vm.device.ParaVirtualSCSIController()
        else:
            scsi_ctrl = vim.vm.device.VirtualLsiLogicSASController()
        scsi_ctrl.busNumber = 0
        scsi_ctrl.key = 0
        scsi_ctrl.sharedBus = vim.vm.device.VirtualSCSIController.Sharing.noSharing
        scsi_spec = vim.vm.device.VirtualDeviceSpec(operation="add", device=scsi_ctrl)

        disk = vim.vm.device.VirtualDisk(
            key=0,
            controllerKey=0,
            unitNumber=0,
            capacityInKB=order.disk // 1024,
            backing=vim.vm.device.VirtualDisk.FlatVer2BackingInfo(
                fileName=f"[{datastore}]", diskMode="persistent"
            ),
        )
        disk_spec = vim.vm.device.VirtualDeviceSpec(
            operation="add", fileOperation="create", device=disk
        )

        nic = vim.vm.device.VirtualVmxnet3(
            key=0, addressType="generated", backing=nic_backing
        )
        nic_spec = vim.vm.device.VirtualDeviceSpec(operation="add", device=nic)

        device_change = [scsi_spec, disk_spec, nic_spec]
        if order.appdisk:
            appdisk = vim.vm.device.VirtualDisk(
                key=1,
                controllerKey=0,
                unitNumber=1,
                capacityInKB=int(order.appdisk["size"]) // 1024,
                backing=vim.vm.device.VirtualDisk.FlatVer2BackingInfo(
                    fileName=f"[{datastore}]", diskMode="persistent"
                ),
            )
            device_change.append(
                vim.vm.device.VirtualDeviceSpec(
                    operation="add", fileOperation="create", device=appdisk
                )
            )

        return vim.vm.ConfigSpec(
            name=order.name,
            version=HW_VERSION,
            guestId=guest_id,
            firmware="efi",
            numCPUs=order.cpus,
            memoryMB=order.memory // 1024 // 1024,
            files=vim.vm.FileInfo(vmPathName=f"[{datastore}]"),
            deviceChange=device_change,
        )

    # -- Provisioner -----------------------------------------------------

    def list_vms(self) -> list[VmInfo]:
        from pyVmomi import vim

        vms = []
        for vm in self._view(vim.VirtualMachine):
            uuid = (vm.config.uuid or "") if vm.config else ""
            if not uuid:
                continue
            vms.append(
                VmInfo(name=vm.name, uuid=swap_uuid(uuid), backend_ref=vm._moId)
            )
        return vms

    def create_vm(self, order: CreateOrder, deploy_vlan: int) -> VmInfo:
        datacenter = self._get_datacenter(order.datacenter)
        cluster = self._get_active_cluster(datacenter)
        if not cluster.host:
            raise ProvisionerError("no ESXi servers exist in the cluster")
        datastore = self.find_datastore(cluster, order.datastore)
        nic_backing = self._nic_backing(deploy_vlan, datacenter)
        spec = self.build_config_spec(order, datastore, nic_backing)

        task = datacenter.vmFolder.CreateVM_Task(
            config=spec, pool=cluster.resourcePool
        )
        vm = self._wait_task(task)
        self._wait_task(vm.PowerOnVM_Task())
        return VmInfo(
            name=order.name, uuid=swap_uuid(vm.config.uuid), backend_ref=vm._moId
        )

    def _vm_by_ref(self, vm: VmInfo) -> Any:
        from pyVmomi import vim

        for candidate in self._view(vim.VirtualMachine):
            if candidate._moId == vm.backend_ref:
                return candidate
        raise ProvisionerError(f"VM {vm.name} ({vm.backend_ref}) not found")

    def provision_vm(self, vm: VmInfo, vlan: int, datacenter: str | None = None) -> None:
        from pyVmomi import vim

        vsphere_vm = self._vm_by_ref(vm)
        # Only vCenter has the proper notion of a datacenter; a bare ESXi
        # host calls its implicit one ha-datacenter (parity with gen-2).
        dc_name = datacenter if self._is_vcenter() else "ha-datacenter"
        dc = self._get_datacenter(dc_name)

        nic = next(
            (
                dev
                for dev in vsphere_vm.config.hardware.device
                if isinstance(
                    dev, (vim.vm.device.VirtualE1000, vim.vm.device.VirtualVmxnet3)
                )
            ),
            None,
        )
        if nic is None:
            raise ProvisionerError("no NIC found")
        nic.backing = self._nic_backing(vlan, dc)
        change = vim.vm.device.VirtualDeviceSpec(operation="edit", device=nic)
        self._wait_task(
            vsphere_vm.ReconfigVM_Task(spec=vim.vm.ConfigSpec(deviceChange=[change]))
        )

    def delete_vm(self, vm: VmInfo) -> None:
        from pyVmomi import vim

        vsphere_vm = self._vm_by_ref(vm)
        if vsphere_vm.runtime.powerState == vim.VirtualMachine.PowerState.poweredOn:
            self._wait_task(vsphere_vm.PowerOffVM_Task())
        self._wait_task(vsphere_vm.Destroy_Task())

    # -- vCenter management (configure-vcenter-* flow) -------------------

    def _is_vcenter(self) -> bool:
        return bool(self._content().about.apiType == "VirtualCenter")

    def get_server_fqdn(self) -> str:
        dns = self._view_hosts(self._get_datacenter(None))[0].config.network.dnsConfig
        return f"{dns.hostName}.{dns.domainName}"

    def get_server_ip(self) -> str:
        vnics = self._view_hosts(self._get_datacenter(None))[0].config.network.vnic
        # Gen-2 edge case: skip link-local mgmt vnic
        if vnics[0].spec.ip.ipAddress.startswith("169"):
            return str(vnics[1].spec.ip.ipAddress)
        return str(vnics[0].spec.ip.ipAddress)

    def get_ssl_thumbprint(self) -> str:
        host = self._view_hosts(self._get_datacenter(None))[0]
        cert = bytes(host.config.certificate)
        body = b"".join(
            line for line in cert.splitlines() if not line.startswith(b"--")
        )
        digest = hashlib.sha1(base64.b64decode(body)).hexdigest()
        return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)).upper()

    def get_or_create_datacenter(self, name: str) -> Any:
        from pyVmomi import vim

        for dc in self._view(vim.Datacenter):
            if dc.name == name:
                return dc
        return self._content().rootFolder.CreateDatacenter(name=name)

    def get_or_create_cluster(self, datacenter: Any, name: str) -> Any:
        from pyVmomi import vim

        for cr in self._view(vim.ClusterComputeResource, root=datacenter.hostFolder):
            if cr.name == name:
                return cr
        spec = vim.cluster.ConfigSpecEx(
            drsConfig=vim.cluster.DrsConfigInfo(
                enabled=True, defaultVmBehavior="fullyAutomated"
            )
        )
        return datacenter.hostFolder.CreateClusterEx(name=name, spec=spec)

    def create_dvswitch(self, datacenter: Any, name: str, uplinks: int = 2) -> None:
        from pyVmomi import vim

        spec = vim.DistributedVirtualSwitch.CreateSpec(
            configSpec=vim.dvs.VmwareDistributedVirtualSwitch.ConfigSpec(
                name=name,
                uplinkPortPolicy=vim.DistributedVirtualSwitch.NameArrayUplinkPortPolicy(
                    uplinkPortName=[f"Uplink{i}" for i in range(1, uplinks + 1)]
                ),
            )
        )
        self._wait_task(datacenter.networkFolder.CreateDVS_Task(spec=spec))

    def _get_dvswitch(self, datacenter: Any, name: str) -> Any:
        from pyVmomi import vim

        for dvs in self._view(
            vim.dvs.VmwareDistributedVirtualSwitch, root=datacenter.networkFolder
        ):
            if dvs.name == name:
                return dvs
        raise ProvisionerError(f"switch {name} not found")

    def create_dvs_portgroup(
        self, datacenter: Any, switch: str, name_vlan_map: dict[str, int | None]
    ) -> None:
        from pyVmomi import vim

        dvs = self._get_dvswitch(datacenter, switch)
        specs = []
        for name, vlan_id in name_vlan_map.items():
            if vlan_id is None:
                continue
            specs.append(
                vim.dvs.DistributedVirtualPortgroup.ConfigSpec(
                    name=name,
                    type="earlyBinding",
                    numPorts=0,
                    defaultPortConfig=vim.dvs.VmwareDistributedVirtualSwitch.VmwarePortConfigPolicy(
                        vlan=vim.dvs.VmwareDistributedVirtualSwitch.VlanIdSpec(
                            inherited=False, vlanId=vlan_id
                        )
                    ),
                )
            )
        self._wait_task(dvs.AddDVPortgroup_Task(spec=specs))

    def add_host_to_dvs(
        self, host_fqdn: str, datacenter: Any, switch: str, interface: str
    ) -> None:
        from pyVmomi import vim

        host = next(
            (h for h in self._view_hosts(datacenter) if h.name == host_fqdn), None
        )
        if host is None:
            raise ProvisionerError(f"host {host_fqdn} not found")
        dvs = self._get_dvswitch(datacenter, switch)
        spec = vim.dvs.VmwareDistributedVirtualSwitch.ConfigSpec(
            configVersion=dvs.config.configVersion,
            host=[
                vim.dvs.HostMember.ConfigSpec(
                    operation="add",
                    host=host,
                    backing=vim.dvs.HostMember.PnicBacking(
                        pnicSpec=[vim.dvs.HostMember.PnicSpec(pnicDevice=interface)]
                    ),
                )
            ],
        )
        self._wait_task(dvs.ReconfigureDvs_Task(spec=spec))

    def ensure_setup(self) -> None:
        """Set up a freshly deployed vCenter on first contact (gen-2 parity)."""
        from pyVmomi import vim

        if not self._is_vcenter():
            return
        if self._view(vim.Datacenter):
            return
        from provisiond.backends import vcsa

        log.info("[%s] discovered new vCenter, setting up", self.name)
        vcsa.setup_vcenter(self, self._config.fqdn or "", VCENTER_DEFAULTS)

    def deploy_vcenter(self, order: CreateOrder) -> None:
        from provisiond.backends import vcsa

        # Force connection so username/password are resolved for the config.
        _ = self.si
        vcsa.vcenter_deploy(self, self._secrets, order)

    def configure_vcenter(self, request: dict[str, Any]) -> None:
        """Handle add-esxi-server / add-host-to-dvs requests (gen-2 parity)."""
        from dataclasses import replace

        from provisiond import ipplan

        name = str(request["name"])
        login = self._secrets.read("login:" + name)
        target_host = ipplan.host_to_ip(name)
        if target_host is None:
            raise ProvisionerError(f"host {name} not found in ipplan")
        target = VmwareBackend(
            replace(
                self._config,
                host=target_host,
                username=login["username"],
                password=login["password"],
            ),
            self._secrets,
        )
        try:
            datacenter = self.get_or_create_datacenter(VCENTER_DEFAULTS["datacenter"])
            operation = request.get("operation")
            if operation == "add-esxi-server":
                cluster = self.get_or_create_cluster(
                    datacenter, VCENTER_DEFAULTS["cluster"]
                )
                self.add_esxi_to_vcenter(target, cluster)
                log.info("[%s] added ESXi %s to vCenter", self.name, name)
            elif operation == "add-host-to-dvs":
                self.add_host_to_dvs(
                    target.get_server_fqdn(),
                    datacenter,
                    VCENTER_DEFAULTS["dvs"],
                    str(request["interface"]),
                )
                log.info("[%s] added host %s to DVS", self.name, name)
            else:
                log.error("[%s] unknown configure operation %r", self.name, operation)
        finally:
            target.close()

    def add_esxi_to_vcenter(self, esxi: VmwareBackend, cluster: Any) -> None:
        spec_kwargs = {
            "force": True,
            "hostName": esxi.get_server_fqdn(),
            "userName": esxi._username,
            "password": esxi._password,
            "sslThumbprint": esxi.get_ssl_thumbprint(),
        }
        from pyVmomi import vim

        spec = vim.host.ConnectSpec(**spec_kwargs)
        self._wait_task(cluster.AddHost_Task(spec=spec, asConnected=True))
