# rsctf blind playtest room

Act only as a player. Read `PLAYER-BRIEF.md` and the declared files under `inputs/`.
Interact only with the listed target, supplied files, and ordinary player-owned callback
capabilities explicitly stated in the brief.

Do not inspect parent directories, repository history, author source, package manifests,
solutions, reference solvers, build logs, host processes, container internals, undeclared
localhost services, organizer interfaces, or previous reports. Do not ask the author for a
hint during the timed run. If any forbidden context becomes visible, stop and mark the run
`contaminated`.

Record work in `PLAYTEST-REPORT.md` as it happens. For every important attempt, include UTC
time, observation, hypothesis, exact action, result, and capability gained. Mark guesses and
possible shortcuts explicitly. The valid final verdicts are `solved`, `partial`, `stuck`,
`blocked-environment`, and `contaminated`.

Do not modify `PLAYER-BRIEF.md`, replace supplied artifacts, or erase failed attempts from the
report. You may create player work files inside this room.
