#!/usr/bin/env python3
# Expose the host's production network settings as iPXE variables.
#
# Gen-3 flow: the machine installs entirely on the deployment VLAN, so by
# default this only *sets* the variables (the old 'noset' behavior); pass
# vcreate=1 to actually switch iPXE onto the production VLAN (legacy flow).

import os
import urllib.parse

import runtime
from ipplanlib import metadata

query_string = urllib.parse.parse_qs(os.environ.get('QUERY_STRING', ''))
hostname = query_string['hostname'][0]
network = runtime.installation_network(hostname)

print('')
print('#!ipxe')
for key, value in sorted(network.items()):
    print('set', key, value)

if 'vcreate' in query_string:
    # Legacy: move iPXE itself onto the production VLAN
    print('set net0/ip 0.0.0.0')
    print('vcreate --tag ${vlan} net0')
    print('set net0-${vlan}/ip ${v4_address}')
    print('set net0-${vlan}/netmask ${v4_netmask}')
    print('set net0-${vlan}/gateway ${v4_gateway}')

print('echo Production address ${v4_address} on VLAN ${vlan}')
