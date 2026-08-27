# Getting started

This repository is the private rsctf Repository Bindings source for two independent
INTECHFEST 2026 events. The warmup is intentionally empty. The main event currently has two
shared Leaderboard KotH challenges. Start from a real contract, not a placeholder copied only
to make a directory look populated.

For AI-assisted work, ask normally or invoke the matching integrated skill explicitly:

| Phase | Skill |
| --- | --- |
| design the intended path and difficulty target | `$rsctf-challenge-design` |
| create or edit a package | `$rsctf-challenge-authoring` |
| write and reproduce the organizer solution | `$rsctf-challenge-solution` |
| run a clean blind solve | `$rsctf-challenge-playtest` |
| assess the blind report | `$rsctf-playtest-review` |
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
| `events/main-event/challenges/Koth/Reverse/rythme/` | hybrid shared service, player ZIP, read-only checker, independent Python observer | a Leaderboard KotH arena uses a separate signed API observer |
| `events/main-event/challenges/Koth/PPC/minions-in-32k/` | hybrid shared service, player ZIP, read-only checker, independent Go bridge | a Leaderboard KotH arena needs a trusted relay and scoring bridge |

These are not generic Jeopardy or A&D templates. For a new mode, use the
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
├── observer/            # separately deployed trusted infrastructure, when applicable
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
5. Put only player-visible bytes in `dist/`; keep service build input in `src/`, trusted
   checkers in `checker/`, independent infrastructure in `observer/`, and organizer material
   in `solution/`.
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
make validate
make matrix
make checkers
make solutions
```

`make validate` invokes the official `rsctf challenge check --deny-warnings .` command.
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
games. A rescan preserves each game row but recreates challenges owned by that event root, so
challenge IDs and challenge-level Admin edits may change.

Keep both games hidden and all challenges disabled through the first scan. Confirm exactly
two events and the expected per-event challenge counts, reproduce the organizer solution,
run a fresh player-equivalent playtest, exercise the real multi-team KotH lifecycle, and
complete the [release checklist](release-checklist.md) before enabling anything.

Treat all committed flags and credentials as disclosed. Removing a value from the latest
commit does not remove it from Git history; rotate anything that was ever pushed.
