# Event and challenge themes

Theme applies to player-facing descriptions, handout copy, and intentional UI. Technical
writeups, checkers, and operational docs stay direct and professional. A theme may never hide
a required rule, replace a clue, or introduce a technical spoiler.

## INTECHFEST 2026

Use the event title exactly as `INTECHFEST 2026`. The warmup and main event are separate
rsctf games; challenge copy should not imply that progress, credentials, or scheduling carry
between them.

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
