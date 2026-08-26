# ENC generator for pkg "node" (a DEFAULT package - every host
# carries it unless opted out with -node/-default): node metrics for
# the site prometheus, prod's model and prod's package name. Site-
# local: no site prometheus, no exporter; 9100 (and every manifest
# monitor: port the host serves) opens ONLY to that prometheus.

from lib import metadata

from . import prometheus as _prometheus


def generate(host, params, manifest):
    site = metadata.host_site(host)
    proms = [h for h, _ in metadata.hosts_with_pkg('prometheus')
             if metadata.host_site(h) == site]
    if not proms:
        return {}
    out = {'dhnodeexporter': {}}
    # hosts whose os manages its own firewall (pve) get the exporter
    # but never dhfirewall params - the data rule for the old
    # "only where dhfirewall is ours" guard
    if metadata._get_os(host) == 'pve':
        return out
    prom_ips = sorted(metadata.host_ip(h) for h in proms)
    prom_ips6 = sorted(ip for ip in (metadata.host_ip6(h)
                                     for h in proms) if ip)
    scoped = {}
    scoped6 = {}
    # manifest monitor: specs (prod idiom): a monitored pkg's metrics
    # port opens ONLY to the site prometheus - the firewall mirror of
    # the scrape job generated over there. 9100 arrives through the
    # node pkg's own monitor: spec like everything else. v6 mirrors
    # v4 whenever the prometheus carries a derived address (P4).
    for pkg, _ in metadata.pkgs_with_params(host):
        mon = ((manifest.get('packages') or {}).get(pkg)
               or {}).get('monitor')
        if mon:
            port = _prometheus.monitor_port(mon['url'])
            scoped[port] = prom_ips
            if prom_ips6:
                scoped6[port] = prom_ips6
    if scoped:
        out['dhfirewall'] = {'open_tcp_scoped': scoped}
        if scoped6:
            out['dhfirewall']['open_tcp_scoped6'] = scoped6
    return out
