# Solution package contract

## Required layout

```text
solution/
├── README.md            # required organizer writeup
├── solve.py             # required small reference solver
├── requirements.txt     # optional exact direct pins
├── assets/              # optional referenced screenshots or figures
└── fixtures/            # optional small redacted solver inputs
```

`README.md` and `solve.py` are tracked organizer material. Anyone who can read the Git
repository can read them. Keep a real pre-event repository access-controlled, or retain the
unpublished solution in a separate restricted store. Public examples are permanently
disclosed.

Do not put solution files in `dist/`, `src/`, a generator context, an observer bundle, or a
blind-playtest room. Never place `challenge.yaml` or `.gzevent` inside `solution/`; recursive
manifest discovery does not care whether Git ignores a path.

## Verification identity

Start the writeup as `draft`. Mark it `frozen` only after the exact committed revision passes
the documented solver command and all retained evidence has been regenerated and inspected.
Record:

- full Git commit SHA;
- challenge type and player delivery mode;
- handout SHA-256 when applicable;
- immutable image digest or local image ID when applicable;
- backend and redacted target shape;
- UTC verification time and reviewer;
- exact solver command and redacted result; and
- playtest report identifier when one exists.

Any behavior-bearing manifest, handout, source, checker, generator, observer, solver, or
writeup-evidence change returns the record to `draft` until it is reproduced.

## Review audit

Use a clean target or empty handout extraction. Follow the README without author memory, run
`solve.py` exactly as documented, compare every payload and expected result with the current
challenge, inspect retained images at readable scale, and confirm solution material is absent
from every player/build surface. A working solver proves mechanics, not clue fairness.
