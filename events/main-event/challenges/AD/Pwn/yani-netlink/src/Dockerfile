FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        coreutils \
        qemu-system-x86 \
        python3 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/yani /run/rsctf
COPY bzImage /opt/yani/bzImage
COPY initramfs.cpio.gz /opt/yani/initramfs.cpio.gz
COPY start.sh /opt/yani/start.sh
COPY healthcheck.py /opt/yani/healthcheck.py
COPY readme.txt /opt/yani/readme.txt
COPY yani_netfilter.c /opt/yani/yani_netfilter.c
COPY kernel.config /opt/yani/kernel.config
COPY Module.symvers /opt/yani/Module.symvers
COPY vmlinux.unstripped.part-* /tmp/yani-vmlinux/
RUN chmod 0755 /opt/yani/start.sh /opt/yani/healthcheck.py \
    && cat /tmp/yani-vmlinux/vmlinux.unstripped.part-* > /opt/yani/vmlinux.unstripped \
    && chmod 0444 /opt/yani/readme.txt /opt/yani/yani_netfilter.c /opt/yani/kernel.config /opt/yani/vmlinux.unstripped /opt/yani/Module.symvers \
    && rm -rf /tmp/yani-vmlinux

EXPOSE 31337/tcp
EXPOSE 31337/udp
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python3", "/opt/yani/healthcheck.py"]
CMD ["/opt/yani/start.sh"]
