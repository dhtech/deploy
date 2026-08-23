# Firewall flows, gen-2 semantics (ipplan2sqlite lib/firewall.py): the
# manifest's packages declare client/server roles on services. A spec
# is 'service' or 'flow-service'; the default flow is the host's site
# (the network name before the @, lowercased), which is what keeps a
# client talking to the NEAREST server - specs only pair up when both
# sides name the same flow. Cross-site flows (ldaprepl, ldapwrite) are
# named explicitly on both ends.
#
# A manifest entry may be parameterized like the ipplan pkg syntax:
# an 'ldap(role=master)' entry overrides, per key, the base 'ldap'
# entry for hosts whose pkg params match.

import collections

from . import metadata


def _parse_spec(spec, default_flow):
    """'ldaprepl-ldaps' -> ('ldaprepl', 'ldaps'); 'ldaps' -> (site, 'ldaps')."""
    if '-' in spec:
        flow, service = spec.split('-', 1)
        if flow == 'default':
            flow = default_flow
        return flow, service
    return default_flow, spec


def _tcp_ports(service_def):
    """destport entries like '636/tcp' or '5900-5910/tcp' to a port list.
    Only tcp: dhfirewall has no scoped udp support yet."""
    ports = []
    for entry in service_def.get('destport', []):
        port, _, proto = entry.partition('/')
        if proto != 'tcp':
            continue
        lo, _, hi = port.partition('-')
        ports.extend(range(int(lo), int(hi or lo) + 1))
    return ports


def _specs(packages, pkg, params, access):
    """A pkg's client/server specs: a parameterized manifest entry like
    'ldap(role=master)' overrides the base entry's list for hosts whose
    pkg params match it."""
    for key in sorted(packages):
        if '(' not in key:
            continue
        name, want = metadata.parse_pkg(key)
        entry = packages.get(key) or {}
        if (name == pkg and access in entry
                and all(params.get(k) == v for k, v in want.items())):
            return entry[access]
    return (packages.get(pkg) or {}).get(access, [])


def firewall_params(hostname, manifest):
    """dhfirewall parameters for a host derived from the manifest's
    client/server flow declarations: each service this host serves gets
    its tcp destports opened to the hosts whose client spec matches on
    (flow, service). Empty dict when the host serves nothing."""
    packages = manifest.get('packages', {})
    services = manifest.get('services', {})

    server_specs = []
    for pkg, params in metadata.pkgs_with_params(hostname):
        server_specs.extend(_specs(packages, pkg, params, 'server'))
    if not server_specs:
        return {}

    # (flow, service) -> client IPs, from every host's client specs
    clients = collections.defaultdict(set)
    for other, pkgs in metadata.all_hosts_pkgs().items():
        if other == hostname:
            continue
        site = metadata.host_site(other)
        ip = metadata.host_ip(other)
        if not ip:
            continue
        for pkg, params in pkgs:
            for spec in _specs(packages, pkg, params, 'client'):
                clients[_parse_spec(spec, site)].add(ip)

    my_site = metadata.host_site(hostname)
    scoped = collections.defaultdict(set)
    for spec in server_specs:
        flow, service = _parse_spec(spec, my_site)
        sources = clients.get((flow, service))
        if not sources:
            continue
        for port in _tcp_ports(services.get(service) or {}):
            scoped[port] |= sources

    if not scoped:
        return {}
    return {'open_tcp_scoped': {port: sorted(ips)
                                for port, ips in scoped.items()}}
