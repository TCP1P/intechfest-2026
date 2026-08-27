ARG RUST_IMAGE=rust:1.96.0-bookworm@sha256:5e2214abe154fe26e39f64488952e5c991eeed1d6d6da7cc8381ae83927f0cfc
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241

FROM ${RUST_IMAGE} AS build

WORKDIR /src
COPY . .
RUN cargo build --locked --release -p server

FROM ${RUNTIME_IMAGE} AS runtime

RUN groupadd --system --gid 10001 minions \
    && useradd --system --uid 10001 --gid 10001 --home-dir /srv/minions --shell /usr/sbin/nologin minions \
    && mkdir -p /srv/minions/replays /srv/minions/config \
    && chown -R minions:minions /srv/minions

COPY --from=build /src/target/release/server /usr/local/bin/minions-server
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY deploy/prod.toml /srv/minions/config/feature_flags.toml

USER 10001:10001
WORKDIR /srv/minions
EXPOSE 4433/udp
VOLUME ["/srv/minions/replays"]

ENTRYPOINT ["/usr/local/bin/minions-server"]
CMD ["--bind", "0.0.0.0:4433", "--replay-dir", "/srv/minions/replays", "--feature-flags", "/srv/minions/config/feature_flags.toml"]
