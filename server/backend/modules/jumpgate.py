# ENC generator for pkg "jumpgate": the ssh entry points (marker pkg
# for the firewall's jumpgate list - base owns dhfirewall). They face
# the internet on ssh, so they get sshd brute-force protection; the
# other jumpgates (and thereby the lab's masquerade source) are never
# banned - that would lock everyone out.

from lib import metadata


def generate(host, params, manifest):
    out = {
        'dhfail2ban': {
            'ignore_ips': sorted(metadata.host_ip(h) for h, _ in
                                 metadata.hosts_with_pkg('jumpgate')),
            'jails': {'sshd': {'backend': 'systemd'}},
        },
    }
    # the ssh entry accepts the WORLD on 22 (the jail handles abuse) -
    # that is the role. The deploy server also carries this pkg but
    # keeps its own ruleset (documented carve-out): no dhfirewall
    # while pkg deploy is present.
    if not any(pkg == 'deploy'
               for pkg, _ in metadata.pkgs_with_params(host)):
        out['dhfirewall'] = {'open_tcp': [22]}
    return out
