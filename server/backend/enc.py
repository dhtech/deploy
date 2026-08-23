#!/usr/bin/env python3
# External Node Classifier endpoint for the puppetserver.
# Maps a host's ipplan pkg options (including their parameters, e.g.
# "web(port=80)") to puppet classes and parameters, emitted as ENC YAML.
#
# The class mapping lives in /etc/manifest under packages.<pkg>.puppet:
#   packages:
#     web:
#       puppet:
#         classes: [dhfirewall]
#         params:
#           dhfirewall:
#             open_tcp: [80]

import os
import sqlite3
import urllib.parse

import yaml

from lib import metadata

query_string = urllib.parse.parse_qs(
    os.environ.get('QUERY_STRING', ''), keep_blank_values=True)
hostname = query_string['hostname'][0]


def pkgs_with_params(host):
  """pkg options incl. parsed parameters: web(port=80) -> (web, {port: 80})."""
  conn = sqlite3.connect(metadata.DB_FILE)
  c = conn.cursor()
  c.execute(
      'SELECT option.value FROM host, option '
      'WHERE host.node_id = option.node_id '
      'AND option.name = "pkg" AND host.name = ?', (host,))
  rows = [r[0] for r in c.fetchall()]
  conn.close()
  result = []
  for raw in rows:
    if raw.startswith('-'):
      continue
    name, _, rest = raw.partition('(')
    params = {}
    if rest.endswith(')'):
      for pair in rest[:-1].split(','):
        if '=' in pair:
          key, value = pair.split('=', 1)
          params[key.strip()] = int(value) if value.strip().isdigit() else value.strip()
    result.append((name, params))
  return result


def puppet_environment(host):
  """Explicit environment pin from ipplan (option puppet_environment);
  None means the agent's own choice wins (branch-env testing)."""
  conn = sqlite3.connect(metadata.DB_FILE)
  c = conn.cursor()
  c.execute(
      'SELECT option.value FROM host, option '
      'WHERE host.node_id = option.node_id '
      'AND option.name = "puppet_environment" AND host.name = ?', (host,))
  res = c.fetchone()
  conn.close()
  return res[0] if res else None


with open('/etc/manifest') as f:
  manifest = yaml.safe_load(f)

# Classification only: the ENC maps packages to classes; all class
# parameters are puppet data (hiera). pkg parameters from ipplan are
# passed through for future module generators.
classes = {}
for pkg, params in pkgs_with_params(hostname):
  spec = (manifest.get('packages', {}).get(pkg) or {}).get('puppet') or {}
  for cls in spec.get('classes', []):
    classes.setdefault(cls, {})

output = {'classes': classes if classes else {'dhfirewall': {}}}
env = puppet_environment(hostname)
if env:
  output['environment'] = env

print('')
print(yaml.safe_dump(output, default_flow_style=False))
