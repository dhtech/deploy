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
DEPLOY\t10.100.0.0/24\tR1\t100\tnat;native;client=colo-ldaps
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
    },
}


def build(tmp_path):
    root = tmp_path / 'svn'
    site = root / 'allevents' / 'colo' / 'colo'
    site.mkdir(parents=True, exist_ok=True)
    (site / 'ipplan').write_text(IPPLAN)
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
                     .replace('DEPLOY\t10.100.0.0/24\tR1\t100\tnat;native;client=colo-ldaps',
                              'DEPLOY\t10.100.0.0/24\tR1\t100\tnat;native;gw=10.100.0.2;client=colo-ldaps')
        params = router_params(build(tmp_path))
        assert params == {}
    finally:
        IPPLAN = kept


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
