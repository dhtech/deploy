# ENC generator for pkg "ldapmaster": the mirror-mode directory
# masters, ldapmaster(id=N) in ipplan. They serve only the directory
# fleet (ldaprepl flow) and the admin UI (ldapwrite flow) - both
# declared in the manifest and rendered by the flow engine.

from . import ldap as _ldap


def generate(host, params, manifest):
    return {
        'dhldap::server': {
            'role': 'master',
            'server_id': params.get('id'),
                    'master_uris': _ldap.master_uris(),
                    'vault_addr': _ldap.vault_addr(),
        },
    }
