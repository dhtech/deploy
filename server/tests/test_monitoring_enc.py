# Per-site monitoring model: one prometheus + one grafana per site.
# Prometheus scrapes ONLY its own site; node exporters admit ONLY the
# same-site prometheus; grafana provisions exactly ONE datasource - the
# same site's prometheus - and the prometheus-web flow is site-local.

import importlib
import sys

import pytest
import yaml

from conftest import IPPLAN_COLO, IPPLAN_EVENT, load_ipplan2db

enc = importlib.import_module('enc')


IPPLAN_COLO_MON = IPPLAN_COLO.replace(
    '#$ web1.test\t10.200.0.60\tos=debian;pkg=base,web(port=80)',
    '#$ web1.test\t10.200.0.60\tos=debian;pkg=base,web(port=80)\n'
    '#$ prometheus.test\t10.200.0.70\tos=debian;pkg=prometheus;'
    'webname=prometheus.colo.example\n'
    '#$ grafana.test\t10.200.0.71\tos=debian;pkg=grafana;'
    'webname=grafana.colo.example')


@pytest.fixture
def mon_manifest(manifest):
    manifest['services']['prometheus'] = {'destport': ['9090/tcp']}
    manifest['services']['dhssh'] = {'destport': ['2022/tcp']}
    manifest['packages']['node'] = {
        'monitor': {'url': 'http://{host}:9100/metrics'}}
    manifest['packages']['secure'] = {
        'monitor': {'url': 'https://{host}:8443/metrics', 'auth': True}}
    manifest['packages']['prometheus'] = {
        'server': ['prometheus'],
        'puppet': {'classes': ['dhfirewall', 'dhprometheus']}}
    manifest['packages']['grafana'] = {
        'client': ['prometheus'],
        'monitor': {'url': 'http://{host}:3000/metrics'},
        'puppet': {'classes': ['dhfirewall', 'dhgrafana']}}
    return manifest


@pytest.fixture
def mon_ipplan(tmp_path, monkeypatch, mon_manifest):
    root = tmp_path / 'svn'
    site = root / 'allevents' / 'colo' / 'colo'
    event = root / 'test' / 'core'
    site.mkdir(parents=True)
    event.mkdir(parents=True)
    (site / 'ipplan').write_text(IPPLAN_COLO_MON)
    (event / 'ipplan').write_text(IPPLAN_EVENT)
    (root / 'currentevent').write_text(
        'currentevent=test\napt_freeze=false\nchange_freeze=false\n')
    mpath = tmp_path / 'manifest.yaml'
    mpath.write_text(yaml.safe_dump(mon_manifest))
    db = tmp_path / 'ipplan.db'
    monkeypatch.setattr(sys.modules['lib.metadata'], 'DB_FILE', str(db))
    load_ipplan2db().build(str(root), [str(mpath)], str(db))
    return db


def test_prometheus_scrapes_own_site_only(mon_ipplan, mon_manifest):
    result = enc.classify('prometheus.test', mon_manifest)
    # manifest monitor: specs become site-scoped scrape jobs
    jobs = {j['job_name']: j for j in result['dhprometheus']['scrape_jobs']}
    assert jobs['grafana']['targets'] == ['grafana.test:3000']
    assert jobs['grafana']['metrics_path'] == '/metrics'
    # the node job rides the same idiom via the node DEFAULT pkg
    targets = jobs['node']['targets']
    assert 'web1.test:9100' in targets
    assert 'prometheus.test:9100' in targets
    # the EVENT site host is another site's problem
    assert 'evtbox1.test:9100' not in targets
    # jumpgate ssh banner probes (dhssh port); deploy.test carries
    # pkg jumpgate only as the allow-list marker (carve-out) - the
    # fixture has no other jumpgate, so the probe list is empty
    assert result['dhprometheus']['ssh_targets'] == []
    # auth: only over https (prod semantics)
    assert jobs['grafana'].get('auth') is None


def test_node_exporter_admits_same_site_prometheus_only(mon_ipplan,
                                                        mon_manifest):
    result = enc.classify('web1.test', mon_manifest)
    assert result['dhfirewall']['open_tcp_scoped'][9100] == ['10.200.0.70']
    # a host in a site WITHOUT a prometheus gets no exporter baseline
    other = enc.classify('evtbox1.test', mon_manifest)
    assert 'dhnodeexporter' not in other


def test_grafana_single_site_datasource(mon_ipplan, mon_manifest):
    result = enc.classify('grafana.test', mon_manifest)
    g = result['dhgrafana']
    assert g['prometheus_server'] == 'prometheus.test'
    assert g['site'] == 'colo'
    assert g['svc_name'] == 'grafana'
    assert g['archive_dir'] == 'colo'
    assert g['root_url'] == 'https://grafana.colo.example/'
    assert result['dhnginx::grafana'] == {
        'server_name': 'grafana.colo.example'}
    assert result['dhacme::cert']['cert_name'] == 'grafana.colo.example'
    # the monitor: port admits only the site prometheus (generic)
    assert result['dhfirewall']['open_tcp_scoped'][3000] == ['10.200.0.70']


def test_prometheus_web_flow_is_site_local(mon_ipplan, mon_manifest):
    # the flow engine grants 9090 on the prometheus host to the site's
    # grafana - and nobody else
    result = enc.classify('prometheus.test', mon_manifest)
    scoped = result['dhfirewall']['open_tcp_scoped']
    assert scoped[9090] == ['10.200.0.71']
