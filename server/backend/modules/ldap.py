# ENC generator for pkg "ldap": site slave directory servers. The
# masters are pkg "ldapmaster" (prod naming). 636 openings come from
# the manifest's client/server flow specs via the flow engine, not
# from here.

from lib import metadata


def generate(host, params, manifest):
    return {
        'dhldap::server': {
            'role': 'slave',
            'master_uris': master_uris(),
                    'vault_addr': vault_addr(),
        },
    }


def master_hosts():
    """The mirror masters, ordered by their server id."""
    masters = sorted(metadata.hosts_with_pkg('ldapmaster'),
                     key=lambda hp: hp[1].get('id', 0))
    return [h for h, _ in masters]


def master_uris():
    return ['ldaps://%s' % h for h in master_hosts()]


def slave_hosts():
    """Application read/auth endpoints: the site's slaves. Policy:
    applications bind to slaves; writes and directory admin (LAM) go to
    the masters. Falls back to the masters while a site has no slaves."""
    slaves = sorted(h for h, _ in metadata.hosts_with_pkg('ldap'))
    return slaves if slaves else master_hosts()


def slave_uris():
    return ['ldaps://%s' % h for h in slave_hosts()]


def vault_addr():
    vaults = metadata.hosts_with_pkg('vault')
    return 'https://%s:8200' % vaults[0][0] if vaults else None
