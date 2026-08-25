# ENC generator for pkg "prometheus": the metrics server from the
# appstore (official release tarball), fronted by an LE website with
# directory login (the puppetboard pattern).

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
    out = {}
    webname = metadata.host_option(host, 'webname')
    if webname:
        out['dhfirewall'] = {'open_tcp': [443]}
        out['dhacme::cert'] = {'cert_name': webname,
                               'vault_addr': _ldap.vault_addr()}
        out['dhnginx::prometheus'] = {'server_name': webname}
        out['dhprometheus'] = {
            'external_url': 'https://%s/' % webname,
            # scrape targets FROM ipplan: every host is a node
            # target the moment it exists in the plan
            'node_targets': _node_targets()}
    return out


def _node_targets():
    import sqlite3
    conn = sqlite3.connect(metadata.DB_FILE)
    rows = conn.execute(
        'SELECT name FROM host WHERE ipv4_addr_txt IS NOT NULL '
        'ORDER BY name').fetchall()
    conn.close()
    return ['%s:9100' % r[0] for r in rows]
