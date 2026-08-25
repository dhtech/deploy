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
                               'port': 'ssh,2022'}},
        },
    }
    # PROD MODEL: the entry service is dhssh (2022/tcp) - declared
    # as a WORLD spec on the jumpgate pkg in the manifest (the port
    # comes from the service, not from here); dhssh (the class) makes
    # sshd listen on it. 22 stays internal via the built-in jumpgates
    # rule. The deploy server (also jumpgate-pkg) keeps plain 22: its
    # ssh is the slirp-only mgmt door (workstation hostfwd 4455).
    if any(pkg == 'deploy'
           for pkg, _ in metadata.pkgs_with_params(host)):
        out['dhfirewall'] = {'open_tcp': [22]}
    else:
        out['dhssh'] = {}
    return out
