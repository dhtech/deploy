# ENC generator for pkg "lam": the directory web UI (LDAP Account
# Manager) with an LE website; manages the directory on the masters
# (URI list from ipplan).

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
  webname = metadata.host_option(host, 'webname')
  masters = sorted(
      ((h, p) for h, p in metadata.hosts_with_pkg('ldap')
       if p.get('role') == 'master'),
      key=lambda hp: hp[1].get('id', 0))
  out = {
      'dhfirewall': {'open_tcp': [443]},
      'dhlam': {
          'ldap_uris': ['ldaps://%s' % h for h, _ in masters],
          'suffixes': ['dc=tech,dc=dreamhack,dc=se',
                       'dc=event,dc=dreamhack,dc=se'],
      },
  }
  if webname:
    out['dhacme::cert'] = {'cert_name': webname,
                           'vault_addr': _ldap.vault_addr()}
    out['dhnginx::lam'] = {'server_name': webname}
  return out
