ARG GO_IMAGE=golang:1.24-alpine@sha256:8bee1901f1e530bfb4a7850aa7a479d17ae3a18beb6e09064ed54cfd245b7191
ARG RYTHME_VERSION=2026.08.28.1
ARG SOURCE_REVISION=unknown
FROM ${GO_IMAGE} AS builder

ARG RYTHME_VERSION

WORKDIR /build
COPY shared/pool.json ./shared/pool.json
RUN apk add --no-cache curl jq
RUN --mount=type=cache,target=/var/cache/rythme-beatmaps,sharing=locked \
    set -eu; \
    mkdir -p /out/beatmaps /var/cache/rythme-beatmaps; \
    jq -r '.songs[] | [.id, .beatmapset_id, .archive_sha256] | @tsv' /build/shared/pool.json | \
    while IFS="$(printf '\t')" read -r song_id beatmapset_id expected_sha256; do \
      case "$song_id" in ''|*[!A-Za-z0-9._-]*) exit 1 ;; esac; \
      case "$beatmapset_id" in ''|*[!0-9]*) exit 1 ;; esac; \
      test "${#expected_sha256}" -eq 64; \
      case "$expected_sha256" in *[!0-9a-f]*) exit 1 ;; esac; \
      destination="/out/beatmaps/${song_id}.osz"; \
      cached="/var/cache/rythme-beatmaps/${song_id}.osz"; \
      downloaded=0; \
      if test -f "$cached" \
        && printf '%s  %s\n' "$expected_sha256" "$cached" | sha256sum -c -; then \
        downloaded=1; \
      else \
        for source in \
          "https://api.nerinyan.moe/d/${beatmapset_id}" \
          "https://beatconnect.io/b/${beatmapset_id}/" \
          "https://catboy.best/d/${beatmapset_id}" \
          "https://dl.sayobot.cn/beatmaps/download/full/${beatmapset_id}"; do \
          if curl --fail --location --silent --show-error \
            --retry 3 --retry-all-errors --connect-timeout 10 --max-time 120 \
            --max-filesize 100663296 "$source" --output "$cached" \
            && printf '%s  %s\n' "$expected_sha256" "$cached" | sha256sum -c -; then \
            downloaded=1; \
            break; \
          fi; \
        done; \
      fi; \
      test "$downloaded" -eq 1; \
      cp "$cached" "$destination"; \
    done; \
    chown -R 65532:65532 /out/beatmaps
COPY server/go.mod ./server/go.mod
COPY server ./server
WORKDIR /build/server
RUN go test ./... \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -buildvcs=false \
    -trimpath \
    -ldflags="-s -w -X main.serverBuild=${RYTHME_VERSION}" \
    -o /out/rythme-server \
    ./cmd/rythme-server
RUN mkdir -p /out/state && chown 65532:65532 /out/state

FROM scratch

ARG RYTHME_VERSION
ARG SOURCE_REVISION

COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /out/rythme-server /app/rythme-server
COPY --from=builder --chown=65532:65532 /out/beatmaps /app/beatmaps
COPY --from=builder --chown=65532:65532 /out/state /var/lib/rythme
COPY shared/pool.json /app/pool.json

ENV RYTHME_LISTEN=:1337
ENV RYTHME_POOL_PATH=/app/pool.json
ENV RYTHME_BEATMAP_DIR=/app/beatmaps
ENV RYTHME_BEATMAP_PROXY=false
ENV RYTHME_READY_DURATION=30s
ENV RYTHME_STATE_PATH=/var/lib/rythme/arena.json

LABEL org.opencontainers.image.title="Rythme coordinator"
LABEL org.opencontainers.image.description="Durable server-authoritative Rythme Crown arena"
LABEL org.opencontainers.image.version="${RYTHME_VERSION}"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/TCP1P/intechfest-2026/tree/main/events/main-event/challenges/Koth/Reverse/rythme"

USER 65532:65532
EXPOSE 1337
HEALTHCHECK --interval=5s --timeout=2s --start-period=3s --retries=12 \
    CMD ["/app/rythme-server", "healthcheck"]
ENTRYPOINT ["/app/rythme-server"]
