"""Bathrooms — stalls, silly feel.

Footprint: x in [12,36], z in [0,16]. Zone center (24, 0, 8).
Matches bathroom-stall AABBs (three thin partitions at x=18/22/26, z=10,
size 0.3 x 1.5 x 4).

Hero props: three stalls, sinks + mirrors on the south wall, tile walls,
little round toilets, puddles.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyasset import MeshBuilder, box, cylinder, icosphere, torus
from pyasset.builder import translate, rotate_y
from pyasset.write import build_glb, write_collision_glb

from zones import palette as P
from zones.common import (
    hang_art, art_duck,
    ZoneBuild, write_zone, wall_x, wall_z,
    pipe_strip, vert_pipe,
)


def build():
    z = ZoneBuild("bathrooms", bounds=((12.0, 0.0, 0.0), (36.0, 8.0, 16.0)))
    x0, x1, z0, z1 = 12.0, 36.0, 0.0, 16.0
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2

    # --- Tile floor: teal checker
    tile = 1.2
    for ix in range(int((x1 - x0) / tile)):
        for iz in range(int((z1 - z0) / tile)):
            c = P.TILE_TEAL if (ix + iz) % 2 == 0 else P.TILE_LIGHT
            z.add(box(size=(tile * 0.95, 0.08, tile * 0.95),
                      color=c,
                      center=(x0 + (ix + 0.5) * tile, -0.04,
                              z0 + (iz + 0.5) * tile)))
    # Grout seams (thin dark strips)
    # (Skip — relying on gap between tiles is enough visually.)

    # --- Walls (x=12 internal owned by machine_room.py; the z=0 divider
    # is owned by kitchen.py — one wall, one owner)
    z.solid_wall_z(z0, z1, 36.0, thick=0.5, color=P.TILE_LIGHT)
    z.solid_wall_x(x0, x1, 16.0, thick=0.5, color=P.TILE_LIGHT)

    # --- Ceiling
    # v3: no full ceiling — the hall layer (hall.py) owns decks + light wells

    # --- Tile wall accent strip (horizontal band on interior walls)
    for (wx, wz_a, wz_b, thick, nrm) in [
        # east wall interior face at x=35.75
        (35.7, z0 + 0.5, z1 - 0.5, 0.05, "x"),
        # south wall interior face at z=15.75
        (cx, 15.7, 15.7, 0.05, "z"),
    ]:
        pass  # simplified — skip interior decor band

    # --- Three stalls at x=18,22,26, z center 10, size 0.3 x 1.5 x 4
    # Partition wall (tall thin panel)
    for x_part in (18.0, 22.0, 26.0):
        z.add(box(size=(0.3, 2.2, 4.0), color=P.STALL_PINK,
                  center=(x_part, 1.1, 10.0)))
        z.collide((x_part, 1.1, 10.0), (0.3, 2.2, 4.0))
        # vertical pipe trim on the partition edge
        z.add(box(size=(0.35, 0.15, 4.1), color=P.PORCELAIN,
                  center=(x_part, 2.2, 10.0)))

    # --- Back wall of stalls (along z=12)
    z.add(box(size=(26.0 - 18.0 + 0.5, 2.2, 0.15), color=P.STALL_PINK,
              center=(22.0, 1.1, 12.0)))
    z.collide((22.0, 1.1, 12.0), (8.5, 2.2, 0.15))

    # --- Toilets in each stall (simplified: base cylinder + tank box)
    for x_stall in (20.0, 24.0, 28.0):
        # base bowl
        z.add(cylinder(radius=0.4, height=0.5, segments=12,
                       color=P.PORCELAIN, center=(x_stall, 0.25, 10.5)))
        # seat
        z.add(cylinder(radius=0.45, height=0.08, segments=12,
                       color=P.PORCELAIN, center=(x_stall, 0.55, 10.5)))
        z.collide((x_stall, 0.45, 10.7), (0.9, 0.9, 1.5))
        # tank
        z.add(box(size=(0.6, 0.8, 0.3), color=P.PORCELAIN,
                  center=(x_stall, 0.9, 11.3)))
        # flush handle
        z.add(box(size=(0.08, 0.08, 0.12), color=P.CHROME,
                  center=(x_stall + 0.32, 1.2, 11.2)))

    # --- Sinks + mirrors on south wall (z~15)
    # three sinks — the fourth sat under the launch pad's rim tube
    for i in range(3):
        sx = 14.0 + i * 5.5
        # sink basin
        z.add(box(size=(1.2, 0.2, 0.8), color=P.PORCELAIN,
                  center=(sx, 1.0, 14.5)))
        z.add(box(size=(1.1, 0.05, 0.7), color=P.WATER_BLUE,
                  center=(sx, 1.08, 14.5)))
        z.collide((sx, 0.6, 14.6), (1.2, 1.2, 1.0))
        # pedestal
        z.add(box(size=(0.3, 1.0, 0.4), color=P.PORCELAIN,
                  center=(sx, 0.5, 14.7)))
        # faucet
        z.add(box(size=(0.08, 0.3, 0.08), color=P.CHROME,
                  center=(sx, 1.25, 14.85)))
        z.add(box(size=(0.08, 0.08, 0.3), color=P.CHROME,
                  center=(sx, 1.4, 14.7)))
        # mirror
        z.add(box(size=(1.0, 1.2, 0.08), color=P.WATER_BLUE,
                  center=(sx, 2.5, 15.55)))
        z.add(box(size=(1.1, 1.3, 0.05), color=P.CHROME,
                  center=(sx, 2.5, 15.6)))

    # --- Shower head on east wall (x=35.5)
    for zi in (2.5, 6.5):
        z.add(box(size=(0.1, 0.08, 0.1), color=P.CHROME,
                  center=(35.4, 2.8, zi)))
        z.add(cylinder(radius=0.2, height=0.1, segments=10, color=P.CHROME,
                       center=(35.3, 2.6, zi)))
        # water stream
        for k in range(6):
            z.add(box(size=(0.03, 0.8, 0.03), color=P.WATER_BLUE,
                      center=(35.0, 2.1 - k * 0.1, zi + (k - 3) * 0.05)))

    # --- Puddle on the floor somewhere (flat disc)
    z.add(cylinder(radius=0.7, height=0.04, segments=14, color=P.WATER_BLUE,
                   center=(15.5, 0.02, 3.5)))

    # --- Tile wall mural: horizontal accent stripe along interior east wall
    z.add(box(size=(0.05, 0.5, 15.0),
              color=P.WATER_BLUE,
              center=(35.7, 2.5, 8.0)))
    # --- Circular mirror over sinks (big one)
    z.add(torus(radius=1.2, tube_radius=0.15, segments=18, sides=6,
                color=P.CHROME, center=(30.0, 4.5, 15.5)))

    # --- Pipe network across ceiling
    z.solid_pipe_x(x0 + 1, x1 - 1, 7.3, 4.0, radius=0.2, color=P.WATER_BLUE)
    z.solid_pipe_x(x0 + 1, x1 - 1, 7.3, 15.5, radius=0.2, color=P.WATER_BLUE)

    # --- Sign
    from zones.sports import _banner
    _banner(z, "WC", cx=24.0, cy=7.3, cz=15.6, color=P.STALL_PINK,
            letter_w=1.0, letter_h=1.4, thick=0.14)

    # --- Light strip in ceiling
    for i in range(3):
        z.add(box(size=(4.0, 0.1, 0.3), color=P.GLOW_CORE,
                  center=(16.0 + i * 6.0, 7.8, 7.5)))

    # --- Gameplay
    hang_art(z, art_duck(), 26.0, 2.4, 15.55, "-z")
    z.spawn(28.0, 0.0, 12.0)
    z.spawn(19.5, 0.0, 0.0)  # in the kitchen/bathrooms doorway
    z.powerup("health", 30.0, 0.0, 2.0)
    z.powerup("armor", 24.0, 0.0, 5.0)
    z.jump_pad(33.0, 0.0, 13.0, target=(27.6, 8.3, 13.0), clearance=2.5)

    return z


def main():
    write_zone(build())


if __name__ == "__main__":
    main()
