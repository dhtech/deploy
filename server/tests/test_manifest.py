# The manifest file itself (testvm/deploy-stack/manifest.yaml - the
# file that will live in svn): it must be valid yaml and structurally
# sound, so a bad edit fails here instead of on provision1.

import os

import yaml

MANIFEST = os.path.join(os.path.dirname(__file__), '..', '..',
                        'testvm', 'deploy-stack', 'manifest.yaml')


def load():
    with open(MANIFEST) as f:
        return yaml.safe_load(f)


def test_parses_as_yaml():
    data = load()
    assert isinstance(data, dict)
    assert {'globals', 'apps', 'flows', 'services', 'packages'} <= set(data)


def test_lists_are_block_style_lists():
    data = load()
    for pkg, spec in data['packages'].items():
        spec = spec or {}
        for access in ('client', 'server'):
            if access in spec:
                assert isinstance(spec[access], list), (pkg, access)
        classes = (spec.get('puppet') or {}).get('classes', [])
        assert isinstance(classes, list), pkg
        assert all(isinstance(c, str) for c in classes), pkg


def test_flow_specs_reference_known_services():
    data = load()
    services = set(data['services'])
    flows = set(data['flows'])
    for pkg, spec in data['packages'].items():
        for access in ('client', 'server'):
            for entry in (spec or {}).get(access, []):
                if '-' in entry:
                    flow, service = entry.split('-', 1)
                    assert flow in flows, (pkg, entry)
                else:
                    service = entry
                assert service in services, (pkg, entry)


def test_appdisks_have_size_and_mountpoint():
    data = load()
    for pkg, spec in data['packages'].items():
        disk = (spec or {}).get('appdisk')
        if disk:
            assert 'size' in disk and 'mountpoint' in disk, pkg
            assert str(disk['mountpoint']).startswith('/'), pkg
