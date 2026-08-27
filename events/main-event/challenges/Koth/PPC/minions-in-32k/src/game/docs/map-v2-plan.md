# Map V2 Plan: Vertical Lab Silo

Goal: replace the flat six-zone lab with a more vertical, toy-like villain lab built around a large central cylinder. It should still feel cute, readable at 16k internal render resolution, and server-authoritative.

Working name: **Silo Lab**

## Design Targets

- **More verticality:** three playable heights, not just raised platforms.
- **Central landmark:** one large cylinder in the middle that players can orient around from any room.
- **Multiple rooms on different floors:** each floor has 2-4 themed rooms connected to the central cylinder ring.
- **Cute lab aesthetic:** chunky proportions, rounded-looking silhouettes, bright yellow/teal/magenta accents, soft hazard stripes, oversized toy props.
- **CTF readability:** no photoreal clutter; props must be recognizable as gameplay devices from a distance.
- **Authoritative collision:** keep sim collision deterministic with AABBs. Cylinders can be visual meshes with segmented AABB collision approximations.
- **Props do things:** props become deterministic gameplay systems, not only decoration.

## Core Layout

Coordinate target:

- Overall footprint: about `72m x 48m`
- Height: `0m..14m`
- Central cylinder: radius about `6m`, visual full height, collision as segmented ring or solid AABB core depending on playtest.
- Three floors:
  - `F0` at `y=0`: main fight floor
  - `F1` at `y=4`: catwalk/control floor
  - `F2` at `y=8`: gantry/observation floor

The central cylinder is not just a wall. It is the map's compass, elevator shaft, hazard machine, and route switch.

## Sketches

See [map-v2-sketch.svg](./map-v2-sketch.svg) for a first vector sketch.

### F0: Main Floor

```text
z-
       [ SPORTS TEST BAY ]     [ FREEZER / KITCHEN ]
       spring pads + ball      slippery floor + cabinets
              |                         |
     +--------+-----------+-------------+--------+
     |                    |                      |
     |      outer loop    |      outer loop      |
     |                    |                      |
     |        +-----------+-----------+          |
     |        |   CENTRAL CYLINDER    |          |
     |        | reactor / elevator    |          |
     |        +-----------+-----------+          |
     |                    |                      |
     |      outer loop    |      outer loop      |
     |                    |                      |
     +--------+-----------+-------------+--------+
              |                         |
       [ DORM / BUNK ROOM ]      [ BATHROOMS ]
       spring beds + drawers     hand dryers + stalls
z+
```

F0 role:

- Easiest navigation and spawn floor.
- Strong ring route around the cylinder.
- Four cute themed rooms, each with a useful interactive prop.
- Multiple line-of-sight breaks around the cylinder so the map does not become one big rail arena.

### F1: Control Catwalk

```text
z-
        [ ROCKET CONTROL ]----catwalk----[ JELLY LAB ]
             buttons                       goop vents
                 \                         /
                  \                       /
                 +-------------------------+
                 |  ring catwalk around    |
                 |  central cylinder       |
                 +-------------------------+
                  /                       \
                 /                         \
        [ STORAGE CLAW ]----catwalk----[ LAUNDRY / FAN ]
             crane arm                    wind tunnels
z+
```

F1 role:

- Medium-risk power position.
- Lets players cross over F0 without hard camping every room.
- Has exposed catwalks, short rail sightlines, and prop controls that influence F0.

### F2: Upper Gantry

```text
z-
              +-------------------+
              | observation deck  |
              | low cover + vents |
     +--------+---------+---------+--------+
     |                  |                  |
     |    narrow gantry around silo        |
     |      gaps, jump exits, glass        |
     |                  |                  |
     +--------+---------+---------+--------+
              | trophy / blueprint nook |
              +-------------------------+
z+
```

F2 role:

- Short visits, not a permanent safe sniper nest.
- Strong information but weak escape if another player commits upward.
- Access through elevator, launch tubes, bunk springs, and one maintenance ladder/step-stack.

### Side Section

```text
     F2 y=8       gantry ---- observation ---- gantry
                  |              |             |
                  |        central silo        |
     F1 y=4   catwalk ---- control ring ---- catwalk
                  |              |             |
                  |        lift / core         |
     F0 y=0   rooms  ---- main ring floor ---- rooms
```

## Traversal Loops

Primary loops:

- **Ground ring:** fast, safe, intuitive. Connects all F0 rooms.
- **Catwalk ring:** riskier, faster cross-map path. Exposed to rail shots.
- **Vertical burst loop:** jump pad / spring bed / launch tube up, drop chute or slide back down.
- **Cylinder elevator loop:** slower but direct; announces itself with lights/sound/event.

Access points:

- F0 to F1:
  - central elevator
  - sports trampoline
  - bathroom hand-dryer boost
  - kitchen dumbwaiter lift
- F1 to F2:
  - rocket-control lift platform
  - storage claw ride
  - catwalk jump pad
- F2 to F0:
  - slide chute around cylinder
  - emergency drop vents
  - elevator descent

## Prop Interaction Model

The props should be deterministic server-side systems. Visual props live in GLBs; gameplay props live in sim metadata.

Proposed data model:

```rust
pub struct PropSpawn {
    pub id: u16,
    pub kind: PropKind,
    pub trigger: Aabb,
    pub body: Aabb,
    pub cooldown_ticks: Tick,
}

pub enum PropKind {
    Elevator,
    SpringBed,
    HandDryer,
    FreezeRayLeak,
    Conveyor,
    ClawCrane,
    RocketButton,
    GoopVent,
    CabinetDoor,
    BallCannon,
}
```

Proposed events:

```rust
GameEvent::PropActivated { id, kind, actor }
GameEvent::PropState { id, active }
GameEvent::PropImpulse { id, target }
GameEvent::PropDamage { id, target, amount }
```

This keeps replay/spectator/client synchronized without trusting client-side prop logic.

## Prop Behavior Sketch

| Prop | Room | Server behavior | Cute read |
| --- | --- | --- | --- |
| Central elevator | cylinder | raises/lowers platform on timed loop or button press | glowing banana-yellow platform inside silo |
| Spring bed | dorm | vertical impulse like jump pad, short cooldown | bunk bed mattress squashes visually |
| Hand dryer | bathrooms | lateral wind impulse in a cone/box | oversized dryer with teal stream |
| Freezer leak | kitchen | temporary low-friction floor or slow effect | frosty pipe and snow puff |
| Conveyor belt | kitchen/storage | applies constant directional impulse | tiny belt with bananas/crates |
| Claw crane | storage | moving cover/hazard path; can shove players | toy claw grabs crates above catwalk |
| Rocket button | control | opens silo vents or toggles elevator route | big red button with label-free shape |
| Goop vent | jelly lab | periodic damage/slow puddle | bubbly green lab goo |
| Cabinet door | kitchen | timed moving cover; blocks rail LOS | cabinet flaps open like a toy |
| Ball cannon | sports | launches slow knockback ball on cooldown | foam soccer-ball cannon |

## First Implementation Slice

1. Add a separate arena choice:
   - `ArenaChoice::SiloLab`
   - `Arena::silo_lab()`
   - `assets/scenes_v2/`
   - `assets/zones_v2/`
2. Build collision first:
   - floor plates at `y=0`, `y=4`, `y=8`
   - central cylinder approximated as either solid core or 12 AABB ring segments
   - outer room boxes and catwalk slabs
   - eight spawn points spread across F0 and F1
3. Build visuals second:
   - new `tools/zones_v2/` scripts or parameterized shared zone builder
   - central cylinder hero GLB first
   - one room per floor at a time
4. Add prop metadata:
   - extend scene JSON with gameplay prop fields
   - load props into `Arena`
   - implement two low-risk props first: `SpringBed` and `HandDryer`
5. Add prop events to protocol/replay/client/spectator.
6. Add previews:
   - top per floor
   - side section
   - hero iso render

## Implementation Risks

- **AABB-only collision:** true ramps/cylinders need approximation. Prefer catwalks, step stacks, elevators, and jump pads.
- **Spawn fairness:** vertical maps can spawn-trap. Spawns need at least two nearby exits and one vertical option.
- **Sightline dominance:** F2 must be useful but not a safe rail nest. Add low cover, glass-like visual blockers, and multiple flank routes.
- **Prop spam:** all props need cooldowns and replay events.
- **Cute vs noisy:** keep props chunky and sparse. One hero prop per room beats many tiny details.

## Acceptance Criteria

- Player can identify the central cylinder from every major route.
- Every floor has at least two routes to another floor.
- Every room has exactly one obvious interactive prop.
- A replay can reconstruct all prop activations.
- Spectator can render prop states from replay events.
- At 160x100 internal render, doorways, floors, props, and enemies remain readable.
