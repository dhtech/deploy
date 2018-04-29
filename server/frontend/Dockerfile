FROM debian:testing

RUN apt-get update && apt-get install -y \
  dumb-init apache2 python-redis python-yaml
RUN a2enmod cgi

ADD frontend/vhost.conf /etc/apache2/sites-available/000-default.conf
ADD frontend/ports.conf /etc/apache2/ports.conf
ADD frontend/index.py /var/www/
ADD frontend/start.sh /
ADD libdhdeploy /usr/local/lib/python2.7/dist-packages/dhdeploy/

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/start.sh"]
