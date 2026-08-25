# The late script is served over CGI to "wget && sh" in d-i: it must be
# emitted complete or not at all. A secret store (bao sealed/down) or
# redis failure mid-generation must produce NO output (apache then
# answers 500 and the installer's wget fails hard) - never a
# truncated-but-valid script.

import json
import os
import runpy
import sys

import pytest

HERE = os.path.dirname(__file__)
LATE = os.path.join(HERE, '..', 'backend', 'debian', 'late.py')


class FakeRedis:
    """Enough redis for late.py: find() reads the enrollment record,
    the enroll token is setex'd."""

    def get(self, key):
        return json.dumps({'manufacturer': 'QEMU'}).encode()

    def setex(self, *args, **kwargs):
        pass


def run_late(monkeypatch, capsys, fqdn='web1.test'):
    metadata = sys.modules['lib.metadata']
    monkeypatch.setattr(metadata, 'connection', lambda: FakeRedis())
    monkeypatch.setattr(metadata, 'config', lambda: {})
    monkeypatch.setenv('QUERY_STRING', 'fqdn=%s' % fqdn)
    monkeypatch.setattr(sys, 'argv', ['late.py'])
    captured = sys.stdout
    try:
        runpy.run_path(LATE, run_name='__main__')
    finally:
        sys.stdout = captured
    return capsys.readouterr().out


def test_complete_script_when_stores_up(ipplan, monkeypatch, capsys):
    metadata = sys.modules['lib.metadata']
    monkeypatch.setattr(metadata, 'vault_read',
                        lambda path: {'dhtech_password': 'hunter2'})
    out = run_late(monkeypatch, capsys)
    assert out.startswith('\n#!/bin/sh')
    assert 'csr_attributes.yaml' in out
    assert 'chpasswd' in out
    # the finish signal is the last generated line - proof of completeness
    assert 'finish.py?fqdn=web1.test' in out.splitlines()[-1]


def test_no_partial_output_when_vault_down(ipplan, monkeypatch, capsys):
    metadata = sys.modules['lib.metadata']

    def sealed(path):
        raise OSError('secret store sealed')
    monkeypatch.setattr(metadata, 'vault_read', sealed)
    with pytest.raises(OSError):
        run_late(monkeypatch, capsys)
    # nothing may have reached the client - not even the CGI blank line
    assert capsys.readouterr().out == ''


def test_no_partial_output_when_redis_down(ipplan, monkeypatch, capsys):
    metadata = sys.modules['lib.metadata']

    class DeadRedis(FakeRedis):
        def setex(self, *args, **kwargs):
            raise ConnectionError('redis down')
    monkeypatch.setattr(metadata, 'vault_read',
                        lambda path: {'dhtech_password': 'hunter2'})
    monkeypatch.setattr(metadata, 'connection', lambda: DeadRedis())
    monkeypatch.setattr(metadata, 'config', lambda: {})
    monkeypatch.setenv('QUERY_STRING', 'fqdn=web1.test')
    monkeypatch.setattr(sys, 'argv', ['late.py'])
    captured = sys.stdout
    try:
        with pytest.raises(ConnectionError):
            runpy.run_path(LATE, run_name='__main__')
    finally:
        sys.stdout = captured
    assert capsys.readouterr().out == ''
