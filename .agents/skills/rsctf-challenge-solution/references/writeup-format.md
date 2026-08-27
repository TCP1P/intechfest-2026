# README writeup format

Keep the writeup short enough to follow in one sitting. Use these headings in order:

1. `# <Challenge name> solution`
2. `## Verification record`
3. `## Summary`
4. `## Player inputs`
5. `## Walkthrough`
6. `## Why it works`
7. `## Solver`
8. `## Evidence`
9. `## Notes`

## Content rules

- Write in plain technical language. Prefer short sentences and concrete verbs.
- Follow the player's chronology. For each meaningful transition, show the observation,
  hypothesis, exact action, visible result, and capability gained.
- Do not use author source or solver constants to justify what a player tries earlier. Put
  author-only implementation detail under `Why it works` and label it clearly.
- Copy commands exactly. Use genuine trimmed output and redact flags as `rsctf{...}`.
- Link `solve.py`; do not paste a shortened second solver or repeat its orchestration in
  prose.
- Remove generic introductions, tool tutorials, decorative diagrams, and claims unsupported
  by the exact challenge revision.
- State when the challenge is illustrative or currently unplayable. Do not fabricate a solve.

The verification record uses `draft` until the exact commit and artifacts have been tested.
Only then use `frozen`. Keep screenshots under `assets/` and reference each image exactly once.
