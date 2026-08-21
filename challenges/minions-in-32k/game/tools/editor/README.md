# tools/editor/

Browser-based arena editor for `sim::Arena::gru_lab()`.

## Usage

```bash
python3 tools/editor/serve.py           # default http://127.0.0.1:8080/
```

The server:
- serves the HTML / JS / CSS
- on every `GET /arena.json`, runs `cargo run --release -p sim --example dump_arena`
  and returns the result (each wall tagged with its themed Gru's-lab zone)
- on `POST /save`, splits the edited JSON into per-zone files under
  `assets/scenes/<zone>.json` and writes a flat `tools/editor/edits.json`
  backup. The client's demo mode loads `assets/scenes/` if present, so
  edits apply on the next `cargo run -p client -- --demo`.

## Controls

- **left drag** — orbit
- **right drag** — pan
- **scroll** — zoom
- **click a wall / pad / spawn** — select; the inspector on the right opens
  for editing center, size, and (for pads) radius + launch velocity
- **drag the transform gizmo** to move objects; values in the inspector
  update live
- **duplicate / delete** work on the current selection
- **↓ download json** downloads the current edited arena as a local file
- **⇪ apply** POSTs the arena to `edits.json` so a follow-up commit can
  fold it into the scene definitions

## Per-zone split

The themed zones live under `assets/scenes/`:

- `manifest.json` — floor/ceiling + zone order
- `machine_room.json`, `sports.json`, `bathrooms.json`, `sleeping.json`,
  `kitchen.json`, `secret.json`, `outer.json` — each has `walls`,
  `jump_pads`, `spawn_points`, and a `props` slot (for `.glb` references,
  not yet wired into the client).

Write-back is automatic: the editor's **⇪ apply** POSTs the full arena,
which the server splits by zone and rewrites the matching files.

Bootstrap (first time, or to reset from the baked arena):

```bash
cargo run --release -p sim --example export_scenes
```

Round-trip check:

```bash
cargo run --release -p sim --example verify_scenes
```
