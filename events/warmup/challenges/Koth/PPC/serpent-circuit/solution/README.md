# Serpent Circuit solution

## Verification record

- Status: `draft`.
- Candidate source: uncommitted working tree based on
  `e46198a5eebe3762a6ab40a28e00bfdf1651cce9`. Freeze only after the multiplayer revision
  is committed and reproduced in hidden staging.
- Challenge type and delivery: shared `KingOfTheHill`, direct browser/API service, managed
  Leaderboard reporting, no handout and no gateway.
- Service image: local image ID
  `sha256:e4fd6b41a08b51b0f55423cce7be6ad93a409d383e267081b8615530020aa38c`.
- Backend and target: hardened local Docker (`nobody`, read-only root filesystem,
  `cap-drop ALL`, no-new-privileges, 128 MiB), `http://127.0.0.1:18779`.
- Verification time and reviewer: `2026-09-01T12:14:25Z`; Codex.
- Solver command: `python3 solution/solve.py --url http://127.0.0.1:18779 --capability
  koth_local_player_00001 --capability koth_local_player_00002`.
- Result: `room QE6D2L: Solver 1 won after 54 rolls; Solver 1=100, Solver 2=99;
  score=recorded`.
- Playtest: none for this multiplayer revision. A fresh player-equivalent test with at least
  two people is still required.

## Summary

Serpent Circuit is a traditional online Snakes & Ladders match for two to four teams. One
player creates a private room, shares its six-character code, and starts after another player
joins. The server owns the die, turn order, positions, snakes, ladders, exact-finish rule,
timeouts, winner, and Leaderboard evidence.

This revision intentionally replaces the former single-player route-optimization puzzle.
There is no client-side solution to compute: play proceeds through ordinary server-confirmed
rolls until one piece reaches square 100. The expected active match time is approximately
5–15 minutes, with substantial random variation. Prerequisites are only a browser, a current
KotH capability, and at least one opponent.

## Player inputs

- The issued Serpent Circuit service URL.
- One current KotH capability per participating team.
- A display name.
- For guests, the six-character room code shared by the host.
- No download, flag, organizer endpoint, reporter credential, or source file is delivered.

## Walkthrough

### 1. Read the public game contract

- Observation: `GET /api/rules` publishes the 100-square board, fourteen transitions,
  two-to-four-player limit, 30-second turn timer, exact-finish rule, and extra turn on six.
- Action: open the browser UI or inspect the same JSON contract.
- Result: the player knows every rule and transition before joining.

### 2. Create or join a room

- Host action: submit a display name and current capability to `POST /api/rooms`.
- Guest action: submit the same inputs plus the host's code to `POST /api/rooms/join`.
- Confirmation: the response contains a private room player token and a public room document.
  The browser clears the capability field and keeps only the room token in browser storage.
- Capability gained: the authenticated team controls exactly one seat in that room.

### 3. Start the match

- Observation: the live roster labels the host and shows whether the minimum of two players
  has joined.
- Action: the host presses **Start match**, which calls `POST /api/rooms/start` with an
  idempotent action ID.
- Confirmation: the room changes from `lobby` to `playing`, selects the host's first turn,
  and publishes the 30-second server deadline.

### 4. Take server-authoritative turns

- Action: the current player presses **Roll dice**. The client sends only the room code,
  private player token, and a new action ID to `POST /api/rooms/roll`.
- Confirmation: every client polls `POST /api/rooms/state` and sees the same die value,
  movement, transport, next player, timer, and piece positions.
- Rules: an overshoot stays in place, a ladder or snake applies once after movement, a six
  retains the turn, and an expired turn is rolled automatically by the server.

### 5. Finish on square 100

- Confirmation: a legal exact roll changes the room to `finished`, identifies one winner,
  freezes the final positions, and shows whether native evidence was recorded.
- Capability gained: each participating team receives one completed `board-progress` result
  for that 60-second settlement wave, bounded by its final square over 100.

## Why it works

The raw capability crosses the rsctf authentication boundary only when a seat is created.
The service retains its SHA-256 hash, lifecycle identity, opaque room player token, and game
state. A room code alone cannot impersonate a seat, and one capability hash cannot occupy two
active rooms in the same scoring lifecycle.

The client never supplies a roll, position, winner, or score. It can request a roll only for
the authenticated current seat. Action IDs make a retried start or roll return the first
result instead of rolling twice. Lifecycle rotation, roster revocation, room expiry, player
limits, request limits, and a 1,000-turn draw cap bound the shared state.

When a match finishes, the same locked process emits one native result per participating team:
`finalPosition / 100`. Only the first completed match result for a team in a settlement wave
is retained. The in-process managed reporter filters hashes against rsctf's current roster,
finalizes the ordinary 60-second evidence grid, and asserts a Crown only for one unique
positive leader. It never submits platform points or a raw capability.

| Before | Player-visible clue | Action | Confirmation | Capability gained | Shortcut that fails |
| --- | --- | --- | --- | --- | --- |
| Service URL and capability | Create/join form and `/api/rules` | Authenticate a seat | Room token and roster | Control of one room piece | Room code without the private player token |
| Lobby seat | Host and player-count labels | Host starts at 2–4 players | Shared `playing` state | First timed turn | Guest start or one-player start |
| Current turn | Roll button and countdown | Request one roll | Server move and next turn | Updated board position | Client-chosen die, position, winner, or score |
| Piece near 100 | Exact-finish rule and live board | Continue legal turns | Winner on exactly 100 | Completed native evidence | Overshoot, replayed action, stale lifecycle, or forged result |

Non-goals are public matchmaking, chat, spectators, AI seats, persistent accounts, custom
boards, and strategic route choice. This is deliberately a traditional luck-driven social
game; its PPC aspect is the documented automatable HTTP protocol rather than a hidden exploit.

## Solver

[`solve.py`](solve.py) accepts two to four distinct player-visible capabilities. It creates
one room, joins every supplied seat, starts as the host, follows the authoritative turn ID,
and requests legal rolls until the server confirms an exact-finish winner.

```sh
python3 solution/solve.py \
  --url http://127.0.0.1:PORT \
  --capability koth_local_player_00001 \
  --capability koth_local_player_00002
```

The solver is a multiplayer protocol rehearsal, not a way to predict or control the die.

## Evidence

![Serpent Circuit live two-player match](assets/serpent-circuit-board.png)

The screenshot is a genuine 1440×1250 browser capture of local-development room `S6XAW5`
with two ordinary player seats, an active timed turn, server-confirmed auto-roll text, and
both pieces on the live board. It was captured from `http://127.0.0.1:18779` at
`2026-09-01T12:14Z` through an isolated Chromium profile; no capability or room player token
is visible. The 319,447-byte PNG has SHA-256
`4ba9a2eeb3dc0f63007c10986f75a881a70a696f82758aa07acf3d05e64a269e`.

Exact screenshot command after the profile had joined the real room through the public API:

```sh
chromium --headless --no-sandbox --disable-gpu --hide-scrollbars \
  --window-size=1440,1250 --virtual-time-budget=3500 \
  --user-data-dir=/tmp/serpent-circuit-final.RWYbpP \
  --screenshot=/root/idea/intechfest/intechfest-2026/events/warmup/challenges/Koth/PPC/serpent-circuit/solution/assets/serpent-circuit-board.png \
  http://127.0.0.1:18779
```

Focused commands for the current working tree:

```sh
cd events/warmup/challenges/Koth/PPC/serpent-circuit/src
python3 -m unittest -v test_game.py test_capability_auth.py test_reporter.py
node --test test_ui.mjs test_clock.mjs

cd ../checker
python3 -m unittest -v test_run.py
```

The source suite passed 79 Python tests and 9 Node tests; the checker suite passed 14 tests.
The Docker build reran the 79 Python tests with only the Node clock subprocess skipped because
Node is intentionally absent from the runtime build stage. The hardened container reached
Docker `healthy`, and the documented two-seat solver completed a real random match.

## Notes

- Local development requires `SERPENT_LOCAL_DEV=1`; it accepts syntactically valid local
  capabilities but still stores only hashes.
- Lobby rooms expire after 30 minutes without requests, active matches after two hours, and
  finished rooms after ten minutes. The process caps state at 500 rooms and 2,000 seats.
- The checker remains read-only. Its capability probe uses a deterministic non-roster value
  and cannot allocate a room.
- A pure Snakes & Ladders outcome is random rather than skill-ranked. Confirm that this is the
  intended warmup KotH scoring policy during hidden staging.
- Remaining release work: target-architecture image build and health, real two-team managed
  reporting rehearsal, normal-player access checks, fresh multiplayer playtest, committed
  revision evidence, and release review.
