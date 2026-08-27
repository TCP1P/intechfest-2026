---
name: rsctf-challenge-playtest
description: Prepare and run a player-equivalent blind playtest for an rsctf challenge. Use when asked to playtest, blind-solve, test discoverability, measure practical difficulty, find unintended solutions, or validate service/handout/hybrid/BYOC exposure; do not use the author solution or reference solver during the run.
---

# rsctf Challenge Playtest

Prepare the target from the author side, then give a fresh solver exactly what a real player
receives.

## Prepare the contract

1. Inspect Git status, `challenge.yaml`, the exact `provide` artifact, and the target setup.
   Read [exposure and isolation](references/exposure-isolation.md).
2. Select exactly one mode: `service`, `handout`, `hybrid`, or `byoc`. If the real delivery
   cannot be determined, ask the author instead of choosing the more revealing mode.
3. Run `make validate` and `make matrix` with the matching official rsctf binary. Build and
   health-check changed contexts, then exercise the ordinary player-visible protocol. Keep
   author build logs and administration outside the room.
4. Rebuild the exact player artifact or issue a redacted target. Hash handouts. For BYOC,
   use the exact team setup/image flow and only the team-owned controls available in-event.

## Create the room

Create the ignored root `playtest/` directory. If it already contains unreviewed work, do not
replace it without the user's approval; preserve any report the author still needs.
Instantiate these skill assets:

- [clean-room-agents.md](assets/clean-room-agents.md) as `playtest/AGENTS.md`;
- [player-brief.md](assets/player-brief.md) as `playtest/PLAYER-BRIEF.md`; and
- [playtest-report.md](assets/playtest-report.md) as `playtest/PLAYTEST-REPORT.md`.

Copy declared handout files to `playtest/inputs/` byte for byte. Do not copy package source,
`solution/`, the reference solver, repository history, build logs, real flags, earlier reports,
or undeclared artifacts. Initialize an empty nested Git repository only as an accidental
history guard; state clearly that it is not a filesystem sandbox.

## Run blind

For a meaningful run, use a fresh human or agent with no author conversation. When a
collaboration tool is available, spawn exactly one solver with no forked turns and give it only
the prepared room and declared target. Do not summarize the intended chain or suspected bug.

Shared-filesystem procedural isolation is suitable for smoke/adversarial feedback but is not
release-grade secrecy. Label the boundary honestly in the report. A release-grade run needs an
enforced workspace root and network policy that permits only declared targets plus ordinary
player-owned callbacks. If those controls are unavailable, complete a procedural run or report
the missing evidence; do not call it release-grade.

Let the solver stop. Do not answer stuck questions with hints during the timed run. Require a
completed `PLAYTEST-REPORT.md` with one verdict: `solved`, `partial`, `stuck`,
`blocked-environment`, or `contaminated`.

## Finish

Check the report for timestamps, exact inputs, evidence chronology, guesses, dead ends,
shortcuts, environment failures, and screenshot provenance. Do not inspect the organizer
solution from the blind solver context. Use `$rsctf-playtest-review` from the author context
after the report exists. Rebuild and start a fresh room after changing source, player copy, or
artifacts.
