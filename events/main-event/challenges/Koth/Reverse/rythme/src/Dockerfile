ARG GO_IMAGE=golang:1.24-alpine@sha256:8bee1901f1e530bfb4a7850aa7a479d17ae3a18beb6e09064ed54cfd245b7191
ARG RYTHME_VERSION=2026.08.21.1
ARG SOURCE_REVISION=unknown
FROM ${GO_IMAGE} AS builder

ARG RYTHME_VERSION

WORKDIR /build
COPY server/go.mod ./server/go.mod
COPY server ./server
WORKDIR /build/server
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -buildvcs=false \
    -trimpath \
    -ldflags="-s -w -X main.serverBuild=${RYTHME_VERSION}" \
    -o /out/rythme-server \
    ./cmd/rythme-server
RUN mkdir -p /out/beatmaps && chown 65532:65532 /out/beatmaps
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
ENV RYTHME_BEATMAP_PROXY=true
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
