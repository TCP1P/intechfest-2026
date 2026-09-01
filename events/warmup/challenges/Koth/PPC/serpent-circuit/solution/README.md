# Serpent Circuit solution

## Verification record

- Status: `draft`.
- Candidate source: uncommitted working tree based on
  `c6dbe7de38938c8fcdfd028c4f740fd2dcd63d30`. Freeze only after the NPC revision is
  committed and reproduced in hidden staging.
- Challenge type and delivery: shared `KingOfTheHill`, direct browser/API service, managed
  Leaderboard reporting, no handout and no gateway.
- Service image: local amd64 image ID
  `sha256:10d870f7b73d7e272ab927fadab5c222a0fb184812a31712fd35b39144cb5870`.
- Backend and target: hardened local Docker (`nobody`, read-only root filesystem,
  `cap-drop ALL`, no-new-privileges, 128 MiB), `http://127.0.0.1:18780`.
- Verification time and reviewer: `2026-09-01T15:24:56Z`; Codex.
- Solver command: `python3 solution/solve.py --url http://127.0.0.1:18780 --capability
  koth_local_player_00001 --capability koth_local_player_00002`.
- Result: `room 7GVGEP: Solver 1 won after 66 rolls; Solver 1=100, Solver 2=50;
  score=recorded`.
- Playtest: none for this NPC revision. A fresh player-equivalent test of both human and NPC
  rooms is still required.

## Summary

Serpent Circuit is a traditional online Snakes & Ladders match for two to four seats. One
player creates a private room, shares its six-character code, and starts after another player
joins. If nobody joins, the host can add one server-controlled Circuit Bot and play a
practice-only match. The server owns the die, NPC turns, turn order, positions, snakes,
ladders, exact-finish rule, timeouts, winner, and Leaderboard evidence.

This revision intentionally replaces the former single-player route-optimization puzzle.
There is no client-side solution to compute: play proceeds through ordinary server-confirmed
rolls until one piece reaches square 100. The expected active match time is approximately
5–15 minutes, with substantial random variation. Prerequisites are only a browser and a
current KotH capability; an authenticated human opponent is required only for scored play.

## Player inputs

- The issued Serpent Circuit service URL.
- One current KotH capability per participating team.
- A display name.
- For guests, the six-character room code shared by the host.
- No download, flag, organizer endpoint, reporter credential, or source file is delivered.

## Walkthrough

### 1. Read the public game contract

- Observation: `GET /api/rules` publishes the 100-square board, fourteen transitions,
  two-to-four-seat limit, optional practice NPC, 30-second human turn timer, exact-finish
  rule, and extra turn on six.
- Action: open the browser UI or inspect the same JSON contract.
- Result: the player knows every rule and transition before joining.

### 2. Create or join a room

- Host action: submit a display name and current capability to `POST /api/rooms`.
- Guest action: submit the same inputs plus the host's code to `POST /api/rooms/join`.
- Confirmation: the response contains a private room player token and a public room document.
  The browser clears the capability field and keeps only the room token in browser storage.
- Capability gained: the authenticated team controls exactly one seat in that room.

If the host is still alone, **Add NPC opponent** sends the same private room token and an
idempotent action ID to `POST /api/rooms/npc`. The response adds `Circuit Bot` with an
`isNpc` label. It has no capability hash or private player token. The lobby immediately and
persistently identifies the room as practice-only; a late human may still join, but the NPC
remains and the match cannot produce evidence.

### 3. Start the match

- Observation: the live roster labels the host, human players, and NPC, and shows whether the
  minimum of two seats is ready.
- Action: the host presses **Start match**, which calls `POST /api/rooms/start` with an
  idempotent action ID.
- Confirmation: the room changes from `lobby` to `playing`, selects the host's first turn,
  and publishes the 30-second server deadline.

### 4. Take server-authoritative turns

- Action: the current player presses **Roll dice**. The client sends only the room code,
  private player token, and a new action ID to `POST /api/rooms/roll`.
- Confirmation: every client polls `POST /api/rooms/state` and sees the same die value,
  movement, transport, next player, timer, and piece positions. On an NPC turn, the same
  state polling advances its roll after the server's short thinking deadline.
- Rules: an overshoot stays in place, a ladder or snake applies once after movement, a six
  retains the turn, and an expired turn is rolled automatically by the server.

### 5. Finish on square 100

- Confirmation: a legal exact roll changes the room to `finished`, identifies one winner,
  freezes the final positions, and shows whether native evidence was recorded.
- Capability gained: in a human-only room, each participating team receives one completed
  `board-progress` result for that 60-second settlement wave, bounded by its final square
  over 100. A room containing an NPC finishes with `scoreStatus: practice-npc` and emits no
  evidence for any seat.

## Why it works

The raw capability crosses the rsctf authentication boundary only when a seat is created.
The service retains its SHA-256 hash, lifecycle identity, opaque room player token, and game
state. A room code alone cannot impersonate a seat, and one capability hash cannot occupy two
active rooms in the same scoring lifecycle.

The client never supplies a roll, position, winner, or score. It can request a roll only for
the authenticated current seat. Action IDs make a retried start or roll return the first
result instead of rolling twice. Lifecycle rotation, roster revocation, room expiry, player
limits, request limits, and a 1,000-turn draw cap bound the shared state.

Only the host of a one-human lobby can add the single NPC. Circuit Bot is represented by a
server-owned player ID with neither a room player token nor a capability hash, so it cannot
authenticate, call an action endpoint, occupy a team seat, or enter the eligible reporter
roster. The turn scheduler gives it a one-second deadline and performs its ordinary
server-generated roll when a client next polls state. A six schedules another NPC turn;
every other roll returns control to the next seat.

When a human-only match finishes, the same locked process emits one native result per
participating team: `finalPosition / 100`. Before examining a scoring window or player row,
the recorder rejects any room containing the NPC and marks it `practice-npc`. Only the first
human-only result for a team in a settlement wave is retained. The in-process managed
reporter filters hashes against rsctf's current roster, finalizes the ordinary 60-second
evidence grid, and asserts a Crown only for one unique positive leader. It never submits
platform points or a raw capability.

| Before | Player-visible clue | Action | Confirmation | Capability gained | Shortcut that fails |
| --- | --- | --- | --- | --- | --- |
| Service URL and capability | Create/join form and `/api/rules` | Authenticate a seat | Room token and roster | Control of one room piece | Room code without the private player token |
| Lone lobby seat | NPC fallback and practice warning | Host adds Circuit Bot | Labeled two-seat practice lobby | Solo practice opponent | Guest add, second NPC, NPC capability, or scored NPC result |
| Ready lobby | Host and seat-count labels | Host starts at 2–4 seats | Shared `playing` state | First timed turn | Guest start or one-seat start |
| Current turn | Roll button and countdown | Request one roll | Server move and next turn | Updated board position | Client-chosen die, position, winner, or score |
| Piece near 100 | Exact-finish rule and live board | Continue legal turns | Winner on exactly 100 | Completed native evidence | Overshoot, replayed action, stale lifecycle, or forged result |

Non-goals are public matchmaking, chat, spectators, sophisticated NPC strategy, persistent
accounts, custom boards, and strategic route choice. Circuit Bot follows the same random die
rules as a person and makes no decisions. This is deliberately a traditional luck-driven
social game; its PPC aspect is the documented automatable HTTP protocol rather than a hidden
exploit.

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

![Serpent Circuit NPC practice lobby](assets/serpent-circuit-board.png)

The screenshot is a genuine 1440×1250 browser capture of local-development room `NFCMSD`.
It shows one authenticated human, Circuit Bot's explicit NPC label, the practice-only room
message, enabled **Start match** control, and the shared live board. It was captured from
`http://127.0.0.1:18780` at `2026-09-01T15:24:56Z` through isolated Chromium profile
`/tmp/serpent-circuit-npc-v3-profile.urr41s`; no capability or room player token is visible.
The temporary profile was removed after capture. The 326,254-byte PNG has SHA-256
`8eb84957b0354234cc93a81f9e3ef77b4c46aabe3600e79e8e7cd66595773747`.

Exact screenshot command after the profile had created the real room, called the public NPC
endpoint, saved its opaque seat, and reconnected through the browser UI:

```sh
chromium --headless --no-sandbox --disable-gpu --hide-scrollbars \
  --window-size=1440,1250 --virtual-time-budget=3500 \
  --user-data-dir=/tmp/serpent-circuit-npc-v3-profile.urr41s \
  --screenshot=/root/idea/intechfest/intechfest-2026/events/warmup/challenges/Koth/PPC/serpent-circuit/solution/assets/serpent-circuit-board.png \
  http://127.0.0.1:18780
```

The same hardened container completed a real server-visible NPC turn through only the public
HTTP routes. The retained output excludes the private room token:

```json
{"nextTurnIsHuman":true,"npcMovePlayer":"Circuit Bot","npcMoveWasAutomatic":true,"room":"TEVR3S","roster":[{"isNpc":false,"name":"Solo Rehearsal"},{"isNpc":true,"name":"Circuit Bot"}],"scoreStatusDuringPlay":"pending"}
```

Focused commands for the current working tree:

```sh
cd events/warmup/challenges/Koth/PPC/serpent-circuit/src
python3 -m unittest -v test_game.py test_capability_auth.py test_reporter.py
node --test test_ui.mjs test_clock.mjs

cd ../checker
python3 -m unittest -v test_run.py
```

The source suite passed 83 Python tests and 9 Node tests; the checker suite passed 14 tests.
The Docker build reran the 83 Python tests with only the Node clock subprocess skipped because
Node is intentionally absent from the runtime build stage. The hardened container reached
Docker `healthy`, the public NPC flow auto-rolled Circuit Bot, and the documented two-human
solver completed a real random scored match.

The repository-wide `make check` passed with the workflow-matching rsctf `v0.1.102` image at
digest `sha256:f18de802833eaa1f0f3e100187310d59038e30bfa676bacb864a6dae86a8beee`:
two events and five challenges, with zero manifest errors and zero warnings.

## Notes

- Local development requires `SERPENT_LOCAL_DEV=1`; it accepts syntactically valid local
  capabilities but still stores only hashes.
- Lobby rooms expire after 30 minutes without requests, active matches after two hours, and
  finished rooms after ten minutes. The process caps state at 500 rooms and 2,000 seats.
- A host can add the one NPC only while alone in a lobby. Any room that retains Circuit Bot
  is practice-only even if another human joins before the host starts it.
- The checker remains read-only. Its capability probe uses a deterministic non-roster value
  and cannot allocate a room.
- The classic board direction was inspired by
  [`yashksaini/snakes-and-ladders-game` at `d8dc452`](https://github.com/yashksaini/snakes-and-ladders-game/tree/d8dc452601b7075ba21e3f81179fe57e30f4c64e)
  after an earlier concept reference to
  [`MichaelDepp/snake-ladder` at `7bf22a3`](https://github.com/MichaelDepp/snake-ladder/tree/7bf22a3d7f14c18984ab67782531fed1a297169a).
  Neither reference publishes a license, so this package copies none of their code, assets,
  board mappings, or screenshots.
- A pure Snakes & Ladders outcome is random rather than skill-ranked. Confirm that this is the
  intended warmup KotH scoring policy during hidden staging.
- Remaining release work: real two-team managed reporting rehearsal, normal-player access
  checks, fresh human and NPC playtests, committed revision evidence, hidden staging, and
  release review.
