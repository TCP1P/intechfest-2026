# Rythme deployment

Rythme is one persistent global arena, not an rCTF per-team instance. Publish
port `1337` through the event ingress, require HTTPS, and disable proxy
buffering for Server-Sent Events.

## Client release build

`build-release.sh` creates the one-file Windows x86-64 handout from the pinned
osu!lazer commit. The build host needs .NET 8, Git, Rust 1.96.0 with the
`x86_64-pc-windows-gnu` target, `x86_64-w64-mingw32-gcc`, `file`, and `zip`.
The Rust toolchain and target are pinned in
`src/client/native/rythme-native/rust-toolchain.toml`.

The release build compiles a stripped Rust `cdylib`, binds its SHA-256 into
the managed bridge, and embeds it as root-level `rythme_native.dll` in the
.NET single-file bundle so P/Invoke resolves the extracted file. It verifies
the byte-for-byte managed and native bundle entries before signing the whole
executable and running the tamper rejection test.

## Production settings

```text
RYTHME_LOCAL_AUTH=false
RYTHME_ENROLLMENT_DURATION=60s
RYTHME_READY_DURATION=30s
RYTHME_SUBMISSION_DURATION=12s
RYTHME_RESULTS_DURATION=20s
RYTHME_CROWN_THRESHOLD=2100000
RYTHME_RAW_SCORE_FLOOR=700000
RYTHME_BEATMAP_DIR=/app/beatmaps
RYTHME_BEATMAP_PROXY=true
RYTHME_STATE_PATH=/var/lib/rythme/arena.json
RSCTF_BASE_URL=http://rsctf-network:8080
RSCTF_GAME_ID=<numeric game ID>
RSCTF_CHALLENGE_ID=<numeric challenge ID>
```

Mount `/var/lib/rythme` on durable storage owned by UID/GID `65532`. The
coordinator atomically persists finalized wave evidence, the Crown, current
series progress, rotation bags, and the monotonic heat index. A restart after a
finalized heat continues the series. A heat interrupted before finalization is
void because its ephemeral channel and cue keys cannot be restored; the next
process skips to leg one of a fresh four-mode series while retaining all prior
finalized waves and the Crown. State write failures make `/healthz` return 503
and terminate the scheduler so the deployment cannot keep scoring only in RAM.

RSCTF also launches the event-scoped image built from `target.Dockerfile` as
its internal KotH health target. That thin image contains only public event
identifiers and points capability validation at the control replica reachable
on the services network; the player-facing, scoring-authoritative coordinator
remains the durable singleton from `compose.production.yml`.

The public endpoint may be a domain plus port, for example
`https://1pc.tf:PORT/`. The release client rejects plain HTTP for non-loopback
hosts.

## Identity and transport

The client sends its event-issued `koth_…` capability once to
`POST /api/v2/session/rctf`. The coordinator scopes it to the configured RSCTF
game and challenge, receives an authoritative pseudonym and team name, discards
the capability, and returns a narrow Rythme bearer. Players never provide a
local team ID or crew name in event mode.

Session creation performs fresh P-256 ECDH. Both peers derive separate request,
response, and sealed-state keys with HKDF-SHA256. Each protected
request signs its method, exact target, body digest, timestamp, strict
sequence, and previous request MAC. The response is bound to that accepted
request. The transport has no long-term team identity key; the release-coupled
native proof key is separate from the session channel.

`POST /api/v2/session/exchange` remains available for deployments with a
separate External Apps identity bridge. Set `RYTHME_IDENTITY_BRIDGE_KEY` only
for that topology. Local onboarding must remain disabled in production.

## Wave lifecycle

Every finalized heat is one independent Leaderboard KotH wave. A four-wave
rotation contains osu!, taiko, catch, and native 4K mania once each. Song
selection is shuffled within each mode. Every player receives the same reveal
and absolute start for a wave.

Immediately after authentication, the client downloads all 21 pinned `.osz`
archives directly from the allowlisted `catboy.best` HTTPS source before it can
enroll. It uses an unauthenticated HTTP client with redirects disabled, verifies
the embedded SHA-256, and falls back to the coordinator for any unavailable or
invalid archive. The content-addressed cache survives restarts, verifies each
SHA-256 on reuse, writes new files atomically, and repairs a missing or corrupted
file. After reveal, the selected archive is already local; the client checks its
BeatmapID and exact `.osu` content identity before reusing or importing it
through lazer's `BeatmapManager`. The lobby keeps one native Mod Select loadout
for each built-in ruleset, then applies the matching loadout only after the
hidden mode is revealed. Ready-up sends a native-built binary payload containing
the selected acronyms. The server validates the heat-bound policy profile and
returns the canonical playback rate and native multiplier.

During play, the client obtains short-lived, per-object cue tokens. Submitted
frames are bound to the team, ticket, current heat, note index, action, and cue
window. Server receipt time is the scoring clock.

A run qualifies for the optional exploit boost when it reaches:

- at least 700,000 raw basis;
- at least 65 percent object coverage;
- at least 2,100,000 settled score;
- a valid native-policy completion record.

Every completed run is retained as an official native score whether or not it
qualifies for the exploit boost. At heat finalization, one unique best positive
score takes the Crown. An exact top-score tie awards no Crown. This matches the
RSCTF wave validator and avoids inventing an ordering between equal official
performances. The trusted observer submits every finalized heat to RSCTF, which
applies relative normalization and the constant 95/5 performance/Crown formula.

## Beatmap delivery

`src/shared/pool.sources.json` pins 21 real community difficulties selected
from the organizer's list. No synthetic beatmap packages are part of the
challenge.

The normal client path downloads the source URL embedded in its signed pool
directly. It accepts only HTTPS on `catboy.best`, the exact `/d/<BeatmapSetID>`
shape, and the embedded archive SHA-256. It uses a separate connection with no
coordinator authorization header or cookies, and refuses redirects. This avoids
relaying roughly 284 MiB per cold player through the event host; the mirror can
still observe player source IPs.

The coordinator fallback first checks `RYTHME_BEATMAP_DIR` for `<song-id>.osz`
or `<BeatmapSetID>.osz`. If neither exists and `RYTHME_BEATMAP_PROXY=true`, it
retrieves the pinned HTTPS source server-side, verifies the exact SHA-256, and
caches it atomically. Its endpoint returns archive bytes or a local `503`; it
never redirects. See `CURATED-POOL.md` for the exact pool and importer.

For a deterministic event deployment, populate a read-only cache only after
rights review and mount it at `/app/beatmaps`. The client warms its own roughly
284 MiB event cache before enrollment, so the fixed 30-second ready window has
no archive transfer on its normal path. For local Docker validation, pass the
host directory as `RYTHME_BEATMAP_CACHE=/path/to/cache ./build-docker.sh`.
The client and server validate the archive SHA-256. No Spotify or osu! OAuth
token is required.

## RSCTF Leaderboard scoring

Run `observer.py` as a separate trusted service. It reads the arena's private
`GET /api/v1/scoring` window, filters rows against the capability hashes from
the current RSCTF context, and submits an HMAC-signed wave snapshot. The
player-facing ingress must block `/api/v1/scoring`; only the isolated observer
network may reach it. The observer secret belongs only in its secret mount.

Rythme reports one `official-score` objective. Each completed run is divided
by that wave's best score before RSCTF applies the fixed three-quarter-power
curve. A unique positive leader receives the Crown; a tied wave has no Crown.
RSCTF validates that invariant from the signed snapshot and computes all points.

The versioned read-only RSCTF checker lives in `checker/`. It verifies the
exact release build identity, secure event-only onboarding, live scheduler
clock, complete arena state contract, and mode/series invariants without
creating a session or mutating scoring state.

## Coordinator judge performance

The permanent `BenchmarkJudgeHundredLargestRuns` workload judges 100 completed
runs on the pool's largest real chart. On the same AMD EPYC 9354P host, the
August 2026 hardening changed the median result from 3.192 s, 44,848,616 bytes,
and 2,048,230 allocations to 11.996 ms, 819,200 bytes, and 100 allocations. That
is about 266 times less wall time, 98.2 percent fewer allocated bytes, and more
than 99.99 percent fewer allocations for this finalize workload. The change
indexes candidate receipts by their bound note, caches the canonical transcript
digest while accepting frames, and preserves the old judge result through a
direct equivalence regression test.

Reproduce the measurement from `src/server/`:

```console
go test -run '^$' -bench '^BenchmarkJudgeHundredLargestRuns$' -benchmem -count=7 ./internal/arena
```
