# tools/

Source files and pipelines for the runtime-loaded assets under `assets/`.

Phase 3 ships a **procedural Python pipeline** — no Blender, no GPU, no hand
meshing. Each zone and the minion character is authored as a Python script
that composes primitives (box, cylinder, capsule, icosphere, torus, cone)
into a glTF 2.0 `.glb` with embedded per-vertex color. At the game's internal
resolution (160x100 upscaled), vertex colors read indistinguishably from
baked textures and skip the texture-pipeline headache entirely.

## Layout

```
tools/
  pyasset/            small glTF/GLB writer + mesh primitives + color helpers
  zones/              one script per zone; shared palette + common helpers
    build_all.py      THE entry point: glbs + scene JSONs + manifest + check
    palette.py        colors used across the lab (and for the minion)
    common.py         ZoneBuild: visual + collision + gameplay in one object
    sports.py         -> assets/zones/sports.glb + sports.collision.glb
    sleeping.py       -> assets/zones/sleeping.glb + sleeping.collision.glb
    machine_room.py   -> assets/zones/machine_room.glb + ...
    kitchen.py        -> assets/zones/kitchen.glb + ...
    bathrooms.py      -> assets/zones/bathrooms.glb + ...
  minion/
    minion.py         rigged character variants + gun + baked animation clips
                      -> assets/minion/minion.glb + animations.json
  preview/
    render.py         CPU software rasterizer for headless PNG previews
    contact_sheet.py  render every zone + character into a single grid PNG
  previews/           output PNGs (gitignored-ish; cheap to rebuild)
  blender/            reserved — empty; see README there
```

## Rebuild

```bash
# One-time setup
pip install pygltflib trimesh numpy pillow

# Build the whole level: glbs + scene JSONs + manifest + consistency check
PYTHONPATH=tools python3 tools/zones/build_all.py

# The sim embeds assets/scenes/*.json at compile time, so afterwards:
cargo build

# Character (separate from the level)
PYTHONPATH=tools python3 tools/minion/minion.py

# Render previews (optional)
python3 tools/preview/contact_sheet.py
PYTHONPATH=tools python3 tools/map_v3/build_lab_v3.py
```

Single zone scripts can be run individually, but `build_all.py` is the
canonical entry point — it also writes the manifest and runs
`tools/level/check.py` (invisible-wall / pad-landing / elevator-shaft
checks).

## Conventions

- **Y-up, meters, glTF 2.0 / GLB single-file.** The client is Bevy 0.14 and
  loads these verbatim.
- **Vertex colors only** — no textures, no materials beyond a default PBR slot
  with `metallicFactor=0, roughnessFactor=0.95, baseColorFactor=(1,1,1,1)`.
  Colors are authored sRGB-in-hex, converted to linear for COLOR_0.
- **World-space authoring.** Each zone's geometry lives at its world position
  from `crates/sim/src/arena.rs::Arena::gru_lab()`. The client spawns them at
  identity transform and they line up with the authoritative collision.
- **Collision AABBs.** Each zone script also emits a `*.collision.glb` that
  contains the same AABBs as boxes, for future server-side collision work.
  (Today the server still reads collision from `sim/arena.rs`.)
- **Flat shading.** Primitives duplicate verts per face so the pixel-art look
  pops. Exception: the minion body capsule + head are smooth-shaded because
  rounded skin silhouettes read better than faceted ones.
- **Minion variants.** `assets/minion/minion.glb` contains four scenes:
  classic, Bob-style, Kevin-style, and Stuart-style. Each scene carries the
  same skeleton and the same 8 animation clips, grouped by variant in
  animation index order.

## Level source of truth

The zone scripts ARE the level. Each `ZoneBuild` carries the visual mesh
and the gameplay data (collision, jump pads, spawns, powerups, elevators)
side by side; `build_all.py` writes both the glbs and `assets/scenes/`,
and `Arena::gru_lab_v3()` parses those JSONs embedded at compile time.
There is no hand-mirrored geometry in Rust anymore (only the legacy
`gru_lab()` v1 remains hand-written). Never edit `assets/scenes/*.json`
by hand — regenerate.

## Adding a new prop

1. Pick a zone script.
2. Compose primitives inside `build()` using `z.add(...)` for visual and
   `z.collide(center, size)` for anything players should collide with —
   every visible obstacle needs a collider, or check.py will flag drift
   the other way (collider with no visuals fails the build).
3. `PYTHONPATH=tools python3 tools/zones/build_all.py && cargo build`
4. Spot-check with `PYTHONPATH=tools python3 tools/map_v3/build_lab_v3.py`.
