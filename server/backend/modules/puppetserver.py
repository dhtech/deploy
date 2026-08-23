# ENC generator for pkg "puppetserver": the ACME issuer's certificate
# list is every webname in ipplan - it maintains itself. ACME account
# constants come from the manifest globals. With a webname of its own
# the host also serves puppetboard (cert + nginx site + 443).

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
    acme = (manifest.get('globals') or {}).get('acme') or {}
    domains = sorted(set(metadata.all_host_options('webname').values()))
    out = {
        'dhfirewall': {'open_tcp': [8140]},
        'dhacme::issuer': {
            'domains': domains,
            'email': acme.get('email'),
            'acme_server': acme.get('server'),
            'vault_addr': _ldap.vault_addr(),
        },
    }
    webname = metadata.host_option(host, 'webname')
    if webname:
        out['dhfirewall']['open_tcp'].append(443)
        out['dhacme::cert'] = {'cert_name': webname,
                               'vault_addr': _ldap.vault_addr()}
        out['dhnginx::puppetboard'] = {'server_name': webname}
    return out
