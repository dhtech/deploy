# The lam generator: directory UI wired to the masters from ipplan.

import enc


def add_lam_host(ipplan_db, manifest):
  import sqlite3
  conn = sqlite3.connect(ipplan_db)
  c = conn.cursor()
  c.execute("INSERT INTO host VALUES (14, 'directory1.test', "
            "'10.200.0.63', NULL, 1)")
  c.executemany('INSERT INTO option VALUES (?, ?, ?)', [
      (14, 'pkg', 'lam'),
      (14, 'webname', 'directory.dh.example'),
  ])
  conn.commit()
  conn.close()
  manifest['packages']['lam'] = {'puppet': {'classes': [
      'dhfirewall', 'dhacme::cert', 'dhnginx::lam', 'dhlam']}}


def test_lam_params(ipplan, manifest):
  add_lam_host(str(ipplan), manifest)
  classes = enc.classify('directory1.test', manifest)
  assert classes['dhlam']['ldap_uris'] == [
      'ldaps://ldap1-master.test', 'ldaps://ldap2-master.test']
  assert classes['dhlam']['suffixes'] == [
      'dc=tech,dc=dreamhack,dc=se', 'dc=event,dc=dreamhack,dc=se']
  assert classes['dhacme::cert']['cert_name'] == 'directory.dh.example'
  assert classes['dhnginx::lam']['server_name'] == 'directory.dh.example'
  assert classes['dhfirewall']['open_tcp'] == [443]
