# The svn pre-commit gate's compile path (utils/svn-pre-commit):
# good input compiles, bad input is rejected with ALL failing checks,
# and the input hash is stable across path ordering.

import importlib.machinery
import importlib.util
import os

import pytest
import yaml

from conftest import IPPLAN_COLO, IPPLAN_EVENT, HERE, load_ipplan2db


def load_gate():
    tool = os.path.join(HERE, '..', '..', 'utils', 'svn-pre-commit')
    spec = importlib.util.spec_from_loader(
        'svn_pre_commit', importlib.machinery.SourceFileLoader(
            'svn_pre_commit', tool))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def base_inputs(manifest):
    reformat = load_ipplan2db().reformat
    return {
        'allevents/colo/colo/ipplan': reformat(IPPLAN_COLO),
        'test/core/ipplan': reformat(IPPLAN_EVENT),
        'currentevent':
            'currentevent=test\napt_freeze=false\nchange_freeze=false\n',
        'services/manifest.yaml': yaml.safe_dump(manifest),
    }


def test_good_input_compiles(tmp_path, manifest):
    gate = load_gate()
    db = tmp_path / 'out.db'
    gate.compile_tree(base_inputs(manifest), str(db))
    assert db.exists()


def test_bad_input_rejected_with_all_errors(tmp_path, manifest):
    gate = load_gate()
    inputs = base_inputs(manifest)
    # a duplicate host name AND a duplicate ip: BOTH must be reported
    # inserted INSIDE the COLO section (appending at the end would
    # land them under MGMT and trip the out-of-section gate instead)
    inputs['allevents/colo/colo/ipplan'] = load_ipplan2db().reformat(
        IPPLAN_COLO.replace(
            '#$ web1.test\t10.200.0.60\tos=debian;pkg=base,web(port=80)\n',
            '#$ web1.test\t10.200.0.60\tos=debian;pkg=base,web(port=80)\n'
            '#$ web1.test\t10.200.0.90\tpkg=base\n'
            '#$ other.test\t10.200.0.60\tpkg=base\n'))
    with pytest.raises(Exception) as excinfo:
        gate.compile_tree(inputs, str(tmp_path / 'out.db'))
    errors = excinfo.value.errors
    assert len(errors) == 2
    assert any('duplicate host web1.test' in e for e in errors)
    assert any('duplicate ip 10.200.0.60' in e for e in errors)


def test_missing_manifest_rejected(tmp_path, manifest):
    gate = load_gate()
    inputs = base_inputs(manifest)
    del inputs['services/manifest.yaml']
    with pytest.raises(Exception, match='manifest.yaml missing'):
        gate.compile_tree(inputs, str(tmp_path / 'out.db'))


def test_input_hash_is_content_addressed(manifest):
    gate = load_gate()
    inputs = base_inputs(manifest)
    reordered = dict(reversed(list(inputs.items())))
    assert gate.input_hash(inputs) == gate.input_hash(reordered)
    changed = dict(inputs)
    changed['currentevent'] = 'currentevent=other\n'
    assert gate.input_hash(changed) != gate.input_hash(inputs)
