# Map V3 Plan: Raise the Roof

> **Status: implemented.** `Arena::gru_lab_v3()` (default in `--demo` and
> exported to `assets/scenes/`), `assets/zones/hall.glb`, deterministic
> tick-driven elevator (no protocol changes). Replay-verified.

Verticality redesign that **keeps the shipped five-zone lab intact**. Unlike
the v2 silo prototype (standalone blockout, lost the toy-lab look) and the
old Q3DM6-scale arena v3 (reverted), this is additive: the current cute
ground floor stays verbatim and the lab grows upward around it.

Preview: `PYTHONPATH=tools python3 tools/map_v3/build_lab_v3.py`
→ `tools/previews/map_v3/v3_{iso,top,ground_top,side}.png`. The script
imports the real zone builders, so the ground floor in the renders *is* the
current scene.

## Concept

The lab's roof lifts from y=8 to y=20. Above the rooms, the whole 72×32
footprint becomes one huge open hall — you stand in any ground-floor room,
look up through light wells, and see the goo tube rising 16 m with Gru's
rocket perched on top. A giant yellow **GRU** sign on the north hall wall.

Three combat layers:

- **L0 (y=0)** — the current lab, unchanged. Same walls, doorways, props,
  powerups, spawns, secret room. Zero relearning for players.
- **L1 (y=8.3)** — rooftop decks + bridge layer. The side-zone ceilings
  become walkable decks over the *outer 14 m* of each wing, checker-tinted
  per zone (turf green / pajama navy / cabinet cream / tile teal). The
  10 m strip next to the machine room is an open light well: decks drop
  into rooms, rooms see up to decks. Two 44 m bridges (z=±8) span
  deck-to-deck across the atrium, resting on the existing yellow-trimmed
  internal walls, linked by a square ring catwalk around the tube.
- **L2 (y=12.5) + tube top (y=16.3)** — two narrow gantries hugging the
  tube, then the tube-top platform: quad damage spawns next to the rocket,
  visible and shootable from the entire hall. Classic risk/reward perch.

The machine-room core keeps its steel base, hazard stripes, and coils;
above y=8 it continues as a glass goo tube (lava-lamp read: water blue,
white bubbles, steel ribs and collars). An open **elevator** rides the
tube's south face from the atrium floor to the top platform — slow, direct,
exposed: the alternative to the jump-pad route.

## Routes up

| From | Via | To |
| --- | --- | --- |
| each zone (pad in outer corner) | themed jump pad through a roof hatch | own rooftop deck |
| atrium floor (4 pads at ±8,±8) | jump pad | bridges |
| ring catwalk (2 pads) | jump pad | L2 gantries |
| north gantry (1 pad) | jump pad | tube top |
| atrium floor | elevator | tube top |
| orbiting platforms | existing y=2 hop route | (unchanged) |

Down: drop anywhere — light wells put you back in a themed room, the
atrium puts you mid-fight. Every layer has ≥2 exits.

Zone pad theming (visual only, same `JumpPad` mechanic): sports trampoline,
sleeping spring bunk, kitchen dumbwaiter, bathrooms hand dryer.

## Server / sim changes

- `Arena::gru_lab()`: ceiling_y 8→20; add deck/bridge/ring/gantry/tube-top
  AABBs (~25 boxes); add ~11 `JumpPad`s (mechanic already exists); move
  quad to the tube top; add 2–4 deck spawn points.
- Elevator is the only new mechanic: one vertical moving platform on a
  fixed loop — deterministic, tick-driven, state in the replay stream.
  (Subset of the v2 plan's `PropKind::Elevator`; the rest of the prop
  system stays future work.)
- Zone glbs: cut roof hatches + light-well openings into ceilings, add
  deck/bridge/tube/rocket meshes. `tools/map_v3/build_lab_v3.py` already
  contains all geometry at final coordinates — porting it into the zone
  scripts is mechanical.

## Readability at 160×100

Chunky 2 m checker decks, one hero landmark (tube + rocket) visible from
everywhere, yellow trim marking every walkable edge, cyan pads on dark
floors. No new small props in the upper hall — it stays sparse so players
read silhouettes, not clutter.

## Risks

- **Deck camping:** decks are big; rails have gaps at bridges and wells,
  and the well edge is a 270° exposure. If camping persists, feature-flag
  a powerup spawn rotation that forces movement (CTF runbook material).
- **Spawn safety under wells:** ground spawns sit under deck cover, not
  under open wells.
- **Elevator determinism:** fixed-loop platform, no player-triggered
  state in v1 of the feature; button trigger can come later as a flag.
