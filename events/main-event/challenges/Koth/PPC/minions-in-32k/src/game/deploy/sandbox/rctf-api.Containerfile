ARG RUST_IMAGE=rust:1.96.0-bookworm@sha256:5e2214abe154fe26e39f64488952e5c991eeed1d6d6da7cc8381ae83927f0cfc
ARG RUNTIME_IMAGE=debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241

FROM ${RUST_IMAGE} AS build

WORKDIR /src
COPY . .
RUN cargo build --locked --release -p backend

FROM ${RUNTIME_IMAGE} AS runtime

RUN groupadd --system --gid 10001 minions \
    && useradd --system --uid 10001 --gid 10001 --home-dir /srv/minions --shell /usr/sbin/nologin minions \
    && mkdir -p /srv/minions/state /srv/minions/wasm-pkg \
    && chown -R minions:minions /srv/minions

COPY --from=build /src/target/release/backend /usr/local/bin/minions-rctf-api
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

USER 10001:10001
WORKDIR /srv/minions
EXPOSE 8080/tcp

ENTRYPOINT ["/usr/local/bin/minions-rctf-api"]
CMD ["--bind", "0.0.0.0:8080", "--instancer", "k8s", "--state-file", "/srv/minions/state/backend.json", "--wasm-dir", "/srv/minions/wasm-pkg"]
