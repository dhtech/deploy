# Test wiring: the CGIs import "lib.metadata" (deployment layout under
# /var/www/deploy); map that to server/libdhdeploy and put server/backend
# on the path so "modules.<pkg>" resolves like in production.

import importlib
import importlib.util
import os
import sqlite3
import sys
import types

import pytest
import yaml

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..', 'backend'))

_lib = types.ModuleType('lib')
sys.path.insert(0, os.path.join(HERE, '..'))
_lib.metadata = importlib.import_module('libdhdeploy.metadata')
_lib.flows = importlib.import_module('libdhdeploy.flows')
sys.modules['lib'] = _lib
sys.modules['lib.metadata'] = _lib.metadata
sys.modules['lib.flows'] = _lib.flows


@pytest.fixture
def ipplan(tmp_path, monkeypatch, manifest):
    """A seeded throwaway ipplan.db mirroring the lab topology."""
    db = tmp_path / 'ipplan.db'
    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE network (node_id INTEGER PRIMARY KEY, name TEXT,
            vlan INTEGER, ipv4_gateway_txt TEXT, ipv4_netmask_txt TEXT,
            ipv4_netmask_dec INTEGER, ipv6_gateway_txt TEXT,
            ipv6_netmask_txt TEXT);
        CREATE TABLE host (node_id INTEGER PRIMARY KEY, name TEXT,
            ipv4_addr_txt TEXT, ipv6_addr_txt TEXT, network_id INTEGER);
        CREATE TABLE option (node_id INTEGER, name TEXT, value TEXT);
        CREATE TABLE meta_data (name TEXT, value TEXT);
    ''')
    c.execute("INSERT INTO network VALUES (1, 'colo@prod', 200, "
              "'10.200.0.2', '255.255.255.0', 24, NULL, NULL)")
    c.execute("INSERT INTO network VALUES (2, 'EVENT@prod', 300, "
              "'10.201.0.2', '255.255.255.0', 24, NULL, NULL)")
    hosts = [
        (13, 'provision1.test', '10.200.0.2', 1),
        (11, 'vault1.test', '10.200.0.61', 1),
        (12, 'puppet1.test', '10.200.0.62', 1),
        (14, 'directory1.test', '10.200.0.63', 1),
        (16, 'ldap1-master.test', '10.200.0.65', 1),
        (17, 'ldap2-master.test', '10.200.0.66', 1),
        (18, 'ldap1.test', '10.200.0.67', 1),
        (10, 'web1.test', '10.200.0.60', 1),
        (20, 'pve1.test', '10.10.10.1', 1),
        (30, 'evtbox1.test', '10.201.0.60', 2),
    ]
    c.executemany('INSERT INTO host VALUES (?, ?, ?, NULL, ?)', hosts)
    c.executemany('INSERT INTO option VALUES (?, ?, ?)', [
        (13, 'pkg', 'jumpgate'),
        (11, 'pkg', 'vault'), (11, 'webname', 'vault.dh.example'),
        (12, 'pkg', 'puppetserver'),
        (14, 'pkg', 'lam'),
        (16, 'pkg', 'ldap(role=master,id=1)'),
        (17, 'pkg', 'ldap(role=master,id=2)'),
        (18, 'pkg', 'ldap'),
        (10, 'pkg', 'base'), (10, 'pkg', 'web(port=80)'),
        (10, 'pkg', 'login'),
        (20, 'pkg', 'pve'), (20, 'webname', 'pve.dh.example'),
        (30, 'pkg', 'login'),
    ])
    conn.commit()
    conn.close()
    monkeypatch.setattr(sys.modules['lib.metadata'], 'DB_FILE', str(db))
    # compile the fixture manifest with the REAL loader so the db has
    # service/flow/package_spec and precomputed firewall_rule tables
    mpath = tmp_path / 'manifest.yaml'
    mpath.write_text(yaml.safe_dump(manifest))
    tool = os.path.join(HERE, '..', '..', 'utils', 'manifest2db')
    spec = importlib.util.spec_from_loader(
        'manifest2db', importlib.machinery.SourceFileLoader('manifest2db',
                                                            tool))
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    loader.load(str(mpath), str(db))
    return db


@pytest.fixture
def manifest():
    return {
        'globals': {'acme': {'email': 'a@example', 'server': 'https://acme'}},
        'flows': ['ldaprepl', 'ldapwrite'],
        'services': {
            'ldaps': {'destport': ['636/tcp']},
        },
        'packages': {
            'base': {'puppet': {'classes': ['dhfirewall']}},
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
