"""Render the v3 "raise the roof" lab from the real zone builders.

Composes the shipped zone scripts (including hall.py, the v3 upper layer)
into one mesh and renders preview PNGs. What you see here is what the
client loads, plus preview-only markers: the elevator platform frozen
mid-ride and the quad pickup (both client-rendered from arena data in
the real game).

Run:  PYTHONPATH=tools python3 tools/map_v3/build_lab_v3.py
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from pyasset import MeshBuilder, box, icosphere
from pyasset.write import build_glb

from zones import palette as P
from zones import sports, sleeping, kitchen, bathrooms, machine_room, hall, sky
from zones.common import wall_x, wall_z
from preview.render import render

def ground_extras(mb):
    """Secret room shell — the client draws it as plain cuboids from the
    arena data, so it has no glb of its own."""
    mb.extend(box(size=(24, 0.1, 10), color=P.GRU_PURPLE,
                  center=(0, -0.05, 21.0)))
    mb.extend(wall_x(-12.0, 12.0, 26.0, thick=0.5))
    mb.extend(wall_z(16.0, 26.0, -12.0, thick=0.5))
    mb.extend(wall_z(16.0, 26.0, 12.0, thick=0.5))


def build(cutaway: bool, with_sky: bool = False) -> MeshBuilder:
    mb = MeshBuilder(name="lab_v3")
    for zone in (sports, sleeping, kitchen, bathrooms, machine_room):
        mb.extend(zone.build().mesh)
    mb.extend(hall.build(cutaway=cutaway).mesh)
    if with_sky:
        # moon + shooting star only — the full dome would wrap the camera
        mb.extend(sky.build(preview=True).mesh)
    ground_extras(mb)
    # elevator platform frozen mid-ride + quad marker on the tube top
    # (pads are baked into the zone glbs themselves now)
    mb.extend(box(size=(2.5, 0.3, 2.5), color=P.PLATFORM_YELLOW,
                  center=(0, 8.85, 4.5)))
    for k in range(-2, 3):
        mb.extend(box(size=(0.16, 0.04, 2.3), color=P.WARN_STRIPE,
                      center=(k * 0.5, 9.02, 4.5)))
    mb.extend(icosphere(radius=0.35, subdivisions=1, color=P.GLOW_CORE,
                        center=(1.9, 16.85, 1.9)))
    return mb


def main():
    out = TOOLS / "previews" / "map_v3"
    out.mkdir(parents=True, exist_ok=True)
    mb = build(cutaway=True)
    glb = str(out / "lab_v3_cutaway.glb")
    build_glb(mb, glb, scene_name="lab_v3")
    print(f"lab_v3: {mb.tri_count()} tris")
    render(glb, str(out / "v3_iso.png"), 960, 640, "iso", y_cut=19.6)
    render(glb, str(out / "v3_top.png"), 960, 640, "top", y_cut=19.6)
    render(glb, str(out / "v3_ground_top.png"), 960, 640, "top", y_cut=7.5)
    night = build(cutaway=True, with_sky=True)
    glb_n = str(out / "lab_v3_night.glb")
    build_glb(night, glb_n, scene_name="lab_v3_night")
    render(glb_n, str(out / "v3_night_side.png"), 960, 640, "side")
    render(glb_n, str(out / "v3_night_hero.png"), 960, 640, "hero")


if __name__ == "__main__":
    main()
