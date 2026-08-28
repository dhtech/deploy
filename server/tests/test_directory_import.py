# The directory-import transform: prod slapcat -> clean gen-3 LDIF.

import importlib.machinery
import importlib.util
import io
import os

# extensionless script: needs an explicit SourceFileLoader
_path = os.path.join(os.path.dirname(__file__), '..', '..', 'utils',
                     'directory-import')
_loader = importlib.machinery.SourceFileLoader('directory_import', _path)
spec = importlib.util.spec_from_loader('directory_import', _loader)
di = importlib.util.module_from_spec(spec)
_loader.exec_module(di)

DUMP = """dn: dc=dreamhack,dc=se
objectClass: dcObject
objectClass: organization
dc: dreamhack

dn: ou=fusiondirectory,dc=dreamhack,dc=se
objectClass: organizationalUnit
ou: fusiondirectory

dn: cn=config,ou=fusiondirectory,dc=dreamhack,dc=se
objectClass: fdConfig
cn: config

dn: cn=fd-admin,dc=dreamhack,dc=se
objectClass: inetOrgPerson
cn: fd-admin
sn: admin

dn: ou=people,dc=tech,dc=dreamhack,dc=se
objectClass: organizationalUnit
ou: people

dn: uid=anna,ou=people,dc=tech,dc=dreamhack,dc=se
objectClass: inetOrgPerson
objectClass: posixAccount
objectClass: fdPersonalInfo
objectClass: gosaMailAccount
objectClass: ldapPublicKey
uid: anna
cn: Anna A
sn:: QQ==
uidNumber: 1000
gidNumber: 1000
homeDirectory: /home/anna
mail: anna@example.se
gosaMailServer: mail1
fdBankAccountNumber: 123
sshPublicKey: ssh-ed25519 AAAA anna
jpegPhoto:: /9j/qqqq

dn: ou=services,dc=event,dc=dreamhack,dc=se
objectClass: organizationalUnit
objectClass: gosaDepartment
ou: services

dn: ou=groups,ou=services,dc=event,dc=dreamhack,dc=se
objectClass: organizationalUnit
ou: groups

dn: cn=svc,ou=groups,ou=services,dc=event,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: gosaGroupOfNames
objectClass: posixGroup
cn: svc
gidNumber: 2000
member: uid=anna,ou=people,dc=tech,dc=dreamhack,dc=se
member: cn=config,ou=fusiondirectory,dc=dreamhack,dc=se
memberUid: anna

dn: cn=aclthing,dc=tech,dc=dreamhack,dc=se
objectClass: gosaRole
cn: aclthing
"""


def run_transform():
    out = io.StringIO()
    di.transform(io.StringIO(DUMP), out)
    return out.getvalue()


def test_fd_subtree_and_root_accounts_skipped():
    out = run_transform()
    assert 'fusiondirectory' not in out
    assert 'fd-admin' not in out
    assert 'aclthing' not in out


def test_seeded_containers_skipped_but_deep_ous_kept():
    out = run_transform()
    assert 'dn: ou=people,dc=tech,dc=dreamhack,dc=se' not in out
    assert 'dn: dc=dreamhack,dc=se' not in out
    assert 'dn: ou=services,dc=event,dc=dreamhack,dc=se' in out
    assert 'dn: ou=groups,ou=services,dc=event,dc=dreamhack,dc=se' in out


def test_user_sanitized():
    out = run_transform()
    assert 'objectClass: fdPersonalInfo' not in out
    assert 'objectClass: gosaMailAccount' not in out
    assert 'gosaMailServer' not in out
    assert 'fdBankAccountNumber' not in out
    assert 'objectClass: ldapPublicKey' in out
    assert 'sshPublicKey: ssh-ed25519 AAAA anna' in out
    assert 'jpegPhoto:: /9j/qqqq' in out
    assert 'sn:: QQ==' in out
    assert 'mail: anna@example.se' in out


def test_department_keeps_organizational_unit_only():
    out = run_transform()
    assert 'objectClass: gosaDepartment' not in out


def test_dangling_member_pruned():
    out = run_transform()
    assert 'member: uid=anna,ou=people,dc=tech,dc=dreamhack,dc=se' in out
    assert 'cn=config,ou=fusiondirectory' not in out
    assert 'memberUid: anna' in out


def test_orphan_continuations_dropped():
    # password-stripped dumps leave continuation lines of deleted attrs;
    # they must not fold onto the previous attribute
    dump = ("dn: uid=x,ou=people,dc=tech,dc=dreamhack,dc=se\n"
            "objectClass: inetOrgPerson\n"
            "uid: x\n"
            " R3I3Ly83em1vSDA=\n"
            "cn: X\n"
            "sn: X\n")
    out = io.StringIO()
    di.transform(io.StringIO(dump), out)
    assert 'uid: x\n' in out.getvalue()
    assert 'R3I3' not in out.getvalue()


def test_parents_written_before_children():
    dump = ("dn: cn=g,ou=groups,ou=deep,dc=event,dc=dreamhack,dc=se\n"
            "objectClass: groupOfNames\n"
            "cn: g\n"
            "member: uid=y,ou=people,dc=tech,dc=dreamhack,dc=se\n"
            "\n"
            "dn: ou=deep,dc=event,dc=dreamhack,dc=se\n"
            "objectClass: organizationalUnit\n"
            "ou: deep\n"
            "\n"
            "dn: ou=groups,ou=deep,dc=event,dc=dreamhack,dc=se\n"
            "objectClass: organizationalUnit\n"
            "ou: groups\n")
    out = io.StringIO()
    di.transform(io.StringIO(dump), out)
    text = out.getvalue()
    assert text.index('ou=deep,dc=event') < text.index('ou=groups,ou=deep')
    assert text.index('ou=groups,ou=deep') < text.index('cn=g,ou=groups')


TEAM_DUMP = """dn: dc=dreamhack,dc=se
objectClass: dcObject
dc: dreamhack

dn: uid=alice,ou=people,dc=tech,dc=dreamhack,dc=se
objectClass: inetOrgPerson
objectClass: posixAccount
uid: alice
cn: Alice
sn: A
uidNumber: 5001
gidNumber: 5001
homeDirectory: /home/alice
loginShell: /bin/bash

dn: uid=bob,ou=people,dc=tech,dc=dreamhack,dc=se
objectClass: inetOrgPerson
objectClass: posixAccount
uid: bob
cn: Bob
sn: B
uidNumber: 5002
gidNumber: 5002
homeDirectory: /home/bob
loginShell: /bin/bash

dn: cn=access-participants-team,ou=groups,ou=access-participants,dc=event,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: posixGroup
cn: access-participants-team
gidNumber: 10001
member: uid=alice,ou=people,dc=tech,dc=dreamhack,dc=se

dn: cn=access-wifi-team,ou=groups,ou=access-wifi,dc=event,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: posixGroup
cn: access-wifi-team
gidNumber: 10002
member: uid=bob,ou=people,dc=tech,dc=dreamhack,dc=se

dn: cn=radius-access-access,ou=groups,ou=access-participants,dc=event,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: posixGroup
cn: radius-access-access
gidNumber: 10020
member: uid=alice,ou=people,dc=tech,dc=dreamhack,dc=se

dn: cn=core-gl-team,ou=groups,ou=core,dc=event,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: posixGroup
cn: core-gl-team
gidNumber: 10003
member: uid=alice,ou=people,dc=tech,dc=dreamhack,dc=se

dn: cn=gl,ou=groups,dc=event,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: posixGroup
cn: gl
gidNumber: 10046
member: cn=core-gl-team,ou=groups,ou=core,dc=event,dc=dreamhack,dc=se

dn: cn=services-colo-team,ou=groups,dc=colo,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: posixGroup
cn: services-colo-team
gidNumber: 10004
member: uid=bob,ou=people,dc=tech,dc=dreamhack,dc=se

dn: cn=tgl,ou=groups,dc=event,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: posixGroup
cn: tgl
gidNumber: 10048
member: uid=alice,ou=people,dc=tech,dc=dreamhack,dc=se

dn: cn=tech,ou=groups,dc=event,dc=dreamhack,dc=se
objectClass: groupOfNames
objectClass: posixGroup
cn: tech
gidNumber: 10047
member: uid=alice,ou=people,dc=tech,dc=dreamhack,dc=se
"""


def test_canonical_teams(capsys):
    """The 8 canonical teams replace the prod team zoo: access merged
    from both access-* teams, gl-team resolves nested gl-team groups
    to PEOPLE, colo-team renames services-colo-team, gids are fresh
    (prod's never carry over), non-team groups import untouched."""
    out = io.StringIO()
    di.transform(io.StringIO(TEAM_DUMP), out)
    text = out.getvalue()
    entries = {}
    for block in text.strip().split('\n\n'):
        lines = block.split('\n')
        entries[lines[0][4:]] = lines[1:]
    ev = 'dc=event,dc=dreamhack,dc=se'
    access = entries['cn=access-team,ou=groups,ou=access,' + ev]
    assert 'gidNumber: 10001' in access  # participants' gid (primary)
    assert 'member: uid=alice,ou=people,dc=tech,dc=dreamhack,dc=se' in access
    assert 'member: uid=bob,ou=people,dc=tech,dc=dreamhack,dc=se' in access
    gl = entries['cn=gl-team,ou=groups,' + ev]
    assert 'gidNumber: 10046' in gl      # prod cn=gl's gid
    # nested: gl held the core-gl-team GROUP - resolved to alice
    assert 'member: uid=alice,ou=people,dc=tech,dc=dreamhack,dc=se' in gl
    colo = entries['cn=colo-team,ou=groups,dc=colo,dc=dreamhack,dc=se']
    assert 'gidNumber: 10004' in colo    # services-colo-team's gid
    assert 'member: uid=bob,ou=people,dc=tech,dc=dreamhack,dc=se' in colo
    # the access dept OU is scaffolded
    assert 'ou=access,' + ev in entries
    # husk OUs die; their radius group moves into the merged dept
    radius = entries['cn=radius-access-access,ou=groups,ou=access,' + ev]
    assert 'gidNumber: 10020' in radius
    assert not any('access-participants' in dn or 'access-wifi' in dn
                   for dn in entries)
    # tgl is retired outright
    assert not any(dn.startswith('cn=tgl,') for dn in entries)
    # old team groups are gone; fresh gids only (no 10xxx on teams)
    assert not any('services-colo-team' in dn or 'access-wifi-team' in dn
                   or dn.startswith('cn=gl,') for dn in entries)
    # colo-gl-team: created empty (self-member placeholder) with the
    # fixed fresh gid when the dump lacks it; feeds gl-team when a
    # future dump carries it
    cgl = entries['cn=colo-gl-team,ou=groups,dc=colo,dc=dreamhack,dc=se']
    assert 'gidNumber: 10100' in cgl
    assert ('member: cn=colo-gl-team,ou=groups,dc=colo,dc=dreamhack,dc=se'
            in cgl)
    # non-team groups untouched, prod gid intact
    assert 'gidNumber: 10047' in entries['cn=tech,ou=groups,' + ev]
