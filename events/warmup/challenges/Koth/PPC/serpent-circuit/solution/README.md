# Serpent Circuit solution

> Organizer material. Anyone with repository access can read this file. Keep it out of
> player handouts, service images, and blind playtests.

## Verification record

- Status: `draft`
- Candidate source revision: `757c82c89a6a3a4b30e5a0d3c273f73dd117515f`;
  freeze only after hidden staging
- Challenge type: `KingOfTheHill`, managed Leaderboard scoring
- Delivery: shared HTTP service
- Artifact or image: local image
  `sha256:ceae2812ec5a107239f0d0f677b4cb8265c7171f35cc6cfffbba0a710b555817`
- Backend and target: hardened local Docker, `http://127.0.0.1:32924`
  (ephemeral, stopped after verification)
- Tested at: local image `2026-08-30T11:01:15Z`–`11:01:49Z`
- Tested by: Codex author check
- Command: `python3 solution/solve.py --url http://127.0.0.1:32924 --token koth_REDACTED`
- Result: local health and two identical solver retries returned
  `wave serpent-1788087660000: final 100/100, efficiency 99/99`; the 63-test
  source suite, 14-test checker suite, and three browser-clock tests passed

## Summary

Each 60-second wave gives a team one ordered sequence of two-choice dice turns. The intended
solve models every reachable position, accounts for snakes and ladders, and submits the route
that finishes furthest around the board. rsctf scores the server-confirmed route efficiency;
the service never assigns platform points itself.

## Player inputs

- Description: choose one offered roll for every turn and maximize the final board position.
- Target: the issued HTTP service URL.
- Credentials or rules: the team's current KotH capability and the public rules returned by
  `GET /api/rules`.
- Files: none.
- Not supplied: another team's capability, server state, managed reporter credentials, or
  platform administration access.

## Walkthrough

### 1. Read the current board

- Observation: `GET /api/rules` returns the start, finish, board size, and every snake or
  ladder transition.
- Hypothesis: the best immediate roll may be worse after a transition, so a greedy strategy
  is insufficient.
- Action: fetch the rules and keep the transition map exactly as returned.
- Result: the complete player-visible state-transition function is available.
- Capability gained: candidate routes can be simulated locally.

### 2. Open the team's wave session

- Observation: `POST /api/session` accepts the team capability and returns a session ID, wave
  ID, deadline, and ordered turns with two options each.
- Hypothesis: a complete choice sequence can be optimized before making the single scoring
  submission.
- Action: create one session and preserve every `turn` identifier and offered value.
- Result: the team has one bounded puzzle instance for the active wave.
- Capability gained: all legal routes for this run are known.

### 3. Compute the optimal route

- Observation: a roll that passes the finish leaves the piece in place, after which at most
  one transition is applied.
- Hypothesis: dynamic programming over reachable positions retains every route that can
  affect the final result without enumerating duplicate histories.
- Action: for each ordered turn, advance every reachable position with both choices, apply
  the exact-finish rule and one transition, and retain one choice path per resulting square.
- Result: the largest position after the last turn and its exact turn/choice sequence are
  known.
- Capability gained: a server-verifiable optimal submission is ready.

### 4. Submit once

- Observation: `POST /api/play` accepts the session ID and exact `{turn, choice}` objects,
  where `choice` is index `0` or `1` in that turn's two-option list.
- Hypothesis: the server will replay the choices against its immutable wave state.
- Action: submit the computed option-index sequence before `waveEndsAt`.
- Result: the response reports matching `finalPosition` and `optimalPosition` with full route
  efficiency.
- Capability gained: the team's result is eligible for the current Leaderboard wave.

## Why it works

Author-only implementation detail: the service owns the wave seed, offered rolls, session,
deadline, and replay. It evaluates selections in turn order using the same exact-finish and
single-transition rules exposed to players. Dynamic programming is complete because future
behavior depends only on the current position, not on the path used to reach it.

Each capability can complete one run per 60-second wave. The managed reporter converts only
server-confirmed results into the `route-efficiency` objective expected by rsctf. The ratio is
exactly `(finalPosition - startPosition) / (optimalPosition - startPosition)`. Equal best
results are exact ties and therefore do not assert a Crown.

## Solver

Run the tracked [`solve.py`](solve.py) with the issued service URL and team capability:

```console
$ python3 solution/solve.py --url TARGET_URL --token TEAM_CAPABILITY
```

A successful run prints the server-confirmed wave, final position, optimal position, and
efficiency once. The solver uses only the Python standard library.

## Evidence

![Serpent Circuit player interface and complete board](assets/serpent-circuit-board.png)

The screenshot proves that the genuine player route exposes the capability form, protocol
status, full 1–100 board, snake/ladder distinction, and keyboard-focused input without an
organizer credential. It was captured from `http://127.0.0.1:18777/` in explicit
`SERPENT_LOCAL_DEV=1` mode at `2026-08-30T09:38:51Z`; it contains no live capability or
production hostname. Exact capture command:

```sh
/snap/bin/chromium --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --window-size=1440,1100 \
  --virtual-time-budget=5000 \
  --screenshot=/root/snap/chromium/common/serpent-circuit-board.png \
  http://127.0.0.1:18777/
```

The retained PNG is 447,474 bytes with SHA-256
`066e51882fcd8f563b86969e320c21a600e1e77d527b03973209dc49970f6d93`.
The interface did not change visually, but this capture predates the candidate image and must
be regenerated during hidden staging before this writeup can be frozen.

The candidate requests managed context v2 with
`X-RSCTF-API-Version: v2` and requires both authoritative `cycleStartsAt` and
`scoringEndsAt` fields. It accepts only the exact legacy v1 shape when an older rsctf ignores
that negotiation header. Deterministic tests cover exact-cutoff admission, initial partial
wave rejection, reset replay fencing, one synchronized play/finalization clock, 999- and
1,000-team three-wave rollover, future-event cursor deferral, context rejection rollback,
local roster authentication bursts, bounded aggregate HTTP input, slow/disconnected clients,
and health capacity reserved from player traffic.

The exact `/healthz` reporter object is `{alive, consecutiveFailures, state}`. The challenge
checker accepts a live `starting`, `waiting`, or `ready` reporter with zero failures and a live
`retrying` reporter for only one or two transient failures. A stopped reporter, malformed
diagnostics, or three consecutive failures is a checker error. `scoringReady` independently
states whether gameplay can currently produce scoreable evidence; the Docker health check
continues to test process availability during preactivation or a downstream outage.

The image build ran all 63 Python source regressions (with the Node-only clock test skipped in
Alpine). Local Node 24 ran the three clock regressions; the 14 checker regressions passed; and
`rsctf challenge check --deny-warnings .` reported two events, five challenges, and no errors
or warnings. The runtime was healthy as `nobody` with a read-only root filesystem, every Linux
capability dropped, one CPU, and a 128 MiB limit; observed use was about 25 MiB and two PIDs.
Hidden staging against the exact rsctf managed lifecycle remains required before freezing.

## Notes

- Cleanup or reset behavior: sessions expire with their 60-second wave; obtain a new session
  after the wave changes.
- Important negative or unintended-path checks: reject missing or repeated turns, values not
  offered for that turn, expired sessions, and different-choice replays. Identical session
  creation and play retries return the original result without emitting duplicate evidence.
- Known limitations or environment requirements: the solver must finish before the returned
  `waveEndsAt`; it deliberately does not retry into a different wave.
- Evidence that must be regenerated after a change: image identity, exact command output,
  multi-team tie behavior, reporter acknowledgement, and reset/credential-rotation checks.
- The board-game concept references
  [MichaelDepp/snake-ladder at `7bf22a3`](https://github.com/MichaelDepp/snake-ladder/tree/7bf22a3d7f14c18984ab67782531fed1a297169a). Upstream declares no license, so no upstream
  code, assets, questions, or board mapping were copied; this implementation is original.
