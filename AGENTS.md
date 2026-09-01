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
| prepare, run, or review a fresh blind solve | `$rsctf-challenge-playtest` |
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
- The warmup currently has three distinct engine examples. Do not duplicate one into another
  mode or silently change its player delivery or claim source.
- Keep Jeopardy scoring fields absent when rsctf defaults are intended. They do not belong
  on A&D or KotH manifests.
- Set `ad.allowEgress: true` on managed A&D and KotH manifests so challenge targets can
  reach the Internet. Use `false` only for a deliberate, reviewed isolation requirement.
  Confirm the selected runtime backend can enforce that policy; rsctf's Docker backend
  currently rejects competitive `true` workloads. BYOC host egress is outside this field.
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

## Current warmup contracts

- `Parcel Panic` is self-hosted/BYOC Attack & Defense. The team-owned service reads the
  relay-managed rotating flag file on every request; its checker verifies health, public
  parcel behavior, and the exact current flag through the legitimate vault contract.
- `Draft Control` is a shared Boot2Root/Marker KotH challenge. Its intended path atomically
  writes the current player capability to `/koth/king`; it has no managed reporter and must
  not be configured as Leaderboard.
- `Serpent Circuit` is a shared managed Leaderboard KotH scoring game. The target owns
  server-authoritative two-to-four-seat Snake & Ladders rooms, retains only authenticated
  human capability hashes, and reports one bounded furthest-board-progress result for every
  active human team in each completed 60-second scoring tick. A lone host may add one
  server-controlled NPC, but every NPC room is practice-only and emits no Leaderboard
  evidence. Lobby and finished rooms do not carry progress into later ticks. It has no marker
  or gateway.

All warmup checkers are bounded and read-only. The A&D checker receives the current flag;
both KotH checkers receive no flag and never inspect or alter their claim source.

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

Never commit a live flag, capability, reporter secret, admin credential, TLS private key,
undisclosed internal endpoint, database dump, or unredacted secret evidence. A reviewed
public player endpoint may appear only in the release input that owns it. Private GitHub
visibility protects organizer material from the public; it does not make committed secrets
safe.

The ignored `.agents/skills/rsctf-challenge-playtest/playtest/` room is convenience space,
not a sandbox. A credible blind run uses a separate workspace and receives only the event's
real player delivery.

## Completion gate

From the repository root run:

```sh
make check RSCTF=/path/to/rsctf
```

Use `make check` without the assignment when the matching official binary is already on
`PATH`. Runtime changes also
require the relevant language tests, target-architecture image builds, Docker health,
player-visible protocol checks, checker verdict tests, and a real multi-team KotH rehearsal.
Import/release work remains incomplete until hidden staging, normal-player access checks,
the fresh blind report, and the release review all pass on the exact candidate revision.
