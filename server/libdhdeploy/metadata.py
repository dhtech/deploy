import collections
import json
import redis
import sqlite3
import yaml


Client = collections.namedtuple('Client', ('hostname', 'ip', 'virtual',
                                           'managed', 'os', 'os_human',
                                           'interface', 'domain'))
Network = collections.namedtuple('Network', (
  'bonded', 'interface', 'vlan_interface',
  'v4_address', 'v4_netmask', 'v4_gateway',
  'v6_address', 'v6_netmask', 'v6_gateway', 'vlan',
  'dns_domain', 'shortname'))


def connection():
  config = yaml.safe_load(file('/etc/deploy/deploy.yaml'))
  return redis.StrictRedis(**config['redis'])


def _get_os(hostname):
  conn = sqlite3.connect('/etc/ipplan/ipplan/ipplan.db')
  c = conn.cursor()
  c.execute(
      'SELECT option.value FROM host '
      'LEFT JOIN option ON option.node_id = host.node_id '
      'WHERE option.name = "os" AND host.name = ?', (hostname,))
  res = c.fetchone()
  return res[0] if res else None


def lookup_ip(ip):
  conn = sqlite3.connect('/etc/ipplan/ipplan.db')
  c = conn.cursor()
  c.execute('SELECT name FROM host WHERE ipv4_addr_txt = ?', (ip,))
  res = c.fetchone()
  return res[0] if res else None


def find(ip, first_if=None):
  if first_if is None:
    first_if = 'eth0'
  hostname = None
  virtual = False
  managed = False
  os = None
  interface = None

  hostname = lookup_ip(ip)

  r = connection()
  raw = r.get('host-' + hostname)
  if not raw:
    return None, None

  metadata = json.loads(raw)

  if 'vmware' in metadata['manufacturer'].lower():
    virtual = True
  if metadata['manufacturer'] == 'QEMU':
    virtual = True

  os = _get_os(hostname)
  if os == 'debian':
    os_human = 'Debian'
  elif os == 'ubuntu':
    os_human = 'Ubuntu'
  elif os == 'openbsd':
    os_human = 'OpenBSD'
  elif os == 'coreos':
    os_human = 'CoreOS'
  elif os == 'tectonic':
    os_human = 'Tectonic'
  elif os == 'esxi':
    if metadata['manufacturer'] == 'QEMU':
      # QEMU needs to have an ISO to boot
      # TODO(bluecmd): Remove this if we get QEMU to netboot ESXi.
      os = 'cdrom'
      os_human = 'ESXi via CDROM'
    else:
      os_human = 'ESXi'
  else:
    os_human = 'Unsupported OS: %s' % os

  if not interface:
    interface = 'bond0' if not virtual else first_if

  my_net, _ = get_vlan(hostname)
  my_domain, _ = my_net.split('@', 1)
  managed = True
  client = Client(hostname=hostname, ip=ip, virtual=virtual, managed=managed,
                  os=os, os_human=os_human, interface=interface,
                  domain=my_domain)
  return client, metadata


def network(client, cm):
  interface = client.interface
  bonded = interface.startswith('bond')
  conn = sqlite3.connect('/etc/ipplan/ipplan.db')
  c = conn.cursor()
  c.execute('SELECT h.ipv4_addr_txt, ipv4_netmask_txt, ipv4_gateway_txt, '
            'h.ipv6_addr_txt, ipv6_netmask_txt, ipv6_gateway_txt, vlan '
            'FROM host h, network n WHERE h.network_id = n.node_id '
            'AND h.name = ?', (client.hostname, ))
  res = c.fetchone()
  if not res:
    return None

  vlan_interface = interface if client.virtual else '%s.%s' % (
      interface, res[6])
  shortname, dns_domain = client.hostname.split('.', 1)

  return Network(bonded, interface, vlan_interface, *res,
          shortname=shortname, dns_domain=dns_domain)


def installation_network(hostname):
  # Simplified getter for installation network
  conn = sqlite3.connect('/etc/ipplan/ipplan.db')
  c = conn.cursor()
  c.execute('SELECT h.ipv4_addr_txt, ipv4_netmask_txt, ipv4_gateway_txt, '
            'vlan FROM host h, network n WHERE h.network_id = n.node_id '
            'AND h.name = ?', (hostname, ))
  res = c.fetchone()
  if not res:
    return None

  shortname, dns_domain = hostname.split('.', 1)
  return {'v4_address': res[0], 'v4_netmask': res[1], 'v4_gateway': res[2],
          'vlan': res[3], 'dns_domain': dns_domain, 'shortname': shortname}


def update(client, cm):
  r = connection()
  r.setex('host-' + client.hostname, 3600, json.dumps(cm))


def get_deploy(hostname):
  conn = sqlite3.connect('/etc/ipplan/ipplan.db')
  c = conn.cursor()
  c.execute(
      'SELECT option.value FROM host '
      'LEFT JOIN option ON option.node_id = host.node_id '
      'WHERE option.name = "deploy" AND host.name = ?', (hostname,))
  res = c.fetchone()
  return res[0].split(',') if res else []


def get_vlan(hostname):
  conn = sqlite3.connect('/etc/ipplan/ipplan.db')
  c = conn.cursor()
  c.execute('SELECT n.name, vlan '
            'FROM host h, network n WHERE h.network_id = n.node_id '
            'AND h.name = ?', (hostname, ))
  res = c.fetchone()
  return res if res else (None, None)


def all_vlans_in_same_domain(hostname):
  my_net, _ = get_vlan(hostname)
  my_domain, _ = my_net.split('@', 1)

  conn = sqlite3.connect('/etc/ipplan/ipplan.db')
  c = conn.cursor()
  c.execute('SELECT name, vlan FROM network ORDER BY vlan')
  for network, vlan in c:
    if vlan == 0:
      continue
    if network.startswith(my_domain + '@'):
      yield network, vlan

def get_current_event():
  conn = sqlite3.connect('/etc/ipplan/ipplan.db')
  c = conn.cursor()
  c.execute(
    'SELECT value FROM meta_data WHERE name = "current_event"')
  res = c.fetchone()
  conn.close()
  return res[0]
