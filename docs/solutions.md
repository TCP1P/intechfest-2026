# Solutions and reference solvers

Every challenge package includes a short organizer writeup and a working reference solver:

```text
events/<event>/challenges/<mode>/<category>/<slug>/solution/
├── README.md            # required concise writeup
├── solve.py             # required simple Python solver
├── requirements.txt     # optional exact direct pins
├── assets/              # optional referenced screenshots or freezed figures
└── fixtures/            # optional small redacted inputs
```

Start from [`templates/solution.md`](templates/solution.md) and
[`templates/solve.py`](templates/solve.py). AI agents apply the same contract through
`$rsctf-challenge-solution`.

## Repository and player boundary

The solution files are tracked organizer material. Anyone with Git read access can see them.
Keep this pre-event repository access-controlled, or retain an unpublished solution in a
separate restricted store. Every repository reader can see the tracked solutions.

Repository access is not player delivery. Never copy `solution/` into `dist/`, a `src/`
Docker context, generated content, a gateway bundle, or a blind-playtest room. Do not store
live flags, reusable credentials, organizer tokens, production targets, or unredacted secrets
in a solution or its screenshots.

## README.md format

Keep these headings and order:

1. `# <Challenge name> solution`
2. `## Verification record`
3. `## Summary`
4. `## Player inputs`
5. `## Walkthrough`
6. `## Why it works`
7. `## Solver`
8. `## Evidence`
9. `## Notes`

Write plainly. A teammate should understand the intended path without reading the solver
first. Keep each exploit stage chronological:

1. observation available to the player;
2. hypothesis supported by that observation;
3. exact action or command;
4. genuine visible result; and
5. capability or information gained.

Put author-source explanations under `Why it works`. Do not use hidden source, a solver
constant, or organizer knowledge to justify an earlier player decision. Use exact commands,
trim long output, and redact the flag as `rsctf{...}`. Link the complete `solve.py` instead of
pasting a shorter second implementation into the writeup.

When a future package is intentionally illustrative or blocked by platform support, say that
it is unplayable and show the expected nonzero solver result. Never invent a successful solve
to fill the template.

## solve.py format

The solver should be the smallest clear implementation of the intended player path.

- Use `#!/usr/bin/env python3` and a short module docstring.
- Put the exploit or calculation in `solve()`.
- Keep `main()` to argument parsing, one call, concise output, and exit status.
- Prefer the Python standard library. If a dependency materially improves clarity, pin its
  exact direct version in `requirements.txt`.
- Accept explicit player inputs such as `--url`, `--host` and `--port`, `--input`, or a
  current KotH token. Do not hard-code author machines, production targets, live flags,
  hidden routes, container names, or organizer APIs.
- Bound timeouts, reads, brute-force ranges, retries, subprocesses, and output.
- Validate success and exit nonzero on failure. Catch only errors that can be explained more
  clearly; do not hide bugs with `except Exception`.
- Use context managers for files, sockets, and HTTP responses.
- Avoid classes, frameworks, configuration layers, generic retry engines, plugin patterns,
  banners, emoji, and debug modes unless the challenge itself genuinely requires them.
- Keep a normal solver well under 100 lines. Complexity belongs only where the exploit needs
  it.

The README command and solver interface must match exactly. Compile and run it before review:

```sh
python3 -m py_compile events/<event>/challenges/<mode>/<category>/<slug>/solution/solve.py
python3 events/<event>/challenges/<mode>/<category>/<slug>/solution/solve.py <documented arguments>
```

## Screenshots and freezed

Use screenshots only when they prove a visual state or make a source relationship easier to
understand than text. A plain HTTP, TCP, JSON, or file challenge normally needs genuine
terminal output instead.

Store referenced images under `solution/assets/`. For each image, record the route or command,
identity, prerequisite state, UTC capture time, and the fact it proves. Capture the exact
revision through the correct player or organizer boundary. Never mock a UI, hand-type terminal
output, or edit source before rendering it.

Use `freezed` for an annotated figure of real source:

`--arrow` requires the TCP1P Freezed fork at commit
[`7acde4f1f02abda8224b740a722b32652d1907a2`](https://github.com/dimasma0305/freezed/commit/7acde4f1f02abda8224b740a722b32652d1907a2);
no tagged release contains it. Build and install that exact checkout as `freezed`, then run
this preflight before rendering:

```sh
freezed --help | grep -F -- '--arrow' >/dev/null
```

Stop if the preflight fails; upstream `freeze` and older `freezed` binaries do not implement
the documented arrow option.

```sh
freezed path/to/source.py \
  --lines 20,45 \
  --show-line-numbers \
  --window \
  --theme github-dark \
  --padding 20 \
  --margin 26 \
  --title 'path/to/source.py' \
  --arrow 'vulnerable_call(user_input)' \
  -o solution/assets/vuln-input-flow.png
```

`--arrow` points from a right-hand gutter to the selected line or range without covering the
source. Repeat it for multiple independent targets; use repeated `--mark` options when the
figure needs numbered relationships. A selector may be an absolute line (`12`), an inclusive
range (`12,14`), unique quoted source text, or a text-delimited block (`start..end`). Prefer
quoted source text when practical so harmless line movement does not stale the command. An
internal organizer writeup may show author source but must label it author-only. A
player-facing publication may show only source the player receives or earns.

Open every generated image at readable scale. Check clipping, line visibility, stale source,
wrong identity, tokens, flags, production hosts, and unrelated personal data. Regenerate it
after the source changes.

## Draft and frozen states

Every verification record starts as `draft`. Change it to `frozen` only when all of these refer
to the same committed revision:

- full Git commit SHA;
- challenge type and delivery mode;
- handout SHA-256 or image identity when applicable;
- exact backend and redacted target shape;
- UTC test time and reviewer;
- exact successful solver command and redacted output;
- every retained screenshot and its reproducible command; and
- applicable checker, generator, flag-rotation, KotH reporter/gateway, or BYOC evidence.

Any change to behavior, player copy, solver, or retained evidence returns the writeup to
`draft`. A working solver proves mechanics. A separate blind playtest proves discoverability
and informs difficulty.
