#!/usr/bin/env python3
# JSON status endpoint for the deploy frontend (read-only Redis views).
# Needs /etc/deploy.yaml to contain redis information.

import json
import time

import redis
import yaml


def connection():
    with open('/etc/deploy.yaml') as f:
        config = yaml.safe_load(f)
    return redis.Redis(**config['redis'])


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
    return data


print('Content-Type: application/json')
print('')
print(json.dumps(collect(connection())))
