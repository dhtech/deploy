# ENC generator for pkg "vault": OpenBao + its public website. The
# website name is the host's ipplan webname option.

from ipplanlib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
    webname = metadata.host_option(host, 'webname')
    out = {'dhfirewall': {'open_tcp': [8200, 443]}}
    if webname:
        out['dhacme::cert'] = {'cert_name': webname,
                               'vault_addr': _ldap.vault_addr()}
        out['dhnginx::vault'] = {'server_name': webname}
    return out
