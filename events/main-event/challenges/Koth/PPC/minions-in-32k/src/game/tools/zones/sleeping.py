"""Sleeping quarters — bunk rows, striped pajamas energy.

Footprint: x in [-36,-12], z in [0,16]. Zone center (-24, 0, 8).
Hero props: stacked bunk beds matching arena.rs AABBs, striped bedding,
chevron pajamas mural, softly lit by magenta night-lamps.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyasset import MeshBuilder, box, cylinder, icosphere
from pyasset.builder import translate
from pyasset.write import build_glb, write_collision_glb

from zones import palette as P
from zones.common import (
    hang_art, art_minion_portrait, art_banana,
    ZoneBuild, write_zone, wall_x, wall_z,
    pipe_strip, vert_pipe,
)


def build():
    z = ZoneBuild("sleeping", bounds=((-36.0, 0.0, 0.0), (-12.0, 8.0, 16.0)))
    x0, x1, z0, z1 = -36.0, -12.0, 0.0, 16.0
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2

    # --- Floor: wood-plank cream (striped pattern of alternating 2m boards)
    plank_w = 1.6
    n_planks = int((x1 - x0) / plank_w)
    for i in range(n_planks):
        c = P.BUNK_WOOD if i % 2 == 0 else (P.BUNK_WOOD[0] * 0.85,
                                            P.BUNK_WOOD[1] * 0.85,
                                            P.BUNK_WOOD[2] * 0.85)
        z.add(box(size=(plank_w * 0.98, 0.08, z1 - z0), color=c,
                  center=(x0 + (i + 0.5) * plank_w, -0.04, cz),
                  name="plank"))

    # --- Walls (x=-12 internal owned by machine_room.py)
    z.solid_wall_z(z0, z1, -36.0, thick=0.5, color=P.PAJAMA_NAVY)
    z.solid_wall_x(x0, x1, 16.0, thick=0.5, color=P.PAJAMA_NAVY)
    # Divider at z=0: sleeping owns x [-15,-12]; doorway [-18,-15] open
    z.solid_wall_x(-15.0, -12.0, 0.0, thick=0.4, color=P.WALL_BASE)

    # --- Ceiling
    # v3: no full ceiling — the hall layer (hall.py) owns decks + light wells

    # --- Pajama-stripe mural on south wall (interior side, z just < 16)
    # Centre must sit at least (wall_half + panel_half) inside the wall face
    # to avoid z-fighting: wall inner face at z=15.75, panel depth 0.1, so
    # center ≤ 15.65. Using 15.55 for a visible 0.15 m offset from the wall.
    stripe_y0, stripe_y1 = 1.5, 6.8
    stripes = 10
    for i in range(stripes):
        y_top = stripe_y1 - i * ((stripe_y1 - stripe_y0) / stripes)
        c = P.PAJAMA_NAVY if i % 2 == 0 else P.PAJAMA_STRIPE
        z.add(box(size=(x1 - x0 - 1.0, (stripe_y1 - stripe_y0) / stripes - 0.02, 0.1),
                  color=c,
                  center=(cx, y_top - ((stripe_y1 - stripe_y0) / stripes) / 2, 15.55)))

    # --- Bunk beds matching arena.rs AABBs:
    #  (-28, 0, 8) size (10, 1.5, 2) -> long axis X
    #  (-18, 0, 12) size (4, 1.5, 2) -> long axis X
    _bunk_long(z, center=(-28.0, 8.0), length=10.0, orientation="x")
    _bunk_short(z, center=(-18.0, 12.0), length=4.0, orientation="x")
    # extra bunks for flavor (not collide-able, must be inside the matches)
    # we'll stay conservative and NOT add extras that collide differently

    # --- Night-lamp cylinders hanging from ceiling
    for (lx, lz) in [(-30.0, 3.0), (-20.0, 3.0), (-30.0, 13.0), (-20.0, 13.0)]:
        # cord
        z.add(box(size=(0.05, 2.0, 0.05), color=P.RIVET,
                  center=(lx, 6.0, lz)))
        # shade (cone-ish — use cylinder)
        z.add(cylinder(radius=0.35, height=0.5, segments=10, color=P.COIL_MAGENTA,
                       center=(lx, 4.8, lz)))
        # glowing bulb
        z.add(icosphere(radius=0.18, subdivisions=0, color=P.GLOW_CORE,
                        center=(lx, 4.5, lz)))

    # --- Pipes along ceiling
    z.solid_pipe_x(x0 + 1, x1 - 1, 7.3, 15.5, radius=0.2, color=P.PAJAMA_STRIPE)

    # --- SLEEPING sign on south wall
    _banner_compact(z, "SLEEP", cx=-24.0, cy=7.3, cz=15.6, color=P.PAJAMA_STRIPE)

    # --- Small nightstand between bunks
    z.add(box(size=(1.2, 0.9, 0.9), color=P.BUNK_WOOD,
              center=(-32.0, 0.45, 4.0)))
    z.collide((-32.0, 0.45, 4.0), (1.2, 0.9, 0.9))
    # Alarm clock (tiny box)
    z.add(box(size=(0.4, 0.25, 0.2), color=P.PAJAMA_NAVY,
              center=(-32.0, 1.02, 4.0)))
    z.add(box(size=(0.35, 0.2, 0.05), color=P.GOOP_GREEN,
              center=(-32.0, 1.02, 3.9)))

    # --- Gameplay
    hang_art(z, art_minion_portrait(), -26.0, 2.6, 15.55, "-z")
    hang_art(z, art_banana(), -35.55, 2.4, 6.0, "+x")
    z.spawn(-28.0, 0.0, 12.0)
    z.spawn(-16.5, 0.0, 0.0)  # in the sports/sleeping doorway
    z.jump_pad(-32.0, 0.0, 13.0, target=(-26.6, 8.3, 13.0), clearance=2.5)
    z.powerup("shotgun", -22.0, 0.0, 4.0)
    z.powerup("health", -25.0, 0.0, 10.0)
    z.powerup("health", -25.0, 0.0, 10.0)

    return z


def _bunk_long(z: ZoneBuild, center: tuple, length: float, orientation: str):
    """Two-tier bunk. Matches arena.rs AABB at (-28, 0, 8) size (10, 1.5, 2)."""
    cx, cz = center
    # The collision AABB is 10 long, 2 wide, 1.5 tall. Build the physical bunk
    # within that footprint.
    if orientation == "z":
        long_sz = (2.0, None, length)  # x=2 deep, z=length long
    else:
        long_sz = (length, None, 2.0)

    # Bottom frame
    frame_color = P.BUNK_WOOD
    z.add(box(size=(long_sz[0], 0.2, long_sz[2]), color=frame_color,
              center=(cx, 0.1, cz)))
    # Mattress 1 (bottom)
    z.add(box(size=(long_sz[0] * 0.9, 0.3, long_sz[2] * 0.95),
              color=P.MATTRESS_CREAM,
              center=(cx, 0.35, cz)))
    # Pillows (one every 2m along length, pajama striped)
    n_pillows = int(length / 2.5)
    for i in range(n_pillows):
        if orientation == "z":
            px = cx - 0.3
            pz = cz - length / 2 + 0.9 + i * 2.5
        else:
            px = cx - length / 2 + 0.9 + i * 2.5
            pz = cz - 0.3
        z.add(box(size=(0.9, 0.18, 0.6), color=P.PILLOW,
                  center=(px, 0.6, pz)))
        # stripe on blanket
        stripe_c = P.PAJAMA_STRIPE if i % 2 == 0 else P.PAJAMA_NAVY
        if orientation == "z":
            z.add(box(size=(long_sz[0] * 0.92, 0.04, 1.2), color=stripe_c,
                      center=(cx, 0.52, pz + 0.8)))
        else:
            z.add(box(size=(1.2, 0.04, long_sz[2] * 0.92), color=stripe_c,
                      center=(px + 0.8, 0.52, cz)))

    # Upper deck at y ~= 1.2 (within 1.5 AABB tall)
    # 4 posts
    post_color = frame_color
    corners = [
        (cx - long_sz[0] / 2 + 0.15, cz - long_sz[2] / 2 + 0.15),
        (cx + long_sz[0] / 2 - 0.15, cz - long_sz[2] / 2 + 0.15),
        (cx - long_sz[0] / 2 + 0.15, cz + long_sz[2] / 2 - 0.15),
        (cx + long_sz[0] / 2 - 0.15, cz + long_sz[2] / 2 - 0.15),
    ]
    for (px, pz) in corners:
        z.add(box(size=(0.2, 1.4, 0.2), color=post_color,
                  center=(px, 0.7, pz)))
    # Top frame (thin; within the AABB top)
    z.add(box(size=(long_sz[0], 0.15, long_sz[2]), color=frame_color,
              center=(cx, 1.3, cz)))
    # Top mattress + striped blanket
    z.add(box(size=(long_sz[0] * 0.9, 0.25, long_sz[2] * 0.95),
              color=P.MATTRESS_CREAM,
              center=(cx, 1.5, cz)))
    # Blanket stripes lengthwise
    if orientation == "z":
        for i in range(6):
            c = P.PAJAMA_NAVY if i % 2 == 0 else P.PAJAMA_STRIPE
            z.add(box(size=(0.28, 0.28, long_sz[2] * 0.9),
                      color=c,
                      center=(cx - long_sz[0] * 0.4 + i * 0.33, 1.55, cz)))
    else:
        for i in range(6):
            c = P.PAJAMA_NAVY if i % 2 == 0 else P.PAJAMA_STRIPE
            z.add(box(size=(long_sz[0] * 0.9, 0.28, 0.28),
                      color=c,
                      center=(cx, 1.55, cz - long_sz[2] * 0.4 + i * 0.33)))

    # Solid for the full visual height (the old half-height AABB let
    # players and rails pass through the upper mattress).
    if orientation == "x":
        z.collide((cx, 0.875, cz), (length, 1.75, 2.0))
    else:
        z.collide((cx, 0.875, cz), (2.0, 1.75, length))


def _bunk_short(z: ZoneBuild, center: tuple, length: float, orientation: str):
    """Mirror of _bunk_long but shorter. At (-18, 0, 12) size (4, 1.5, 2)."""
    cx, cz = center
    sx, sz = (length, 2.0) if orientation == "x" else (2.0, length)
    # bottom frame
    z.add(box(size=(sx, 0.2, sz), color=P.BUNK_WOOD, center=(cx, 0.1, cz)))
    # mattress
    z.add(box(size=(sx * 0.9, 0.3, sz * 0.9), color=P.MATTRESS_CREAM,
              center=(cx, 0.35, cz)))
    # pillow
    z.add(box(size=(0.9, 0.2, 0.6), color=P.PILLOW,
              center=(cx - sx / 2 + 0.7, 0.58, cz)))
    # blanket stripes
    for i in range(5):
        c = P.PAJAMA_STRIPE if i % 2 == 0 else P.PAJAMA_NAVY
        z.add(box(size=(sx * 0.75, 0.05, 0.3),
                  color=c,
                  center=(cx + 0.3, 0.55, cz - 0.7 + i * 0.3)))
    # posts
    for dx in (-1, 1):
        for dz in (-1, 1):
            z.add(box(size=(0.18, 1.4, 0.18), color=P.BUNK_WOOD,
                      center=(cx + dx * (sx / 2 - 0.2), 0.7, cz + dz * (sz / 2 - 0.2))))
    # top mattress
    z.add(box(size=(sx, 0.15, sz), color=P.BUNK_WOOD, center=(cx, 1.3, cz)))
    z.add(box(size=(sx * 0.9, 0.25, sz * 0.9), color=P.MATTRESS_CREAM,
              center=(cx, 1.5, cz)))
    z.collide((cx, 0.875, cz), (sx, 1.75, sz))


def _banner_compact(z: ZoneBuild, text: str, cx: float, cy: float, cz: float,
                    color=P.GRU_YELLOW, letter_w=0.5, letter_h=0.8, thick=0.1):
    from zones.sports import _banner
    _banner(z, text, cx=cx, cy=cy, cz=cz, color=color,
            letter_w=letter_w, letter_h=letter_h, thick=thick)


def main():
    write_zone(build())


if __name__ == "__main__":
    main()
