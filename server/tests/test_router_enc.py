# Router plan P3: the router.py ENC generator. Everything derived
# from ipplan: routed networks (gateway ownership), masquerade list
# (nat), DNAT (expose=), forward permits (flow pairs crossing its
# networks). While the gw= overrides still point elsewhere the
# generator emits {} - the ruleset arrives via the ENC at the exact
# moment the data makes the host the router (the P5 flip).

import pathlib
import sys

import yaml

from conftest import load_ipplan2db

TOOL = load_ipplan2db()

# the what-if plan: gw= overrides REMOVED (post-P5 shape), computed
# gateways at .1 = the router
IPPLAN = '''\
#@ IPV4-COLO-NET\t10.200.0.0/24
COLO\t10.200.0.0/24\tR1\t200\tnat
#$ router.colo.test\t10.200.0.1\tos=debian;pkg=router,resolver,ntp;addr=10.100.0.1,10.0.2.17
#$ vault1.colo.test\t10.200.0.61\tos=debian;pkg=vault;expose=443:443,8200:8200
#$ jumpgate1.colo.test\t10.200.0.69\tos=debian;pkg=base;expose=2022:22
DEPLOY\t10.100.0.0/24\tR1\t100\tnat;native;deploy;client=colo-ldaps
MGMT\t10.10.10.0/24\tR1\t10\tothernet;gw=10.10.10.254
OUTSIDE\t10.0.2.0/24\t-\t-\tothernet;gw=10.0.2.2
'''

MANIFEST = {
    'services': {'ldaps': {'destport': ['636/tcp']}},
    'packages': {
        'base': {},
        'router': {},
        'resolver': {},
        'ntp': {},
        'vault': {'server': ['ldaps']},
        'bgp': {},
    },
}


def build(tmp_path):
    root = tmp_path / 'svn'
    site = root / 'allevents' / 'colo' / 'colo'
    site.mkdir(parents=True, exist_ok=True)
    (site / 'ipplan').write_text(TOOL.reformat(IPPLAN))
    (root / 'currentevent').write_text(
        'currentevent=none\napt_freeze=false\nchange_freeze=false\n')
    mpath = tmp_path / 'manifest.yaml'
    mpath.write_text(yaml.safe_dump(MANIFEST))
    db = tmp_path / 'ipplan.db'
    TOOL.build(str(root), [str(mpath)], str(db))
    return db


def router_params(db):
    sys.modules['lib.metadata'].DB_FILE = str(db)
    from modules import router
    return router.generate('router.colo.test', {}, MANIFEST)


def test_router_owns_gateway_networks_only(tmp_path):
    """COLO + DEPLOY (computed .1 gateways) are the router's; MGMT's
    gw= points elsewhere and stays out."""
    params = router_params(build(tmp_path))
    nat = params['dhfirewall']['router']['nat_networks']
    assert nat == ['10.100.0.0/24', '10.200.0.0/24']


def test_dnat_from_expose(tmp_path):
    params = router_params(build(tmp_path))
    dnat = params['dhfirewall']['router']['dnat']
    assert {'port': 443, 'to': '10.200.0.61:443'} in dnat
    assert {'port': 8200, 'to': '10.200.0.61:8200'} in dnat
    assert {'port': 2022, 'to': '10.200.0.69:22'} in dnat
    assert [e['port'] for e in dnat] == sorted(e['port'] for e in dnat)


def test_forward_rules_cross_network_only(tmp_path):
    """DEPLOY's client=colo-ldaps pairs with vault1's ldaps server:
    a DEPLOY->COLO crossing = a forward permit. Same-network flows
    and flows touching the router itself emit nothing."""
    params = router_params(build(tmp_path))
    fwd = params['dhfirewall']['router']['forward']
    assert {'saddr': '10.100.0.0/24', 'daddr': '10.200.0.61',
            'proto': 'tcp', 'port': 636} in fwd
    assert all(r['daddr'] != '10.200.0.1' for r in fwd)


def test_not_the_router_yet_emits_nothing(tmp_path):
    """With the CURRENT lab overrides (gw=.2 on everything) the host
    owns no gateways: generate() returns {} - the ruleset arrives
    exactly when the P5 data flip makes it the router."""
    import re
    global IPPLAN
    kept = IPPLAN
    try:
        IPPLAN = kept.replace('COLO\t10.200.0.0/24\tR1\t200\tnat',
                              'COLO\t10.200.0.0/24\tR1\t200\tnat;gw=10.200.0.2') \
                     .replace('DEPLOY\t10.100.0.0/24\tR1\t100\tnat;native;deploy;client=colo-ldaps',
                              'DEPLOY\t10.100.0.0/24\tR1\t100\tnat;native;gw=10.100.0.2;client=colo-ldaps')
        params = router_params(build(tmp_path))
        assert params == {}
    finally:
        IPPLAN = kept


def test_bird_from_asn_token(tmp_path):
    """pkg=router(asn=N) switches BIRD on: announce = the routed
    networks (v4 + derived v6, MGMT's foreign gw excluded), peers
    from pkg=bgp(asn=M) hosts inside them (the LINK convention)."""
    global IPPLAN
    kept = IPPLAN
    try:
        IPPLAN = kept.replace(
            '#@ IPV4-COLO-NET\t10.200.0.0/24',
            '#@ IPV4-COLO-NET\t10.200.0.0/24\n'
            '#@ IPV6-COLO-NET\tfdd8:1::/48').replace(
            '#$ jumpgate1.colo.test\t10.200.0.69\t'
            'os=debian;pkg=base;expose=2022:22',
            '#$ jumpgate1.colo.test\t10.200.0.69\t'
            'os=debian;pkg=base;expose=2022:22\n'
            '#$ upstream.colo.test\t10.200.0.9\t'
            'os=ios;pkg=-default,bgp(asn=64900);nodns')
        db = build(tmp_path)
        sys.modules['lib.metadata'].DB_FILE = str(db)
        from modules import router
        params = router.generate(
            'router.colo.test', {'asn': '65200'}, MANIFEST)
        bird = params['dhbird']
        assert bird['asn'] == 65200
        assert bird['router_id'] == '10.200.0.1'
        assert bird['networks4'] == ['10.200.0.0/24']  # deploy net site-local
        assert bird['networks6'] == ['fdd8:1:200::/64']
        assert bird['peers'] == [{'ip': '10.200.0.9', 'asn': 64900}]
    finally:
        IPPLAN = kept


def test_colovpn_from_wg_net_and_uplink_peer(tmp_path):
    """P5: a wg= link net + an uplink=colo event router = the wg
    listener (address .1, roaming peer with its site nets) plus the
    bgp peer, the 179 scoping, and the vpn forward permits. The
    egress=colo token flips default export and masquerade."""
    root = tmp_path / 'svn'
    site = root / 'allevents' / 'colo' / 'colo'
    event = root / 'ev' / 'core'
    site.mkdir(parents=True)
    event.mkdir(parents=True)
    (site / 'ipplan').write_text(TOOL.reformat(
        IPPLAN + 'COLOVPN\t172.29.16.0/24\tR1\t-\tothernet;wg=51820\n'))
    (event / 'ipplan').write_text(TOOL.reformat(
        '#@ IPV4-EVENT-NET\t10.201.0.0/24\n'
        'EVENT\t10.201.0.0/24\tR1\t300\tnat\n'
        '#$ router.ev.test\t10.201.0.1\tos=debian;'
        'pkg=router(asn=65201,uplink=colo,egress=colo);'
        'addr=172.29.16.11\n'))
    (root / 'currentevent').write_text(
        'currentevent=ev\napt_freeze=false\nchange_freeze=false\n')
    mpath = tmp_path / 'manifest.yaml'
    mpath.write_text(yaml.safe_dump(MANIFEST))
    db = tmp_path / 'ipplan.db'
    TOOL.build(str(root), [str(mpath)], str(db))
    sys.modules['lib.metadata'].DB_FILE = str(db)
    from modules import router
    params = router.generate(
        'router.colo.test', {'asn': '65200'}, MANIFEST)
    vpn = params['dhcolovpn']
    assert vpn['address'] == '172.29.16.1/24'
    assert vpn['port'] == 51820
    assert vpn['peers'] == [{'site': 'ev', 'tunnel_ip': '172.29.16.11',
                             'networks': ['10.201.0.0/24']}]
    fw = params['dhfirewall']
    assert fw['open_udp'] == [51820]
    assert fw['open_tcp_scoped'] == {179: ['172.29.16.0/24']}
    assert fw['router']['vpn_networks'] == ['10.201.0.0/24']
    # routable only: no deploy net (and MGMT's gw= keeps it unrouted
    # in this fixture)
    assert fw['router']['vpn_site_networks'] == ['10.200.0.0/24']
    assert fw['router']['vpn_egress_networks'] == ['10.201.0.0/24']
    bird = params['dhbird']
    assert {'ip': '172.29.16.11', 'asn': 65201,
            'export_default': True} in bird['peers']
    assert bird['default_export'] is True
    # egress=local: no default, no masquerade - prefixes only
    (event / 'ipplan').write_text(TOOL.reformat(
        '#@ IPV4-EVENT-NET\t10.201.0.0/24\n'
        'EVENT\t10.201.0.0/24\tR1\t300\tnat\n'
        '#$ router.ev.test\t10.201.0.1\tos=debian;'
        'pkg=router(asn=65201,uplink=colo,egress=local);'
        'addr=172.29.16.11\n'))
    TOOL.build(str(root), [str(mpath)], str(db))
    params = router.generate(
        'router.colo.test', {'asn': '65200'}, MANIFEST)
    assert 'vpn_egress_networks' not in params['dhfirewall']['router']
    assert params['dhbird']['default_export'] is False


def test_no_asn_no_bird(tmp_path):
    """Without the asn token the router stays daemonless: firewall
    ruleset only, no dhbird class."""
    params = router_params(build(tmp_path))
    assert 'dhbird' not in params


def test_interfaces_router_config(tmp_path):
    """interfaces.py renders the trunk shape: native DEPLOY leg
    untagged on the trunk, COLO as trunk.200, the OUTSIDE leg on the
    second NIC carrying the only default route."""
    import importlib.machinery
    import importlib.util
    import os
    db = build(tmp_path)
    sys.modules['lib.metadata'].DB_FILE = str(db)
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'backend', 'debian', 'interfaces.py')
    spec = importlib.util.spec_from_loader(
        'dh_interfaces', importlib.machinery.SourceFileLoader(
            'dh_interfaces', tool))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.is_router('router.colo.test')
    assert not mod.is_router('vault1.colo.test')
    text = mod.router_config('router.colo.test', ['ens18', 'ens19'])
    assert 'auto ens18\niface ens18 inet static' in text
    assert '\taddress 10.100.0.1' in text
    assert 'auto ens18.200\niface ens18.200 inet static' in text
    assert '\tvlan-raw-device ens18' in text
    assert '\taddress 10.200.0.1' in text
    assert 'auto ens19\niface ens19 inet static' in text
    assert '\taddress 10.0.2.17' in text
    assert text.count('gateway') == 1
    assert '\tgateway 10.0.2.2' in text
