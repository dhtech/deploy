# libdhdeploy's authoritative home is the ipplan2db repo (the
# compiler needs it standalone); the deploy backend carries this
# vendored copy. Byte drift is a test failure - same guard as
# test_enc_vendor. Skipped where the sibling checkout is absent.

import os

import pytest

DEPLOY = os.path.join(os.path.dirname(__file__), '..', 'libdhdeploy')
UPSTREAM = os.path.expanduser('~/repos/dh/ipplan2db/libdhdeploy')

pytestmark = pytest.mark.skipif(
    not os.path.isdir(UPSTREAM),
    reason='ipplan2db checkout not present')


@pytest.mark.parametrize('name', ['__init__.py', 'flows.py',
                                  'metadata.py'])
def test_vendored_libdhdeploy_is_identical(name):
    with open(os.path.join(UPSTREAM, name), 'rb') as f:
        want = f.read()
    with open(os.path.join(DEPLOY, name), 'rb') as f:
        have = f.read()
    assert want == have, (
        'server/libdhdeploy/%s drifted from the ipplan2db repo - '
        're-copy and commit both' % name)
