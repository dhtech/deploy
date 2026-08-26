# ENC generator for pkg "managed" (a DEFAULT package - every host
# carries it unless opted out): what being run by the deploy system
# means. Moved OUT of the global enc (which imposes nothing - it only
# derives from ipplan + manifest data; this pkg in the default: list
# IS the data that grants these).

from lib import metadata


def generate(host, params, manifest):
    out = {
        # every managed host consumes the ipplan db: granted from the
        # manifest, never from the db itself - a host whose served db
        # lags (an operator pin) must still keep receiving updates
        'dhipplan': {},
        # the guest agent (virt-fact aware; udev-activated - covers
        # pre-pipeline VMs the hardening never touched)
        'dhguest': {},
    }
    # apt through the deploy server's cache (the installed system, not
    # just the installer) - except the cache host itself. The class is
    # ALWAYS in the catalog (so a host that leaves the servers scope
    # gets the file removed); the proxy value only inside a
    # pkg=servers network
    deploys = metadata.hosts_with_pkg('deploy')
    if deploys and not any(h == host for h, _ in deploys):
        if _in_servers_network(host):
            out['dhaptcache'] = {
                'proxy': 'http://%s:3142' % metadata.host_ip(
                    deploys[0][0])}
        else:
            out['dhaptcache'] = {}
    # apt auto-updates: colo machines only, and the event change
    # freeze (meta_data) switches them off fleet-wide
    site = metadata.host_site(host)
    if site:
        freeze = metadata.get_meta('change_freeze', 'false') == 'true'
        out['dhautoupdate'] = {'enabled': site == 'colo' and not freeze}
    return out


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
