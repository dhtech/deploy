# ENC generator for pkg "trac": the doc website (LE cert via webname).
# svn shares the host; the website terminates both.

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
  webname = metadata.host_option(host, 'webname')
  out = {'dhfirewall': {'open_tcp': [443]}}
  if webname:
    out['dhacme::cert'] = {'cert_name': webname,
                           'vault_addr': _ldap.vault_addr()}
  return out
