# Main-event production release contract

Release candidate: `2026.08.28.1`

This frozen release candidate covers only the INTECHFEST main event. The hidden warmup is
still an unfrozen staging candidate; record its BYOC, Marker, and managed Leaderboard
topology plus exact image identities here before promoting it. Yani-netlink is a draft
self-hosted/BYOC addition and remains disabled until its hidden staging gates pass. Live game IDs, capabilities,
reporter secrets, arena
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

Yani-netlink has no `dist/` attachment; its player-facing artifact is the BYOC service image.
Its current draft service identity is:

| Artifact | SHA-256 |
| --- | --- |
| `bzImage` | `0262643f67d2939e43261a7577a15fd2a0d04f191979cc851ec81d46432e9a4a` |
| `initramfs.cpio.gz` | `ff06f3f8278c424021c3407d1752e24797b16c426cd353ccd63f21c7ec5d9e89` |
| `yani_netfilter.c` snippet | `cd008d236232aa989537634767e0e4c520cd295e06105aef61232daf8beaee14` |
| `vmlinux.unstripped` | `48bc49a2b9995dea834630568412483be0af58e85b20aec4984c846abdfdf5db` |
| Docker image index | `sha256:bb0700192797c342b5beae86730eb0cf68fa1dd55dcec6232682102e7980d790` |
| Docker image config | `sha256:859b7b03a3bbdce492b72a2f8fa5b24c42082fb976a961f00ac02f17e83194d5` |

## Runtime topology

Rythme is one rsctf-managed shared coordinator plus one stateless HTTPS gateway.
The coordinator owns gameplay,
health, its finalized-wave ledger, and managed evidence reporting; there is no
duplicate health-only target or private scoring endpoint. rsctf injects exact
context/observation URLs and a lifecycle-bound credential when it creates the
active managed Leaderboard target. The coordinator filters the credential out of player
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

Yani-netlink is one self-hosted/BYOC A&D kernel target with no gateway or managed reporter.
Its built-in relay owns TCP/UDP port `31337`; the initramfs has no interactive serial shell
and keeps PID 1 alive with a five-second sleep loop. The A&D checker validates the rotating
flag, critical file and memory hashes, CPU policy, and safe protocol probes. The image
includes an incomplete source snippet with only `EAT`, `PUKE`, and `SMOKING`, plus
`vmlinux.unstripped` for reverse engineering.

Set `RSCTF_KOTH_REPORTER_BASE_URL` to the private control-service origin before
enabling Leaderboard scoring. Both gateway Compose files use a read-only
filesystem, non-root user, dropped capabilities, `no-new-privileges`, bounded
CPU/memory/PIDs and logs, a health check, and only the A&D-services plus Traefik
networks.

## Release gates

Run these from the repository root before creating immutable images:

```sh
make check RSCTF=/path/to/rsctf
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
(cd events/main-event/challenges/AD/Pwn/yani-netlink/checker && python3 -m unittest test_run.py)
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
