---
name: rsctf-challenge-solution
description: Create, update, reproduce, or review an rsctf challenge's organizer writeup and simple solve.py reference solver. Use for solution formatting, intended-path verification, test solves, screenshots or freezed source figures, artifact/image identity, and stale-solution checks; do not use for blind playtesting.
---

# rsctf Challenge Solution

Explain and prove the intended solve on the exact challenge revision. Keep the result easy for
another teammate to read and keep all organizer knowledge out of player delivery and blind
playtests.

## Workflow

1. Inspect Git status, the challenge package, manifest, player delivery, and exact candidate
   revision. Preserve unrelated work.
2. Read [the package contract](references/solution-contract.md). Before editing `README.md`,
   read [the writeup format](references/writeup-format.md). Before editing `solve.py`, read
   [the solve.py format](references/solve-py-format.md). Read
   [screenshots and freezed](references/screenshots-and-freezed.md) whenever the writeup uses
   or would materially benefit from images. For mode-specific behavior, read the relevant
   checker, provenance, or KotH documentation under `docs/`.
3. Require tracked `solution/README.md` and `solution/solve.py`. Instantiate them from
   [`docs/templates/solution.md`](../../../docs/templates/solution.md) and
   [`docs/templates/solve.py`](../../../docs/templates/solve.py) when missing, then replace
   every placeholder with challenge-specific content. A schema-only or blocked example still
   gets both files, but its solver must fail clearly instead of fabricating success.
4. Write the player inputs and intended path chronologically. Put author-only implementation
   explanation under `Why it works`; it cannot justify an earlier player action. Keep prose
   plain, concise, and evidence-backed.
5. Keep `solve.py` linear and small. Put the exploit in `solve()` and argument parsing in
   `main()`. Prefer the standard library, explicit player inputs, bounded operations, focused
   validation, and one concise result. Do not add abstractions or configuration that the
   exploit does not need.
6. Build a clean target or extract the exact handout outside author scratch state. Run every
   documented action and the reference solver. Record the full revision, artifact hash,
   image identity, backend, UTC time, exact command, redacted result, and cleanup.
7. Exercise negative and unintended-path probes plus applicable checker, rotation,
   generator, KotH, or BYOC behavior. Capture only useful genuine evidence. Generate an
   annotated source figure with `freezed` when it materially clarifies the bug, record the
   exact command, and inspect the final image.
8. Leave the verification status `draft` while working. Set it to `frozen` only after the
   exact committed revision, artifact identities, documented solver command, and retained
   images all pass. Report what remains unverified. A working solver proves mechanics only;
   do not claim clue fairness, difficulty, or release readiness.

## Exposure boundary

The tracked solution is organizer material. Anyone with repository read access can see it, so
keep real pre-event repositories restricted and treat public examples as disclosed. Never
place a manifest inside `solution/`, copy it into `dist/` or a Docker context, show it to a
blind solver, or retain a live flag, credential, or production target in evidence.

Use `$rsctf-challenge-playtest` for a fresh player-equivalent run and
`$rsctf-challenge-release-review` for final readiness.
