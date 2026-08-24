# ENC generator for pkg "lam": the directory web UI (LDAP Account
# Manager) with an LE website; manages the directory on the masters
# (URI list from ipplan).

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
    webname = metadata.host_option(host, 'webname')
    out = {
        'dhfirewall': {'open_tcp': [443]},
        'dhlam': {
            'ldap_uris': _ldap.master_uris(),
            'suffixes': ['dc=dreamhack,dc=se'],
        },
    }
    if webname:
        out['dhacme::cert'] = {'cert_name': webname,
                               'vault_addr': _ldap.vault_addr()}
        out['dhnginx::lam'] = {'server_name': webname}
    # brute-force protection on the outer pam gate: auth_pam does not
    # set PAM_RHOST (no ip in the pam log), but nginx's error log has
    # 'PAM: user ... not authenticated ... client: <ip>' - a custom
    # filter bans on that. Never ban jumpgates / the lab masquerade.
    out['dhfail2ban'] = {
        'ignore_ips': sorted(metadata.host_ip(h) for h, _ in
                             metadata.hosts_with_pkg('jumpgate')),
        'jails': {'dh-nginx-pam': {
            'port': 'http,https',
            'logpath': '/var/log/nginx/error.log',
        }},
        'filters': {'dh-nginx-pam': (
            '# Managed by puppet (dhfail2ban): nginx auth_pam failures\n'
            '[Definition]\n'
            "failregex = ^ \\[error\\] \\d+#\\d+: \\*\\d+ PAM: user \\S+ "
            '- not authenticated: .*, client: <HOST>,\n'
            'datepattern = {^LN-BEG}\n')},
    }
    return out
