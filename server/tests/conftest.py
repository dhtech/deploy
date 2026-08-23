# Test wiring: the CGIs import "lib.metadata" (deployment layout under
# /var/www/deploy); map that to server/libdhdeploy and put server/backend
# on the path so "modules.<pkg>" resolves like in production.

import importlib
import os
import sqlite3
import sys
import types

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..', 'backend'))

_lib = types.ModuleType('lib')
sys.path.insert(0, os.path.join(HERE, '..'))
_lib.metadata = importlib.import_module('libdhdeploy.metadata')
sys.modules['lib'] = _lib
sys.modules['lib.metadata'] = _lib.metadata


@pytest.fixture
def ipplan(tmp_path, monkeypatch):
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
    hosts = [
        (13, 'provision1.test', '10.200.0.2'),
        (11, 'vault1.test', '10.200.0.61'),
        (12, 'puppet1.test', '10.200.0.62'),
        (16, 'ldap1-master.test', '10.200.0.65'),
        (17, 'ldap2-master.test', '10.200.0.66'),
        (18, 'ldap1.test', '10.200.0.67'),
        (10, 'web1.test', '10.200.0.60'),
        (20, 'pve1.test', '10.10.10.1'),
    ]
    c.executemany('INSERT INTO host VALUES (?, ?, ?, NULL, 1)', hosts)
    c.executemany('INSERT INTO option VALUES (?, ?, ?)', [
        (13, 'pkg', 'jumpgate'),
        (11, 'pkg', 'vault'), (11, 'webname', 'vault.dh.example'),
        (12, 'pkg', 'puppetserver'),
        (16, 'pkg', 'ldap(role=master,id=1)'),
        (17, 'pkg', 'ldap(role=master,id=2)'),
        (18, 'pkg', 'ldap'),
        (10, 'pkg', 'base'), (10, 'pkg', 'web(port=80)'),
        (20, 'pkg', 'pve'), (20, 'webname', 'pve.dh.example'),
    ])
    conn.commit()
    conn.close()
    monkeypatch.setattr(sys.modules['lib.metadata'], 'DB_FILE', str(db))
    return db


@pytest.fixture
def manifest():
    return {
        'globals': {'acme': {'email': 'a@example', 'server': 'https://acme'}},
        'packages': {
            'base': {'puppet': {'classes': ['dhfirewall']}},
            'jumpgate': {},
            'web': {'puppet': {'classes': ['dhfirewall']}},
            'vault': {'puppet': {'classes': [
                'dhfirewall', 'dhacme::cert', 'dhnginx::vault']}},
            'puppetserver': {'puppet': {'classes': [
                'dhfirewall', 'dhacme::issuer']}},
            'ldap': {'puppet': {'classes': ['dhfirewall', 'dhldap::server']}},
            'pve': {'puppet': {'classes': ['dhacme::cert', 'dhpve']}},
        },
    }
