# Minions in 32K

Quake-like arena shooter built as a CTF challenge. Up to 8-player lobbies in
Gru's lab — a two-storey vertical arena under an open night sky (the moon is
right there; the rocket is aimed at it) — starring minion characters.

- **Server** (Rust): authoritative, sandboxed, feature-flagged, records
  deterministic replays, and emits performance telemetry. The player handout
  contains only the client builds, notices, and licenses.
- **Client** (Bevy, Rust): distributed as a **single self-contained exe**
  (all assets embedded). Internal render resolution is 256×128 — exactly
  32 768 pixels. The CTF rewards fighting, then converting frags into
  server-authoritative banana confirmations before an opponent denies them.
- **Spectator**: replay verification, full-stats leaderboards, and a web
  replay viewer.
- **Matchmaking backend**: queues teams from rCTF, forms lobbies, spawns
  per-match server instances, ingests signed match reports, runs Elo, and
  publishes scores back to rCTF. See `docs/matchmaking-backend-and-live-replay-plan.md`.

See `CLAUDE.md` for working notes and `PLAN.md` for the protocol log.

## Layout

```
crates/
  protocol/   wire format and shared types (v13; log in PLAN.md)
  sim/        pure deterministic simulation (movement, weapons, arena)
  server/     authoritative game server (QUIC) + instance mode
  client/     Bevy client — the single binary players download
  headless-client/
              protocol-level AI clients (NPC/elite/mediocre profiles)
  replay/     replay record/playback library (.mrpl)
  spectator/  replay verify / leaderboard / web viewer
  common/     shared math (fixed-point helpers), constants
assets/       generated .glb files + authoritative scene JSONs
tools/        the asset/level pipeline that generates assets/
docs/         plans, runbooks, public protocol notes
scripts/      build-win.sh and helpers
deploy/       sandboxing / deployment
```

## Level pipeline — single source of truth

The python zone scripts under `tools/zones/` author the level: visuals,
collision, jump pads, spawns, powerups, and the elevator all come from the
same `ZoneBuild` calls, so they cannot drift apart.

```bash
# rebuild everything: glbs + assets/scenes/*.json + manifest + checks
PYTHONPATH=tools python3 tools/zones/build_all.py
cargo build   # the sim embeds the scene JSONs at compile time
```

`tools/level/check.py` runs automatically and fails the build on: colliders
with no visible geometry, jump-pad arcs that clip anything (every pad's
flight is simulated from 9 trigger-entry points plus steering variants, in
the sim's exact integration order), floating powerups, or anything intruding
into the elevator shaft. `cargo run -p sim --example verify_scenes` further
walks a scripted player up both staircases and the tube rungs and checks
every spawn point can move.

Never edit `assets/scenes/*.json` or hand-write arena geometry in Rust —
edit a zone script and re-run `build_all.py`.

## Quick start

```bash
cargo run -p client            # offline --demo vs AI bots (default)
cargo run -p server            # open dev server on 0.0.0.0:4433 (QUIC/UDP)
cargo run -p client -- --remote 127.0.0.1:4433
cargo run -p headless-client -- --remote 127.0.0.1:4433 --count 3
cargo run -p headless-client -- --remote 127.0.0.1:4433 \
    --count 1 --npc-count 1 --insecure

# Netcode latency testing: shape UDP through a local proxy (one-way
# delay, +/-jitter, loss), then point clients at the proxy port.
cargo run -p latency-proxy -- --upstream 127.0.0.1:4433 \
    --listen 0.0.0.0:4435 --delay-ms 80 --jitter-ms 15 --loss 0.02
cargo run -p client -- --remote <host>:4435
```

After building the arena image, the container-level regression starts an
isolated timed match, rejects a stale protocol client, joins all eight slots,
validates confirmation events and ordered reports, and samples CPU/RAM:

```bash
IMAGE=minions-in-32k:confirmed-v1 node scripts/arena-regression.mjs
```

Windows client: `./scripts/build-win.sh` → `dist/minions-in-32k-win/client.exe`
(cross-compiled, assets embedded, ships as just the exe).

## Banana Confirmed scoring boundary

Protocol v13 adds server-owned kill confirmation. Opponent frags drop a banana
at the authoritative death position for 12 seconds. Only the killer can
confirm it; only the victim can deny it after respawning. Render patches,
transparent walls, ESP, and aim assistance can still change what a player
sees, but cannot manufacture a confirmation event or score on their own.

The shipped HUD and colored world objectives identify each local action. See
`docs/public-protocol.md` for exact wire fields and mechanics. The
`--npc-count` headless profile exists for regression tests and exhibition
backfill; it is not a scoring-eligible player implementation.

Player-built assistance is part of the challenge. It may analyze the public
protocol, present tactical information, recommend actions, and assist a human
input. The human must still drive the match. Sustained unattended combat is
privately marked in authoritative match evidence and earns no RSCTF activity,
objective, or Crown credit; it does not create a player-visible anti-cheat
scoreboard.

Server feature flags live-reload from `feature_flags.toml`:

```toml
[features]
powerups = true
kitchen_lava = true
```

## Instance mode (matchmade lobbies)

The same server binary becomes a single-match instance when player tokens
are present in the environment (never argv — argv is world-readable):

```bash
PLAYER_TOKENS="team_uuid:key,team_uuid:key,..."   # or PLAYER_TOKENS_FILE
MATCH_ID=match-123
REPORT_URL=https://backend/api/report
REPORT_TOKEN=<one-shot bearer secret>
server --arena gru-lab-v3 --grace-secs 30 --match-secs 300
```

An integration may set `MINIONS_SCORING_LEG_SECS=60` to emit ordered
one-minute delta reports plus a cumulative final reconciliation. The match
duration must be evenly divisible by the leg duration. Without that variable,
the normal matchmaking backend retains its one-shot report contract.

Behavior: joins require a valid `--key` (protocol v5); a live key is
**first-connect-wins**, so a duplicate connection is rejected without evicting
the incumbent. A disconnected player may reconnect to the retained slot,
score, and simulation state. Disconnects keep the slot — crashes and no-shows
just lose time.
At `grace + match` the server kicks everyone, POSTs a ranked match report
(dense ranking, confirmations desc / frags desc / deaths asc, exact ties =
draws) to
`REPORT_URL` with the bearer token, flushes the replay, and exits 0.

## Matchmaking backend

```bash
cargo run -p backend    # :8080 — see --help for knobs
```

rCTF-facing queue (`POST /api/queue/join {team_uuid}` → poll
`GET /api/queue/status?team_uuid=`), matchmaker (lobbies form when 2+
teams wait >30 s or the queue hits 20; rating-biased sampling, ≤8 per
lobby), per-match instance spawning with minted keys, one-shot report
ingestion → pairwise Elo (K=32) → anchored public scores (1000..2000 →
1..500) → `GET /api/leaderboard`. rCTF publication is dry-run logged
(`target: rctf_dryrun`) until the API is live. The local-process
instancer spawns real servers; the k8s ChallengeInstance impl slots
behind the same trait.

## Browser client (wasm + WebTransport)

Browsers can't open raw QUIC, so each instance also runs a WebTransport
bridge (`--wt-bind`, default :4434) that proxies sessions onto its own
QUIC endpoint over loopback — one code path for anti-cheat/telemetry.
The backend mints a 13-day ECDSA cert per instance (env
`WT_CERT_DER_B64`/`WT_KEY_DER_B64`) and pins its hash in the player URL:
`/wasm?host=H&port=P&key=K&certhash=SHA256`.

```bash
./scripts/build-wasm.sh          # wasm32 build + wasm-bindgen → dist/wasm-pkg/
cargo run -p backend             # serves /wasm (page) + /wasm/pkg/* (bundle)
```

Native WT path is smoke-tested (`cargo run -p server --example wt_smoke`
against a running server). The in-browser handshake needs a real
browser; note WebTransport requires a secure context (localhost or
HTTPS) for the page itself.

## Replays, leaderboards, telemetry

Replays are the record of truth: inputs + seed + flag flips reproduce the
match bit-exactly (verified cross-architecture, aarch64 ↔ x86_64).

```bash
spectator verify <file.mrpl> --arena gru-lab-v3   # re-simulate, diff snapshots
spectator leaderboard <file.mrpl>                 # K/D, damage, accuracy, powerups
spectator serve <file.mrpl> --arena gru-lab-v3    # browser replay viewer
```

The server logs a `perf` line every 10 s (tick p50/p99/max µs vs the
15.6 ms budget, sim time, snapshot KiB/s, budget overruns, player count), a
per-connection `conn perf` line (RTT, cwnd, lost packets), and a lifetime
`perf summary` on shutdown (SIGINT/SIGTERM are handled gracefully).
Reference point: 8-player match on a 4-core arm box runs at ~0.2 % tick
budget utilization.

## Testing

```bash
cargo test --workspace                            # sim mechanics incl. relay authorization
PYTHONPATH=tools python3 tools/zones/build_all.py # level consistency gate
cargo run -p sim --example verify_scenes          # traversal + spawn regressions
```

The full pre-ship loop used in development: workspace tests → `build_all`
→ `verify_scenes` → record a bot match against a real server → `spectator
verify` (0 divergences expected) → `build-win.sh`.
