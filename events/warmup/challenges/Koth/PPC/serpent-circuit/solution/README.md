# Serpent Circuit solution

## Verification record

- Status: `draft`.
- Candidate source: uncommitted working tree based on
  `0ad9bb2bc8c80d3460caa7dca23467ec3b2a83ec`; freeze only after the exact commit and hidden
  staging target pass.
- Challenge type and delivery: shared `KingOfTheHill`, direct browser/API service, managed
  Leaderboard reporting, no handout and no gateway.
- Service image: local amd64 image ID
  `sha256:10bf340affc90cf180fa34e80f8b5ec5484100061f19ff9f64db027fb02038d6`.
- Backend and target: hardened local Docker (`nobody`, read-only root filesystem,
  `cap-drop ALL`, no-new-privileges, 128 MiB), ephemeral loopback port.
- Verification time and reviewer: `2026-09-03T04:11:50Z`; Codex.
- Solver result: `NPC 2 won; player rolls=20; Solver=76, NPC 2=100, NPC 3=52,
  NPC 4=95; score=recorded`.
- Playtest: pending a fresh player-equivalent automatic-matchmaking run.

## Summary

Serpent Circuit is a server-authoritative four-seat Snakes & Ladders match. A team submits its
current KotH capability once. The server places that team in a FIFO queue and prevents the
same capability from holding two active seats. Other humans may join during a visible
15-second countdown. The server then fills empty seats with capability-free NPCs and starts
the match. Players cannot create private rooms, choose opponents, or start a match.

Only one match runs at a time. A later group waits while the current group plays, then starts
automatically with NPCs in any empty seats. Reaching square 100 wins immediately. If nobody reaches 100 within
five minutes, the sole player on the highest square wins; a tied highest square has no match
winner. Every completed 60-second scoring tick reports each team's furthest confirmed square
as the native `board-progress` ratio.

## Player inputs

- The issued Serpent Circuit service URL.
- One current KotH capability for the player's team.
- A display name.
- No room code, opponent identity, download, flag, reporter credential, organizer endpoint,
  or source file is delivered.

## Walkthrough

### 1. Read the public contract

- Observation: `GET /api/rules` publishes protocol version 5, the original 100-square board,
  fourteen transitions, four-seat limit, 15-second matchmaking timer, 30-second human turn
  timer, five-minute match timer, and the exact-finish and extra-turn rules.
- Action: open the browser UI or inspect the same JSON route.
- Result: the player knows the complete board and ranked-match behavior before joining.

### 2. Enter matchmaking

- Action: submit a display name and current capability to `POST /api/matchmaking`.
- Confirmation: the response contains an opaque player token and the server-assigned match
  state. It contains no selectable room code. The browser clears the raw capability and
  stores only the player token for reconnection.
- Capability gained: control of one authenticated piece in one waiting or active match.

The client polls `POST /api/matchmaking/state` with the player token. Waiting players must
keep the page open; an abandoned waiting seat expires after 60 seconds.

### 3. Wait for automatic assignment and start

- Observation: waiting state shows the assigned human roster, queue position, and automatic
  start countdown.
- Confirmation: four humans start immediately. Otherwise, when the countdown ends and no
  earlier match is active, the server adds NPCs until the room has four seats and changes the
  state from `waiting` to `playing` without a host or start request.
- Result: the server publishes the first turn deadline and fixed match deadline.

### 4. Take authoritative turns

- Action: the current player sends its private player token and a fresh action ID to
  `POST /api/matchmaking/roll`.
- Confirmation: all clients see the same server-generated die, movement, transition, next
  player, turn timer, match timer, and board positions. NPC turns run automatically after a
  short server delay.
- Rules: an overshoot stays in place, one snake or ladder applies after movement, a six keeps
  the turn, and the server rolls an expired turn automatically.

### 5. Finish or reach the deadline

- Exact finish: a legal roll onto square 100 immediately ends the match with that player as
  winner.
- Deadline: at exactly five minutes, the server compares current positions. One unique
  highest position wins; an exact top tie returns no winner.
- Queue transition: finishing releases every human capability seat and starts the oldest
  eligible waiting group automatically.
- Scoring: waiting and finished matches emit no later progress. A completed active tick keeps
  one furthest-position row per capability hash.

## Why it works

The raw capability crosses the rsctf authentication boundary only during matchmaking. The
service retains its SHA-256 hash, lifecycle identity, opaque player token, and authoritative
game state. The queue lookup and one-seat index run under the same lock, so concurrent retries
return the existing seat instead of duplicating a team.

The browser never supplies a room, opponent, roll, position, winner, or score. FIFO grouping,
the one-active-match rule, countdown, NPC fill, auto-start, turn deadlines, match deadline,
and highest-position settlement all run in the authoritative process. A modified client
therefore cannot arrange a favorable private group or hold the active slot indefinitely.

Action IDs make a retried roll return its first result. Capability lifecycle changes, roster
revocation, stale waiting seats, bounded rooms and seats, request limits, and the 1,000-turn
safety cap bound shared state. The managed reporter reads the same locked state, emits bounded
native ratios for authenticated humans only, and never reports raw capabilities, NPCs, or
platform points.

| Before | Player-visible clue | Action | Confirmation | Capability gained | Shortcut that fails |
| --- | --- | --- | --- | --- | --- |
| Service URL and capability | Matchmaking form and `/api/rules` | Submit one capability | Opaque player token and assigned group | Control of one piece | Private room or duplicate team seat |
| Waiting group | Human roster, queue-ahead count, and start countdown | Keep polling | Automatic four-seat `playing` state with NPC fill | Timed match seat | Manual start, chosen opponent, stale abandoned seat |
| Current turn | Roll button and timers | Request one roll | Server move and next turn | Updated position and tick evidence | Client die, position, winner, or replayed roll |
| Active match | Five-minute countdown | Continue legal turns | Exact finish or deadline result | Final match outcome | Extending the deadline or blocking the next group |

## Solver

[`solve.py`](solve.py) is a small protocol rehearsal. On an isolated target, it submits one
capability, waits through automatic NPC matchmaking, requests legal turns for its human
piece, and lets the server advance NPC turns until the match finishes.

```sh
python3 solution/solve.py \
  --url http://127.0.0.1:PORT \
  --capability koth_local_player_00001
```

The solver does not predict or control the die or the NPCs. Run it on a quiet local or hidden
staging target so unrelated queue arrivals do not change the human roster.

## Evidence

Focused verification commands for this revision:

```sh
cd events/warmup/challenges/Koth/PPC/serpent-circuit/src
python3 -m unittest -v test_game.py test_capability_auth.py test_reporter.py
node --test test_ui.mjs test_clock.mjs

cd ../checker
python3 -m unittest -v test_run.py
```

The source suite passed 78 Python tests and 9 Node tests. The checker suite passed 14 tests.
The Docker build reran all 78 Python tests (with its expected Node-only clock-test skip), the
container reached `healthy`, and the exact one-capability solver completed a server-visible
match through only public routes. This run also proves that an NPC can win without appearing
in managed Leaderboard evidence.

A fixed-rate local load held 100 matchmaking-state polls per second for 15 seconds against
one active four-team match: 1,505 HTTP requests, zero failed checks, zero failed requests,
14.24 ms p95, and 30.88 ms maximum response time. Every response retained `playing` state
and an exact four-player roster.

![Serpent Circuit fair matchmaking entry](assets/serpent-circuit-board.png)

The screenshot is a genuine 1440×1250 capture of the current local-development service. It
shows the four-seat FIFO matchmaking copy, 15-second automatic NPC-fill rule, single **Join
queue** action, five-minute deadline rule, and absence of private-room, opponent-selection,
and manual-start controls. It was captured at `2026-09-03T04:11:50Z`; no capability or player
token is visible. The 330,622-byte PNG has SHA-256
`98f94361762eec17858e2f19c0f4505b37acfc5a7811a9ca003df7359329dce0`.

```sh
chromium --headless --no-sandbox --disable-gpu --hide-scrollbars \
  --window-size=1440,1250 --virtual-time-budget=2500 \
  --user-data-dir=/tmp/serpent-circuit-v6-screenshot \
  --screenshot=solution/assets/serpent-circuit-board.png \
  http://127.0.0.1:PORT
```

The temporary browser profile was removed after the screenshot. Hidden-staging evidence and
the exact committed identity remain pending while this record is `draft`.

## Notes

- Local development requires `SERPENT_LOCAL_DEV=1`; syntactically valid local capabilities
  are accepted but only their hashes are retained.
- A waiting seat expires after 60 seconds without its own state poll. Waiting rooms otherwise
  expire after 30 minutes, active state is bounded by the five-minute match timer, and
  finished matches expire after ten minutes.
- The checker remains read-only. Its deterministic non-roster capability probe cannot
  allocate matchmaking state.
- NPCs have no capability or player token. They can win the player-visible match, but only
  authenticated human progress enters managed Leaderboard evidence.
- A pure Snakes & Ladders result is luck-driven. Tick scoring rewards relative furthest
  progress throughout the match; the five-minute winner is a player-facing match result, not
  a direct platform-points submission.
