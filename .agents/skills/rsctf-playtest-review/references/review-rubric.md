# Playtest review rubric

## Boundary comparison

For each intended step, answer:

| Question | Fair evidence | Defect signal |
| --- | --- | --- |
| required capability | earned in a prior visible step | assumed or obtained administratively |
| next clue | literal public or earned evidence | solution/source explains it only after the fact |
| experiment | controlled and reproducible | unexplained constant, luck, or unbounded brute force |
| confirmation | stable player-visible result | hidden logs, source, or author interpretation |
| shortcut resistance | negative probes fail | alternate path reaches equal privilege early |
| active identity | follows from visible evidence | hidden identity assumption is required |

## Outcome handling

- `solved`: the player obtained the goal under the declared boundary.
- `partial`: the player gained meaningful capability but did not finish.
- `stuck`: the environment worked but progress stopped.
- `blocked-environment`: target, delivery, callback, or runner failure prevented a fair run.
- `contaminated`: author-only information or access entered the run.

Only an uncontaminated run on the exact candidate can inform difficulty. An environment
blocker requires repair and rerun.

## Unintended-path severity

- `critical`: obtains the flag/control goal while skipping most intended boundaries.
- `high`: skips a complete exploit stage or privilege identity.
- `medium`: removes a required primitive but preserves the main chain.
- `low`: simplifies mechanics without changing the gained capability.

Fix critical and high paths before another difficulty judgment. Record the exact action and
boundary skipped; do not dismiss a reproducible path because it differs from the solver.

## Feedback boundary

Spoiler-free feedback may confirm facts the player already proved, say that a belief is false,
identify an unintended report step, or request a controlled comparison. It must not name the
missing component, route, payload, identity, vulnerability, or intended next action.
