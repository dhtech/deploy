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
    return out
