ARG GO_IMAGE=golang:1.24-alpine@sha256:8bee1901f1e530bfb4a7850aa7a479d17ae3a18beb6e09064ed54cfd245b7191
ARG GATEWAY_VERSION=2026.08.28.1
ARG SOURCE_REVISION=unknown

FROM ${GO_IMAGE} AS build

ARG GATEWAY_VERSION
WORKDIR /src
COPY go.mod *.go ./
RUN go test ./... \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath \
      -ldflags="-s -w -buildid= -X main.gatewayBuild=${GATEWAY_VERSION}" \
      -o /out/gateway .

FROM scratch

ARG GATEWAY_VERSION
ARG SOURCE_REVISION
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/gateway /usr/local/bin/gateway

LABEL org.opencontainers.image.title="Rythme transport gateway"
LABEL org.opencontainers.image.description="Stateless HTTPS adapter for the RSCTF-managed Rythme target"
LABEL org.opencontainers.image.version="${GATEWAY_VERSION}"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
LABEL org.opencontainers.image.source="https://github.com/TCP1P/intechfest-2026/tree/main/events/main-event/challenges/Koth/Reverse/rythme/gateway"

USER 65533:65533
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=18 \
    CMD ["/usr/local/bin/gateway", "healthcheck"]
ENTRYPOINT ["/usr/local/bin/gateway"]
