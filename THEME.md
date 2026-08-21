# Theme

> **Narrative contract for this challenge repo.** Every `challenge.yml` Description, challenge name, and story-facing UI element must honor the world described here. Technical Synopsis and writeup sections stay professional and do not adopt the narrative voice. Replace this starter content with your own, or run `/set-theme <google-docs-url>` to populate it from a Google Doc. If HTB provided a full Scenario document, that document is the primary source and this file is the working summary used for review.

## World / Universe

A near-future professional rhythm-game circuit where rankings trigger bracket placement and tournament payouts. The circuit runs a shared Crown arena: one team owns the stage at a time, while every crew performs the same randomly selected track in the same randomly selected discipline from one synchronized start signal. Every completed scoring interval under the owner's name becomes leaderboard points. Match infrastructure is modern and recognizable: signed desktop clients, group lobbies, live input telemetry, automated leaderboards, and human integrity reviewers.

## Company / Organization

Syncore operates Rythme, the official qualifier platform for the circuit. Its room coordinator selects a track and discipline from a curated tournament pool, starts every connected performer together, records live runs, transfers ownership of the Crown arena, and settles tournament eligibility and prize payments.

## Mission Framing

Each player team joins Syncore's controlled external audit as a competing integrity crew. One performer represents each crew in a shared live heat while teammates and rivals can observe the room. The performer must demonstrate a reproducible client-side cheat and then complete the coordinator-selected track in the revealed discipline with the modified client. Neither legitimate play nor a forged result alone can take the shared Crown arena.

## Tone & Voice

Terse esports operations with restrained corporate tension. Story-facing text should feel like a live tournament incident and sanctioned red-team exercise, not science-fiction exposition.

## Naming Conventions

- **Challenge names**: Single-word product names.
- **Operator / NPC names**: Job titles or short handles only.
- **Hostnames / domains**: Functional names under `.rythme.htb`.
- **Hill terminology**: Use `Crown`, `owner`, `capture`, `protected`, and `hold`; avoid military territory metaphors.
- **Room terminology**: Use `heat`, `performer`, `spectator`, `ready`, `reveal`, and `start`; all crews play one shared track-and-discipline selection.

## Visual Style

- **Banner**: Charcoal, off-white, and restrained magenta accents with timing-lane geometry.
- **Logo placement**: Upper-left when a product UI needs one.
- **Screenshots**: Clean captures with direct annotations and no decorative filters.

## Flag Flavor

If a conventional flag fallback is required, use client-attestation, tempo, timing, Crown, or ranking language. The primary dynamic-scoring deployment has no player flag submission.

## Hard Constraints

- Every `challenge.yml` Description must reference the Company and Mission Framing above.
- No real-world company, person, or brand names unless explicitly listed in this document.
- No technical spoilers in player-facing copy (`challenge.yml`, banner alt text, release artifacts).
- When the theme and a skill rule conflict, the skill rule wins. Theme voice and lore apply only to Description and story-facing UI copy, never to the technical writeup sections.
- Every app or UI element you build must earn its place in the story: who/what uses it, why it has this functionality, and how it fits the worldbuilding above. See the `htb-challenge-template` skill's `references/source-challenge-design-patterns.md`.

---

_Last updated: <!-- set-theme fills this -->_
