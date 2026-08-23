# ENC generator for pkg "puppetserver": the ACME issuer's certificate
# list is every webname in ipplan - it maintains itself. ACME account
# constants come from the manifest globals.

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
  acme = (manifest.get('globals') or {}).get('acme') or {}
  domains = sorted(set(metadata.all_host_options('webname').values()))
  return {
      'dhfirewall': {'open_tcp': [8140]},
      'dhacme::issuer': {
          'domains': domains,
          'email': acme.get('email'),
          'acme_server': acme.get('server'),
          'vault_addr': _ldap.vault_addr(),
      },
  }
