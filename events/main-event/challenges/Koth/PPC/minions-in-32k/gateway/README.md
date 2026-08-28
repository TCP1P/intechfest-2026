# Minions transport gateway

rsctf launches the authoritative arena from `src/Dockerfile`. That target owns
capability authentication, the generated arena roster, match validation, replay
retention, scoring evidence, and the managed rsctf callback.

The gateway exists only because the current challenge manifest exposes one TCP
port while the game protocol uses UDP. It performs two bounded operations:

- forwards `POST /api/v1/arena-session` to the current target and replaces only
  the returned public UDP address;
- relays rate-limited UDP sessions to the target's port `4433`.

It has no scoring secret, roster credentials, score state, or
rsctf network access. A compromised gateway can interrupt or relay traffic, but
cannot sign a scoring snapshot.

## Production binding

`reconcile.sh` reads the active target from rsctf's PostgreSQL container,
accepts only the private `10.13.40.0/24` service range, and recreates the
stateless gateway when the target identity changes. The active event file needs
the numeric game and challenge IDs plus an immutable gateway image ID:

```text
RSCTF_GAME_ID=<numeric game ID>
RSCTF_CHALLENGE_ID=<numeric challenge ID>
MINIONS_GATEWAY_IMAGE=sha256:<64 lowercase hex characters>
```

The Compose deployment publishes UDP `4532` and HTTPS metadata/session traffic
through Traefik. No secret or state volume is required.

## Verification

```console
go test -race ./...
go vet ./...
docker build --build-arg GATEWAY_VERSION=2026.08.28.1 -t minions-gateway:test .
```
