#!/bin/bash

# This might show up in "ps ax" on the host. Do we care?
sed -i "s/REDIS_PASSWORD/$REDIS_PASSWORD/g" /etc/redis/redis.conf

/usr/bin/stunnel4 /etc/stunnel/stunnel.conf &

exec /usr/bin/redis-server /etc/redis/redis.conf
