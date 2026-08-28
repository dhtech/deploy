"""Deploy-server runtime: the provisioning flow's state and
secrets. Redis holds the install-time machine records, bao answers
with machine-cert auth, config() is this server's own file. This is
deploy APPLICATION code - the pure plan-reading API is ipplanlib
(the toolchain repo), which this module builds on.

Split out of libdhdeploy/metadata.py 2026-08-28 (the toolchain
carried redis/vault/URLs it never used)."""

import collections
import json
import sqlite3
import ssl
import urllib.request

import yaml

from ipplanlib import metadata

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
    # hand-enrolled hypervisors: never deployed, but the os type keys
    # their own fleet defaults (manifest default: section)
    'pve': 'Proxmox VE',
}


def config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def connection():
    # imported lazily: the ipplan toolchain (ipplan2db + this module)
    # is vendored onto hosts like doc1 that have no redis at all
    import redis
    return redis.Redis(**config()['redis'])


def base_url():
    """Base URL clients use to reach this deploy server."""
    return config().get('base_url', 'https://deploy.tech.dreamhack.se')


def lookup_ip(ip):
    conn = sqlite3.connect(metadata.DB_FILE)
    c = conn.cursor()
    c.execute('SELECT name FROM host WHERE ipv4_addr_txt = ?', (ip,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None


class IdentityError(Exception):
    """The install-time caller could not prove a valid identity."""


def request_host(query_string):
    """Install-time caller identity: the fqdn= parameter, which MUST
    name a host row in ipplan. fqdn-only (beta - no hack_ip fallback,
    no address guessing): missing or unknown raises IdentityError and
    the CGI answers 403 - anomalies must be SEEN."""
    fqdn = query_string.get('fqdn', [''])[0].strip().lower()
    if not fqdn:
        raise IdentityError('no fqdn= parameter: identity required')
    if metadata.get_vlan(fqdn)[0] is None:
        raise IdentityError('%s: not in ipplan' % fqdn)
    return fqdn


def find(hostname, first_if=None):
    """Install-state record for a host, by its fqdn (the identity the
    whole system keys on - gen-3 dropped the ip detour)."""
    if first_if is None:
        first_if = 'eth0'

    r = connection()
    raw = r.get('host-' + hostname)
    if not raw:
        return None, None

    machine = json.loads(raw)

    manufacturer = machine.get('manufacturer', '').lower()
    # Gen-3: QEMU/Proxmox VMs are first-class virtual machines.
    virtual = 'vmware' in manufacturer or 'qemu' in manufacturer

    os = metadata._get_os(hostname)
    os_human = OS_HUMAN.get(os, 'Unsupported OS: %s' % os)

    interface = 'bond0' if not virtual else first_if

    my_net, _ = metadata.get_vlan(hostname)
    my_domain, _ = my_net.split('@', 1)
    client = Client(hostname=hostname, ip=metadata.host_ip(hostname),
                    virtual=virtual, managed=True,
                    os=os, os_human=os_human, interface=interface,
                    domain=my_domain)
    return client, machine


def network(client, cm):
    interface = client.interface
    bonded = interface.startswith('bond')
    conn = sqlite3.connect(metadata.DB_FILE)
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
    conn = sqlite3.connect(metadata.DB_FILE)
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


def _vault_context(cfg):
    context = ssl.create_default_context(cafile=cfg['vault_cacert'])
    context.load_cert_chain(cfg['vault_cert'], cfg['vault_key'])
    return context


def _vault_token(cfg, context):
    if cfg.get('vault_token'):
        return cfg['vault_token']
    # named role: the deploy server's cert maps to the scoped 'deploy'
    # role (services/*); generic hosts use the minimal 'host' role
    request = urllib.request.Request(
        '%s/v1/auth/cert/login' % cfg['vault_addr'],
        data=b'{"name": "deploy"}', method='POST')
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        return json.load(response)['auth']['client_token']


def vault_write(path, **data):
    cfg = config()
    context = _vault_context(cfg) if cfg.get('vault_cacert') else None
    request = urllib.request.Request(
        '%s/v1/%s' % (cfg['vault_addr'], path),
        data=json.dumps(data).encode(),
        headers={'X-Vault-Token': _vault_token(cfg, context)},
        method='PUT')
    urllib.request.urlopen(request, timeout=10, context=context)


def vault_read(path):
    cfg = config()
    context = _vault_context(cfg) if cfg.get('vault_cacert') else None
    request = urllib.request.Request(
        '%s/v1/%s' % (cfg['vault_addr'], path),
        headers={'X-Vault-Token': _vault_token(cfg, context)})
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        return json.load(response)['data']


def vault_login_path(client):
    """The gen-2 secret path for a host's login credentials."""
    if client.domain == 'EVENT':
        return 'services-%s/login:%s' % (metadata.get_current_event(), client.hostname)
    return 'services/login:%s' % client.hostname
