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
    return out


def _native_gateway():
    import sqlite3
    conn = sqlite3.connect(metadata.DB_FILE)
    row = conn.execute(
        'SELECT n.ipv4_gateway_txt FROM network n, option o '
        'WHERE o.node_id = n.node_id AND o.name = "native"').fetchone()
    conn.close()
    return row[0] if row else None
