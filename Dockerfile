# Builder
FROM debian:bullseye

RUN echo "deb-src http://ftp.debian.org/debian bullseye main" \
       > /etc/apt/sources.list.d/src.list
RUN apt-get update
RUN apt-get install -y build-essential

RUN mkdir /data
COPY ca.crt /build/

COPY ipxe /build/ipxe/
COPY build-ipxe /build/
RUN /build/build-ipxe

COPY build-esxi vmware-esxi.iso /build/
RUN /build/build-esxi

COPY debian /build/debian/
COPY build-debian /build/
RUN /build/build-debian

# Runtime container
FROM debian:bullseye
COPY --from=0 /data/ /data/

RUN apt-get update
