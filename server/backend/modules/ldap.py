# ENC generator for pkg "ldap": directory servers. Topology comes from
# ipplan: masters are the hosts whose pkg says ldap(role=master,id=N);
# everything else is a site slave. 636 openings are NOT emitted here -
# they come from the manifest's client/server flow specs (the ldap and
# ldap(role=master) entries) via the flow engine.

from lib import metadata


def generate(host, params, manifest):
    server = {'master_uris': master_uris(), 'vault_addr': vault_addr()}
    if params.get('role') == 'master':
        server['role'] = 'master'
        server['server_id'] = params.get('id')
    else:
        server['role'] = 'slave'
    return {'dhldap::server': server}


def master_hosts():
    """The mirror masters, ordered by their server id."""
    masters = sorted(
        ((h, p) for h, p in metadata.hosts_with_pkg('ldap')
         if p.get('role') == 'master'),
        key=lambda hp: hp[1].get('id', 0))
    return [h for h, _ in masters]


def master_uris():
    return ['ldaps://%s' % h for h in master_hosts()]


def slave_hosts():
    """Application read/auth endpoints: the site's slaves. Policy:
    applications bind to slaves; writes and directory admin (LAM) go to
    the masters. Falls back to the masters while a site has no slaves."""
    slaves = sorted(h for h, p in metadata.hosts_with_pkg('ldap')
                    if p.get('role') != 'master')
    return slaves if slaves else master_hosts()


def slave_uris():
    return ['ldaps://%s' % h for h in slave_hosts()]


def vault_addr():
    vaults = metadata.hosts_with_pkg('vault')
    return 'https://%s:8200' % vaults[0][0] if vaults else None
