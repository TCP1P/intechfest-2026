# Serpent Circuit solution

> Organizer material. Anyone with repository access can read this file. Keep it out of
> player handouts, service images, and blind playtests.

## Verification record

- Status: `draft`
- Candidate source revision: `50a78fa14ad8386f29398696a32801f6588af039`;
  freeze only after hidden staging
- Challenge type: `KingOfTheHill`, managed Leaderboard scoring
- Delivery: shared HTTP service
- Artifact or image: local image
  `sha256:6b490144bad255021c8666a4355be435e7e65b09c0d36e726e696c64e019a590`
- Backend and target: hardened local Docker, `http://127.0.0.1:32918`
  (ephemeral, stopped after verification), plus an ephemeral strict managed-rsctf
  contract harness
- Tested at: local image `2026-08-30T09:38:09Z`; managed lifecycle
  `2026-08-30T09:24:55Z`–`09:25:06Z`
- Tested by: Codex author check
- Command: `python3 solution/solve.py --url http://127.0.0.1:32918 --token koth_REDACTED`
- Result: local health and two identical solver retries returned
  `wave serpent-…: final 100/100, efficiency 99/99`; managed readiness,
  scoring, rotation, roster shrink, and reset tests passed

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
Exact solver output from the final committed candidate image is still required before this
writeup can be frozen.

The managed-contract rehearsal used the real application with a strict ephemeral rsctf
fixture. It proved preactivation `scoringReady: false` plus the exact roster-not-ready `503`,
activation after a signed empty snapshot, one authenticated and idempotent run, a finalized
positive unique Crown, capability rotation, roster shrink, and reset fencing. Six distinct
HMAC-signed snapshots received exact acknowledgements. The test fixture was outside the
repository and had SHA-256
`8cf635b6ffad44b8c098fa0f04d1ca0c505fd861acec9643e00b22f8f550abfc`; the exact retained
invocation was `python3 /tmp/serpent_managed_integration.py` from the repository root. It was
removed after the run; hidden staging against a real rsctf deployment is still required
before freezing.

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
