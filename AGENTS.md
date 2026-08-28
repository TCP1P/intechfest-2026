# INTECHFEST 2026 challenge repository agent entry point

This private repository owns two independent rsctf Repository Bindings event roots. Keep
the warmup and main event isolated, preserve the player/organizer boundary, and keep human
documentation under `docs/`.

## Automatic skill routing

When Codex starts from this repository or a descendant, select the smallest matching
workflow under `.agents/skills/`, read its `SKILL.md` completely, and read every reference
it routes before acting:

| Task | Skill |
| --- | --- |
| design an exploit, clue path, or difficulty target | `$rsctf-challenge-design` |
| create or edit manifests, source, handouts, checkers, managed reporters, or gateways | `$rsctf-challenge-authoring` |
| write or reproduce `solution/README.md` and `solution/solve.py` | `$rsctf-challenge-solution` |
| prepare and run a fresh blind solve | `$rsctf-challenge-playtest` |
| assess a completed blind report and unintended paths | `$rsctf-playtest-review` |
| decide whether a revision is ready to import or enable | `$rsctf-challenge-release-review` |

Do not use author or solution context as the blind solver.

## Multi-event repository contract

- Event roots are exactly `events/warmup/` and `events/main-event/`.
- Each event root owns one `.gzevent` and only manifests below its own directory. Never add
  a root `.gzevent`, nest an event root, or move one event's challenge below the other.
- Put packages at
  `events/<event>/challenges/<mode>/<category>/<slug>/challenge.yaml`, where `<event>` is
  `warmup` or `main-event`, mode is `AD`, `Jeopardy`, or `Koth`, and category is supported by
  rsctf.
- The warmup currently has no challenge. Do not invent or duplicate one merely to populate
  the directory.
- Keep Jeopardy scoring fields absent when rsctf defaults are intended. They do not belong
  on A&D or KotH manifests.
- Put exact player downloads in `dist/`, image input in `src/`, A&D/KotH checks in
  `checker/`, an unavoidable transport-only adapter in `gateway/`, and organizer material
  in `solution/`. Leaderboard reporting normally runs inside the managed target in `src/`.
- Every challenge requires tracked `solution/README.md` and `solution/solve.py`. Neither may
  enter `dist/`, a Docker context, a gateway bundle, or a blind-playtest room.
- Direct `src/Dockerfile` and `generator/Dockerfile` contexts are discovered by
  `rsctf challenge matrix`; never hand-edit a container matrix or add a repository script.
- Every long-running service declares a meaningful Docker `HEALTHCHECK`. Docker health is
  separate from checker correctness and solvability.
- Keep validation on the official `rsctf challenge check` command through the maintained
  `dimasma0305/rsctf` action.

## Current main-event contracts

- `Rythme` is a hybrid, shared Leaderboard KotH challenge. Players receive the exact ZIP;
  the rsctf-managed coordinator reports native evidence in process, while `gateway/` only
  keeps its stable HTTPS hostname pointed at the current target.
- `Minions in 32K` is a hybrid, shared Leaderboard KotH challenge. Players receive the exact
  ZIP; the rsctf target owns identity, match validation, and scoring evidence, while the
  separately deployed `gateway/` only adapts HTTPS and UDP transport.

Both challenge checkers are bounded and read-only. They receive no flag and never inspect or
alter the KotH claim source.

## Secret and player boundary

Never commit a live flag, capability, reporter/legacy-observer secret, admin credential, TLS private key,
undisclosed internal endpoint, database dump, or unredacted secret evidence. A reviewed
public player endpoint may appear only in the release input that owns it. Private GitHub
visibility protects organizer material from the public; it does not make committed secrets
safe.

`playtest/` is ignored convenience space, not a sandbox. A credible blind run uses a separate
workspace and receives only the event's real player delivery.

## Completion gate

From the repository root run:

```sh
make check
```

Set `RSCTF` to the matching official binary when it is not on `PATH`. Runtime changes also
require the relevant language tests, target-architecture image builds, Docker health,
player-visible protocol checks, checker verdict tests, and a real multi-team KotH rehearsal.
Import/release work remains incomplete until hidden staging, normal-player access checks,
the fresh blind report, and the release review all pass on the exact candidate revision.
