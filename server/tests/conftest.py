# Test wiring: the CGIs import "lib.metadata" (deployment layout under
# /var/www/deploy); map that to server/libdhdeploy and put server/backend
# on the path so "modules.<pkg>" resolves like in production.

import importlib
import importlib.util
import os
import sys
import types

import pytest
import yaml

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..', 'backend'))

_lib = types.ModuleType('lib')
# libdhdeploy's only home is the ipplan2db sibling checkout
sys.path.insert(0, os.path.expanduser('~/repos/dh/ipplan2db'))
_lib.metadata = importlib.import_module('libdhdeploy.metadata')
_lib.flows = importlib.import_module('libdhdeploy.flows')
sys.modules['lib'] = _lib
sys.modules['lib.metadata'] = _lib.metadata
sys.modules['lib.flows'] = _lib.flows


IPPLAN_COLO = '''\
#@ IPV4-COLO-NET\t10.200.0.0/24
COLO\t10.200.0.0/24\tR1\t200\tgw=10.200.0.2
#$ deploy.test\t10.200.0.2\tos=debian;pkg=jumpgate
#$ web1.test\t10.200.0.60\tos=debian;pkg=base,web(port=80)
#$ vault1.test\t10.200.0.61\tos=debian;pkg=vault,-login;webname=vault.dh.example
#$ puppet1.test\t10.200.0.62\tos=debian;pkg=puppetserver
#$ directory1.test\t10.200.0.63\tos=debian;pkg=lam
#$ ldap1-master.test\t10.200.0.65\tos=debian;pkg=ldap(role=master,id=1)
#$ ldap2-master.test\t10.200.0.66\tos=debian;pkg=ldap(role=master,id=2)
#$ ldap1.test\t10.200.0.67\tos=debian;pkg=ldap
MGMT\t10.10.10.0/24\tR1\t10\tgw=10.10.10.1
#$ pve1.test\t10.10.10.1\tpkg=pve;webname=pve.dh.example
'''

IPPLAN_EVENT = '''\
#@ IPV4-EVENT-NET\t10.201.0.0/24
EVENT\t10.201.0.0/24\tR1\t300\tgw=10.201.0.2
#$ evtbox1.test\t10.201.0.60\tos=debian;pkg=login
'''


def load_ipplan2db():
    # the compiler is its own repo (dhtech/ipplan2db), cloned as a
    # sibling checkout - same pattern as the prod-corpus checkouts
    tool = os.path.expanduser('~/repos/dh/ipplan2db/ipplan2db')
    if not os.path.exists(tool):
        raise RuntimeError(
            'clone dhtech/ipplan2db next to deploy: %s missing' % tool)
    spec = importlib.util.spec_from_loader(
        'ipplan2db', importlib.machinery.SourceFileLoader('ipplan2db',
                                                          tool))
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    return loader


@pytest.fixture
def ipplan(tmp_path, monkeypatch, manifest):
    """A throwaway ipplan.db built by the REAL compiler from a small
    ipplan text tree mirroring the lab topology."""
    root = tmp_path / 'svn'
    site = root / 'allevents' / 'colo' / 'colo'
    event = root / 'test' / 'core'
    site.mkdir(parents=True)
    event.mkdir(parents=True)
    tool = load_ipplan2db()
    # fixtures are written single-tab for readability; the compiler
    # enforces prod's column discipline, so canonicalize on write
    (site / 'ipplan').write_text(tool.reformat(IPPLAN_COLO))
    (event / 'ipplan').write_text(tool.reformat(IPPLAN_EVENT))
    (root / 'currentevent').write_text(
        'currentevent=test\napt_freeze=false\nchange_freeze=false\n')
    mpath = tmp_path / 'manifest.yaml'
    mpath.write_text(yaml.safe_dump(manifest))
    db = tmp_path / 'ipplan.db'
    monkeypatch.setattr(sys.modules['lib.metadata'], 'DB_FILE', str(db))
    tool.build(str(root), [str(mpath)], str(db))
    return db


@pytest.fixture
def manifest():
    return {
        'globals': {'acme': {'email': 'a@example', 'server': 'https://acme'}},
        'flows': ['ldaprepl', 'ldapwrite'],
        'default': {'debian': ['login', 'node', 'managed'],
                    'pve': ['login', 'node', 'managed']},
        'services': {
            'ldaps': {'destport': ['636/tcp']},
        },
        'packages': {
            'base': {'puppet': {'classes': ['dhfirewall']}},
            'node': {},
            'managed': {},
            'jumpgate': {},
            'web': {'puppet': {'classes': ['dhfirewall']}},
            'login': {'client': ['ldaps'],
                      'puppet': {'classes': ['dhlogin']}},
            'vault': {'client': ['ldaps'],
                      'puppet': {'classes': [
                          'dhfirewall', 'dhacme::cert', 'dhnginx::vault']}},
            'puppetserver': {'puppet': {'classes': [
                'dhfirewall', 'dhacme::issuer']}},
            'lam': {'client': ['ldapwrite-ldaps'],
                    'puppet': {'classes': [
                        'dhfirewall', 'dhacme::cert', 'dhnginx::lam',
                        'dhlam']}},
            'ldap': {'server': ['ldaps'],
                     'client': ['ldaprepl-ldaps'],
                     'puppet': {'classes': ['dhfirewall',
                                            'dhldap::server']}},
            'ldap(role=master)': {'server': ['ldaprepl-ldaps',
                                             'ldapwrite-ldaps']},
            'pve': {'client': ['ldaps'],
                    'puppet': {'classes': ['dhacme::cert', 'dhpve'],
                               'params': {'dhpve': {
                                   'admin_group_dn': 'cn=g,dc=x',
                                   'admin_role': 'Administrator'}}}},
        },
    }
