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
            # the svc bind account is provisioned under the host's
            # IPPLAN shortname - never derive it from the OS hostname
            # (pve-test's hostname predates its ipplan identity)
            'svc_name': host.split('.', 1)[0],
        },
    }
    webname = metadata.host_option(host, 'webname')
    if webname:
        # the exporter talks to the local API by the certificate's
        # name (LE cert -> verify_ssl true)
        out['dhpveexporter'] = {'api_url_host': webname}
        out['dhpve']['cert_name'] = webname
        # WebAuthn rp id: the parent domain, so credentials work on
        # every pve web name (pve1/pve2 both end in it)
        out['dhpve']['webauthn_rp_id'] = webname.split('.', 1)[1]
        out['dhacme::cert'] = {
            'cert_name': webname,
            'vault_addr': _ldap.vault_addr(),
            'reload_cmd': '/usr/local/sbin/dh-pve-cert-install',
        }
    return out
