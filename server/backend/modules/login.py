# ENC generator for pkg "login": directory logins via sssd. Machines
# authenticate against the site's ldap slaves (site-flows policy);
# sudo groups come from the manifest's static params.

from . import ldap as _ldap


def generate(host, params, manifest):
  return {
      'dhlogin': {
          'ldap_uris': _ldap.slave_uris(),
          'search_base': 'dc=dreamhack,dc=se',
      },
  }
