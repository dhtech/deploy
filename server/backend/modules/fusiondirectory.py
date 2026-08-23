# ENC generator for pkg "fusiondirectory": web UI with an LE website;
# talks to the directory masters (URI list from ipplan for the future
# dhfusion class).

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
  webname = metadata.host_option(host, 'webname')
  out = {'dhfirewall': {'open_tcp': [443]}}
  if webname:
    out['dhacme::cert'] = {'cert_name': webname,
                           'vault_addr': _ldap.vault_addr()}
  return out
