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
            elif isinstance(value, dict) and isinstance(slot.get(key), dict):
                # dict params merge per key (open_tcp_scoped maps from
                # several sources must COMBINE - a later contributor
                # must not wipe an earlier one's ports)
                for k2, v2 in value.items():
                    if (isinstance(v2, list)
                            and isinstance(slot[key].get(k2), list)):
                        slot[key][k2] = slot[key][k2] + [
                            v for v in v2 if v not in slot[key][k2]]
                    else:
                        slot[key][k2] = v2
            else:
                slot[key] = value


def classify(hostname, manifest):
    """Everything a host gets derives from DATA - its ipplan pkgs
    and the manifest (fleet baselines are DEFAULT packages with
    their own generator modules). The global enc imposes nothing:
    dispatch, the flow-engine merge, the world: grammar, and the
    site jumpgates rule - a host without pkgs gets an empty
    classification."""
    classes = {}
    for pkg, params in metadata.pkgs_with_params(hostname):
        pkg_spec = manifest.get('packages', {}).get(pkg) or {}
        spec = pkg_spec.get('puppet') or {}
        for cls in spec.get('classes', []):
            classes.setdefault(cls, {})
        merge_params(classes, spec.get('params') or {})
        # world specs (prod model): the named services' destports
        # open UNSCOPED - the jumpgate's dhssh 2022 entry
        for svc in pkg_spec.get('world') or []:
            ports = metadata.ports_by_proto(
                (manifest.get('services', {}).get(svc) or {})
                .get('destport') or [])
            merge_params(classes, {'dhfirewall': {
                'open_tcp': ports.get('tcp', []),
                'open_udp': ports.get('udp', []),
            }})
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
    # the v6 mirror (P4 parity): rules exist whenever both flow ends
    # carry a derived v6 - dual-stack arrives as data, not config
    scoped6 = metadata.firewall_rules_to6(hostname)
    if scoped6.get('tcp'):
        merge_params(classes, {'dhfirewall': {
            'open_tcp_scoped6': scoped6['tcp']}})
    if scoped6.get('udp'):
        merge_params(classes, {'dhfirewall': {
            'open_udp_scoped6': scoped6['udp']}})

    if 'dhfirewall' in classes:
        gates = [(metadata.host_ip(h), metadata.host_ip6(h))
                 for h, _ in metadata.hosts_with_pkg('jumpgate')]
        if gates:
            classes['dhfirewall'].setdefault(
                'jumpgates', [v4 for v4, _ in gates if v4])
            jumpgates6 = [v6 for _, v6 in gates if v6]
            if jumpgates6:
                classes['dhfirewall'].setdefault('jumpgates6',
                                                 jumpgates6)
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
