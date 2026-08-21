"""Render the current `sim::Arena::gru_lab()` as a glb and preview PNGs.

Pipeline:
    cargo run --release -p sim --example dump_arena   -> arena.json
    build an aggregated glb from pyasset primitives   -> /tmp/arena.glb
    render.render(...)                                -> tools/previews/arena_*.png

Run this after changing arena.rs to get visual confirmation of the layout
(colliders + jump pads + spawn points).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from pyasset import MeshBuilder, box, cylinder, icosphere  # noqa: E402
from pyasset.write import build_glb  # noqa: E402
from preview.render import render  # noqa: E402


# ---------- colour keys mirror the client's per-region materials ----------
OUTER = (0.22, 0.24, 0.29)
ATRIUM = (0.30, 0.32, 0.38)
HANGAR = (0.42, 0.36, 0.24)
PIT = (0.42, 0.26, 0.28)
CORRIDOR = (0.24, 0.28, 0.36)
COURTYARD = (0.30, 0.40, 0.30)
PLATFORM = (0.55, 0.55, 0.62)
REACTOR = (0.85, 0.55, 0.15)
SECRET = (0.32, 0.33, 0.38)

PAD_COLOR = (0.10, 0.85, 0.95)
SPAWN_COLOR = (0.20, 0.95, 0.30)


def wall_color(cx: float, cy: float, cz: float, sx: float, sy: float, sz: float):
    """Match the client's per-region wall colouring so the render looks like
    the in-game geometry."""
    if cz > 40.5:
        return SECRET
    if (
        abs(sx - 5.0) < 0.5 and abs(sy - 15.0) < 0.5
        and abs(cx) < 3.0 and abs(cz) < 3.0
    ):
        return REACTOR
    if sy < 0.5 and cy > 0.8:
        return PLATFORM
    if abs(cx) >= 59.5 or abs(cz) >= 39.5:
        return OUTER
    if abs(cx) <= 15.5 and abs(cz) <= 15.5:
        return ATRIUM
    if cx < -15.0:
        return HANGAR
    if cx > 15.0:
        return PIT
    if cz < -15.0:
        return CORRIDOR
    return COURTYARD


def build_arena_mesh(arena: dict) -> MeshBuilder:
    mb = MeshBuilder(name="arena")

    # Floor — subtle dark so the walls pop.
    mb.extend(box(
        size=(260.0, 0.02, 180.0),
        color=(0.10, 0.11, 0.14),
        center=(0.0, arena["floor_y"] - 0.02, 0.0),
        name="floor",
    ))

    for w in arena["walls"]:
        mn, mx = w["min"], w["max"]
        cx = (mn[0] + mx[0]) * 0.5
        cy = (mn[1] + mx[1]) * 0.5
        cz = (mn[2] + mx[2]) * 0.5
        sx = mx[0] - mn[0]
        sy = mx[1] - mn[1]
        sz = mx[2] - mn[2]
        mb.extend(box(
            size=(sx, sy, sz),
            color=wall_color(cx, cy, cz, sx, sy, sz),
            center=(cx, cy, cz),
        ))

    for p in arena["jump_pads"]:
        c = p["center"]
        r = p["radius"]
        # Flat disc base
        mb.extend(cylinder(
            radius=r,
            height=0.10,
            segments=18,
            color=PAD_COLOR,
            center=(c[0], c[1] + 0.05, c[2]),
        ))
        # Inner ring (brighter)
        mb.extend(cylinder(
            radius=r * 0.5,
            height=0.12,
            segments=14,
            color=(0.9, 0.95, 1.0),
            center=(c[0], c[1] + 0.06, c[2]),
        ))

    for s in arena["spawn_points"]:
        mb.extend(icosphere(
            radius=0.5,
            subdivisions=1,
            color=SPAWN_COLOR,
            center=(s[0], s[1] + 0.5, s[2]),
        ))

    return mb


def main():
    print("==> running `cargo run -p sim --example dump_arena`")
    out = subprocess.run(
        ["cargo", "run", "--release", "-p", "sim", "--example", "dump_arena"],
        cwd=str(ROOT),
        capture_output=True,
        check=True,
    )
    arena = json.loads(out.stdout.decode())

    print(f"   walls: {len(arena['walls'])}  pads: {len(arena['jump_pads'])}  "
          f"spawns: {len(arena['spawn_points'])}")

    mb = build_arena_mesh(arena)
    glb_path = Path("/tmp/arena_preview.glb")
    build_glb(mb, str(glb_path), scene_name="arena")
    print(f"==> wrote {glb_path}  ({mb.tri_count()} tris)")

    previews = TOOLS / "previews"
    previews.mkdir(parents=True, exist_ok=True)

    # Four views. Large radius so the 120 × 80 arena fits comfortably.
    views = [
        ("top", {"view": "top", "radius": 80.0, "target": (0.0, 0.0, 0.0)}),
        ("iso", {"view": "iso", "radius": 85.0, "target": (0.0, 5.0, 0.0)}),
        ("front", {"view": "front", "radius": 90.0, "target": (0.0, 6.0, 0.0)}),
        ("side", {"view": "side", "radius": 90.0, "target": (0.0, 6.0, 0.0)}),
    ]
    for name, kwargs in views:
        out_path = previews / f"arena_{name}.png"
        render(
            str(glb_path), str(out_path),
            width=960, height=640,
            **kwargs,
        )


if __name__ == "__main__":
    main()
