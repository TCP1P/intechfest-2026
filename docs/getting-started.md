# Getting started

This repository is the private rsctf Repository Bindings source for two independent
INTECHFEST 2026 events. The warmup has one BYOC A&D challenge, one marker KotH challenge,
and one managed Leaderboard KotH scoring game. The main event has two shared Leaderboard
KotH challenges. Start from a real contract, not a placeholder copied only to make a
directory look populated.

For AI-assisted work, ask normally or invoke the matching integrated skill explicitly:

| Phase | Skill |
| --- | --- |
| design the intended path and difficulty target | `$rsctf-challenge-design` |
| create or edit a package | `$rsctf-challenge-authoring` |
| write and reproduce the organizer solution | `$rsctf-challenge-solution` |
| prepare, run, and review a clean blind solve | `$rsctf-challenge-playtest` |
| decide whether the exact revision is ready | `$rsctf-challenge-release-review` |

The skills are eligible for automatic selection from this repository's `AGENTS.md`.

## Prerequisites

- the official `rsctf` binary matching the target deployment;
- Docker for service or generator packages;
- Python 3.12 or newer for the bundled checkers and solution tooling; and
- the language toolchain required by the challenge being changed.

Run `make help` for the supported repository commands. Set
`RSCTF=/absolute/path/to/rsctf` when the matching binary is not on `PATH`.

## Choose the event first

Use one of these roots:

```text
events/warmup/
events/main-event/
```

Each owns one `.gzevent` and only the manifests below its own directory. Do not add a root
`.gzevent`, nest event roots, or copy a main-event challenge into the warmup as filler.

The current packages are examples of this repository's actual delivery model:

| Package | Contract | Reuse it when |
| --- | --- | --- |
| `events/warmup/challenges/AD/Web/parcel-panic/` | self-hosted/BYOC A&D service, rotating flag file, read-only checker | teams must run and patch the issued image on their own host |
| `events/warmup/challenges/Koth/Web/draft-control/` | shared marker/Boot2Root hill with an atomic `/koth/king` control path | control is exclusive and proven by placing the current KotH capability in the marker |
| `events/warmup/challenges/Koth/PPC/serpent-circuit/` | shared browser/API game, read-only checker, in-process managed reporter | every team can produce bounded native evidence during the same Leaderboard wave |
| `events/main-event/challenges/Koth/Reverse/rythme/` | hybrid shared service, player ZIP, read-only checker, in-process managed reporter, HTTPS gateway | a Leaderboard KotH coordinator owns its native evidence but needs a stable public origin |
| `events/main-event/challenges/Koth/PPC/minions-in-32k/` | hybrid shared service, player ZIP, read-only checker, optional transport gateway | a managed target reports evidence but needs HTTPS/UDP adaptation |

These are mode-specific examples, not interchangeable templates. For a new mode, use the
[authoring contract](authoring-contract.md) and [manifest reference](configuration.md), then
implement only the components that mode requires.

## Create a package

The canonical path is:

```text
events/<event>/challenges/<mode>/<category>/<slug>/
├── challenge.yaml       # rsctf import and player-copy contract
├── dist/                # exact player delivery, when applicable
├── src/                 # direct Docker build context, when applicable
├── checker/             # required for A&D and KotH
├── generator/           # deterministic variant generator, when applicable
├── gateway/             # transport-only adapter, only when the protocol requires it
└── solution/            # required tracked organizer material
    ├── README.md
    └── solve.py
```

`challenge.yaml`, `solution/README.md`, and `solution/solve.py` are required for every
challenge. Delete unused optional directories.

When copying a nearby package:

1. Pick `warmup` or `main-event` deliberately.
2. Use an exact mode (`AD`, `Jeopardy`, or `Koth`), supported category, and lowercase
   kebab-case slug.
3. Rewrite `name`, `author`, description, type, delivery, and runtime fields for the new
   challenge. Do not retain copied behavior or lore.
4. Omit `minScoreRate`, `difficulty`, and `submissionLimit` when rsctf defaults are intended.
   These Jeopardy fields do not belong on A&D or KotH manifests.
5. Put only player-visible bytes in `dist/`; keep service and managed reporting code in
   `src/`, trusted checks in `checker/`, unavoidable transport adaptation in `gateway/`, and
   organizer material in `solution/`.
6. Adapt the canonical checker to the ordinary player-visible protocol. Keep every check
   bounded, read-only, and order-independent.
7. Replace both solution templates with the real intended path as soon as it is testable.

For a new solution, copy the human templates into the new package:

```sh
cp docs/templates/solution.md \
  events/<event>/challenges/<mode>/<category>/<slug>/solution/README.md
cp docs/templates/solve.py \
  events/<event>/challenges/<mode>/<category>/<slug>/solution/solve.py
```

Then follow [Solutions and reference solvers](solutions.md). The private repository protects
the material from the public, but every repository reader can still see it.

## Check the work

Run focused language tests first, then:

```sh
make check RSCTF=/path/to/rsctf
```

Use `make check` without the assignment when the matching official binary is already on
`PATH`. The validation step invokes the official
`rsctf challenge check --deny-warnings .` command.
`make matrix` invokes `rsctf challenge matrix .`; it discovers direct
`events/<event>/challenges/<mode>/<category>/<slug>/src/Dockerfile` and
`generator/Dockerfile` contexts dynamically. Do not add a hand-written container matrix or a
repository-local validation script.

GitHub Actions imports `dimasma0305/rsctf`, validates both event trees, verifies player
artifact hashes, runs checker regressions, compiles solution Python, builds every emitted
context, and waits for each service Docker `HEALTHCHECK`. Docker health proves startup only;
it does not prove checker verdicts, player protocol behavior, scoring, or solvability.

## Import safely

Repository Bindings discovers both sibling `.gzevent` files and imports two independent
games. Event settings seed a game only on first import. On later scans, a challenge at the
same manifest path is updated in place, preserving its ID, history, review state, enabled
state, and protected runtime state. A missing manifest is guarded and disabled as a
tombstone rather than deleted; a path rename creates a new challenge identity and leaves the
old path to that removal flow. Rehearse renames and removals in hidden staging.

Keep both games hidden and all challenges disabled through the first scan. Confirm exactly
two events and the expected per-event challenge counts, reproduce the organizer solution,
run a fresh player-equivalent playtest, exercise the real multi-team KotH lifecycle, and
complete the [release checklist](release-checklist.md) before enabling anything.

Treat all committed flags and credentials as disclosed. Removing a value from the latest
commit does not remove it from Git history; rotate anything that was ever pushed.
