# The ENC: ipplan + manifest as single source of truth (no hiera).

import json
import sqlite3

import enc


def test_ldap_master_params(ipplan, manifest):
    classes = enc.classify('ldap1-master.test', manifest)
    server = classes['dhldap::server']
    assert server['role'] == 'master'
    assert server['server_id'] == 1
    assert server['master_uris'] == [
        'ldaps://ldap1-master.test', 'ldaps://ldap2-master.test']
    assert server['vault_addr'] == 'https://vault1.test:8200'
    # flows: masters serve ONLY the directory - replication (mirror
    # partner + slaves) and admin writes (lam); no plain site clients
    assert classes['dhfirewall']['open_tcp_scoped'] == {
        636: ['10.200.0.63', '10.200.0.66', '10.200.0.67']}


def test_ldap_slave_defaults(ipplan, manifest):
    classes = enc.classify('ldap1.test', manifest)
    assert classes['dhldap::server']['role'] == 'slave'
    assert 'server_id' not in classes['dhldap::server']
    # flows: a slave serves the declared ldaps clients of its OWN site
    # (login/vault/pve; pve1 sits on the mgmt net outside the site
    # CIDR, the flow still finds it; evtbox1 is another site - no)
    assert classes['dhfirewall']['open_tcp_scoped'] == {
        636: ['10.10.10.1', '10.200.0.60', '10.200.0.61']}


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
    # pve manages its own firewall; being an ldaps CLIENT must not
    # drag dhfirewall in
    assert 'dhfirewall' not in classes


def test_pve_ldap_realm_params(ipplan, manifest):
    classes = enc.classify('pve1.test', manifest)
    pve = classes['dhpve']
    # realm against the site slaves; who may log in comes from the
    # manifest params (policy), topology from the generator
    assert pve['ldap_servers'] == ['ldap1.test']
    assert pve['ldap_base'] == 'dc=dreamhack,dc=se'
    assert pve['admin_group_dn'] == 'cn=g,dc=x'
    assert pve['admin_role'] == 'Administrator'


def test_login_host_flows_only_reach_same_site(ipplan, manifest):
    # evtbox1 declares client ldaps but sits in the EVENT site: it must
    # not appear on the colo slave (see test_ldap_slave_defaults) and
    # it serves nothing itself
    classes = enc.classify('evtbox1.test', manifest)
    assert 'dhfirewall' not in classes or (
        'open_tcp_scoped' not in classes['dhfirewall'])


def test_appstore_apps_and_event_freeze(ipplan, manifest):
    manifest['apps'] = {'puppetboard': {'kind': 'pip',
                                        'packages': ['puppetboard']}}
    classes = enc.classify('puppet1.test', manifest)
    data = json.loads(classes['dhappstore']['apps_json'])
    assert data == {'freeze': False, 'apps': manifest['apps']}
    # the freeze flag is operational state in ipplan meta_data (the
    # svn current-event file in production), not manifest policy
    conn = sqlite3.connect(str(ipplan))
    conn.execute("INSERT INTO meta_data VALUES ('change_freeze', 'true')")
    conn.commit()
    conn.close()
    classes = enc.classify('puppet1.test', manifest)
    assert json.loads(classes['dhappstore']['apps_json'])['freeze'] is True


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
