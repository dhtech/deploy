# ENC generator for pkg "prometheus": the SITE's metrics server (one
# per site, number-less) from the appstore (official release tarball),
# fronted by an LE website with directory login (the puppetboard
# pattern). Monitoring is site-local: this prometheus scrapes only its
# own site's hosts - cross-site visibility is the other site's problem.

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
            # scrape targets FROM ipplan: every SAME-SITE host is a
            # node target the moment it exists in the plan
            'node_targets': _node_targets(metadata.host_site(host))}
    return out


def site_prometheus(site):
    """The site's prometheus host, or None - the per-site singleton
    every monitoring consumer (grafana, the 9100 baseline) keys on."""
    for h, _ in metadata.hosts_with_pkg('prometheus'):
        if metadata.host_site(h) == site:
            return h
    return None


def _node_targets(site):
    import sqlite3
    conn = sqlite3.connect(metadata.DB_FILE)
    rows = conn.execute(
        'SELECT name FROM host WHERE ipv4_addr_txt IS NOT NULL '
        'ORDER BY name').fetchall()
    conn.close()
    return ['%s:9100' % r[0] for r in rows
            if metadata.host_site(r[0]) == site]
