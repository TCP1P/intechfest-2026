# syntax=docker/dockerfile:1.7

ARG RUST_IMAGE=rust:1.96.0-bookworm@sha256:5e2214abe154fe26e39f64488952e5c991eeed1d6d6da7cc8381ae83927f0cfc
ARG GO_IMAGE=golang:1.25.1-bookworm@sha256:c423747fbd96fd8f0b1102d947f51f9b266060217478e5f9bf86f145969562ee
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241
ARG MINIONS_VERSION=2026.08.21.1
ARG SOURCE_REVISION=unknown

FROM ${RUST_IMAGE} AS rust-build

ENV RUSTUP_TOOLCHAIN=1.96.0
WORKDIR /build
COPY game/ ./
RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,target=/build/target,sharing=locked \
    cargo build --locked --release -p server -p headless-client \
    && install -Dm755 target/release/server /out/minions-server \
    && install -Dm755 target/release/headless-client /out/minions-headless

FROM ${GO_IMAGE} AS supervisor-build

ARG MINIONS_VERSION
WORKDIR /build
COPY supervisor/ ./
RUN --mount=type=cache,target=/root/.cache/go-build,sharing=locked \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath \
      -ldflags="-s -w -buildid= -X main.supervisorBuild=${MINIONS_VERSION}" \
      -o /out/minions-supervisor .

FROM ${RUNTIME_IMAGE}

ARG MINIONS_VERSION
ARG SOURCE_REVISION

RUN groupadd --gid 32032 minions \
    && useradd --uid 32032 --gid 32032 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin minions \
    && install -d -o 32032 -g 32032 -m 700 /tmp/minions-replays

COPY --from=rust-build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=rust-build /out/minions-server /usr/local/bin/minions-server
COPY --from=rust-build /out/minions-headless /usr/local/bin/minions-headless
COPY --from=supervisor-build /out/minions-supervisor /usr/local/bin/minions-supervisor

LABEL org.opencontainers.image.title="Minions in 32K arena"
LABEL org.opencontainers.image.description="Server-authoritative objective-aware Minions arena"
LABEL org.opencontainers.image.version="${MINIONS_VERSION}"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/TCP1P/intechfest-2026/tree/main/events/main-event/challenges/Koth/PPC/minions-in-32k"

ENV REPORT_URL=http://rsctf-minions-bridge:8081/internal/match-report \
    REPLAY_UPLOAD_URL=http://rsctf-minions-bridge:8081/internal/replay \
    MINIONS_GRACE_SECS=30 \
    MINIONS_MATCH_SECS=300 \
    MINIONS_SCORING_LEG_SECS=60 \
    RUST_LOG=info,wtransport=warn

USER 32032:32032
EXPOSE 8080/tcp 4433/udp
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD ["/usr/local/bin/minions-supervisor", "healthcheck"]
ENTRYPOINT ["/usr/local/bin/minions-supervisor"]
