# The apache vhost for the deploy backend exists twice: the stack copy
# (seed.sh, bench bootstrap) and the vendored dhdeploy module copy
# (puppet, steady state). The deploy repo stays the source of truth;
# the vendored copy is the same bytes plus one managed-by header line.
# Same guard idea as test_enc_vendor: drift is a test failure.

import os

import pytest

STACK = os.path.join(os.path.dirname(__file__), '..', '..',
                     'testvm', 'deploy-stack', 'apache-deploy.conf')
VENDORED = os.path.expanduser(
    '~/repos/dh/local/puppet/modules/dhdeploy/files/apache-deploy.conf')

pytestmark = pytest.mark.skipif(
    not os.path.isfile(VENDORED), reason='puppet repo checkout not present')


def test_vendored_apache_conf_is_stack_conf_plus_header():
    with open(STACK, 'rb') as f:
        want = f.read()
    with open(VENDORED, 'rb') as f:
        header, _, body = f.read().partition(b'\n')
    assert header.startswith(b'# Managed by puppet')
    assert body == want, (
        'dhdeploy/files/apache-deploy.conf drifted from '
        'testvm/deploy-stack/apache-deploy.conf - re-copy and commit both')
