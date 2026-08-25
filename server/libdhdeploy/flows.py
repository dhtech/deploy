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
#
# Clients are hosts (pkg client specs) OR NETWORKS (client= on the
# network line): a network client pairs like a host, and its CIDR
# becomes the rule source - how the deployment VLAN's installers get
# their flows without being ipplan hosts. tcp and udp destports both
# emit (open_tcp_scoped / open_udp_scoped).

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


def _ports(service_def):
    """destport entries to {proto: [ports]} (tcp + udp)."""
    return metadata.ports_by_proto(service_def.get('destport', []))


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


def firewall_pairs(hostname, manifest):
    """The flow pairings for a host: for each service this host
    serves, every host whose client spec matches on (flow, service).
    Returns sorted (client_host, flow, service) tuples - what
    ipplan2db precomputes into prod's node-id firewall_rule form."""
    packages = manifest.get('packages', {})

    server_specs = []
    for pkg, params in metadata.pkgs_with_params(hostname):
        server_specs.extend(_specs(packages, pkg, params, 'server'))
    if not server_specs:
        return []

    # (flow, service) -> client hosts, from every host's client specs
    clients = collections.defaultdict(set)
    for other, pkgs in metadata.all_hosts_pkgs().items():
        if other == hostname:
            continue
        site = metadata.host_site(other)
        if not metadata.host_ip(other):
            continue
        for pkg, params in pkgs:
            for spec in _specs(packages, pkg, params, 'client'):
                clients[_parse_spec(spec, site)].add(other)

    # network client= specs: flow clients whose members are not
    # ipplan hosts (the deployment VLAN's installers) - the network
    # itself is the client, its CIDR becomes the rule source
    for net, (site, specs) in metadata.network_clients().items():
        for spec in specs:
            clients[_parse_spec(spec, site)].add(net)

    my_site = metadata.host_site(hostname)
    pairs = set()
    for spec in server_specs:
        flow, service = _parse_spec(spec, my_site)
        for client in clients.get((flow, service), ()):
            pairs.add((client, flow, service))
    return sorted(pairs)


def firewall_params(hostname, manifest):
    """dhfirewall parameters for a host derived from the manifest's
    client/server flow declarations: each service this host serves gets
    its tcp destports opened to the hosts whose client spec matches on
    (flow, service). Empty dict when the host serves nothing."""
    services = manifest.get('services', {})
    scoped = {'tcp': collections.defaultdict(set),
              'udp': collections.defaultdict(set)}
    for client, _flow, service in firewall_pairs(hostname, manifest):
        source = metadata.node_ipv4(client)
        for proto, ports in _ports(services.get(service) or {}).items():
            for port in ports:
                scoped[proto][port].add(source)
    out = {}
    for proto, entries in scoped.items():
        if entries:
            out['open_%s_scoped' % proto] = {
                port: sorted(ips) for port, ips in entries.items()}
    return out
