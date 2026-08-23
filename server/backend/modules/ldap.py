# ENC generator for pkg "ldap": directory servers.
# Topology comes from ipplan: masters are the hosts whose pkg says
# ldap(role=master,id=N); everything else is a site slave.

from lib import metadata


def generate(host, params, manifest):
  masters = sorted(
      ((h, p) for h, p in metadata.hosts_with_pkg('ldap')
       if p.get('role') == 'master'),
      key=lambda hp: hp[1].get('id', 0))
  uris = ['ldaps://%s' % h for h, _ in masters]

  server = {'master_uris': uris, 'vault_addr': vault_addr()}
  if params.get('role') == 'master':
    server['role'] = 'master'
    server['server_id'] = params.get('id')
  else:
    server['role'] = 'slave'

  return {
      'dhfirewall': {'open_tcp': [636]},
      'dhldap::server': server,
  }


def vault_addr():
  vaults = metadata.hosts_with_pkg('vault')
  return 'https://%s:8200' % vaults[0][0] if vaults else None
