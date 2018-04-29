FROM debian:testing

RUN apt-get update && apt-get install -y stunnel4 dumb-init redis

RUN sed -i 's/ENABLED=0/ENABLED=1/' /etc/default/stunnel4
COPY stunnel.conf /etc/stunnel/
RUN mkdir -p /etc/redis/
COPY redis.conf /etc/redis/redis.conf

COPY start.sh /
ENV REDIS_PASSWORD dummy
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/start.sh"]
