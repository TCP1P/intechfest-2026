# Main-event production release contract

Release candidate: `2026.09.04.1`

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
| Rythme | `events/main-event/challenges/Koth/Reverse/rythme/dist/reversing_rythme.zip` | 165890588 | `0435b353a2342f1b1e9855b97a78224fef647e06d4dbbfd2407fcb0db9a32c18` |
| Minions in 32K | `events/main-event/challenges/Koth/PPC/minions-in-32k/dist/game_minions-in-32k.zip` | 37844249 | `25f164e54aff5df40bf3637b2935de863b747fd094faada11aeb482f4500d1a4` |

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
| Docker image index | `sha256:57b2a9d0f36694a12dc4d4411880d489cb301be17ff77ad6e669de5440e6efc6` |
| Docker image config | `sha256:e2404332b6e855defd75ae94dcc2a83f20d18917e10fd224b60e26f95ef47a1e` |

The unstripped ELF is stored in the Docker context as three sub-50 MB parts. The
Dockerfile joins them into the read-only `/opt/yani/vmlinux.unstripped` path.

## Runtime topology

Rythme is one rsctf-managed shared coordinator. The coordinator owns gameplay,
health, its finalized-wave ledger, and managed evidence reporting; there is no
duplicate health-only target or private scoring endpoint. rsctf injects exact
context/observation URLs and a lifecycle-bound credential when it creates the
active managed Leaderboard target. The coordinator filters the credential out of player
responses and submits native heat evidence, never platform points. Players use the current
private IPv4 target through the event VPN on the declared TCP port. A target reset may change
that address, so the challenge card remains the authoritative source.

Minions is one rsctf-managed shared arena target. The supervisor authenticates event
capabilities, deterministically
builds the eligible roster, starts the child game server, validates ordered
match/replay evidence, and reports it directly to rsctf. It filters every
`RSCTF_KOTH_*` value from the child environment and passes only a
domain-separated loopback report credential. The HTTP session API and QUIC gameplay bind
TCP and UDP on the same declared target port. RSCTF permits those two protocols only for the
current private target, and players reach both through the event VPN.

Minions has exactly eight arena slots. The main event must freeze no more than
eight accepted teams before official KotH scoring starts; unused slots become
NPCs. A larger field requires a deliberate game-capacity change and a new load
rehearsal, not a production configuration override.

Yani-netlink is one self-hosted/BYOC A&D kernel target with no gateway or managed reporter.
Its built-in relay owns TCP/UDP port `31337`; the initramfs has no interactive serial shell
and keeps PID 1 alive with a five-second sleep loop. The A&D checker validates the rotating
flag, critical file and memory hashes, CPU policy, and safe protocol probes. The image
includes an incomplete source snippet with only `EAT`, `PUKE`, and `SMOKING`, plus
`vmlinux.unstripped` for reverse engineering. The launcher keeps writable state in
`/tmp/yani` so the service also passes a read-only Docker health run.

Set `RSCTF_KOTH_REPORTER_BASE_URL` to the private control-service origin before
enabling Leaderboard scoring. The runtime images use a read-only filesystem, non-root user,
dropped capabilities, `no-new-privileges`, bounded CPU/memory/PIDs and logs, and a health
check. No separate public transport service is required.

## Release gates

Run these from the repository root before creating immutable images:

```sh
make check RSCTF=/path/to/rsctf
gitleaks git --staged --config .gitleaks.toml .

(cd events/main-event/challenges/Koth/Reverse/rythme/src/server && go test -race ./... && go vet ./...)
(cd events/main-event/challenges/Koth/Reverse/rythme/checker && python3 -m unittest test_run.py)

(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo +1.96.0 fmt --all -- --check)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo +1.96.0 check --locked -p client -p server -p spectator)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo +1.96.0 test --locked --workspace --all-targets)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo +1.96.0 run --locked --release -p sim --example verify_scenes)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/game && cargo audit)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/src/supervisor && go test -race ./... && go vet ./...)
(cd events/main-event/challenges/Koth/PPC/minions-in-32k/checker && python3 -m unittest test_run.py)
(cd events/main-event/challenges/AD/Pwn/yani-netlink/checker && python3 -m unittest test_run.py)
```

Run both `build-release.sh` scripts, validate the ZIP central directories, and
check the adjacent checksum files. Build runtime images with
`SOURCE_REVISION=<committed SHA>` and `*_VERSION=2026.09.04.1`; deploy only
their full `sha256:` image IDs.

## Promotion and rollback

Rehearsal and production use the same artifacts. A hidden/private game is the
promotion stage; visibility is an RSCTF access-policy change, not a rebuild.
Before making it discoverable, require the canonical platform health response,
healthy challenge replicas on the expected image IDs, live checker passes, one
real capability exchange per challenge, one accepted native-evidence snapshot,
a reset proving the old target credential is stale, and clean recent logs.

Rollback means restoring the previous immutable target image IDs.
A platform target reset starts a new scoring lifecycle and rotates the managed
credential. An interrupted Rythme heat is intentionally void; the new target
starts a fresh four-mode series. Do not reuse an old target or manually copy a
reporter credential across lifecycle generations.
