---
name: rsctf-challenge-playtest
description: Prepare, run, and review a player-equivalent blind playtest for an rsctf challenge. Use when asked to playtest, blind-solve, assess clue fairness or practical difficulty, find unintended solutions, review a playtest report, or validate service/handout/hybrid/BYOC exposure; keep organizer knowledge out of the blind run.
---

# rsctf Challenge Playtest

Prepare the target from the author side, give a fresh solver exactly what a real player
receives, then return to the author context to review the completed report.

## Prepare the contract

1. Inspect Git status, `challenge.yaml`, the exact `provide` artifact, and the target setup.
   Read [exposure and isolation](references/exposure-isolation.md).
   For managed Leaderboard KotH, also read
   [the reporting and lifecycle contract](../../../docs/koth-reporting.md).
2. Select exactly one mode: `service`, `handout`, `hybrid`, or `byoc`. If the real delivery
   cannot be determined, ask the author instead of choosing the more revealing mode.
3. Run `make check RSCTF=/path/to/rsctf` with the matching official rsctf binary. Build and
   health-check changed contexts, then exercise the ordinary player-visible protocol. Keep
   author build logs and administration outside the room.
4. Rebuild the exact player artifact or issue a redacted target. Hash handouts. For BYOC,
   use the exact team setup/image flow and only the team-owned controls available in-event.
5. For managed Leaderboard KotH, prepare the target through hidden rsctf staging. Before
   opening the blind room, complete the documented two-team lifecycle: verify exact v2 context
   and half-open/terminal wave boundaries, native-ratio and Crown settlement, checker
   read-only behavior, and `401` rejection of the old credential after target reset. Confirm
   any transport `gateway/` works without receiving scoring state or secrets. Keep all of
   that organizer evidence out of the player brief and room.

## Create the room

Create the ignored `.agents/skills/rsctf-challenge-playtest/playtest/` room. Keep temporary
run state beside this skill instead of adding a scratch directory at repository root. If the
room already contains unreviewed work, do not replace it without the user's approval;
preserve any report the author still needs. The room is temporary run state, not a reusable
skill asset; never commit it or move its reports into `assets/`.
Instantiate these skill assets in that room:

- [clean-room-agents.md](assets/clean-room-agents.md) as `AGENTS.md`;
- [player-brief.md](assets/player-brief.md) as `PLAYER-BRIEF.md`; and
- [playtest-report.md](assets/playtest-report.md) as `PLAYTEST-REPORT.md`.

Copy declared handout files to the room's `inputs/` directory byte for byte. Do not copy package source,
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

## Review from the author context

End the blind solver context before opening any organizer material. The author or reviewer,
not the blind solver, now inspects the exact candidate revision, completed
`PLAYTEST-REPORT.md`, intended solution, and genuine evidence. Read
[the review rubric](references/review-rubric.md) only in this phase.

1. Validate the run boundary first. If author context, forbidden files, hints, or a different
   target revision entered the run, classify it `contaminated` and do not use it for difficulty
   or clue conclusions.
2. Map each reported action to the intended trust-boundary ledger. Separate confirmed
   capability, unsupported belief, literal clue, guess, dead end, environment failure, and
   reproducible shortcut.
3. Classify each unintended path by the highest boundary it skips. Critical and high paths
   block release and difficulty judgment until fixed and rerun.
4. Distinguish challenge defects from player mistakes. A missing breadcrumb is a clue defect;
   an outage is an environment defect; complex but evidenced reasoning may support difficulty.
5. Instantiate [the review template](assets/playtest-review.md) as `PLAYTEST-REVIEW.md` in the
   same ignored room. Keep player-facing feedback spoiler-free: confirm only demonstrated
   facts, identify false beliefs without giving replacements, and request controlled evidence
   rather than hints.
6. Recommend source, copy, or artifact changes only when the evidence supports them. Do not
   implement fixes unless the user also asks for changes.

## Finish

Report whether the run supports a difficulty assessment and list the exact blockers. Check
timestamps, inputs, evidence chronology, dead ends, environment failures, and screenshot
provenance. Rebuild and start a fresh room after changing source, player copy, or artifacts;
never seed a new blind solver with the previous report or review.
