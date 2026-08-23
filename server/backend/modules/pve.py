# ENC generator for pkg "pve": the hypervisor gets the LE cert named by
# its webname installed into pveproxy. No dhfirewall - pve manages its
# own firewall.

from lib import metadata

from . import ldap as _ldap


def generate(host, params, manifest):
  webname = metadata.host_option(host, 'webname')
  if not webname:
    return {}
  return {
      'dhacme::cert': {
          'cert_name': webname,
          'vault_addr': _ldap.vault_addr(),
          'reload_cmd': '/usr/local/sbin/dh-pve-cert-install',
      },
      'dhpve': {'cert_name': webname},
  }
