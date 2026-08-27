---
name: rsctf-challenge-release-review
description: Audit whether an rsctf challenge revision is ready to import, enable, publish, or rescan. Use for ship checks, release readiness, hidden-staging evidence, committed-tree leakage, solution/playtest completeness, container health, checker/generator/KotH/BYOC lifecycle, or final challenge approval; review is read-only unless import or enablement is explicitly authorized.
---

# rsctf Challenge Release Review

Make a readiness decision from evidence for one exact revision. Do not promote a challenge
because its manifest parses or its author solver works.

## Workflow

1. Inspect Git status and identify the exact candidate commit. Read
   [the release evidence contract](references/release-evidence.md).
2. Inspect the committed tree, manifest, player artifacts, source/build contexts,
   dependencies, checkers, generators, observers, and organizer solution verification record.
   Confirm every package has a concise `solution/README.md` and simple `solution/solve.py`,
   neither enters a player/build surface, and no live secret or unintended artifact is
   committed.
3. Run `make validate` and `make matrix` with the official rsctf binary matching the target
   release. Require zero warnings. Confirm every direct service/generator context appears in
   the emitted matrix; do not use a hand-written list or repository validator.
4. Require applicable component tests and target-architecture builds. Every long-running
   service must declare and pass Docker health, followed by independent player-visible
   protocol evidence.
5. Verify a second trusted maintainer reproduced the organizer solution/reference solver on
   the candidate identity. Review the fresh uncontaminated blind report and playtest review;
   critical/high unintended paths must be fixed and rerun.
6. Require hidden, disabled Repository Bindings staging evidence. Check normal-player access,
   exact challenge count, build/preparation results, and rescan consequences. Require the
   real multi-team lifecycle for A&D, KotH, or BYOC changes.
7. Return `ready`, `not ready`, or `blocked`, followed by passed evidence and missing evidence
   tied to the exact revision. Never turn a missing gate into an assumed pass.

## Mutation boundary

This skill is a read-only audit by default. Do not push, import, rescan, enable, publish, or
change live event state unless the user explicitly requests that action. Automatic skill
selection provides workflow context, not external-mutation authorization.
