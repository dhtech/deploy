# Install-time identity (the fqdn switch): request_host accepts ONLY
# a valid in-ipplan fqdn - no hack_ip, no address guessing (beta).

import pytest

from lib import metadata


def test_valid_fqdn_resolves(ipplan):
    assert metadata.request_host(
        {'fqdn': ['web1.test']}) == 'web1.test'


def test_fqdn_is_case_normalized(ipplan):
    assert metadata.request_host(
        {'fqdn': ['WEB1.Test ']}) == 'web1.test'


def test_unknown_fqdn_refused(ipplan):
    with pytest.raises(metadata.IdentityError, match='not in ipplan'):
        metadata.request_host({'fqdn': ['nosuch.test']})


def test_missing_fqdn_refused(ipplan):
    with pytest.raises(metadata.IdentityError, match='identity required'):
        metadata.request_host({})


def test_hack_ip_is_dead(ipplan):
    # the gen-2 parameter is not a fallback - it is simply gone
    with pytest.raises(metadata.IdentityError):
        metadata.request_host({'hack_ip': ['10.200.0.60']})
