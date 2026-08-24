# ENC generator for pkg "deploy": the deploy server's own config -
# everything derivable comes from the db (the resolver is the deploy
# host itself: it runs the site dnsmasq).

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
    puppetservers = metadata.hosts_with_pkg('puppetserver')
    return {
        'dhdeploy::config': {
            'puppet_server': puppetservers[0][0] if puppetservers else None,
            'resolvers': [metadata.host_ip(host)],
            'vault_addr': _ldap.vault_addr(),
        },
    }
