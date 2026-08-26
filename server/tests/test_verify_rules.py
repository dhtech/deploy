# The prod-verify parity rules (gate hardening): option whitelists,
# pkg/os validation, overlapping networks, VLAN and hostname rules,
# column alignment, exact field counts - plus the pre-commit's own
# force: bypass and banned-file check. Each rule proven rejecting AND
# the canonical reformat proven passing.

import os

from conftest import HERE, load_ipplan2db
from test_precommit import load_gate

GOOD = '''\
#@ IPV4-COLO-NET\t10.200.0.0/24
COLO\t10.200.0.0/24\tR1\t200\tnat;pkg=servers
#$ web1.colo.test\t10.200.0.60\tos=debian;pkg=base
MGMT\t10.10.10.0/24\tR1\t10\tothernet
#$ pve1.colo.test\t10.10.10.11\tos=pve;pkg=pve
'''


def parse(text, tmp_path):
    tool = load_ipplan2db()
    path = tmp_path / 'ipplan'
    path.write_text(tool.reformat(text))
    model = tool.parse_all([str(path)])
    return tool, model


def errors_of(text, tmp_path, manifest=None):
    tool, model = parse(text, tmp_path)
    try:
        tool.validate(model, manifest)
    except tool.BuildError:
        pass
    return model.errors


def test_reformat_output_is_style_clean(tmp_path):
    tool, model = parse(GOOD, tmp_path)
    tool.validate(model)
    assert model.errors == []
    # and idempotent: reformatting the canonical form is a no-op
    canon = tool.reformat(GOOD)
    assert tool.reformat(canon) == canon


def test_misaligned_line_rejected(tmp_path):
    tool = load_ipplan2db()
    path = tmp_path / 'ipplan'
    path.write_text(GOOD)  # single tabs, NOT reformatted
    model = tool.parse_all([str(path)])
    assert any('alignment is off' in e for e in model.errors)


def test_double_space_rejected(tmp_path):
    tool = load_ipplan2db()
    path = tmp_path / 'ipplan'
    path.write_text(tool.reformat(GOOD).replace(
        '#$ web1.colo.test', '#$  web1.colo.test'))
    model = tool.parse_all([str(path)])
    assert any('use tabs' in e for e in model.errors)


def test_trailing_whitespace_rejected(tmp_path):
    tool = load_ipplan2db()
    path = tmp_path / 'ipplan'
    # space before a tab (inside the ip field), prod-style wart
    path.write_text(tool.reformat(GOOD).replace(
        '10.200.0.60\t', '10.200.0.60 \t'))
    model = tool.parse_all([str(path)])
    assert any('trailing whitespace' in e for e in model.errors)
    # bare tab at end of line
    path.write_text(tool.reformat(GOOD).replace(
        'os=pve;pkg=pve', 'os=pve;pkg=pve\t'))
    model = tool.parse_all([str(path)])
    assert any('trailing whitespace' in e for e in model.errors)


def test_unknown_flag_rejected(tmp_path):
    errors = errors_of(GOOD.replace('os=debian;pkg=base',
                                    'os=debian;pkg=base;webnmae=x'),
                       tmp_path)
    assert any('unknown flag webnmae' in e for e in errors)


def test_unknown_os_rejected(tmp_path):
    errors = errors_of(GOOD.replace('os=debian', 'os=debain'), tmp_path)
    assert any('unknown OS debain' in e for e in errors)


def test_unknown_pkg_rejected_with_manifest(tmp_path):
    manifest = {'packages': {'base': {}, 'pve': {}, 'servers': {}}}
    errors = errors_of(GOOD.replace('pkg=base', 'pkg=basse'),
                       tmp_path, manifest)
    assert any('no such package basse' in e for e in errors)
    # the clean file passes the same manifest
    assert errors_of(GOOD, tmp_path, manifest) == []


def test_dashed_pkg_name_rejected(tmp_path):
    errors = errors_of(GOOD.replace('pkg=base', 'pkg=web-server'),
                       tmp_path)
    assert any('may not contain dashes' in e for e in errors)


def test_overlapping_networks_rejected(tmp_path):
    errors = errors_of(GOOD.replace(
        'MGMT\t10.10.10.0/24', 'MGMT\t10.200.0.128/25'), tmp_path)
    assert any('overlaps' in e for e in errors)


def test_vlan_rules(tmp_path):
    errors = errors_of(GOOD.replace('\tR1\t10\t', '\tR1\t9999\t'),
                       tmp_path)
    assert any('out of range' in e for e in errors)
    errors = errors_of(GOOD.replace('\tR1\t10\t', '\tR1\t1003\t'),
                       tmp_path)
    assert any('out of range' in e for e in errors)
    errors = errors_of(GOOD.replace('\tR1\t10\t', '\tR1\tx\t'),
                       tmp_path)
    assert any('non-numeric VLAN' in e for e in errors)


def test_hostname_rules(tmp_path):
    errors = errors_of(GOOD.replace('web1.colo.test', 'WEB1.colo.test'),
                       tmp_path)
    assert any('not lower case' in e for e in errors)
    errors = errors_of(GOOD.replace('web1.colo.test', 'web1.colo.xyz'),
                       tmp_path)
    assert any('allowed domain' in e for e in errors)


def test_field_counts(tmp_path):
    # host line without options
    errors = errors_of(GOOD.replace(
        '#$ web1.colo.test\t10.200.0.60\tos=debian;pkg=base',
        '#$ web1.colo.test\t10.200.0.60'), tmp_path)
    assert any('host line needs exactly' in e for e in errors)
    # network line without options
    errors = errors_of(GOOD.replace('\tR1\t10\tothernet', '\tR1\t10'),
                       tmp_path)
    assert any('network line needs exactly' in e for e in errors)


def test_unrecognized_line_rejected(tmp_path):
    errors = errors_of(GOOD + 'colo\t10.9.9.0/24\tR1\t9\tnone\n',
                       tmp_path)
    assert any('unrecognized line' in e for e in errors)


def test_none_only_alone(tmp_path):
    errors = errors_of(GOOD.replace('os=debian;pkg=base',
                                    'none;os=debian'), tmp_path)
    assert any("only valid alone" in e for e in errors)


def test_lab_snapshot_ipplan_is_clean(tmp_path):
    """The vendored lab ipplan must satisfy every rule the gate now
    enforces (it seeds the pipeline that the gate then guards)."""
    tool = load_ipplan2db()
    snapshot = os.path.join(
        HERE, '..', '..', 'testvm', 'deploy-stack', 'ipplan',
        'allevents', 'colo', 'colo', 'ipplan')
    model = tool.parse_all([snapshot])
    tool.validate(model)
    assert model.errors == []


def test_force_message_bypasses():
    gate = load_gate()
    assert gate.is_forced('force: fixing the broken hook')
    assert gate.is_forced('  FORCE: shouty')
    assert not gate.is_forced('forceful commit message')
    assert not gate.is_forced('normal: message')


def test_banned_paths():
    gate = load_gate()
    bad = gate.banned_paths([
        'conf/x', 'hooks/pre-commit', 'format', 'svn.ico',
        'a/b/.svn/entries', 'a/.svn', 'docs/Desktop.ini',
        'x/Thumbs.db', 'weird*',
        'allevents/colo/colo/ipplan', 'README.md', 'test/notes.txt'])
    assert 'allevents/colo/colo/ipplan' not in bad
    assert 'README.md' not in bad
    assert 'test/notes.txt' not in bad
    assert {'conf/x', 'hooks/pre-commit', 'format', 'svn.ico',
            'a/.svn', 'docs/Desktop.ini', 'x/Thumbs.db',
            'weird*'} <= set(bad)
    assert any('.svn' in p for p in bad)


def test_wg_without_wgsrc_rejected(tmp_path):
    """A wg= listener's exposure is declared, never implicit: no
    wgsrc= is a build failure."""
    errors = errors_of(GOOD + 'COLOVPN\t172.29.16.0/24\tR1\t-\t'
                       'othernet;wg=51820\n', tmp_path)
    assert any('needs an explicit wgsrc' in e for e in errors)
    # declared-open passes
    errors = errors_of(GOOD + 'COLOVPN\t172.29.16.0/24\tR1\t-\t'
                       'othernet;wg=51820;wgsrc=0.0.0.0/0\n', tmp_path)
    assert not any('wgsrc' in e for e in errors)
