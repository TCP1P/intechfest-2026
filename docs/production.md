# Production release contract

Release candidate: `2026.08.28.1`

This document defines the production-ready artifact and deployment contract for
the INTECHFEST event. Live game IDs, capabilities, reporter secrets, arena
credentials, and TLS private keys are operational state and must never be
committed.

## Player artifacts

| Challenge | File | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Rythme | `events/main-event/challenges/Koth/Reverse/rythme/dist/reversing_rythme.zip` | 165891448 | `91f378e90010349718074072859706813838af6e281006528a19f941283d9c24` |
| Minions in 32K | `events/main-event/challenges/Koth/PPC/minions-in-32k/dist/game_minions-in-32k.zip` | 37848231 | `6fd7624e24b75b12fc95dcdee18a4ce75b9d54e0509f39a5694f173b3c0c980e` |

Each package's `solution/handout.sha256` is authoritative. Publish the archives by content
hash and verify both the stored content length and digest before binding an attachment to a
challenge.

## Runtime topology

Rythme is one rsctf-managed shared coordinator plus one stateless HTTPS gateway.
The coordinator owns gameplay,
health, its finalized-wave ledger, and managed evidence reporting; there is no
duplicate health-only target or private scoring endpoint. rsctf injects exact
context/observation URLs and a lifecycle-bound credential when it creates the
active API-scored target. The coordinator filters the credential out of player
responses and submits native heat evidence, never platform points. The gateway
keeps the released client's stable hostname pointed at the current target. The
target exposes no scoring endpoint, and the gateway also denies the retired path as
a compatibility defense; it has no scoring state or credential.

Minions is one rsctf-managed shared arena target plus one stateless transport
gateway. The supervisor authenticates event capabilities, deterministically
builds the eligible roster, starts the child game server, validates ordered
match/replay evidence, and reports it directly to rsctf. It filters every
`RSCTF_KOTH_*` value from the child environment and passes only a
domain-separated loopback report credential. The gateway terminates public
HTTPS metadata/session traffic and relays bounded UDP sessions to the current
target. It has no secret volume, roster, replay, or scoring state.

Minions has exactly eight arena slots. The main event must freeze no more than
eight accepted teams before official KotH scoring starts; unused slots become
NPCs. A larger field requires a deliberate game-capacity change and a new load
rehearsal, not a production configuration override.

Set `RSCTF_KOTH_REPORTER_BASE_URL` to the private control-service origin before
enabling Leaderboard scoring. Both gateway Compose files use a read-only
filesystem, non-root user, dropped capabilities, `no-new-privileges`, bounded
CPU/memory/PIDs and logs, a health check, and only the A&D-services plus Traefik
networks.

## Release gates

Run these from the repository root before creating immutable images:

```sh
make check
gitleaks git --staged --config .gitleaks.toml .

(cd events/main-event/challenges/Koth/Reverse/rythme/src/server && go test -race ./... && go vet ./...)
(cd events/main-event/challenges/Koth/Reverse/rythme/gateway && go test -race ./... && go vet ./...)
(cd events/main-event/challenges/Koth/Reverse/rythme/checker && python3 -m unittest test_run.py)

(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo +1.96.0 fmt --all -- --check)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo +1.96.0 check --locked -p client -p server -p spectator)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo +1.96.0 test --locked --workspace --all-targets)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo +1.96.0 run --locked --release -p sim --example verify_scenes)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo audit)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/supervisor && go test -race ./... && go vet ./...)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/gateway && go test -race ./... && go vet ./...)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/checker && python3 -m unittest test_run.py)
```

Run both `build-release.sh` scripts, validate the ZIP central directories, and
check the adjacent checksum files. Build runtime and gateway images with
`SOURCE_REVISION=<committed SHA>` and `*_VERSION=2026.08.28.1`; deploy only
their full `sha256:` image IDs.

## Promotion and rollback

Rehearsal and production use the same artifacts. A hidden/private game is the
promotion stage; visibility is an RSCTF access-policy change, not a rebuild.
Before making it discoverable, require the canonical platform health response,
healthy challenge replicas on the expected image IDs, live checker passes, one
real capability exchange per challenge, one accepted native-evidence snapshot,
a reset proving the old target credential is stale, and clean recent logs.

Rollback means restoring the previous immutable target and gateway image IDs.
A platform target reset starts a new scoring lifecycle and rotates the managed
credential. An interrupted Rythme heat is intentionally void; the new target
starts a fresh four-mode series. Do not reuse an old target or manually copy a
reporter credential across lifecycle generations.
