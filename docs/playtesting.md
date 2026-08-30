# Playtesting

Playtesting answers a different question from automated testing: can a fresh player
understand and solve the challenge using only what the event actually gives them?

AI agents use `$rsctf-challenge-playtest` to prepare and hand off the clean room, run the blind
solve, then analyze the completed report from a separate author context. It is available for
implicit selection from matching requests. Invoke it explicitly when you need deterministic
routing into the full clean-room workflow.

## First verify the build

From the repository root:

```sh
make validate
make matrix
```

When container or generator code changed, require the dynamic GitHub container job
to pass (or reproduce its Docker builds locally). It builds every context emitted by
rsctf and waits for each service's Docker `HEALTHCHECK`. That gate proves startup and
declared health only, so exercise the real player-visible protocol and checker in
hidden staging before the blind solve. Do not use the intended solver as the
playtest; that tests mechanics, not clues.

## Declare the player contract

Choose exactly one mode:

| Mode | Give the playtester | Do not give them |
| --- | --- | --- |
| service | public challenge copy and the issued URL or host/port | source, image, build logs, container access, or a handout that players do not receive |
| handout | byte-for-byte contents of the public `dist/` delivery | target, author notes, repository history, solver, or real flag |
| hybrid | the exact public target and exact public handout | any additional local artifact or privileged service access |
| byoc | public challenge copy, the team-specific setup bundle and immutable service image, and the team-owned host/container controls available during the event | organizer source or notes, build logs, platform administration, another team's bundle or token, or any undeclared capability |

Write the contract down before starting. If the real event delivery is ambiguous,
resolve it with the author instead of choosing the more revealing option.

## Prepare an isolated room

Use the ignored `.agents/skills/rsctf-challenge-playtest/playtest/` directory only as a
staging convenience. Copy in
the public brief and declared handout files, if any. For a managed service, start it
and retain build logs from the author workspace, outside the room. For BYOC, deliver
the exact setup bundle and image through the player-visible flow, then let the tester
operate them on an isolated team-owned host as a real team would. Use a redacted
playtest flag.

The package-local `solution/` directory, its reference solver, organizer screenshots, and
verification record are author inputs. They never enter the skill's `playtest/` room, and the blind solver
must not inherit a conversation that disclosed them. Read
[Solutions and reference solvers](solutions.md) for the organizer format; use it only before
or after, never during, the isolated player run.

The skill's `playtest/` room is not a sandbox. For a credible blind run, give a fresh human account or
agent session a separate workspace containing only the prepared room. Do not inherit
the author conversation, intended chain, prior reports, parent filesystem, Git
history, organizer-side container administration, process inspection outside the
declared BYOC host, or undeclared localhost services. Permit only the issued target,
the declared BYOC controls, and normal player-owned callbacks that the real event
permits.

A useful `PLAYER-BRIEF.md` is deliberately small:

```markdown
# Player brief

- Mode: service | handout | hybrid | byoc
- Target: exact public URL or host/port, if supplied
- Files: exact filenames, if supplied
- BYOC setup: exact setup/image delivery path, if supplied
- Public description: exact platform copy
- Time started: UTC timestamp
```

Do not add hints, implementation details, or organizer vocabulary that is absent
from the real challenge page.

## Run the playtest

Ask the player to record evidence as they work and allow them to stop. `solved`,
`partial`, `stuck`, `blocked-environment`, and `contaminated` are all useful results.
Do not answer a stuck question with a hint during the timed run.

Use this report shape:

```markdown
# Playtest report

## Result
- Verdict:
- Started/finished (UTC):
- Active time:

## Inputs received
- Target:
- Files:
- Public instructions:

## Timeline
- Time — observation — hypothesis — exact action — result

## Intended-looking progress
- Evidence and capability gained

## Guesses and clue gaps
- Non-obvious step and what, if anything, pointed to it

## Dead ends
- Action, evidence, and time spent

## Possible unintended paths
- Reproducible shortcut and boundary skipped

## Environment problems
- Failure and why it is environmental rather than challenge difficulty
```

## Review fairly

Compare each reported step with the intended trust-boundary transitions:

- A missing public breadcrumb is a clue defect, not extra difficulty.
- A working shortcut that skips a capability or identity boundary is an unintended
  solution even when it differs from the official solver.
- A source-assisted solve is not evidence for black-box discoverability.
- An environment outage or missing callback route requires a repaired rerun.
- Any inherited author knowledge makes the run contaminated.

After changing source, player copy, or artifacts, rebuild and start a fresh room. Do
not seed a new blind run with the old report.

## Screenshots and evidence

The current solution claims are protocol and scoring claims, so their writeups retain exact
text output instead of decorative screenshots. Historical promotional media under
`docs/media/` is not playtest evidence. Add a solution screenshot only when a visual state
materially proves something that text cannot.

For any screenshot you keep:

- capture the current build through the same route and identity available to a player;
- show the command for terminal output and never render a hand-written transcript;
- redact flags, tokens, production hostnames, and unrelated personal data;
- do not use organizer-only container shells, internal logs, database views, or mocked
  browser state as player evidence; team-owned BYOC views are valid only when the
  declared player contract includes them;
- use descriptive names such as `ui-login.png`, `terminal-solve.png`, or
  `artifact-inspection.png`; and
- inspect the final image for clipping, stale controls, hidden secrets, and readable
  text before linking it from a guide or report.

Store temporary blind-run captures inside the ignored skill room's `evidence/` directory. Move only
reviewed, still-current documentation images to `docs/assets/screenshots/`, creating
that directory when the first useful image exists.
