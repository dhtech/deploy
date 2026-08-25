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
            'external_url': 'https://%s/' % webname}
    return out
