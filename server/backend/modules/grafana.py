# ENC generator for pkg "grafana": the SITE's dashboard server (one
# per site, number-less, like the router). Prod model throughout:
# official APT package, grafana's NATIVE directory login with the team
# role mappings (nginx only terminates the LE cert - no auth_pam double
# login), and a single provisioned datasource: the same site's
# prometheus, named prometheus_<site> so dashboards port to prod.

from lib import metadata

from . import ldap as _ldap
from . import prometheus as _prometheus


def generate(host, params, manifest):
    out = {}
    site = metadata.host_site(host)
    webname = metadata.host_option(host, 'webname')
    if webname:
        out['dhfirewall'] = {'open_tcp': [443]}
        out['dhacme::cert'] = {'cert_name': webname,
                               'vault_addr': _ldap.vault_addr()}
        out['dhnginx::grafana'] = {'server_name': webname}
        out['dhgrafana'] = {
            'root_url': 'https://%s/' % webname,
            'domain': webname,
            # login: the site slaves, binding as this DEVICE
            # (uid=svc-<shortname> - ipplan name, never the OS hostname)
            'ldap_hosts': _ldap.slave_hosts(),
            'ldap_base': 'dc=dreamhack,dc=se',
            'svc_name': host.split('.', 1)[0],
            # monitoring is site-local: exactly one datasource, the
            # same site's prometheus - never another site's
            'site': site,
            'prometheus_server': _prometheus.site_prometheus(site),
            # dashboard archive: per-site dir; event sites use the
            # current event name (prod's per-event lineage)
            'archive_dir': (metadata.get_current_event()
                            if site == 'event' else site),
        }
    return out
