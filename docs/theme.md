# Event and challenge themes

Theme applies to player-facing descriptions, handout copy, and intentional UI. Technical
writeups, checkers, and operational docs stay direct and professional. A theme may never hide
a required rule, replace a clue, or introduce a technical spoiler.

## INTECHFEST 2026

Use the event title exactly as `INTECHFEST 2026`. The warmup and main event are separate
rsctf games; challenge copy should not imply that progress, credentials, or scheduling carry
between them.

## Parcel Panic

Parcel Panic is a compact parcel-publishing service used to teach the BYOC Attack & Defense
flow. Player copy can use warehouse and dispatch language, but it must state the issued-host
setup, service contract, rotating-flag objective, and game-system scope directly. Do not turn
the path clue into lore or imply that teams may attack rsctf infrastructure.

## Draft Control

Draft Control is a shared page publisher in which teams compete for the live editorial
control marker. Use `draft`, `publish`, `revision`, `control`, and `hold`. The public objective
must name `/koth/king` and the Boot2Root capability workflow. Do not describe it as a
Leaderboard game or suggest that ordinary content points are scored.

## Serpent Circuit

Serpent Circuit is an original server-authoritative multiplayer Snakes & Ladders game. Use
`room`, `turn`, `roll`, `snake`, `ladder`, `match`, and `Crown`. Two to four seats share one
private room and race to square 100 under traditional rules. A lone host may add the
server-controlled `Circuit Bot`; label that room as practice-only because it emits no
Leaderboard evidence. The browser board uses CSS and text labels rather than copied artwork,
exposes the same rules as the API, and must remain keyboard-operable, screen-reader
understandable, and usable at 320 px.

The gameplay concept was requested with
[`MichaelDepp/snake-ladder` at `7bf22a3`](https://github.com/MichaelDepp/snake-ladder/tree/7bf22a3d7f14c18984ab67782531fed1a297169a)
as its first reference. The later classic-board UI direction references
[`yashksaini/snakes-and-ladders-game` at `d8dc452`](https://github.com/yashksaini/snakes-and-ladders-game/tree/d8dc452601b7075ba21e3f81179fe57e30f4c64e).
Neither repository publishes a license, so this repository copies none of their HTML,
JavaScript, CSS, questions, images, avatars, audio, board mappings, or screenshots. Keep both
pinned references and this provenance boundary in the organizer solution.

## Rythme

Rythme is a near-future professional rhythm-game qualifier operated by Syncore. Teams are
integrity crews competing in synchronized heats for a shared Crown. Use restrained esports
operations language: `heat`, `performer`, `spectator`, `ready`, `reveal`, `Crown`, `capture`,
and `hold`.

The visual language is charcoal and off-white with restrained magenta accents and timing-lane
geometry. Historical banner and trailer assets live under [`docs/media/rythme/`](media.md).
The intended reversing path must not appear in player-facing copy or artwork.

## Minions in 32K

Minions in 32K is an objective-aware arena in Gru's Lab. Player copy can mention combat,
confirmation routing, protocol analysis, the 32,768-pixel render constraint, and sustaining
the best confirmed performance. Do not disclose managed reporter credentials, scoring
internals, the reference headless client, or the exact automation strategy.

Retain the upstream Project Sekai attribution and license notices in every player artifact
that contains derived code or content. The package solution records the exact provenance.

## Copy rules

- Use the manifest's `description` for the public objective and rules, not the exploit chain.
- Keep challenge names stable after import.
- Do not name a real person or organization as part of the fiction without approval.
- Reviewed public player endpoints belong only in the release inputs that need them; internal
  topology and credentials do not belong in player copy.
- Every visible control or decorative element must support the game, clue path, accessibility,
  or theme. Remove nonfunctional look-alike controls.
- Screenshots must show the exact current revision and correct player identity. Follow
  [Media and screenshots](media.md).
