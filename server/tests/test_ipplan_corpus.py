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
    # gen-3 rejects the whitespace warts prod tolerated (trailing
    # space in a field / bare tab at end of line, dhb26+sto2 rows);
    # nothing else in the corpus may fail
    real = [e for e in model.errors
            if 'trailing whitespace' not in e]
    assert not real, real
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
    """The corpus fails gen-3 on EXACTLY its known whitespace warts
    (prod-tolerated, gen-3-rejected) and nothing else."""
    loader = load_ipplan2db()
    model = loader.parse_all(FILES)
    try:
        loader.validate(model)
        errors = []
    except loader.BuildError as error:
        errors = error.errors
    assert errors, 'corpus warts vanished - tighten this test'
    others = [e for e in errors if 'trailing whitespace' not in e]
    assert not others, others


def test_host_outside_its_network_section_fails(tmp_path):
    # the pve-under-OUTSIDE incident: a host line filed under the
    # wrong network compiles into wrong attachment (no v6, wrong
    # consumers) - the gate must reject it instead
    root = tmp_path / 'svn'
    site = root / 'allevents' / 'colo' / 'colo'
    site.mkdir(parents=True)
    (site / 'ipplan').write_text(
        '#@ IPV4-COLO-NET\t10.200.0.0/24\n'
        'COLO\t10.200.0.0/24\tR1\t200\tnone\n'
        'OTHER\t10.9.9.0/24\tR1\t9\tnone\n'
        '#$ stray.test\t10.200.0.60\tos=debian;pkg=base\n')
    (root / 'currentevent').write_text(
        'currentevent=test\napt_freeze=false\nchange_freeze=false\n')
    mpath = tmp_path / 'manifest.yaml'
    mpath.write_text('packages:\n  base: {}\n')
    tool = load_ipplan2db()
    with pytest.raises(tool.BuildError) as excinfo:
        tool.build(str(root), [str(mpath)], str(tmp_path / 'i.db'))
    assert 'outside its network section' in str(excinfo.value)
