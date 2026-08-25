# The statistics page generator (utils/ipplan-stats): /ipplan/'s
# index.html on the doc server, rendered from a compiled db. Locked
# here: real counts, escaped content, freeze badges, recent-changes
# section from ipplan-r<rev>.diff files.

import importlib.machinery
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', '..', 'utils', 'ipplan-stats')


def load_stats():
    spec = importlib.util.spec_from_loader(
        'ipplan_stats', importlib.machinery.SourceFileLoader(
            'ipplan_stats', TOOL))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_page_renders_lab_topology(ipplan):
    page = load_stats().render(str(ipplan))
    assert page.startswith('<!doctype html>')
    assert 'ipplan <small>r0</small>' in page
    assert 'event test' in page
    assert 'COLO@COLO' in page
    assert 'deploy.colo.notproduction.net' not in page  # hosts aggregate
    assert '<span>hosts</span>' in page
    assert 'frozen' not in page


def test_recent_changes_from_diff_dir(ipplan, tmp_path):
    (tmp_path / 'ipplan-r7.diff').write_text('host: +1 -0\n  + x\n')
    (tmp_path / 'ipplan-r10.diff').write_text('host: +0 -1\n  - <y>\n')
    page = load_stats().render(str(ipplan), str(tmp_path))
    assert 'Recent changes' in page
    # newest first and open; html escaped
    assert page.index('r10') < page.index('r7')
    assert '&lt;y&gt;' in page


def test_no_diff_dir_no_changes_section(ipplan):
    assert 'Recent changes' not in load_stats().render(str(ipplan))


def test_network_holes(ipplan):
    """Unallocated ranges inside networks: the lab colo /24 has hosts
    at .2 and .60-.69, so .3-.59 must show as a free range."""
    page = load_stats().render(str(ipplan))
    assert 'holes inside networks' in page
    assert '10.200.0.3 &ndash; 10.200.0.59' in page


def test_heatmap(ipplan):
    """One grid per supernet and per network; used cells carry the
    hostname tooltip, free cells the address, infra cells labeled."""
    page = load_stats().render(str(ipplan))
    assert '<h2>Heatmap</h2>' in page
    assert 'title="10.200.0.62 - puppet1.test"' in page
    assert 'title="10.200.0.3 free"' in page
    assert 'broadcast' in page
    assert 'supernet 10.200.0.0/24' in page


def test_heatmap_aggregates_large_blocks():
    """A block bigger than the cell budget gets density slices, not
    one cell per address (the dhb26 /17 must not emit 32k cells)."""
    import sqlite3
    stats = load_stats()
    conn = sqlite3.connect(':memory:')
    cells = stats._heat_cells(
        32768, 0, set(range(0, 4000)), {})
    assert cells.count('<i') == 256
    assert 'h4' in cells and '128/128 used' in cells


def test_supernet_holes_match_prod_free_markers():
    """The computed supernet holes reproduce the last event's
    hand-kept #-FREE- markers (dhb26): every annotated public free
    block appears in the computed output. Skipped without corpus."""
    import os
    import sqlite3
    import pytest
    from conftest import load_ipplan2db
    path = os.path.expanduser('~/repos/dh/svn/dhb26/core/ipplan')
    if not os.path.exists(path):
        pytest.skip('prod corpus not present')
    tool = load_ipplan2db()
    model = tool.parse_all([path])
    conn = sqlite3.connect(':memory:')
    c = conn.cursor()
    tool.create_schema(c)
    tool.emit_topology(model, c)
    holes = load_stats().supernet_hole_rows(c)
    with open(path) as f:
        markers = [line.split()[1] for line in f
                   if line.startswith('#-FREE-')
                   and 'RFC1918' not in line]
    assert markers, 'corpus lost its FREE markers?'
    # the computation merges adjacent hand-marked blocks into larger
    # CIDRs - assert COVERAGE: every marker lies in a computed hole
    import ipaddress
    import re
    blocks = [ipaddress.ip_network(m) for m in re.findall(
        r'<td>(\d+\.\d+\.\d+\.\d+/\d+)</td><td class="num">', holes)]
    for marker in markers:
        net = ipaddress.ip_network(marker)
        assert any(net.subnet_of(b) for b in blocks), marker
