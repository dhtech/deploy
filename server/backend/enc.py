#!/usr/bin/env python3
# External Node Classifier endpoint for the puppetserver.
#
# ipplan and the manifest are the single source of truth - there is no
# hiera data layer. For each of the host's pkgs the ENC emits:
#   - the pkg's puppet classes from the manifest
#     (packages.<pkg>.puppet.classes)
#   - static per-pkg parameters from the manifest
#     (packages.<pkg>.puppet.params: {class: {key: value}})
#   - topology-derived parameters from the pkg's generator
#     (modules/<pkg>.py: generate(host, params, manifest) -> same shape),
#     computed from ipplan (pkg args like ldap(role=master,id=1),
#     host options like webname, hosts_with_pkg lookups)
# Lists merge as unions (stable order), scalars override
# (manifest < generator). dhfirewall::jumpgates is filled globally from
# the hosts carrying pkg "jumpgate".

import importlib
import sys
import os
import urllib.parse

import yaml

from lib import metadata


def merge_params(target, extra):
    for cls, params in extra.items():
        slot = target.setdefault(cls, {})
        for key, value in (params or {}).items():
            if value is None:
                continue
            if isinstance(value, list) and isinstance(slot.get(key), list):
                slot[key] = slot[key] + [v for v in value if v not in slot[key]]
            else:
                slot[key] = value


def classify(hostname, manifest):
    classes = {}
    for pkg, params in metadata.pkgs_with_params(hostname):
        spec = (manifest.get('packages', {}).get(pkg) or {}).get('puppet') or {}
        for cls in spec.get('classes', []):
            classes.setdefault(cls, {})
        merge_params(classes, spec.get('params') or {})
        try:
            generator = importlib.import_module('modules.%s' % pkg)
        except ImportError:
            continue
        merge_params(classes, generator.generate(hostname, params, manifest))

    # Firewall flows: precomputed by ipplan2db at db build (the
    # ruleset is data - query firewall_rule to audit or diff it).
    scoped = metadata.firewall_rules_to(hostname)
    if scoped.get('tcp'):
        merge_params(classes, {'dhfirewall': {
            'open_tcp_scoped': scoped['tcp']}})
    if scoped.get('udp'):
        merge_params(classes, {'dhfirewall': {
            'open_udp_scoped': scoped['udp']}})

    # apt auto-updates: colo machines only, and the event change
    # freeze (meta_data) switches them off fleet-wide
    site = metadata.host_site(hostname)
    if site:
        freeze = metadata.get_meta('change_freeze', 'false') == 'true'
        merge_params(classes, {'dhautoupdate': {
            'enabled': site == 'colo' and not freeze}})

    if not classes:
        classes = {'dhfirewall': {}}
    # every managed host consumes the ipplan db: granted HERE, never
    # from the db itself - a host whose served db lags (an operator
    # pin to an old revision) must still keep receiving updates, or
    # it freezes on the pinned build even after the override clears
    classes.setdefault('dhipplan', {})
    # fleet baseline: the qemu guest agent (udev-activated; covers
    # pre-pipeline VMs the hardening never touched)
    classes.setdefault('dhguest', {})
    # fleet baseline: apt through the deploy server's cache (the
    # installed system, not just the installer) - except the cache
    # host itself
    deploys = metadata.hosts_with_pkg('deploy')
    if (deploys and not any(h == hostname for h, _ in deploys)
            and _in_servers_network(hostname)):
        # only hosts inside a pkg=servers network get the proxy -
        # the cache port is scoped to exactly those networks (a MGMT
        # host pointed at it would just be firewalled off)
        classes.setdefault('dhaptcache', {
            'proxy': 'http://%s:3142' % metadata.host_ip(deploys[0][0])})
    # fleet baseline: node metrics, once a prometheus host exists;
    # 9100 opens ONLY to the prometheus host(s) - and only where
    # dhfirewall is ours (pve manages its own firewall)
    proms = metadata.hosts_with_pkg('prometheus')
    if proms:
        classes.setdefault('dhnodeexporter', {})
        if 'dhfirewall' in classes:
            merge_params(classes, {'dhfirewall': {'open_tcp_scoped': {
                9100: sorted(metadata.host_ip(h) for h, _ in proms)}}})
    if 'dhfirewall' in classes:
        jumpgates = [metadata.host_ip(h)
                     for h, _ in metadata.hosts_with_pkg('jumpgate')]
        if jumpgates:
            classes['dhfirewall'].setdefault('jumpgates', jumpgates)
    return classes


def _in_servers_network(hostname):
    import ipaddress
    import sqlite3
    ip = metadata.host_ip(hostname)
    if not ip:
        return False
    conn = sqlite3.connect(metadata.DB_FILE)
    rows = conn.execute(
        'SELECT n.ipv4_txt FROM network n, option o '
        'WHERE o.node_id = n.node_id AND o.name = "pkg" '
        'AND o.value = "servers"').fetchall()
    conn.close()
    addr = ipaddress.ip_address(ip)
    return any(addr in ipaddress.ip_network(r[0]) for r in rows if r[0])


def main():
    # exec node terminus on the puppetserver (dhenc): hostname as
    # argv; CGI mode (QUERY_STRING + blank body separator) kept for
    # the deploy server's http endpoint
    if len(sys.argv) > 1:
        hostname = sys.argv[1]
    else:
        query_string = urllib.parse.parse_qs(
            os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
        hostname = query_string['hostname'][0]
        print('')

    # a certname that is not in ipplan is an ANOMALY, and anomalies
    # must be SEEN: hard-fail the classification (failed agent run,
    # red in puppetboard) instead of handing out a quiet safe floor
    if metadata.get_vlan(hostname)[0] is None:
        print('%s: not in ipplan' % hostname, file=sys.stderr)
        sys.exit(1)

    manifest = metadata.get_manifest()

    output = {'classes': classify(hostname, manifest)}
    env = metadata.host_option(hostname, 'puppet_environment')
    if env:
        output['environment'] = env

    print(yaml.safe_dump(output, default_flow_style=False))


if __name__ == '__main__':
    main()
