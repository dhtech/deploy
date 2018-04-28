#!/usr/bin/env python2
import collections
import json

from lib import metadata


store = metadata.connection()

print 'Content-Type: text/html'
print ''
print '<html>'
print '''
<head>
<title>remote deploy server</title>
<link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/css/bootstrap.min.css" integrity="sha384-1q8mTJOASx8j1Au+a5WDVnPi2lkFfwwEAa8hDDdjZlpLegxhjVME1fgjWPGmkzs7" crossorigin="anonymous">
<link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.6/css/bootstrap-theme.min.css" integrity="sha384-fLW2N01lMqjakBkx3l/M9EahuwpSfeNvV63J5ezn3uZzapT0u7EYsXMjQV+0En5r" crossorigin="anonymous">
<meta http-equiv="refresh" content="5">
</head>'''

print '<body style="padding-top: 50px;">'
print '''
<nav class="navbar navbar-inverse navbar-fixed-top">
  <div class="container">
    <div class="navbar-header">
      <a class="navbar-brand" href="#">remote deploy server</a>
    </div>
  </div>
</nav>
'''
print '<div class="container">'

print '<h2>Current Installs</h2>'
print '<table class="table">'
print '<tr><th>Host</th><th>Product</th><th width="50%">State</th><th>TTL</th></tr>'
for i in store.keys('host-*'):
  props = json.loads(store.get(i))
  hostname = i.split('-', 1)[1]
  last_log = store.get('last-log-' + hostname)
  state_cls = 'info'
  if props['installed']:
    if props['provisioned']:
      state = 'Done'
      state_cls = ''
    else:
      state = 'Waiting for provision'
  elif last_log:
    state = 'Log: ' + last_log
  else:
    state = 'Starting installation'
  print '<tr class="%s"><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
          state_cls, hostname, props['product'], state, store.ttl(i))
print '</table>'


print '<h2>Install Orders</h2>'
print '<h3>VMware</h3>'
print '<table class="table">'
print '<tr><th>Host</th><th>Destination</th><th>TTL</th></tr>'
for i in store.keys('create-vm-*'):
  props = json.loads(store.get(i))
  print '<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
          props['name'], props['manager'], store.ttl(i))
print '</table>'

print '<h3>Physical</h3>'
print '<table class="table">'
print '<tr><th>Host</th><th>Destination</th><th>Bay</th><th>TTL</th></tr>'
for i in store.keys('install-*'):
  props = json.loads(store.get(i))
  print '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
          props['name'], props['manager'], props['bay'], store.ttl(i))
print '</table>'

print '<h2>Known VMware hosts</h2>'
print '<table class="table">'
print '<tr><th>Host</th><th>Provisioner</th></tr>'
vms = collections.defaultdict(list)
for i in store.keys('vmware-*'):
  provisioner = i.split('-')[1]
  props = json.loads(store.get(i))
  vms[provisioner].append(props)

for provisioner in sorted(vms.keys()):
  for props in vms[provisioner]:
    print '<tr><td>%s</td><td>%s</td></tr>' % (props['name'], provisioner)
print '</table>'

print '<h2>Known C7000 bays</h2>'
for i in store.keys('bays-*'):
  print '<h3>%s</h3>' % i.split('-', 1)[1]
  print '<table class="table">'
  bays = json.loads(store.get(i))
  for bay in sorted(int(x) for x in bays.keys()):
    props = bays[str(bay)]
    serial = props['serial'] if props else ''
    print '<tr><td>%s</td><td>%s</td></tr>' % (bay, serial)
  print '</table>'


print '</div>'
print '</body></html>'
