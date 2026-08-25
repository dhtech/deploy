#!/usr/bin/env python3
# JSON status endpoint for the deploy frontend (read-only Redis views).
# Needs /etc/deploy.yaml to contain redis information.

import json
import sqlite3
import time

import redis
import yaml


def connection():
    with open('/etc/deploy.yaml') as f:
        config = yaml.safe_load(f)
    return redis.Redis(**config['redis'])


def fleet():
    """Every host ipplan knows, VM or not - the deployment source of
    truth, so hand-enrolled machines (the hypervisors) show up too."""
    conn = sqlite3.connect('file:/etc/ipplan.db?mode=ro', uri=True)
    c = conn.cursor()
    options = {}
    for node_id, name, value in c.execute(
            'SELECT node_id, name, value FROM option '
            'WHERE name IN ("pkg", "os")'):
        options.setdefault(node_id, []).append((name, value))
    rows = []
    for node_id, name, ip, net, vlan in c.execute(
            'SELECT host.node_id, host.name, host.ipv4_addr_txt, '
            'network.name, network.vlan FROM host '
            'JOIN network ON host.network_id = network.node_id'):
        opts = options.get(node_id, [])
        pkgs = [v.split('(')[0] for n, v in opts
                if n == 'pkg' and not v.startswith('-')]
        os_name = next((v for n, v in opts if n == 'os'), '')
        rows.append({'hostname': name, 'ip': ip,
                     'network': net.split('@', 1)[0], 'vlan': vlan,
                     'os': os_name, 'pkgs': pkgs})
    conn.close()
    return rows


def collect(store):
    data = {'hosts': [], 'vm_orders': [], 'hw_orders': [], 'vms': [], 'bays': {}}

    for key in store.keys('host-*'):
        key = key.decode()
        try:
            props = json.loads(store.get(key))
        except (ValueError, TypeError):
            continue
        hostname = key.split('-', 1)[1]
        last_log = store.get('last-log-' + hostname)
        if props.get('installed'):
            if not props.get('provisioned'):
                state = 'waiting-for-provision'
            elif props.get('puppet_time'):
                state = 'done'
            elif props.get('puppet_ssl_time'):
                state = 'puppet-ssl'
            else:
                state = 'converging'
        elif last_log:
            state = 'installing'
        else:
            state = 'starting'
        started = props.get('started')
        finished = props.get('finished')
        duration = None
        if started:
            duration = int((finished or time.time()) - started)
        data['hosts'].append({
            'hostname': hostname,
            'started': started,
            'duration': duration,
            'finished': finished,
            'puppet_time': props.get('puppet_time'),
            'product': props.get('product', ''),
            'state': state,
            'log': last_log.decode(errors='replace') if last_log else None,
            'error': props.get('error'),
            'ttl': store.ttl(key),
        })

    for key in store.keys('create-vm-*'):
        try:
            props = json.loads(store.get(key))
        except (ValueError, TypeError):
            continue
        data['vm_orders'].append({
            'name': props.get('name'),
            'manager': props.get('manager'),
            'error': props.get('error'),
            'ttl': store.ttl(key),
        })

    for key in store.keys('install-*'):
        # install-ip-* are installer identity mappings, not orders
        if key.decode().startswith('install-ip-'):
            continue
        try:
            props = json.loads(store.get(key))
        except (ValueError, TypeError):
            continue
        data['hw_orders'].append({
            'name': props.get('name'),
            'manager': props.get('manager'),
            'bay': props.get('bay'),
            'ttl': store.ttl(key),
        })

    for key in store.keys('vm-*'):
        key = key.decode()
        try:
            props = json.loads(store.get(key))
        except (ValueError, TypeError):
            continue
        data['vms'].append({
            'name': props.get('name'),
            'manager': props.get('manager'),
        })

    for key in store.keys('bays-*'):
        key = key.decode()
        data['bays'][key.split('-', 1)[1]] = json.loads(store.get(key))

    # entry order, newest on top
    data['hosts'].sort(key=lambda h: -(h['started'] or 0))
    data['vms'].sort(key=lambda v: (v['manager'] or '', v['name'] or ''))

    # the fleet: ipplan joined with the provisioners' VM inventory
    managers = {v['name']: v['manager'] for v in data['vms']}
    data['fleet'] = fleet()
    for row in data['fleet']:
        row['manager'] = managers.get(row['hostname'])
    data['fleet'].sort(key=lambda r: (r['vlan'] or 0, r['ip'] or ''))
    return data


print('Content-Type: application/json')
print('')
print(json.dumps(collect(connection())))
