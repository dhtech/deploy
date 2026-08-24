# Regression corpus: the REAL prod ipplan files (colo/sto2,
# colo/bogal, dhb26/core) parsed by the retired gen-2 engine and by
# the new ipplan2db parser must produce IDENTICAL topology tables
# (node/host/network/option) - the golden old-vs-new acceptance from
# the pipeline plan. Skipped where the corpus checkouts are absent.

import importlib.util
import os
import sqlite3
import sys

import pytest

from conftest import load_ipplan2db

SVN = os.path.expanduser('~/repos/dh/svn')
GEN2 = os.path.expanduser('~/repos/dh/local/ipplan2sqlite')
FILES = [
    os.path.join(SVN, 'allevents', 'colo', 'sto2', 'ipplan'),
    os.path.join(SVN, 'allevents', 'colo', 'bogal', 'ipplan'),
    os.path.join(SVN, 'dhb26', 'core', 'ipplan'),
]

pytestmark = pytest.mark.skipif(
    not (os.path.isdir(GEN2) and all(os.path.exists(f) for f in FILES)),
    reason='prod corpus / gen-2 checkout not present')


def gen2_topology():
    """Parse the corpus with the ORIGINAL engine. Its package lives at
    lib/, a name conftest already claims - load it under gen2lib."""
    if 'gen2lib' not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            'gen2lib', os.path.join(GEN2, 'lib', '__init__.py'),
            submodule_search_locations=[os.path.join(GEN2, 'lib')])
        module = importlib.util.module_from_spec(spec)
        sys.modules['gen2lib'] = module
        spec.loader.exec_module(module)
    import importlib as _importlib
    tables = _importlib.import_module('gen2lib.tables')
    processor = _importlib.import_module('gen2lib.processor')
    processor._current_domain = None
    processor._current_v6_base = None
    processor._domains = set()

    conn = sqlite3.connect(':memory:')
    tables.create(conn)
    c = conn.cursor()
    for path in FILES:
        with open(path) as f:
            processor.parse(f.readlines(), c)
    return conn


def gen3_topology():
    loader = load_ipplan2db()
    model = loader.parse_all(FILES)
    assert not model.errors, model.errors
    conn = sqlite3.connect(':memory:')
    c = conn.cursor()
    loader.create_schema(c)
    loader.emit_topology(model, c)
    return conn, loader, model


def rows(conn, query):
    return conn.execute(query).fetchall()


def test_topology_matches_gen2_byte_for_byte():
    old = gen2_topology()
    new, _, _ = gen3_topology()
    for query in (
            'SELECT * FROM network ORDER BY node_id',
            'SELECT * FROM host ORDER BY node_id',
            'SELECT node_id, name, value FROM option '
            'ORDER BY node_id, name, value'):
        assert rows(old, query) == rows(new, query), query


def test_corpus_scale_sanity():
    _, _, model = gen3_topology()
    # ~1400 corpus lines: two colo sites and a full event
    assert len(model.networks) > 100
    assert len(model.hosts) > 300
    domains = {n.name.split('@')[0] for n in model.networks}
    assert {'STO2', 'BOGAL', 'EVENT'} <= domains


def test_corpus_passes_validation():
    loader = load_ipplan2db()
    model = loader.parse_all(FILES)
    loader.validate(model)
