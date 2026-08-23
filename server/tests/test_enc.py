# The ENC: ipplan + manifest as single source of truth (no hiera).

import enc


def test_ldap_master_params(ipplan, manifest):
  classes = enc.classify('ldap1-master.test', manifest)
  server = classes['dhldap::server']
  assert server['role'] == 'master'
  assert server['server_id'] == 1
  assert server['master_uris'] == [
      'ldaps://ldap1-master.test', 'ldaps://ldap2-master.test']
  assert server['vault_addr'] == 'https://vault1.test:8200'
  assert classes['dhfirewall']['open_tcp'] == [636]


def test_ldap_slave_defaults(ipplan, manifest):
  classes = enc.classify('ldap1.test', manifest)
  assert classes['dhldap::server']['role'] == 'slave'
  assert 'server_id' not in classes['dhldap::server']


def test_web_port_from_pkg_arg(ipplan, manifest):
  classes = enc.classify('web1.test', manifest)
  assert classes['dhfirewall']['open_tcp'] == [80]


def test_vault_webname_drives_cert_and_nginx(ipplan, manifest):
  classes = enc.classify('vault1.test', manifest)
  assert classes['dhacme::cert']['cert_name'] == 'vault.dh.example'
  assert classes['dhnginx::vault']['server_name'] == 'vault.dh.example'
  assert classes['dhfirewall']['open_tcp'] == [8200, 443]


def test_issuer_domains_are_all_webnames(ipplan, manifest):
  classes = enc.classify('puppet1.test', manifest)
  issuer = classes['dhacme::issuer']
  assert issuer['domains'] == ['pve.dh.example', 'vault.dh.example']
  assert issuer['email'] == 'a@example'
  assert issuer['acme_server'] == 'https://acme'


def test_pve_installs_cert_no_firewall(ipplan, manifest):
  classes = enc.classify('pve1.test', manifest)
  assert classes['dhpve']['cert_name'] == 'pve.dh.example'
  assert classes['dhacme::cert']['reload_cmd'] == (
      '/usr/local/sbin/dh-pve-cert-install')
  assert 'dhfirewall' not in classes


def test_jumpgates_from_ipplan(ipplan, manifest):
  classes = enc.classify('web1.test', manifest)
  assert classes['dhfirewall']['jumpgates'] == ['10.200.0.2']


def test_merge_lists_union_scalars_override():
  target = {'a': {'ports': [1, 2], 'x': 'old'}}
  enc.merge_params(target, {'a': {'ports': [2, 3], 'x': 'new'}, 'b': {}})
  assert target['a']['ports'] == [1, 2, 3]
  assert target['a']['x'] == 'new'
  assert 'b' in target


def test_unknown_host_gets_firewall_only(ipplan, manifest):
  assert enc.classify('nosuch.test', manifest) == {
      'dhfirewall': {'jumpgates': ['10.200.0.2']}}
