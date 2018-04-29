FROM debian:testing

RUN apt-get update && apt-get install -y \
  dumb-init python-redis python-sqlite python-yaml

COPY syslog-receiver /usr/local/bin/

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/usr/local/bin/syslog-receiver"]
