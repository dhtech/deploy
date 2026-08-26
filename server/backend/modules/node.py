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
    scoped = {9100: prom_ips}
    # manifest monitor: specs (prod idiom): a monitored pkg's metrics
    # port opens ONLY to the site prometheus - the firewall mirror of
    # the scrape job generated over there
    for pkg, _ in metadata.pkgs_with_params(host):
        mon = ((manifest.get('packages') or {}).get(pkg)
               or {}).get('monitor')
        if mon:
            scoped[_prometheus.monitor_port(mon['url'])] = prom_ips
    out['dhfirewall'] = {'open_tcp_scoped': scoped}
    return out
