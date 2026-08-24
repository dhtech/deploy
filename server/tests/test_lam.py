# The lam and login generators: directory UI wired to the masters,
# machine logins wired to the slaves (both from ipplan).

import sqlite3

import enc


def test_lam_params(ipplan, manifest):
    conn = sqlite3.connect(str(ipplan))
    conn.execute("INSERT INTO option VALUES (NULL, "
                 "(SELECT node_id FROM host WHERE name = 'directory1.test'), "
                 "'webname', 'directory.dh.example')")
    conn.commit()
    conn.close()
    classes = enc.classify('directory1.test', manifest)
    assert classes['dhlam']['ldap_uris'] == [
        'ldaps://ldap1-master.test', 'ldaps://ldap2-master.test']
    assert classes['dhlam']['suffixes'] == ['dc=dreamhack,dc=se']
    assert classes['dhacme::cert']['cert_name'] == 'directory.dh.example'
    assert classes['dhnginx::lam']['server_name'] == 'directory.dh.example'
    assert classes['dhfirewall']['open_tcp'] == [443]


def test_login_binds_to_slaves(ipplan, manifest):
    manifest['packages']['login']['puppet']['params'] = {
        'dhlogin': {'sudo_groups': ['everyone']}}
    classes = enc.classify('web1.test', manifest)
    assert classes['dhlogin']['ldap_uris'] == ['ldaps://ldap1.test']
    assert classes['dhlogin']['sudo_groups'] == ['everyone']
    assert classes['dhlogin']['search_base'] == 'dc=dreamhack,dc=se'
