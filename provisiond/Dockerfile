FROM debian:testing

RUN apt-get update && \
  apt-get install -y python-redis python-libxml2 python-netsnmp python-pip \
    python-yaml
RUN pip install pysphere hvac

COPY provision /usr/local/lib/python2.7/dist-packages/provision
COPY provisiond /usr/local/bin/

COPY config.yaml.sample /etc/provision/config.yaml

ENV VAULT_MOUNT dummy
ENV VMWARE_VCENTER_ISO /srv/vmware-vcenter.iso

CMD ["/usr/local/bin/provisiond"]
