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
    if scoped:
        merge_params(classes, {'dhfirewall': {'open_tcp_scoped': scoped}})

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
    if 'dhfirewall' in classes:
        jumpgates = [metadata.host_ip(h)
                     for h, _ in metadata.hosts_with_pkg('jumpgate')]
        if jumpgates:
            classes['dhfirewall'].setdefault('jumpgates', jumpgates)
    return classes


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

    manifest = metadata.get_manifest()

    output = {'classes': classify(hostname, manifest)}
    env = metadata.host_option(hostname, 'puppet_environment')
    if env:
        output['environment'] = env

    print(yaml.safe_dump(output, default_flow_style=False))


if __name__ == '__main__':
    main()
