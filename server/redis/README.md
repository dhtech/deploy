# redis

Redis image with stunnel infront for SSL.
Set `REDIS_PASSWORD` to what you want the Redis server to use.

Listens to port 1338.
Expects SSL certificates in `/etc/ssl/deploy-redis/deploy.{crt,key}`.
