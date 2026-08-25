# Gen-2 compiler parity locks (public dhtech/ipplan2sqlite is the
# reference): the DHCP grammar (dhcp/resv=/shnet=/dhcp-*/mac=) must
# pass through to the option table so sqlite2dhcp-scope-style
# consumers can compute pools; IPv6 must be COMPUTED (master v6 base
# -> per-vlan /64 -> host addresses), and the build-to-build diff
# must report exactly what changed.

import sqlite3

import yaml

from conftest import load_ipplan2db

DHCP_IPPLAN = '''\
#@ IPV4-CLIENT-NET\t10.77.0.0/16
#@ IPV6-CLIENT-NET\t2001:db8:77::/48
CLIENTS\t10.77.1.0/24\tR1\t401\tdhcp;resv=10;dhcp-domain-name-servers=10.77.0.2
#$ printer1.test\t10.77.1.9\tpkg=base;mac=aa:bb:cc:dd:ee:ff
GUESTS\t10.77.2.0/24\tR1\t402\tdhcp;shnet=hall-a
STAFF\t10.77.3.0/24\tR1\t403\tdhcp;shnet=hall-a;resv=3
'''


def build(tmp_path, text, name='ipplan.db'):
    root = tmp_path / 'svn'
    site = root / 'allevents' / 'colo' / 'colo'
    site.mkdir(parents=True, exist_ok=True)
    (site / 'ipplan').write_text(text)
    (root / 'currentevent').write_text(
        'currentevent=none\napt_freeze=false\nchange_freeze=false\n')
    mpath = tmp_path / 'manifest.yaml'
    mpath.write_text(yaml.safe_dump({
        'packages': {'base': {'puppet': {'classes': ['dhfirewall']}}}}))
    db = tmp_path / name
    load_ipplan2db().build(str(root), [str(mpath)], str(db))
    return db


def options_of(db, node_name, table):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        'SELECT o.name, o.value FROM option o, %s n '
        'WHERE o.node_id = n.node_id AND n.name = ?' % table,
        (node_name,)).fetchall()
    conn.close()
    return dict(rows)


def test_dhcp_network_options_pass_through(tmp_path):
    db = build(tmp_path, DHCP_IPPLAN)
    clients = options_of(db, 'CLIENT@CLIENTS', 'network')
    assert clients['dhcp'] == '1'
    assert clients['resv'] == '10'
    assert clients['dhcp-domain-name-servers'] == '10.77.0.2'
    guests = options_of(db, 'CLIENT@GUESTS', 'network')
    assert guests['shnet'] == 'hall-a'
    assert 'resv' not in guests   # scope generator defaults it (5)
    staff = options_of(db, 'CLIENT@STAFF', 'network')
    assert (staff['shnet'], staff['resv']) == ('hall-a', '3')


def test_mac_reservation_passes_through(tmp_path):
    db = build(tmp_path, DHCP_IPPLAN)
    assert options_of(db, 'printer1.test', 'host')['mac'] == \
        'aa:bb:cc:dd:ee:ff'


def test_scope_generator_columns_present(tmp_path):
    """The columns sqlite2dhcp_scope reads must exist and be filled
    for dhcp networks: name, vlan, ipv4_txt, netmask, gateway."""
    db = build(tmp_path, DHCP_IPPLAN)
    conn = sqlite3.connect(db)
    row = conn.execute(
        'SELECT name, vlan, ipv4_txt, ipv4_netmask_txt, ipv4_gateway_txt '
        'FROM network WHERE name = ?', ('CLIENT@CLIENTS',)).fetchone()
    conn.close()
    assert row == ('CLIENT@CLIENTS', 401, '10.77.1.0/24', '255.255.255.0',
                   '10.77.1.1')


def test_ipv6_is_computed_not_stubbed(tmp_path):
    db = build(tmp_path, DHCP_IPPLAN)
    conn = sqlite3.connect(db)
    net_v6 = conn.execute(
        'SELECT ipv6_txt FROM network WHERE name = ?',
        ('CLIENT@CLIENTS',)).fetchone()[0]
    host_v6 = conn.execute(
        'SELECT ipv6_addr_txt FROM host WHERE name = ?',
        ('printer1.test',)).fetchone()[0]
    conn.close()
    assert net_v6 == '2001:db8:77:401::/64'
    assert host_v6 == '2001:db8:77:401::9'


def test_rebuild_diff_reports_changes(tmp_path, capsys):
    build(tmp_path, DHCP_IPPLAN)
    changed = DHCP_IPPLAN.replace(
        '#$ printer1.test\t10.77.1.9\tpkg=base;mac=aa:bb:cc:dd:ee:ff',
        '#$ printer2.test\t10.77.1.10\tpkg=base')
    build(tmp_path, changed)
    out = capsys.readouterr().out
    assert 'host: +1 -1' in out
    assert '+ ' in out and 'printer2.test' in out
    assert '- ' in out and 'printer1.test' in out


def test_identical_rebuild_diff_is_silent(tmp_path, capsys):
    build(tmp_path, DHCP_IPPLAN)
    build(tmp_path, DHCP_IPPLAN)
    out = capsys.readouterr().out
    assert '+ ' not in out and '- ' not in out


def test_format_diff_handles_null_columns():
    """Rows with NULLs must not crash the sort (None vs str)."""
    tool = load_ipplan2db()
    lines = tool.format_diff(
        {'host': {('a', None), ('b', '1')}},
        {'host': {('a', None), ('c', None)}})
    assert lines[0] == 'host: +1 -1'
