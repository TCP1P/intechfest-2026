ARG GO_IMAGE=golang:1.25.1-bookworm@sha256:c423747fbd96fd8f0b1102d947f51f9b266060217478e5f9bf86f145969562ee
ARG GATEWAY_VERSION=2026.08.28.1
ARG SOURCE_REVISION=unknown

FROM ${GO_IMAGE} AS build

ARG GATEWAY_VERSION

WORKDIR /src
COPY go.mod *.go ./
RUN go test ./... \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags="-s -w -buildid= -X main.gatewayBuild=${GATEWAY_VERSION}" -o /out/gateway .

FROM scratch

ARG GATEWAY_VERSION
ARG SOURCE_REVISION

COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/gateway /usr/local/bin/gateway

LABEL org.opencontainers.image.title="Minions in 32K transport gateway"
LABEL org.opencontainers.image.description="Stateless HTTPS and UDP adapter for the RSCTF-managed Minions target"
LABEL org.opencontainers.image.version="${GATEWAY_VERSION}"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/TCP1P/intechfest-2026/tree/main/events/main-event/challenges/Koth/PPC/minions-in-32k/gateway"

USER 32033:32033
EXPOSE 8080/tcp 4532/udp
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=18 \
    CMD ["/usr/local/bin/gateway", "healthcheck"]
ENTRYPOINT ["/usr/local/bin/gateway"]
