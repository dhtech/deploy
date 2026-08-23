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
