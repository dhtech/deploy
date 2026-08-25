# Router plan P2: the flow engine grows network-as-client (client= on
# a network line pairs like a host client, CIDR as the rule source)
# and scoped UDP (123/udp etc. no longer dropped on the floor).

import pathlib
import sqlite3
import sys

import yaml

from conftest import load_ipplan2db

TOOL = load_ipplan2db()

IPPLAN = '''\
#@ IPV4-COLO-NET\t10.200.0.0/24
COLO\t10.200.0.0/24\tR1\t200\tnat
#$ router.colo.test\t10.200.0.1\tos=debian;pkg=router,resolver,ntp
#$ web1.colo.test\t10.200.0.60\tos=debian;pkg=base
DEPLOY\t10.100.0.0/24\tR1\t100\tnat;client=resolver-dns,aptcache
'''

MANIFEST = {
    'services': {
        'dns': {'destport': ['53/udp', '53/tcp']},
        'ntp': {'destport': ['123/udp']},
        'aptcache': {'destport': ['3142/tcp']},
    },
    'flows': ['resolver'],
    'default': {'debian': ['timesync']},
    'packages': {
        'base': {'server': ['aptcache']},
        'router': {},
        'resolver': {'server': ['resolver-dns', 'dns']},
        'ntp': {'server': ['ntp']},
        'timesync': {'client': ['ntp']},
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


def rules_to(db, hostname):
    sys.modules['lib.metadata'].DB_FILE = str(db)
    return sys.modules['lib.metadata'].firewall_rules_to(hostname)


def test_network_client_gets_cidr_scoped_rule(tmp_path):
    """The DEPLOY network's client=resolver-dns pairs with the
    router's resolver server spec: the rule source is the CIDR."""
    db = build(tmp_path)
    rules = rules_to(db, 'router.colo.test')
    assert '10.100.0.0/24' in rules['udp'][53]
    assert '10.100.0.0/24' in rules['tcp'][53]


def test_network_client_default_flow_is_its_site(tmp_path):
    """client=aptcache (no flow prefix) pairs on the network's SITE
    (colo) - web1's base pkg serves aptcache on the default flow."""
    db = build(tmp_path)
    rules = rules_to(db, 'web1.colo.test')
    assert rules['tcp'][3142] == ['10.100.0.0/24']


def test_udp_scoped_rules_for_hosts(tmp_path):
    """123/udp flows from host clients (timesync default pkg) land in
    the udp map with host-IP sources."""
    db = build(tmp_path)
    rules = rules_to(db, 'router.colo.test')
    srcs = rules['udp'][123]
    assert '10.200.0.60' in srcs          # web1, timesync client
    assert '10.100.0.0/24' not in srcs    # DEPLOY declares no ntp


def test_tcp_only_hosts_unchanged_shape(tmp_path):
    """No udp key when a host's flows are tcp-only (ENC regression
    guard: existing hosts' params keep their exact shape)."""
    db = build(tmp_path)
    rules = rules_to(db, 'web1.colo.test')
    assert set(rules.keys()) == {'tcp'}
