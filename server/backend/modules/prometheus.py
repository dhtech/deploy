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
        site = metadata.host_site(host)
        out['dhprometheus'] = {
            'external_url': 'https://%s/' % webname,
            # scrape targets FROM ipplan: every SAME-SITE host is a
            # node target the moment it exists in the plan
            'node_targets': _node_targets(site),
            # prod's manifest monitor: idiom - services declare their
            # own scraping, jobs materialize per site
            'scrape_jobs': _monitor_jobs(site, manifest)}
    return out


def monitor_port(url):
    """The port a monitor: url scrapes ({host} placeholder allowed)."""
    import urllib.parse
    parsed = urllib.parse.urlparse(url.replace('{host}', 'H'))
    return parsed.port or (443 if parsed.scheme == 'https' else 80)


def _monitor_jobs(site, manifest):
    """Prod's model: any package may declare
    monitor: {url: 'http://{host}:3000/metrics'} (optional interval,
    labels) - every SAME-SITE host carrying the pkg becomes a target
    of a job named after the pkg. The consumer host's firewall opening
    comes from the enc baseline (scoped to this site's prometheus)."""
    import urllib.parse
    jobs = []
    for pkg, spec in sorted((manifest.get('packages') or {}).items()):
        mon = (spec or {}).get('monitor')
        if not mon:
            continue
        parsed = urllib.parse.urlparse(mon['url'].replace('{host}', 'H'))
        port = monitor_port(mon['url'])
        targets = sorted(
            '%s:%d' % (h, port) for h, _ in metadata.hosts_with_pkg(pkg)
            if metadata.host_site(h) == site)
        if not targets:
            continue
        job = {'job_name': pkg, 'scheme': parsed.scheme or 'http',
               'metrics_path': parsed.path or '/metrics',
               'targets': targets}
        if parsed.query:
            # multi-target exporters (pve): the url's query becomes
            # the scrape params
            job['params'] = {k: v for k, v in
                             urllib.parse.parse_qs(parsed.query).items()}
        if 'interval' in mon:
            job['interval'] = mon['interval']
        if 'labels' in mon:
            job['labels'] = mon['labels']
        jobs.append(job)
    return jobs


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
