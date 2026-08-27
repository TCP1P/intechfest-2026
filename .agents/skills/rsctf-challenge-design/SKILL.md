---
name: rsctf-challenge-design
description: Design or critique an rsctf CTF challenge before implementation. Use for challenge brainstorming, intended exploit chains, player contracts, clue mapping, flag/control flow, difficulty targets, trust boundaries, or unintended-solution threat modeling; do not use for implementing an already-approved package.
---

# rsctf Challenge Design

Turn a challenge idea into a reviewable technical contract before source code or visual
polish makes it expensive to change.

## Workflow

1. Inspect the event manifest, closest challenge examples, and any supplied theme or event
   constraints. Preserve unrelated repository work.
2. Read [the design rubric](references/design-rubric.md). Read the human
   [authoring contract](../../../docs/authoring-contract.md) only when repository-specific
   layout or mode details are needed.
3. Establish the challenge type, category, delivery mode, runtime owner, flag/control path,
   initial player inputs, intended objective, prerequisite skills, and explicit non-goals.
4. Model one trust-boundary transition at a time. For every transition, record the prior
   capability, literal player-visible clue, hypothesis, action, observable confirmation,
   gained capability, and shortcuts that must fail.
5. Check feasibility against rsctf's actual delivery and lifecycle. Reject designs that
   depend on an unsupported dynamic attachment assignment, hidden organizer access, an
   unbounded race, unreliable external state, or a flag path different from grading.
6. Define acceptance tests, resource limits, reset behavior, intended solve-time range, and
   the evidence needed to calibrate difficulty.
7. Return a concise design contract with open decisions and concrete blocker risks. When a
   package already exists and the user asked for a written design record, put it in the
   organizer solution workspace, never in player delivery or a blind-playtest room.

## Decision boundary

Do not implement the challenge unless the request also asks for implementation. If a missing
choice would materially change the exploit, delivery mode, or player experience, present the
alternatives and ask the author rather than silently choosing one.

When implementation is authorized, hand the approved contract to
`$rsctf-challenge-authoring`. A design is not a readiness claim; later use the solution,
playtest, and release-review skills on the exact implemented revision.
