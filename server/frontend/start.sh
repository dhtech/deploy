#!/bin/bash -xe

ls -R /etc/apache2/
ls /var/www/
/usr/sbin/apache2ctl start
tail -f /var/log/apache2/*.log
