# Release evidence contract

## Candidate identity

- Exact full commit SHA, clean/understood working tree, and target rsctf release.
- Every package commits `solution/README.md` and `solution/solve.py`; they are absent from
  player/build surfaces and contain no live flag, credential, production target, debug dump,
  or unrelated output.
- The organizer solution is `frozen` against the matching commit plus applicable handout
  hashes, image identities, solver command, and inspected screenshots.

## Static and build evidence

- `make validate` passes with zero warnings using the matching official rsctf binary.
- `make matrix` emits every direct `src/Dockerfile` and `generator/Dockerfile` context.
- Applicable target-architecture builds and component tests pass.
- Every service image declares and passes a meaningful, non-secret, non-mutating Docker
  `HEALTHCHECK`.
- The exact player-visible protocol works independently of Docker health.
- Player artifacts contain exactly the declared delivery and no private/organizer material.

## Mode-specific evidence

- A&D checker verdicts include OK, Mumble, Offline, and InternalError; the current rotating
  flag is retrieved through ordinary player-visible behavior.
- KotH checks do not read or alter the marker/API control source; attribution and scoring use
  the selected frozen source.
- Deterministic generators build and return byte-identical valid output for identical input.
- API observers keep secrets/state outside player workloads and fail closed on context/feed
  errors.
- BYOC exercises exact image/setup delivery, relay reachability, flag delivery, resets, and
  team isolation on the intended topology.

## Human and staging evidence

- A second trusted maintainer reproduces the organizer solution on the exact candidate.
- A fresh uncontaminated, player-equivalent report supports clue fairness and the provisional
  difficulty. Critical/high shortcuts are fixed and rerun.
- Hidden staging imports the expected event/challenge count with every challenge disabled.
- A normal player cannot cross hidden, pre-start, non-member, or cross-team boundaries.
- Rescan-induced challenge ID and admin-edit churn is rehearsed away from a live event.
- A&D/KotH/BYOC changes complete a real multi-team scheduling, networking, flag/control,
  checker, reset, and scoring rehearsal.

## Verdicts

- `ready`: every applicable gate passed for the exact candidate.
- `not ready`: a challenge defect, leakage, failed check, or missing required test remains.
- `blocked`: required external evidence cannot currently be obtained; name the owner and
  missing state. Do not use `blocked` to hide an ordinary failed gate.

An actual import, rescan, or enablement remains a separate explicitly authorized mutation.
