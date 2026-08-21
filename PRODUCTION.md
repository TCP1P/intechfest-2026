# Production release contract

Release: `2026.08.21.1`

This document defines the production-ready artifact and deployment contract for
the INTECHFEST event. Live game IDs, capabilities, observer secrets, arena
credentials, and TLS private keys are operational state and must never be
committed.

## Player artifacts

| Challenge | File | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Rythme | `challenges/rythme/release/reversing_rythme.zip` | 165891448 | `91f378e90010349718074072859706813838af6e281006528a19f941283d9c24` |
| Minions in 32K | `challenges/minions-in-32k/release/game_minions-in-32k.zip` | 37848231 | `6fd7624e24b75b12fc95dcdee18a4ce75b9d54e0509f39a5694f173b3c0c980e` |

The adjacent `SHA256SUMS` files are authoritative. Publish the archives by
content hash and verify both the stored content length and digest before binding
an attachment to a challenge.

## Runtime topology

Rythme is one global coordinator behind an nginx gateway, plus one trusted
observer. The coordinator is read-only except for `/var/lib/rythme`, which is a
UID/GID 65532 volume. Its finalized waves, Crown, rotation state, and heat index
are atomically durable. Public ingress cannot reach its scoring feed. RSCTF's
internal KotH lifecycle uses a thin event-scoped image derived from the same
immutable release image; it contains only public game/challenge identifiers and
is a checker target, never a second scoring authority.

Minions is one platform-managed shared KotH target plus one trusted bridge. The
target image is reusable and secret-free. RSCTF injects a random internal static
flag at runtime; the supervisor derives the bridge authenticator and generates a
short-lived certificate. The bridge stores roster credentials and scoring state
in UID/GID 32033 volumes, sends the roster only through the authenticated
internal start route, exposes HTTPS metadata plus UDP relay ingress, and removes
itself when no active target exists.

Both production Compose files use read-only filesystems, non-root users,
dropped capabilities, `no-new-privileges`, bounded CPU/memory/PIDs, bounded log
rotation, health checks, pinned third-party image digests, and internal networks
for private dependencies.

## Release gates

Run these from the repository root before creating immutable images:

```sh
make validate
gitleaks git --staged --config .gitleaks.toml ..

cd challenges/rythme/src/server
go test -race ./...
go vet ./...
cd ../deploy
python3 -m unittest observer_test.py
python3 -m unittest checker/test_run.py

cd ../../../minions-in-32k/game
cargo +1.96.0 fmt --all -- --check
cargo +1.96.0 check --locked -p client -p server -p spectator
cargo +1.96.0 test --locked --workspace --all-targets
cargo +1.96.0 run --locked --release -p sim --example verify_scenes
cargo audit

cd ../supervisor
go test -race ./...
go vet ./...
cd ../deploy/bridge
go test -race ./...
go vet ./...
cd ../checker
python3 -m unittest test_run.py
```

Run both `build-release.sh` scripts, validate the ZIP central directories, and
check the adjacent checksum files. Build runtime images with
`SOURCE_REVISION=<committed SHA>` and `*_VERSION=2026.08.21.1`; deploy only their
full `sha256:` image IDs.

## Promotion and rollback

Rehearsal and production use the same artifacts. A hidden/private game is the
promotion stage; visibility is an RSCTF access-policy change, not a rebuild.
Before making it discoverable, require the canonical platform health response,
healthy challenge replicas on the expected image IDs, live checker passes, one
real capability exchange per challenge, and clean recent logs.

Rollback means restoring the previous immutable image IDs while preserving
volumes and platform data. Never roll Rythme back to a build that cannot read the
current `rythme-arena-state-v1` file. If an interrupted Rythme heat is restarted,
that heat is intentionally void and the coordinator starts a fresh four-mode
series without discarding finalized evidence.
