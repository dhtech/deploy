# The ENC is vendored into the puppet repo (dhenc module) because it
# is the puppet server's own component - but the deploy repo stays
# the source of truth. This guards the copies: byte drift is a test
# failure. Skipped where the puppet repo checkout is absent.

import os

import pytest

DEPLOY = os.path.join(os.path.dirname(__file__), '..')
PUPPET = os.path.expanduser('~/repos/dh/local/puppet/modules/dhenc/files')

PAIRS = [
    ('backend/enc.py', 'enc.py'),
] + [('backend/modules/%s.py' % m, 'modules/%s.py' % m)
     for m in ('__init__', 'deploy', 'jumpgate', 'lam', 'ldap', 'login',
               'managed', 'node',
               'grafana', 'prometheus', 'puppetserver', 'pve', 'router', 'trac',
               'vault', 'web')]

pytestmark = pytest.mark.skipif(
    not os.path.isdir(PUPPET), reason='puppet repo checkout not present')


@pytest.mark.parametrize('source,vendored', PAIRS)
def test_vendored_copy_is_identical(source, vendored):
    with open(os.path.join(DEPLOY, source), 'rb') as f:
        want = f.read()
    with open(os.path.join(PUPPET, vendored), 'rb') as f:
        have = f.read()
    assert want == have, (
        '%s drifted from deploy repo %s - re-copy and commit both'
        % (vendored, source))
