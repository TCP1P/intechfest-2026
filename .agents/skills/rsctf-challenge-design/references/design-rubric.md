# Design rubric

## Required contract

Record these facts before implementation:

| Decision | Required answer |
| --- | --- |
| platform mode | `AD`, `Jeopardy`, or `Koth` |
| challenge type | exact rsctf manifest type |
| category and slug | supported category and stable kebab-case slug |
| delivery | `service`, `handout`, `hybrid`, or `byoc` |
| initial inputs | exact public copy, target shape, files, credentials, and rules |
| objective | capability or information the player must obtain |
| flag/control path | static, injected, rotating file, variant, marker, or signed API |
| runtime owner | rsctf, team BYOC host, independent observer, or none |
| intended boundaries | ordered identities, trust zones, or technical properties crossed |
| difficulty target | prerequisite skills and expected active solve-time range |
| non-goals | features and mechanics intentionally excluded |

## Boundary ledger

Use one row per intended transition:

| Before | Player-visible clue | Hypothesis/action | Confirmation | Capability gained | Shortcut that must fail |
| --- | --- | --- | --- | --- | --- |
| initial player capability | literal evidence, not author knowledge | reproducible test | visible result | next capability | direct boundary bypass |

A solver constant, repository-only path, hidden route, undisclosed hostname, unexplained
encoding, or source fact unavailable at that stage is not a clue. Add visible evidence or
change the step.

## Feasibility review

- The platform can deliver exactly the declared service, handout, hybrid, or BYOC contract.
- The intended flag/control path is the path rsctf grades.
- The challenge survives concurrent players, retries, resets, and the target backend.
- Any randomness or timing has a bounded reproducible success condition.
- Network callbacks and egress match the event's real player capabilities.
- A health probe can check availability without exposing the objective or mutating state.
- The intended vulnerability is isolated; accidental vulnerabilities remain defects.
- The smallest useful implementation can be tested before narrative or UI polish.

## Difficulty and fairness

Complex exploitation can be difficult. Missing information cannot. Base the initial
difficulty estimate on required domain knowledge, reasoning depth, exploit reliability,
tooling effort, and expected active time. Mark it provisional until a fresh blind playtest
produces evidence.

## Acceptance evidence

Define observable checks for the intended solve, malformed inputs, cross-team boundaries,
flag rotation or control attribution, resource limits, reset/restart behavior, serious
unintended paths, and the exact player delivery. These become implementation tests and the
organizer solution's verification record.
