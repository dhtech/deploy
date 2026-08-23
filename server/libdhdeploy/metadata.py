import collections
import json
import sqlite3

import redis
import yaml

# Standardized in gen-3: one path for the ipplan database (was three
# different paths across the codebase) and one config file.
DB_FILE = '/etc/ipplan.db'
CONFIG_FILE = '/etc/deploy.yaml'


Client = collections.namedtuple('Client', ('hostname', 'ip', 'virtual',
                                           'managed', 'os', 'os_human',
                                           'interface', 'domain'))
Network = collections.namedtuple('Network', (
  'bonded', 'interface', 'vlan_interface',
  'v4_address', 'v4_netmask', 'v4_gateway',
  'v6_address', 'v6_netmask', 'v6_gateway', 'vlan',
  'dns_domain', 'shortname'))

OS_HUMAN = {
  'debian': 'Debian',
  'ubuntu': 'Ubuntu',
  'openbsd': 'OpenBSD',
  'coreos': 'CoreOS',
}


def config():
  with open(CONFIG_FILE) as f:
    return yaml.safe_load(f)


def connection():
  return redis.Redis(**config()['redis'])


def base_url():
  """Base URL clients use to reach this deploy server."""
  return config().get('base_url', 'https://deploy.tech.dreamhack.se')


def _get_os(hostname):
  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute(
      'SELECT option.value FROM host '
      'LEFT JOIN option ON option.node_id = host.node_id '
      'WHERE option.name = "os" AND host.name = ?', (hostname,))
  res = c.fetchone()
  conn.close()
  return res[0] if res else None


def lookup_ip(ip):
  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute('SELECT name FROM host WHERE ipv4_addr_txt = ?', (ip,))
  res = c.fetchone()
  conn.close()
  return res[0] if res else None


def find(ip, first_if=None):
  if first_if is None:
    first_if = 'eth0'

  hostname = lookup_ip(ip)
  if not hostname:
    return None, None

  r = connection()
  raw = r.get('host-' + hostname)
  if not raw:
    return None, None

  metadata = json.loads(raw)

  manufacturer = metadata.get('manufacturer', '').lower()
  # Gen-3: QEMU/Proxmox VMs are first-class virtual machines.
  virtual = 'vmware' in manufacturer or 'qemu' in manufacturer

  os = _get_os(hostname)
  os_human = OS_HUMAN.get(os, 'Unsupported OS: %s' % os)

  interface = 'bond0' if not virtual else first_if

  my_net, _ = get_vlan(hostname)
  my_domain, _ = my_net.split('@', 1)
  client = Client(hostname=hostname, ip=ip, virtual=virtual, managed=True,
                  os=os, os_human=os_human, interface=interface,
                  domain=my_domain)
  return client, metadata


def network(client, cm):
  interface = client.interface
  bonded = interface.startswith('bond')
  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute('SELECT h.ipv4_addr_txt, ipv4_netmask_txt, ipv4_gateway_txt, '
            'h.ipv6_addr_txt, ipv6_netmask_txt, ipv6_gateway_txt, vlan '
            'FROM host h, network n WHERE h.network_id = n.node_id '
            'AND h.name = ?', (client.hostname, ))
  res = c.fetchone()
  conn.close()
  if not res:
    return None

  vlan_interface = interface if client.virtual else '%s.%s' % (
      interface, res[6])
  shortname, dns_domain = client.hostname.split('.', 1)

  return Network(bonded, interface, vlan_interface, *res,
                 shortname=shortname, dns_domain=dns_domain)


def installation_network(hostname):
  # Simplified getter for installation network
  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute('SELECT h.ipv4_addr_txt, ipv4_netmask_txt, ipv4_gateway_txt, '
            'vlan FROM host h, network n WHERE h.network_id = n.node_id '
            'AND h.name = ?', (hostname, ))
  res = c.fetchone()
  conn.close()
  if not res:
    return None

  shortname, dns_domain = hostname.split('.', 1)
  return {'v4_address': res[0], 'v4_netmask': res[1], 'v4_gateway': res[2],
          'vlan': res[3], 'dns_domain': dns_domain, 'shortname': shortname}


def update(client, cm):
  r = connection()
  r.setex('host-' + client.hostname, 3600, json.dumps(cm))


def get_vlan(hostname):
  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute('SELECT n.name, vlan '
            'FROM host h, network n WHERE h.network_id = n.node_id '
            'AND h.name = ?', (hostname, ))
  res = c.fetchone()
  conn.close()
  return res if res else (None, None)


def all_vlans_in_same_domain(hostname):
  my_net, _ = get_vlan(hostname)
  my_domain, _ = my_net.split('@', 1)

  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute('SELECT name, vlan FROM network ORDER BY vlan')
  for network, vlan in c:
    if vlan == 0:
      continue
    if network.startswith(my_domain + '@'):
      yield network, vlan
  conn.close()


def getpkgs(hostname):
  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute(
      'SELECT option.value FROM host, option '
      'WHERE host.node_id = option.node_id '
      'AND option.name = "pkg" AND host.name = ?', (hostname,))
  pkgs = c.fetchall()
  conn.close()
  return [p[0].split('(', 1)[0] for p in pkgs if not p[0].startswith('-')]


def get_appdisk(hostname, manifest_file='/etc/manifest'):
  """Application-disk spec for a host: union of its packages' appdisk
  entries (largest size wins; mountpoint conflicts are an error)."""
  with open(manifest_file) as f:
    manifest = yaml.safe_load(f)
  chosen = None
  for pkg in getpkgs(hostname):
    entry = (manifest.get('packages', {}).get(pkg) or {}).get('appdisk')
    if not entry:
      continue
    if chosen and chosen['mountpoint'] != entry.get('mountpoint'):
      raise ValueError('conflicting appdisk mountpoints for %s' % hostname)
    if not chosen or _size(entry['size']) > _size(chosen['size']):
      chosen = dict(entry)
  return chosen


def _size(value):
  """Humanized size (16G, 2TiB, plain int bytes) to bytes."""
  if isinstance(value, int):
    return value
  value = value.upper()
  if value.endswith('IB'):
    value = value[:-2]
  suffixes = {'T': 40, 'G': 30, 'M': 20, 'K': 10}
  if value[-1] in suffixes:
    return int(value[:-1]) * (2 ** suffixes[value[-1]])
  return int(value)


def get_current_event():
  conn = sqlite3.connect(DB_FILE)
  c = conn.cursor()
  c.execute('SELECT value FROM meta_data WHERE name = "current_event"')
  res = c.fetchone()
  conn.close()
  return res[0]
