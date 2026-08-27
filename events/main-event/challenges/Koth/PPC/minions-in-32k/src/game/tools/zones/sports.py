"""Sports zone — minion sports chaos.

Footprint: x in [-36,-12], z in [-16,0]. Zone center (-24, 0, -8).
Hero props: soccer field stripes, a tiny goal on each short side, pedestals
with balls, a basketball hoop against the north wall, a red ribbon-boundary.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyasset import MeshBuilder, box, cylinder, icosphere, torus
from pyasset.builder import rotate_y, compose, translate
from pyasset.write import build_glb, write_collision_glb

from zones import palette as P
from zones.common import (
    hang_art, art_trophy,
    ZoneBuild, write_zone, floor_tiles, wall_x, wall_z,
    pipe_strip, vert_pipe,
)


def build():
    z = ZoneBuild("sports", bounds=((-36.0, 0.0, -16.0), (-12.0, 8.0, 0.0)))

    # --- Footprint bounds
    x0, x1, z0, z1 = -36.0, -12.0, -16.0, 0.0
    cx = (x0 + x1) / 2
    cz = (z0 + z1) / 2

    # --- Turf floor (solid green with white center line + circle feel)
    # Large green pad
    z.add(box(size=(x1 - x0, 0.06, z1 - z0), color=P.TURF_GREEN,
              center=(cx, -0.03, cz), name="turf"))
    # Center circle (three concentric rings for readability at 160x100)
    for r, w in [(3.0, 0.15)]:
        # segmented "ring" as boxes
        segs = 20
        for i in range(segs):
            a0 = (i / segs) * math.tau
            a1 = ((i + 1) / segs) * math.tau
            x_a = cx + math.cos((a0 + a1) / 2) * r
            z_a = cz + math.sin((a0 + a1) / 2) * r
            seg_len = r * (math.tau / segs)
            b = box(size=(seg_len * 1.05, 0.02, w), color=P.TURF_LINE,
                    center=(x_a, 0.005, z_a), name="circle")
            # rotate
            from pyasset.builder import rotate_y
            import numpy as np
            theta = (a0 + a1) / 2 + math.pi / 2
            M = translate(x_a, 0.005, z_a) @ rotate_y(theta) @ translate(-x_a, -0.005, -z_a)
            z.mesh.extend(b, transform=M)
    # Midline
    z.add(box(size=(0.2, 0.02, z1 - z0 - 1), color=P.TURF_LINE,
              center=(cx, 0.01, cz), name="midline"))

    # --- Walls (visual + collision emitted together)
    # (the x=-12 internal wall is owned by machine_room.py)
    z.solid_wall_z(z0, z1, -36.0, thick=0.5, color=P.WALL_BASE)
    z.solid_wall_x(x0, x1, -16.0, thick=0.5, color=P.WALL_BASE)
    # Divider at z=0: sports owns x [-36,-18]; the doorway is [-18,-15]
    # and sleeping owns [-15,-12]. The old scripts painted the doorway
    # closed (visual wall over a passable gap).
    z.solid_wall_x(x0, -18.0, 0.0, thick=0.4, color=P.WALL_BASE)

    # --- Ceiling (one slab, darkened)
    # v3: no full ceiling — the hall layer (hall.py) owns decks + light wells

    # --- Industrial pipes across ceiling for flavor
    z.solid_pipe_x(x0 + 1, x1 - 1, 7.3, -15.5, radius=0.2, color=P.GRU_ORANGE)
    z.solid_pipe_x(x0 + 1, x1 - 1, 7.3, -4, radius=0.2, color=P.COIL_CYAN)

    # --- Basketball-esque hoop against north wall at z = -15.5
    # Backboard
    z.add(box(size=(2.0, 1.4, 0.15), color=P.GOAL_WHITE,
              center=(-24.0, 4.5, -15.5), name="backboard"))
    # Red square target
    z.add(box(size=(0.8, 0.5, 0.17), color=P.BALL_RED,
              center=(-24.0, 4.5, -15.42), name="hoop_target"))
    # Rim (use a torus)
    from pyasset import torus as torus_prim
    rim = torus_prim(radius=0.45, tube_radius=0.06, segments=18, sides=8,
                     color=P.GRU_ORANGE)
    z.mesh.extend(rim, transform=translate(-24.0, 3.9, -15.0))
    # Net (a simple white fringe)
    for i in range(10):
        a = (i / 10) * math.tau
        z.add(box(size=(0.04, 0.45, 0.04), color=P.GOAL_WHITE,
                  center=(-24.0 + math.cos(a) * 0.4, 3.65, -15.0 + math.sin(a) * 0.4)))

    # --- Goal on west wall (soccer)
    # Two posts + crossbar + back net (simplified)
    for zp in [-11.5, -5.5]:
        z.add(box(size=(0.2, 2.5, 0.2), color=P.GOAL_WHITE,
                  center=(-34.5, 1.25, zp), name="goal_post"))
    z.add(box(size=(0.2, 0.2, 6.2), color=P.GOAL_WHITE,
              center=(-34.5, 2.5, -8.5), name="goal_crossbar"))
    # Back support
    z.add(box(size=(0.15, 2.2, 0.15), color=P.GOAL_WHITE,
              center=(-35.7, 1.1, -8.5), name="goal_back"))
    # Net (mesh-y pattern — strips)
    for i in range(8):
        zz = -11 + i * 0.8
        z.add(box(size=(1.4, 0.03, 0.04), color=(0.9, 0.9, 0.9),
                  center=(-35.15, 2.0, zz)))
    for i in range(5):
        y = 0.4 + i * 0.45
        z.add(box(size=(1.4, 0.03, 0.04), color=(0.9, 0.9, 0.9),
                  center=(-35.15, y, -8.5)))
    # Goal mouth stays walkable; the posts and the back net are solid so
    # you can't stand inside the west wall.
    for zp in [-11.5, -5.5]:
        z.collide((-34.5, 1.25, zp), (0.25, 2.5, 0.25))
    z.collide((-35.5, 1.25, -8.5), (0.5, 2.5, 6.2))

    # --- Ball pedestals (match arena.rs: (-28,-12), (-22,-10), (-30,-6))
    for (px, pz) in [(-28.0, -12.0), (-22.0, -10.0), (-30.0, -6.0)]:
        # low cylinder pedestal
        z.add(cylinder(radius=0.7, height=0.8, segments=14,
                       color=P.WALL_BASE, center=(px, 0.4, pz),
                       name="pedestal"))
        z.collide((px, 0.4, pz), (1.4, 0.8, 1.4))
    # Balls on top
    z.add(icosphere(radius=0.5, subdivisions=1, color=P.BALL_WHITE,
                    center=(-28.0, 1.3, -12.0)))
    # black soccer patches (pentagons) — approximate with small spheres
    for a in range(6):
        ang = a * math.tau / 6
        z.add(icosphere(radius=0.14, subdivisions=0, color=P.BALL_BLACK,
                        center=(-28.0 + math.cos(ang) * 0.42, 1.5,
                                -12.0 + math.sin(ang) * 0.42)))
    z.add(icosphere(radius=0.5, subdivisions=1, color=P.BALL_RED,
                    center=(-22.0, 1.3, -10.0)))
    z.add(icosphere(radius=0.5, subdivisions=1, color=P.BALL_YELLOW,
                    center=(-30.0, 1.3, -6.0)))

    # --- Bleachers / low stands on south wall — climbable steps
    for i in range(3):
        y = 0.25 + i * 0.35
        width = 6.0 - i * 1.0
        z.add(box(size=(width, 0.3, 0.8), color=P.WALL_BASE,
                  center=(-18.0, y, -1.2 - i * 0.9)))
        z.collide((-18.0, y, -1.2 - i * 0.9), (width, 0.3, 0.8))

    # --- Score board high on north wall (wall inner face at z=-15.75; center
    # must sit inside the room at z > -15.65 or the wall triangles and
    # the board's back face fight).
    z.add(box(size=(4.0, 1.5, 0.2), color=P.STEEL_DARK,
              center=(-18.0, 6.0, -15.55)))
    z.add(box(size=(3.8, 1.3, 0.05), color=P.COIL_MAGENTA,
              center=(-18.0, 6.0, -15.44)))

    # --- Wall padding strips (yellow/black hazard) along internal wall
    for i in range(8):
        xp = -12.5
        zp = -14.5 + i * 1.5
        c = P.BALL_YELLOW if i % 2 == 0 else P.BALL_BLACK
        z.add(box(size=(0.2, 0.5, 1.0), color=c,
                  center=(xp + 0.1, 0.25, zp)))

    # --- SPORTS sign high on north wall
    # Use blocky letters S P O R T S as little boxes
    _banner(z, "SPORTS", cx=-24.0, cy=7.2, cz=-15.55, color=P.GRU_YELLOW)

    # --- Gameplay
    hang_art(z, art_trophy(), -26.0, 2.6, -15.55, "+z")
    z.spawn(-26.0, 0.0, -14.0)
    z.powerup("banana", -24.0, 0.0, -8.0)
    z.powerup("shotgun", -27.0, 0.0, -6.0)
    z.powerup("health", -30.0, 0.0, -2.0)
    z.jump_pad(-32.0, 0.0, -13.0, target=(-26.6, 8.3, -13.0), clearance=2.5)

    return z


def _banner(z: ZoneBuild, text: str, cx: float, cy: float, cz: float,
            color=P.GRU_YELLOW, letter_w=0.55, letter_h=0.9, thick=0.15):
    """Draw blocky uppercase letters using 5x7 bitmap glyphs."""
    glyphs = {
        "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
        "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
        "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
        "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
        "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
        "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
        "G": ["01111", "10000", "10000", "10111", "10001", "10001", "01111"],
        "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
        "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
        "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
        "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
        "M": ["10001", "11011", "10101", "10001", "10001", "10001", "10001"],
        "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
        "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
        "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
        "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
        "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
        "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
        "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
        "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
        " ": ["00000"] * 7,
    }
    text = text.upper()
    total_w = len(text) * 6 * (letter_w / 5)
    px = cx - total_w / 2
    for ch in text:
        glyph = glyphs.get(ch, glyphs[" "])
        cell_w = letter_w / 5
        cell_h = letter_h / 7
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "1":
                    bx = px + col * cell_w + cell_w / 2
                    by = cy + (6 - row) * cell_h - letter_h / 2 + cell_h / 2
                    z.add(box(size=(cell_w * 1.05, cell_h * 1.05, thick),
                              color=color,
                              center=(bx, by, cz)))
        px += 6 * cell_w


def main():
    write_zone(build())


if __name__ == "__main__":
    main()
