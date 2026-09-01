FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31 AS tests

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /test
COPY app.py capability_auth.py http_transport.py reporter.py test_capability_auth.py test_game.py test_reporter.py /test/
COPY static/ /test/static/
RUN python -m unittest -v test_capability_auth.py test_game.py test_reporter.py

FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=tests --chown=nobody:nogroup /test/app.py /app/app.py
COPY --from=tests --chown=nobody:nogroup /test/capability_auth.py /app/capability_auth.py
COPY --from=tests --chown=nobody:nogroup /test/http_transport.py /app/http_transport.py
COPY --from=tests --chown=nobody:nogroup /test/reporter.py /app/reporter.py
COPY --from=tests --chown=nobody:nogroup /test/static/ /app/static/

USER nobody
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=2s --retries=3 \
  CMD ["python", "-c", "import json,sys,urllib.request; value=json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2)); sys.exit(value.get('status')!='ok' or value.get('protocol')!='serpent-circuit/4')"]
CMD ["python", "-u", "/app/app.py"]
