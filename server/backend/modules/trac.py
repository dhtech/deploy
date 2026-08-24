# ENC generator for pkg "trac": the doc website (LE cert via webname).
# svn shares the host; apache terminates both, with directory login
# straight against the site's ldap slaves.

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
    webname = metadata.host_option(host, 'webname')
    out = {'dhfirewall': {'open_tcp': [443]}}
    if webname:
        out['dhacme::cert'] = {'cert_name': webname,
                               'vault_addr': _ldap.vault_addr(),
                               'reload_cmd': 'systemctl reload apache2'}
        out['dhdoc'] = {'server_name': webname,
                        'ldap_uris': _ldap.slave_uris()}
    return out
