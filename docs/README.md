# Documentation

Use this page as the human entry point. Pick the job you are doing instead of
reading every reference front to back.

| I want to… | Read |
| --- | --- |
| create my first challenge | [Getting started](getting-started.md) |
| understand every directory and file owner | [Challenge authoring contract](authoring-contract.md) |
| look up `.gzevent` or `challenge.yaml` fields | [Manifest reference](configuration.md) |
| write the organizer solution or reference solver | [Solutions and reference solvers](solutions.md) |
| write or debug an A&D/KotH checker | [Checker development](checkers.md) |
| run a fair blind solve | [Playtesting](playtesting.md) |
| import or rescan the repository | [Repository Bindings](importing.md) |
| prepare deterministic variants or solve receipts | [Provenance](provenance.md) |
| deploy the signed Leaderboard KotH observer | [Trusted KotH referee](koth-referee.md) |
| place screenshots, freezed figures, or event media | [Media and screenshots](media.md) |
| keep player copy consistent with the event | [Event and challenge themes](theme.md) |
| inspect immutable artifact and production topology | [Production release contract](production.md) |
| decide whether a challenge is ready to import | [Release checklist](release-checklist.md) |

The root [README](../README.md) is intentionally short. Challenge packages contain
only files assigned by the [authoring contract](authoring-contract.md). Put shared
maintainer documentation here so teammates have one place to search. Package-local
solutions are tracked organizer material; restrict repository access for real pre-event
work and keep live secrets out of Git.

AI coding agents use the same facts through the repository's [AGENTS.md](../AGENTS.md)
and task-specific workflows under `.agents/skills/`. The skills are implicitly selectable;
`$rsctf-challenge-design`, `$rsctf-challenge-authoring`,
`$rsctf-challenge-solution`, `$rsctf-challenge-playtest`,
`$rsctf-playtest-review`, and `$rsctf-challenge-release-review` can also be invoked
explicitly.
