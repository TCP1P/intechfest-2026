# Rythme transport gateway

rsctf launches the authoritative Rythme coordinator from `src/Dockerfile`.
The coordinator owns gameplay, capability authentication, finalized native
evidence, and the managed rsctf callback.

The gateway exists because the released desktop client uses the stable HTTPS
origin `rythme-private.tcp.1pc.tf`, while rsctf replaces the shared target on a
Crown lifecycle reset. It only follows the current target and proxies the
player-facing HTTP protocol. `/api/v1/scoring` is deliberately blocked.
The current coordinator exposes no such feed; the denial remains as a
compatibility defense for an accidentally mixed old target image.

The gateway has no capability list, reporter credential, score state, or rsctf
API access. A compromise can relay or interrupt traffic, but cannot sign a
scoring snapshot.

## Production binding

`reconcile.sh` reads the active target from rsctf's PostgreSQL container,
accepts only the private `10.13.40.0/24` service range, and recreates the
stateless gateway when the target identity changes. The active event file
contains only public identifiers and an immutable gateway image ID:

```text
RSCTF_GAME_ID=<numeric game ID>
RSCTF_CHALLENGE_ID=<numeric challenge ID>
RYTHME_GATEWAY_IMAGE=sha256:<64 lowercase hex characters>
```

The Compose deployment sends HTTPS through Traefik and joins the A&D service
network only to reach the current target. It uses no state or secret volume.

## Verification

```console
go test -race ./...
go vet ./...
docker build --build-arg GATEWAY_VERSION=2026.08.28.1 -t rythme-gateway:test .
```
