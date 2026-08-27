# Media and screenshots

`docs/media/` contains durable human-facing artwork and historical promotional source. It is
not a player handout, Docker build context, organizer solution, or blind-playtest input.

The existing `docs/media/rythme/` tree preserves reference artwork, teaser audio/video, and
Remotion trailer source from the original repository. Files prefixed `htb-reference-` are
inherited Hack The Box reference marks, not INTECHFEST branding; never publish them as event
artwork. Keep historical source here unless the event deliberately publishes a reviewed,
properly licensed copy elsewhere.

Store evidence by audience:

| Material | Location |
| --- | --- |
| exact player-delivered image | the challenge's `dist/` |
| runtime UI source | the challenge's `src/` |
| writeup screenshot or freezed figure | the challenge's `solution/assets/` |
| temporary blind-playtest capture | ignored `playtest/evidence/` |
| durable event artwork or documentation image | `docs/media/` |

Do not add a screenshot only for decoration. When an image proves a visual state, capture the
exact revision through the correct player identity, record its command or route and UTC time,
redact secrets and internal endpoints, and inspect the final file at readable scale. For
annotated source, follow [Solutions and reference solvers](solutions.md) and use `freezed` on
the exact source bytes being discussed.
