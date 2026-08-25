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
