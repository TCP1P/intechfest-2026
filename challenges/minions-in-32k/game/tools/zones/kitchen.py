"""Kitchen — appliances, counters, something bubbling.

Footprint: x in [12,36], z in [-16,0]. Zone center (24, 0, -8).
Matches counter AABBs: (20,0,-12) size (12,1,1) and (28,0,-4) size (1,1,4).

Hero props: cream cabinets with trim, a giant stock-pot with green goop,
a stove with flame, a fridge, hanging pans, chrome counters.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyasset import MeshBuilder, box, cylinder, icosphere, torus
from pyasset.builder import rotate_y, translate
from pyasset.write import build_glb, write_collision_glb

from zones import palette as P
from zones.common import (
    hang_art, art_egg,
    ZoneBuild, write_zone, wall_x, wall_z,
    pipe_strip, vert_pipe,
)


def build():
    z = ZoneBuild("kitchen", bounds=((12.0, 0.0, -16.0), (36.0, 8.0, 0.0)))
    x0, x1, z0, z1 = 12.0, 36.0, -16.0, 0.0
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2

    # --- Tile floor: pale diagonal check
    tile = 1.5
    for ix in range(int((x1 - x0) / tile)):
        for iz in range(int((z1 - z0) / tile)):
            c = P.COUNTER_GREY if (ix + iz) % 2 == 0 else (P.COUNTER_GREY[0] * 0.85,
                                                           P.COUNTER_GREY[1] * 0.85,
                                                           P.COUNTER_GREY[2] * 0.85)
            z.add(box(size=(tile * 0.96, 0.08, tile * 0.96),
                      color=c,
                      center=(x0 + (ix + 0.5) * tile, -0.04,
                              z0 + (iz + 0.5) * tile)))

    # --- Walls (x=12 internal owned by machine_room.py; kitchen owns the
    # z=0 divider for both rooms, doorway open at x [18, 21])
    z.solid_wall_z(z0, z1, 36.0, thick=0.5, color=P.CAB_CREAM)
    z.solid_wall_x(x0, x1, -16.0, thick=0.5, color=P.CAB_CREAM)
    z.solid_wall_x(12.0, 18.0, 0.0, thick=0.4, color=P.WALL_BASE)
    z.solid_wall_x(21.0, 36.0, 0.0, thick=0.4, color=P.WALL_BASE)

    # --- Ceiling
    # v3: no full ceiling — the hall layer (hall.py) owns decks + light wells

    # --- North counter run — matches (20, 0, -12) size (12, 1, 1)
    # Counter base (cabinets) along z=-12, x in [14, 26]
    z.add(box(size=(12.0, 1.0, 1.0), color=P.CAB_CREAM,
              center=(20.0, 0.5, -12.0)))
    z.collide((20.0, 0.55, -12.0), (12.2, 1.1, 1.1))
    # Chrome top strip
    z.add(box(size=(12.2, 0.08, 1.1), color=P.CHROME,
              center=(20.0, 1.04, -12.0)))
    # Cabinet doors — segments with trim
    for i in range(6):
        dx = 14.0 + i * 2.0 + 1.0
        z.add(box(size=(1.8, 0.9, 0.05), color=P.CAB_TRIM,
                  center=(dx, 0.5, -11.47)))
        # Handle
        z.add(box(size=(0.12, 0.1, 0.06), color=P.CHROME,
                  center=(dx, 0.7, -11.42)))

    # Pot with green goop on the counter mid
    z.add(cylinder(radius=0.55, height=0.55, segments=14, color=P.POT_BLACK,
                   center=(18.5, 1.3, -12.0), name="pot"))
    z.add(cylinder(radius=0.52, height=0.05, segments=14, color=P.GOOP_GREEN,
                   center=(18.5, 1.6, -12.0)))
    # Bubbles
    for (dx, dy, dz, r) in [(-0.1, 0.1, 0.0, 0.12), (0.2, 0.22, 0.05, 0.08),
                            (0.0, 0.3, -0.15, 0.1), (0.15, 0.45, 0.2, 0.06)]:
        z.add(icosphere(radius=r, subdivisions=0, color=P.GOOP_GREEN,
                        center=(18.5 + dx, 1.65 + dy, -12.0 + dz)))

    # Stove: hob to the right of pot
    z.add(box(size=(2.0, 0.1, 0.9), color=P.POT_BLACK,
              center=(22.0, 1.08, -12.0)))
    for (dx, dz) in [(-0.5, -0.2), (0.5, -0.2), (-0.5, 0.2), (0.5, 0.2)]:
        z.add(cylinder(radius=0.18, height=0.04, segments=10,
                       color=P.RIVET, center=(22.0 + dx, 1.13, -12.0 + dz)))
    # A flame visual on one burner
    z.add(icosphere(radius=0.25, subdivisions=1, color=P.FLAME_RED,
                    center=(21.5, 1.4, -12.2)))
    z.add(icosphere(radius=0.18, subdivisions=0, color=P.GRU_YELLOW,
                    center=(21.5, 1.5, -12.2)))

    # Second small pot
    z.add(cylinder(radius=0.35, height=0.35, segments=12, color=P.POT_BLACK,
                   center=(24.0, 1.25, -12.0)))

    # Splashback tiles (above counter)
    for i in range(12):
        for j in range(3):
            c = P.CHROME if (i + j) % 3 == 0 else P.CAB_CREAM
            z.add(box(size=(0.9, 0.5, 0.06), color=c,
                      center=(14.5 + i, 1.5 + j * 0.55, -12.5)))

    # --- East counter — matches (28, 0, -4) size (1, 1, 4)
    z.add(box(size=(1.0, 1.0, 4.0), color=P.CAB_CREAM,
              center=(28.0, 0.5, -4.0)))
    z.collide((28.0, 0.55, -4.0), (1.1, 1.1, 4.2))
    z.add(box(size=(1.1, 0.08, 4.2), color=P.CHROME,
              center=(28.0, 1.04, -4.0)))
    # Sink
    z.add(box(size=(0.8, 0.15, 1.4), color=P.POT_BLACK,
              center=(28.0, 1.05, -4.5)))
    # Faucet
    z.add(box(size=(0.12, 0.6, 0.12), color=P.CHROME,
              center=(28.0, 1.4, -5.2)))
    z.add(box(size=(0.6, 0.1, 0.12), color=P.CHROME,
              center=(27.75, 1.7, -5.0)))
    # Drying rack with plates
    for i in range(4):
        z.add(box(size=(0.5, 0.3, 0.04), color=P.CAB_CREAM,
                  center=(28.0, 1.35, -2.8 + i * 0.25)))

    # --- Fridge in corner
    z.add(box(size=(1.8, 3.2, 1.6), color=P.CAB_CREAM,
              center=(14.5, 1.6, -14.0)))
    z.collide((14.5, 1.6, -14.0), (1.8, 3.2, 1.6))
    # Door division line
    z.add(box(size=(1.85, 0.05, 1.7), color=P.CAB_TRIM,
              center=(14.5, 2.0, -14.0)))
    # Handles
    for dy in (0.8, 2.4):
        z.add(box(size=(0.08, 0.4, 0.1), color=P.CHROME,
                  center=(13.55, dy, -13.2)))

    # --- Hanging pan rack from ceiling (center of room)
    rack_y = 5.0
    z.add(box(size=(4.0, 0.1, 0.1), color=P.RIVET,
              center=(20.0, rack_y, -8.0)))
    for i, c in enumerate([P.CHROME, P.POT_BLACK, P.CHROME, P.POT_BLACK]):
        hx = 18.5 + i * 1.0
        # chain
        z.add(box(size=(0.04, 0.8, 0.04), color=P.RIVET,
                  center=(hx, rack_y - 0.5, -8.0)))
        # pan (disc)
        z.add(cylinder(radius=0.3, height=0.08, segments=10, color=c,
                       center=(hx, rack_y - 1.0, -8.0)))

    # --- Cupboards along east wall (interior-facing)
    for i in range(4):
        z.add(box(size=(0.25, 1.4, 1.2), color=P.CAB_CREAM,
                  center=(35.6, 2.5, -13.5 + i * 1.5)))
        z.add(box(size=(0.05, 1.3, 1.1), color=P.CAB_TRIM,
                  center=(35.47, 2.5, -13.5 + i * 1.5)))
        z.add(box(size=(0.08, 0.1, 0.08), color=P.CHROME,
                  center=(35.4, 2.5, -13.5 + i * 1.5)))

    # --- "KITCHEN" sign on north wall
    from zones.sports import _banner
    _banner(z, "KITCHEN", cx=24.0, cy=7.3, cz=-15.6,
            color=P.GOOP_GREEN, letter_w=0.55, letter_h=0.85, thick=0.12)

    # --- Pipe across ceiling
    z.solid_pipe_x(x0 + 1, x1 - 1, 7.3, -6.0, radius=0.2, color=P.WATER_BLUE)
    z.solid_pipe_x(x0 + 1, x1 - 1, 7.3, -15.5, radius=0.2, color=P.GRU_ORANGE)

    # --- Trash bin
    # bin lives by the east wall — its old spot sat inside the launch
    # pad's rim tube
    z.add(cylinder(radius=0.4, height=0.9, segments=10, color=P.STEEL_DARK,
                   center=(34.5, 0.45, -10.0)))
    z.collide((34.5, 0.45, -10.0), (0.8, 0.9, 0.8))
    z.add(icosphere(radius=0.15, subdivisions=0, color=P.BUNK_WOOD,
                    center=(34.5, 1.0, -10.0)))  # banana peel reference

    # --- Gameplay
    hang_art(z, art_egg(), 26.0, 2.6, -15.55, "+z")
    z.spawn(28.0, 0.0, -12.0)
    z.powerup("haste", 24.0, 0.0, -8.0)
    # the flamethrower lives in the kitchen, obviously
    z.powerup("flamethrower", 19.0, 0.0, -8.0)
    z.jump_pad(33.0, 0.0, -13.0, target=(27.6, 8.3, -13.0), clearance=2.5)

    return z


def main():
    write_zone(build())


if __name__ == "__main__":
    main()
