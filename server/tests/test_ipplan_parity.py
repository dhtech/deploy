# Gen-2 compiler parity locks (public dhtech/ipplan2sqlite is the
# reference): the DHCP grammar (dhcp/resv=/shnet=/dhcp-*/mac=) must
# pass through to the option table so sqlite2dhcp-scope-style
# consumers can compute pools; IPv6 must be COMPUTED (master v6 base
# -> per-vlan /64 -> host addresses), and the build-to-build diff
# must report exactly what changed.

import sqlite3

import yaml

from conftest import load_ipplan2db

# one shared load: each load_ipplan2db() call creates a fresh module
# with its OWN BuildError class - pytest.raises must see the same one
TOOL = load_ipplan2db()

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
    TOOL.build(str(root), [str(mpath)], str(db))
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
    tool = TOOL
    lines = tool.format_diff(
        {'host': {('a', None), ('b', '1')}},
        {'host': {('a', None), ('c', None)}})
    assert lines[0] == 'host: +1 -1'


ROUTER_IPPLAN = """\
#@ IPV4-COLO-NET\t10.200.0.0/24
COLO\t10.200.0.0/24\tR1\t200\tnat
#$ router.colo.test\t10.200.0.1\tos=debian;pkg=router,resolver,ntp
#$ vault1.colo.test\t10.200.0.61\tos=debian;pkg=base;expose=443:443
#$ doc1.colo.test\t10.200.0.64\tos=debian;pkg=base;expose=445:443
DEPLOY\t10.100.0.0/24\tR1\t100\tnat;gw=10.100.0.2
"""


def test_router_grammar_compiles(tmp_path):
    """P1 grammar: nat network flag, expose= host pairs, the router
    host line and the DEPLOY network all land in the db."""
    db = build(tmp_path, ROUTER_IPPLAN)
    assert options_of(db, 'COLO@COLO', 'network')['nat'] == '1'
    assert options_of(db, 'COLO@DEPLOY', 'network')['nat'] == '1'
    assert options_of(db, 'vault1.colo.test', 'host')['expose'] == \
        '443:443'
    pkgs = options_of(db, 'router.colo.test', 'host')
    import sqlite3
    conn = sqlite3.connect(db)
    rows = [r[0] for r in conn.execute(
        'SELECT o.value FROM option o, host h WHERE o.node_id = '
        'h.node_id AND h.name = ? AND o.name = ?',
        ('router.colo.test', 'pkg'))]
    conn.close()
    assert {'router', 'resolver', 'ntp'} <= set(rows)


def test_gateway_computed_dot1_without_override(tmp_path):
    """With no gw= override the computed gateway is .1 - the router.
    (COLO keeps its gw=.2 override in the LIVE plan until the P5
    cutover; this locks what removing it will do.)"""
    import sqlite3
    db = build(tmp_path, ROUTER_IPPLAN)
    conn = sqlite3.connect(db)
    gw = dict(conn.execute(
        'SELECT name, ipv4_gateway_txt FROM network'))
    conn.close()
    assert gw['COLO@COLO'] == '10.200.0.1'
    assert gw['COLO@DEPLOY'] == '10.100.0.2'   # override honored


def build_expect_error(tmp_path, text):
    import pytest
    with pytest.raises(TOOL.BuildError) as err:
        build(tmp_path, text)
    return '\n'.join(err.value.errors)


def test_expose_malformed_rejected(tmp_path):
    errors = build_expect_error(tmp_path, ROUTER_IPPLAN.replace(
        'expose=443:443', 'expose=443'))
    assert 'bad expose' in errors and 'EXTPORT:INTPORT' in errors


def test_expose_port_out_of_range_rejected(tmp_path):
    errors = build_expect_error(tmp_path, ROUTER_IPPLAN.replace(
        'expose=443:443', 'expose=70000:443'))
    assert 'bad expose' in errors


def test_expose_duplicate_external_port_rejected(tmp_path):
    errors = build_expect_error(tmp_path, ROUTER_IPPLAN.replace(
        'expose=445:443', 'expose=443:8443'))
    assert 'expose port 443 already used in site COLO' in errors


def test_expose_multi_pair_and_cross_checks(tmp_path):
    db = build(tmp_path, ROUTER_IPPLAN.replace(
        'expose=443:443', 'expose=443:443,8200:8200'))
    import sqlite3
    conn = sqlite3.connect(db)
    rows = sorted(r[0] for r in conn.execute(
        'SELECT o.value FROM option o, host h WHERE o.node_id = '
        'h.node_id AND h.name = ? AND o.name = ?',
        ('vault1.colo.test', 'expose')))
    conn.close()
    assert rows == ['443:443', '8200:8200']
