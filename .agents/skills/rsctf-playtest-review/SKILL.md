---
name: rsctf-playtest-review
description: Review an rsctf blind playtest report from the author side. Use to assess clue fairness, guesses, dead ends, practical difficulty, contamination, environment blockers, or unintended-solution severity and to produce spoiler-free retest feedback; do not run the blind solve itself.
---

# rsctf Playtest Review

Compare a completed blind report with the intended organizer solution without retroactively
giving the player knowledge they did not have.

## Workflow

1. Work from the author context. Inspect the exact candidate revision, player contract,
   completed `PLAYTEST-REPORT.md`, organizer-only intended path, and any genuine evidence.
2. Read [the review rubric](references/review-rubric.md). Do not alter the player's report.
3. Validate the run boundary first. If author context, forbidden files, hints, or a different
   target revision entered the run, classify it `contaminated` and do not use it for
   difficulty or clue conclusions.
4. Map each report action to the intended trust-boundary ledger. Separate confirmed
   capability, unsupported belief, literal clue, guess, dead end, environment failure, and
   reproducible shortcut.
5. Classify each unintended path by the highest boundary it skips. Critical and high paths
   block release and difficulty judgment until fixed and rerun.
6. Distinguish challenge defects from player mistakes. A missing breadcrumb is a clue defect;
   an outage is an environment defect; complex but evidenced reasoning may support difficulty.
7. Instantiate [the review template](assets/playtest-review.md) as
   `playtest/PLAYTEST-REVIEW.md`. Keep player-facing feedback spoiler-free: confirm only what
   the player demonstrated, identify false beliefs without giving replacements, and request
   controlled evidence rather than hints.
8. Recommend source/copy/artifact changes only when the evidence supports them. Do not
   implement fixes unless the user also asks for changes. Any changed player input requires a
   fresh room and fresh blind run.

Report whether the run supports a difficulty assessment and list the exact blockers. A clean
solve can still reveal guessing or unintended paths; a stuck run can still provide useful
evidence.
