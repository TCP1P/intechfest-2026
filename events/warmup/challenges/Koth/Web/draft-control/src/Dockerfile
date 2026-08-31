FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup -S -g 10000 draft \
    && adduser -S -D -H -u 10000 -G draft draft \
    && install -d -o draft -g draft -m 0750 /srv/pages \
    && printf 'Release desk: existing drafts may be replaced.\n' > /srv/pages/brief.txt \
    && printf 'Warmup publishing queue is ready.\n' > /srv/pages/status.txt \
    && chown draft:draft /srv/pages/brief.txt /srv/pages/status.txt \
    && install -d -o draft -g draft -m 01777 /koth \
    && printf 'unclaimed' > /koth/king \
    && chown draft:draft /koth/king

WORKDIR /srv
COPY --chown=root:root app.py /srv/app.py
RUN chmod 0555 /srv/app.py

USER 10000:10000
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=2s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2).read().strip()!=b'ok')"]
CMD ["python", "-u", "/srv/app.py"]
