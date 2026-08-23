# ENC generator for pkg "pve": the hypervisor gets the LE cert named by
# its webname installed into pveproxy, and a directory login realm
# against the site's ldap slaves. Who may log in (the admin group) is
# policy and lives in the manifest params, not here. No dhfirewall -
# pve manages its own firewall.

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
    out = {
        'dhpve': {
            'ldap_servers': _ldap.slave_hosts(),
            'ldap_base': 'dc=dreamhack,dc=se',
        },
    }
    webname = metadata.host_option(host, 'webname')
    if webname:
        out['dhpve']['cert_name'] = webname
        out['dhacme::cert'] = {
            'cert_name': webname,
            'vault_addr': _ldap.vault_addr(),
            'reload_cmd': '/usr/local/sbin/dh-pve-cert-install',
        }
    return out
