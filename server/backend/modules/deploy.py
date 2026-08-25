# ENC generator for pkg "deploy": the deploy server's own config -
# everything derivable comes from the db (the resolver is the deploy
# host itself: it runs the site dnsmasq).

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
    puppetservers = metadata.hosts_with_pkg('puppetserver')
    out = {
        'dhdeploy::config': {
            'puppet_server': puppetservers[0][0] if puppetservers else None,
            'resolvers': [metadata.host_ip(host)],
            'vault_addr': _ldap.vault_addr(),
        },
    }
    webname = metadata.host_option(host, 'webname')
    if webname:
        out['dhdeploy::web'] = {'webname': webname}
    # the deployment VLAN's default gateway, from the data: the
    # native-flagged network's computed gateway - flips to the router
    # with the ipplan gw= removal, no code change at cutover
    gateway = _native_gateway()
    if gateway:
        out['dhdeploy::pxe'] = {'gateway': gateway}
    # P6: the deploy server is an ordinary dhfirewall host now (the
    # NAT/router role moved to the router). Its serving ports: the
    # backend/status web (8080 + nginx 443/446), apt-cacher (3142),
    # and udp - dns for the fleet (until dhresolver moves it), dhcp
    # + tftp for the deployment VLAN, installer syslog. ssh comes
    # world-open from the jumpgate pkg.
    out['dhfirewall'] = {
        'open_tcp': [53, 443, 446, 8080],
        # apt-cache: the SERVER vlan (the deploy host's own network)
        # + the deployment vlan (the installers' preseed proxy) -
        # not MGMT, never the outside. More server vlans join here
        # via a network option when prod needs them.
        'open_tcp_scoped': {3142: _cache_networks(host)},
        'open_udp': [53, 67, 69, 514],
    }
    return out


def _cache_networks(host):
    """Where the apt-cache is reachable from: the deploy host's own
    network (the server vlan) and the native/deployment network (the
    installers' preseed proxy)."""
    import sqlite3
    conn = sqlite3.connect(metadata.DB_FILE)
    rows = conn.execute(
        'SELECT n.ipv4_txt FROM network n, host h '
        'WHERE h.name = ? AND n.node_id = h.network_id '
        'UNION SELECT n.ipv4_txt FROM network n, option o '
        'WHERE o.node_id = n.node_id AND o.name = "native" '
        'ORDER BY 1', (host,)).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


def _native_gateway():
    import sqlite3
    conn = sqlite3.connect(metadata.DB_FILE)
    row = conn.execute(
        'SELECT n.ipv4_gateway_txt FROM network n, option o '
        'WHERE o.node_id = n.node_id AND o.name = "native"').fetchone()
    conn.close()
    return row[0] if row else None
