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
    # Site flows: the masters serve ONLY the directory - the admin UI
    # (pkg lam) and the ldap fleet (mirror + slave replication).
    sources = sorted(
        {metadata.host_ip(h) for h, _ in metadata.hosts_with_pkg('ldap')} |
        {metadata.host_ip(h) for h, _ in metadata.hosts_with_pkg('lam')})
    firewall = {'open_tcp_scoped': {636: [s for s in sources if s]}}
  else:
    server['role'] = 'slave'
    # Site flows: a slave serves only its own site's networks.
    cidrs = metadata.site_cidrs(host)
    firewall = ({'open_tcp_scoped': {636: cidrs}} if cidrs
                else {'open_tcp': [636]})

  return {
      'dhfirewall': firewall,
      'dhldap::server': server,
  }


def slave_uris():
  """Application read/auth endpoints: the site's slave pair. Policy:
  applications bind to slaves; writes and directory admin (LAM) go to
  the masters. Falls back to the masters while a site has no slaves."""
  slaves = [h for h, p in metadata.hosts_with_pkg('ldap')
            if p.get('role') != 'master']
  if slaves:
    return ['ldaps://%s' % h for h in sorted(slaves)]
  return ['ldaps://%s' % h for h, _ in metadata.hosts_with_pkg('ldap')]


def vault_addr():
  vaults = metadata.hosts_with_pkg('vault')
  return 'https://%s:8200' % vaults[0][0] if vaults else None
