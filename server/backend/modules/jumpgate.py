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
            'jails': {'sshd': {'backend': 'systemd',
                               'port': '22,2022'}},
        },
    }
    # PROD MODEL (user decision 2026-08-26): the jumpgate's sshd
    # LISTENS on 2022 - that is the internet entry port, world-open
    # (the jail handles abuse); 22 stays internal-only via the
    # built-in jumpgates rule. The deploy server (also jumpgate-pkg)
    # keeps plain 22: its ssh is the slirp-only mgmt door
    # (workstation hostfwd 4455).
    if any(pkg == 'deploy'
           for pkg, _ in metadata.pkgs_with_params(host)):
        out['dhfirewall'] = {'open_tcp': [22]}
    else:
        out['dhjumpgate'] = {}
        out['dhfirewall'] = {'open_tcp': [2022]}
    return out
