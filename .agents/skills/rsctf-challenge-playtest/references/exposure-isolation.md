# Exposure and isolation

## Player modes

| Mode | Inputs | Forbidden author material |
| --- | --- | --- |
| `service` | public copy and exact target only | source, images, build logs, container/process access, undeclared handouts |
| `handout` | exact player files only | target, parent repository, solution, solver, history, real flag |
| `hybrid` | exact public copy, target, and player files | every additional local artifact or privileged target interface |
| `byoc` | public copy, team setup bundle, immutable service image, team-owned host/container controls | organizer source/logs/admin, another team's bundle/token, undeclared capabilities |

The presence of `src/`, `dist/`, an image, or an author delivery archive does not grant player
access. Derive exposure from the actual event contract.

## Contamination

Mark the run `contaminated` when the solver receives or reads any of:

- author source not issued to players;
- package `solution/`, reference solver, intended chain, or prior report/review;
- repository history or inherited author conversation;
- real flag, privileged logs, organizer-side container administration, or undeclared ports;
- another team's BYOC material or credentials; or
- a hint during the timed run.

A nested Git repository blocks accidental parent-history discovery only. It does not prevent
`..`, absolute paths, host processes, or undeclared network access. Record the actual boundary
as `workspace-enforced` only when the runner enforces it; otherwise use `procedural`.

## Evidence standard

Every non-obvious action needs the observation, hypothesis, exact action, result, and
capability gained. Mark an action as a guess when the report cannot show its clue. Treat a
reproducible boundary-skipping shortcut as an unintended path.

Screenshots must be genuine current-build evidence through the player-visible identity and
route. Do not use mocked UI, handwritten terminal transcripts, author-only source views,
container consoles, internal logs, or database state. A team-owned BYOC view is allowed only
when the declared player contract includes it.

An environment failure is `blocked-environment`, not difficulty evidence. A source-assisted
run is not black-box evidence. A solver run validates mechanics, not discoverability.
