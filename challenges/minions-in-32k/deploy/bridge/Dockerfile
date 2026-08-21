ARG GO_IMAGE=golang:1.25.1-bookworm@sha256:c423747fbd96fd8f0b1102d947f51f9b266060217478e5f9bf86f145969562ee
ARG BRIDGE_VERSION=dev
ARG SOURCE_REVISION=unknown

FROM ${GO_IMAGE} AS build

ARG BRIDGE_VERSION

WORKDIR /src
COPY go.mod *.go ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags="-s -w -buildid= -X main.bridgeBuild=${BRIDGE_VERSION}" -o /out/bridge .

FROM scratch

ARG BRIDGE_VERSION
ARG SOURCE_REVISION

COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/bridge /usr/local/bin/bridge

LABEL org.opencontainers.image.title="Minions in 32K RSCTF bridge"
LABEL org.opencontainers.image.description="Durable capability and scoring bridge for Minions in 32K"
LABEL org.opencontainers.image.version="${BRIDGE_VERSION}"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/dimasma0305/idea/tree/main/intechfest/challenges/minions-in-32k/deploy/bridge"

USER 32033:32033
EXPOSE 8080/tcp 8081/tcp 4532/udp
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=18 \
    CMD ["/usr/local/bin/bridge", "healthcheck"]
ENTRYPOINT ["/usr/local/bin/bridge"]
