import collections
import json
import sqlite3
import ssl
import urllib.request

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


def parse_pkg(raw):
    """'ldap(role=master,id=1)' -> ('ldap', {'role': 'master', 'id': 1})."""
    name, _, rest = raw.partition('(')
    params = {}
    if rest.endswith(')'):
        for pair in rest[:-1].split(','):
            if '=' in pair:
                key, value = pair.split('=', 1)
                value = value.strip()
                params[key.strip()] = int(value) if value.isdigit() else value
    return name, params


def pkgs_with_params(hostname):
    """All pkg options of a host with their parsed parameters."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        'SELECT option.value FROM host, option '
        'WHERE host.node_id = option.node_id '
        'AND option.name = "pkg" AND host.name = ?', (hostname,))
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return [parse_pkg(raw) for raw in rows if not raw.startswith('-')]


def hosts_with_pkg(pkg):
    """All (hostname, params) carrying a pkg, sorted by hostname.
    ipplan is the single source of truth for topology questions like
    'which machines are the ldap masters'."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        'SELECT host.name, option.value FROM host, option '
        'WHERE host.node_id = option.node_id AND option.name = "pkg" '
        'ORDER BY host.name')
    result = []
    for host, raw in c.fetchall():
        if raw.startswith('-'):
            continue
        name, params = parse_pkg(raw)
        if name == pkg:
            result.append((host, params))
    conn.close()
    return result


def host_option(hostname, name):
    """A single ipplan option of a host (e.g. webname), or None."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        'SELECT option.value FROM host, option '
        'WHERE host.node_id = option.node_id '
        'AND option.name = ? AND host.name = ?', (name, hostname))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None


def all_host_options(name):
    """{hostname: value} for every host carrying the option, sorted."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        'SELECT host.name, option.value FROM host, option '
        'WHERE host.node_id = option.node_id AND option.name = ? '
        'ORDER BY host.name', (name,))
    result = dict(c.fetchall())
    conn.close()
    return result


def site_cidrs(hostname):
    """All network CIDRs of the host's SITE. Network names are
    <site>@<name> (one ipplan file per site in production, e.g.
    STO2@DHTECH-1, EVENT@DREAMHACK); the site is the part BEFORE the @ -
    the same thing the deploy code calls the domain."""
    import ipaddress
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        'SELECT network.name FROM host, network '
        'WHERE host.network_id = network.node_id AND host.name = ?',
        (hostname,))
    res = c.fetchone()
    if not res:
        conn.close()
        return []
    site = res[0].split('@', 1)[0]
    c.execute('SELECT name, ipv4_gateway_txt, ipv4_netmask_dec FROM network')
    cidrs = []
    for name, gw, mask in c.fetchall():
        if name.startswith(site + '@') and gw and mask is not None:
            net = ipaddress.ip_network('%s/%d' % (gw, mask), strict=False)
            cidrs.append(str(net))
    conn.close()
    return sorted(cidrs)


def all_hosts_pkgs():
    """{hostname: [(pkg, params)]} for every host carrying pkg options."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        'SELECT host.name, option.value FROM host, option '
        'WHERE host.node_id = option.node_id AND option.name = "pkg" '
        'ORDER BY host.name')
    result = collections.defaultdict(list)
    for host, raw in c.fetchall():
        if not raw.startswith('-'):
            result[host].append(parse_pkg(raw))
    conn.close()
    return dict(result)


def host_site(hostname):
    """The host's site: the network name before the @, lowercased (the
    same thing the deploy code calls the domain)."""
    name, _ = get_vlan(hostname)
    return name.split('@', 1)[0].lower() if name else None


def host_ip(hostname):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT ipv4_addr_txt FROM host WHERE name = ?', (hostname,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None


def get_appdisks(hostname, manifest_file='/etc/manifest'):
    """Application LVs for a host: one per appdisk-bearing package, keyed
    by mountpoint (same mountpoint from several packages: max size wins).
    Returns a sorted list of {size (bytes), mountpoint, options, lv}."""
    with open(manifest_file) as f:
        manifest = yaml.safe_load(f)
    by_mount = {}
    for pkg in getpkgs(hostname):
        entry = (manifest.get('packages', {}).get(pkg) or {}).get('appdisk')
        if not entry:
            continue
        mnt = entry['mountpoint']
        size = _size(entry['size'])
        if mnt not in by_mount or size > by_mount[mnt]['size']:
            by_mount[mnt] = {
                'size': size,
                'mountpoint': mnt,
                'options': entry.get('options', 'defaults'),
                'lv': 'lv' + mnt.strip('/').replace('/', '_'),
            }
    return [by_mount[m] for m in sorted(by_mount)]


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


def _vault_context(cfg):
    context = ssl.create_default_context(cafile=cfg['vault_cacert'])
    context.load_cert_chain(cfg['vault_cert'], cfg['vault_key'])
    return context


def _vault_token(cfg, context):
    if cfg.get('vault_token'):
        return cfg['vault_token']
    request = urllib.request.Request(
        '%s/v1/auth/cert/login' % cfg['vault_addr'], data=b'{}', method='POST')
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
        return 'services-%s/login:%s' % (get_current_event(), client.hostname)
    return 'services/login:%s' % client.hostname


def get_meta(name, default=None):
    """A meta_data value (current_event, change_freeze, ...). In
    production these come from the current-event file in svn."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT value FROM meta_data WHERE name = ?', (name,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else default


def get_current_event():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT value FROM meta_data WHERE name = "current_event"')
    res = c.fetchone()
    conn.close()
    return res[0]
