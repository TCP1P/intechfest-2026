"""The upper hall — v3 "raise the roof" layer over the unchanged rooms.

Everything above y=8: upper shell walls with the GRU sign, rooftop decks
with light wells and launch hatches, bridges, the ring catwalk, L2
gantries, the goo tube with Gru's rocket, and the elevator guide rails.
Coordinates mirror `Arena::gru_lab_v3()` exactly — if a slab moves there,
it moves here.

The moving elevator platform is NOT in this glb: the client renders it
from arena data, animating from the tick. Jump-pad pucks ARE baked (via
ZoneBuild.jump_pad, which keeps trigger and visual welded together).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyasset import MeshBuilder, box, cylinder, icosphere, cone, torus
from pyasset.builder import translate, rotate_x, rotate_y, rotate_z, compose
from pyasset.write import build_glb, write_collision_glb

from zones import palette as P
from zones.common import ZoneBuild, write_zone, wall_x, wall_z

HALL_TOP = 20.0
DECK_TOP = 8.3

# Rail rule: rooftop (light-well) rails are SOLID — they collide, with
# hazard-striped drop notches as the intentional ways down. Catwalks,
# bridges, and gantries are open: just a low hazard lip, drop anywhere.


def _solid_rail_z(z: ZoneBuild, z0, z1, x, y0):
    z.add(box(size=(0.1, 0.12, z1 - z0), color=P.WALL_TRIM,
              center=(x, y0 + 0.95, (z0 + z1) / 2)))
    n = max(2, int((z1 - z0) / 2.5) + 1)
    for i in range(n):
        zp = z0 + (z1 - z0) * i / (n - 1)
        z.add(box(size=(0.08, 0.9, 0.08), color=P.RIVET,
                  center=(x, y0 + 0.45, zp)))
    z.collide((x, y0 + 0.5, (z0 + z1) / 2), (0.15, 1.0, z1 - z0))


def _hazard_lip_x(z: ZoneBuild, x0, x1, zp, top_y):
    n = max(1, int((x1 - x0) / 1.0))
    for i in range(n):
        c = P.PLATFORM_YELLOW if i % 2 == 0 else P.WARN_STRIPE
        z.add(box(size=((x1 - x0) / n, 0.1, 0.14), color=c,
                  center=(x0 + (i + 0.5) * (x1 - x0) / n, top_y + 0.05, zp)))


def _hazard_lip_z(z: ZoneBuild, z0, z1, x, top_y):
    n = max(1, int((z1 - z0) / 1.0))
    for i in range(n):
        c = P.PLATFORM_YELLOW if i % 2 == 0 else P.WARN_STRIPE
        z.add(box(size=(0.14, 0.1, (z1 - z0) / n), color=c,
                  center=(x, top_y + 0.05, z0 + (i + 0.5) * (z1 - z0) / n)))


def _slab(z: ZoneBuild, x0, y0, z0, x1, y1, z1, color=P.STEEL_LIGHT,
          collide=True):
    cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
    sx, sy, sz = x1 - x0, y1 - y0, z1 - z0
    z.add(box(size=(sx, sy, sz), color=color, center=(cx, cy, cz)))
    if collide:
        z.collide((cx, cy, cz), (sx, sy, sz))


def _deck_checker(z: ZoneBuild, x0, x1, z0, z1, accent, holes, tile=2.0):
    nx = int(round((x1 - x0) / tile))
    nz = int(round((z1 - z0) / tile))
    for ix in range(nx):
        for iz in range(nz):
            cx = x0 + (ix + 0.5) * tile
            cz = z0 + (iz + 0.5) * tile
            if any(hx0 < cx < hx1 and hz0 < cz < hz1
                   for hx0, hx1, hz0, hz1 in holes):
                continue
            c = P.FLOOR_BASE if (ix + iz) % 2 == 0 else accent
            z.add(box(size=(tile * 0.96, 0.08, tile * 0.96), color=c,
                      center=(cx, DECK_TOP + 0.04, cz)))


def _hatch_rim(z: ZoneBuild, x, zp):
    for dx, dz, sx, sz in [(0, -2.4, 6.3, 0.2), (0, 2.4, 6.3, 0.2),
                           (-3.05, 0, 0.2, 4.6), (3.05, 0, 0.2, 4.6)]:
        z.add(box(size=(sx, 0.18, sz), color=P.WALL_TRIM,
                  center=(x + dx, DECK_TOP + 0.09, zp + dz)))


def _gru_sign(z: ZoneBuild):
    font = {"G": ["111", "100", "101", "101", "111"],
            "R": ["111", "101", "111", "110", "101"],
            "U": ["101", "101", "101", "101", "111"]}
    cell = 1.6
    lx = -9.1
    for ch in "GRU":
        for ri, row in enumerate(font[ch]):
            for ci, v in enumerate(row):
                if v == "1":
                    z.add(box(size=(cell * 0.94, cell * 0.94, 0.3),
                              color=P.GRU_YELLOW,
                              center=(lx + (ci + 0.5) * cell,
                                      18.0 - (ri + 0.5) * cell, -15.55)))
        lx += 3 * cell + 1.9


def _jumbotron(z: ZoneBuild):
    """The moon-heist pitch deck on the south wall — a two-storey screen
    visible from the machine-room floor AND the hall decks, cycling four
    slides (named nodes slide_0..3; the client flips their visibility).
    """
    sy = 10.6          # screen centre height: spans y ~4.6 .. 16.6
    sw, sh = 16.0, 12.0
    z.add(box(size=(sw + 0.7, sh + 0.7, 0.35), color=P.STEEL_DARK,
              center=(0, sy, 15.66)))
    z.add(box(size=(sw, sh, 0.2), color=(0.03, 0.045, 0.11),
              center=(0, sy, 15.48)))
    for ex in (-(sw / 2 + 0.18), sw / 2 + 0.18):
        z.add(box(size=(0.16, sh + 0.5, 0.24), color=P.COIL_CYAN,
                  center=(ex, sy, 15.55)))
    for mx in (-6.0, 0.0, 6.0):
        z.add(box(size=(0.35, 0.35, 0.5), color=P.STEEL_LIGHT,
                  center=(mx, sy + sh / 2 + 0.45, 15.75)))
    z.add(box(size=(sw, 0.5, 0.1), color=P.GRU_YELLOW,
              center=(0, sy + sh / 2 - 0.55, 15.36)))
    z.collide((0.0, sy, 15.75), (sw + 0.7, sh + 0.7, 0.6))

    F = 15.30   # content plane
    F2 = 15.24  # detail layer

    # Per-slide depth stagger: every primitive gets a unique depth so no two
    # coplanar overlaps z-fight. The stagger is tiny relative to the F-0.04
    # layering offsets, so intended ordering is preserved; same-base overlaps
    # get painter's-order (later-drawn slightly in front).
    _layer = [0.0]

    def dz(base=F):
        _layer[0] += 0.0013
        return base - _layer[0]

    def reset_layers():
        _layer[0] = 0.0

    def disc(mb, x, y, r, color, depth=F):
        mb.extend(cylinder(radius=r, height=0.06, segments=16, color=color),
                  transform=compose(translate(x, y, dz(depth)),
                                    rotate_x(math.pi / 2)))

    def minion_face(mb, x, y, r, mood="happy"):
        disc(mb, x, y, r, P.MINION_YELLOW)
        disc(mb, x, y + r * 0.22, r * 0.46, (0.75, 0.76, 0.78), depth=F - 0.04)
        disc(mb, x, y + r * 0.22, r * 0.28, (0.96, 0.96, 0.92), depth=F - 0.07)
        disc(mb, x, y + r * 0.22, r * 0.12, (0.35, 0.22, 0.10), depth=F - 0.09)
        if mood == "happy":
            mb.extend(box(size=(r * 0.8, r * 0.16, 0.04), color=(0.30, 0.18, 0.10),
                          center=(x, y - r * 0.45, dz(F - 0.05))))
        else:  # sad: frown + tear
            mb.extend(box(size=(r * 0.7, r * 0.14, 0.04), color=(0.30, 0.18, 0.10)),
                      transform=compose(translate(x, y - r * 0.5, dz(F - 0.05)),
                                        rotate_z(0.35)))
            mb.extend(box(size=(r * 0.16, r * 0.3, 0.04), color=(0.45, 0.75, 0.95),
                          center=(x + r * 0.5, y - r * 0.15, dz(F - 0.08))))

    # ---- slide 0: THE PLAN (earth -> transfer orbit -> very big moon)
    s0 = MeshBuilder(name="slide_0")
    reset_layers()
    disc(s0, -5.4, sy - 3.2, 1.9, (0.18, 0.45, 0.85))
    disc(s0, -5.9, sy - 2.6, 0.55, (0.25, 0.65, 0.30), depth=F2)
    disc(s0, -4.8, sy - 3.9, 0.42, (0.25, 0.65, 0.30), depth=F2)
    disc(s0, 4.6, sy + 2.4, 2.7, (0.84, 0.82, 0.68))
    disc(s0, 3.9, sy + 3.1, 0.55, (0.70, 0.68, 0.55), depth=F2)
    disc(s0, 5.5, sy + 1.7, 0.40, (0.70, 0.68, 0.55), depth=F2)
    for t in range(9):
        f = (t + 0.5) / 9.0
        ax = -5.4 + f * 10.0
        ay = (sy - 3.2) + f * 5.6 + math.sin(f * math.pi) * 1.9
        s0.extend(box(size=(0.34, 0.34, 0.06), color=(0.95, 0.9, 0.55),
                      center=(ax, ay, dz(F))))
    s0.extend(cone(radius=0.4, height=1.1, segments=8, color=P.GRU_ORANGE),
              transform=compose(translate(-0.2, sy + 1.3, dz(F)), rotate_z(-0.95)))
    for i, (mx, mr) in enumerate(((1.6, 0.55), (2.9, 0.7), (4.3, 0.5))):
        minion_face(s0, mx, sy - 4.4 + (i % 2) * 0.3, mr)
    s0.extend(box(size=(0.22, 0.9, 0.05), color=P.MINION_YELLOW),
              transform=compose(translate(5.0, sy - 3.8, dz(F)), rotate_z(-0.5)))
    z.add_named("slide_0", s0)

    # ---- slide 1: THE MOON (big moon, arrow, pocket moon)
    s1 = MeshBuilder(name="slide_1")
    reset_layers()
    disc(s1, -3.4, sy + 0.6, 3.4, (0.84, 0.82, 0.68))
    disc(s1, -4.3, sy + 1.6, 0.7, (0.70, 0.68, 0.55), depth=F2)
    disc(s1, -2.4, sy - 0.6, 0.5, (0.70, 0.68, 0.55), depth=F2)
    disc(s1, -3.0, sy + 2.0, 0.35, (0.70, 0.68, 0.55), depth=F2)
    s1.extend(box(size=(3.2, 0.4, 0.06), color=(0.95, 0.45, 0.20),
                  center=(2.2, sy + 0.6, dz(F))))
    for k in (-1, 1):
        s1.extend(box(size=(1.1, 0.32, 0.06), color=(0.95, 0.45, 0.20)),
                  transform=compose(translate(3.9, sy + 0.6 + k * 0.36, dz(F)),
                                    rotate_z(k * 0.7)))
    disc(s1, 5.6, sy + 0.6, 0.55, (0.84, 0.82, 0.68))
    s1.extend(box(size=(2.2, 0.18, 0.05), color=(0.5, 0.9, 0.95),
                  center=(5.6, sy - 1.1, dz(F))))   # "actual size" underline
    z.add_named("slide_1", s1)

    # ---- slide 2: THE SHRINK RAY (gun silhouette zapping circles)
    s2 = MeshBuilder(name="slide_2")
    reset_layers()
    s2.extend(box(size=(3.0, 1.1, 0.06), color=P.STEEL_LIGHT,
                  center=(-4.4, sy - 0.4, dz(F))))
    s2.extend(box(size=(1.0, 1.6, 0.06), color=P.STEEL_LIGHT,
                  center=(-5.2, sy - 1.5, dz(F))))
    s2.extend(cylinder(radius=0.45, height=0.9, segments=10, color=P.COIL_CYAN),
              transform=compose(translate(-2.6, sy - 0.4, dz(F)), rotate_z(math.pi / 2)))
    for i in range(5):
        s2.extend(box(size=(1.1, 0.16, 0.05), color=P.COIL_CYAN,
                      center=(-1.2 + i * 1.35, sy - 0.4 + (i % 2) * 0.22, dz(F))))
    disc(s2, 4.4, sy + 1.7, 1.5, (0.62, 0.20, 0.85))
    disc(s2, 5.3, sy - 1.9, 0.5, (0.62, 0.20, 0.85))
    s2.extend(box(size=(0.9, 0.22, 0.05), color=(0.95, 0.9, 0.55)),
              transform=compose(translate(4.9, sy - 0.2, dz(F)), rotate_z(-0.9)))
    z.add_named("slide_2", s2)

    # ---- slide 3: THE BUDGET ("in terms of money... we have no money")
    s3 = MeshBuilder(name="slide_3")
    reset_layers()
    heights = (0.8, 1.8, 3.0, 4.2)
    for i, h in enumerate(heights):
        s3.extend(box(size=(1.7, h, 0.06), color=(0.85, 0.22, 0.18),
                      center=(-4.3 + i * 2.3, sy - 2.8 + h / 2, dz(F))))
    s3.extend(box(size=(10.4, 0.14, 0.05), color=(0.9, 0.9, 0.9),
                  center=(-0.9, sy - 2.9, dz(F2))))
    for k in (-1, 1):  # sad banana where the money should be
        s3.extend(box(size=(1.0, 0.34, 0.06), color=(0.95, 0.82, 0.18)),
                  transform=compose(translate(3.6 + k * 0.42, sy - 1.6, dz(F)),
                                    rotate_z(-k * 0.55)))
    s3.extend(box(size=(7.5, 0.5, 0.06), color=(0.85, 0.22, 0.18),
                  center=(0, sy + 2.6, dz(F))))
    minion_face(s3, 5.6, sy - 1.6, 1.0, mood="sad")
    z.add_named("slide_3", s3)

    # ---- slide 4: GRU (the boss, with adoring staff)
    s4 = MeshBuilder(name="slide_4")
    reset_layers()
    skin = (0.91, 0.76, 0.62)
    shade = (0.82, 0.66, 0.52)
    disc(s4, -2.2, sy + 1.6, 2.5, skin)                    # dome
    disc(s4, -2.2, sy - 0.4, 1.5, skin)                    # jaw
    s4.extend(box(size=(2.4, 2.2, 0.05), color=skin,
                  center=(-2.2, sy + 0.6, dz(F))))         # fill
    for ex in (-3.1, -1.3):                                # eyes + scowl
        disc(s4, ex, sy + 1.3, 0.48, (0.97, 0.97, 0.94), depth=F - 0.04)
        disc(s4, ex, sy + 1.2, 0.20, (0.30, 0.20, 0.12), depth=F - 0.07)
    s4.extend(box(size=(1.1, 0.22, 0.04), color=(0.25, 0.16, 0.10)),
              transform=compose(translate(-3.1, sy + 2.0, dz(F - 0.05)),
                                rotate_z(-0.30)))
    s4.extend(box(size=(1.1, 0.22, 0.04), color=(0.25, 0.16, 0.10)),
              transform=compose(translate(-1.3, sy + 2.0, dz(F - 0.05)),
                                rotate_z(0.30)))
    disc(s4, -2.2, sy + 0.2, 0.62, shade, depth=F - 0.04)  # THE nose
    disc(s4, -2.2, sy - 0.25, 0.45, shade, depth=F - 0.05)
    s4.extend(box(size=(1.3, 0.16, 0.04), color=(0.45, 0.28, 0.16)),
              transform=compose(translate(-2.2, sy - 1.1, dz(F - 0.06)),
                                rotate_z(0.18)))           # smirk
    s4.extend(box(size=(5.4, 1.6, 0.06), color=(0.10, 0.10, 0.13),
                  center=(-2.2, sy - 2.9, dz(F))))         # coat shoulders
    for i, c in enumerate(((0.82, 0.82, 0.84), (0.45, 0.45, 0.50),
                           (0.82, 0.82, 0.84))):
        s4.extend(box(size=(1.7, 0.3, 0.05), color=c,
                      center=(-2.2, sy - 1.8 - i * 0.3, dz(F - 0.03))))  # scarf
    minion_face(s4, 2.6, sy - 2.6, 0.8)
    minion_face(s4, 4.6, sy - 2.2, 0.6)
    minion_face(s4, 5.9, sy - 3.0, 0.7)
    s4.extend(box(size=(0.2, 0.8, 0.05), color=P.MINION_YELLOW),
              transform=compose(translate(3.4, sy - 1.7, dz(F)), rotate_z(-0.45)))
    s4.extend(box(size=(4.0, 0.5, 0.06), color=P.GRU_YELLOW,
                  center=(3.6, sy + 2.2, dz(F))))          # nameplate bar
    z.add_named("slide_4", s4)


def _hall_posters(z: ZoneBuild):
    from zones.common import art_moon, hang_art, framed
    # moon poster, west upper wall
    hang_art(z, art_moon(), -35.5, 12.5, -6.0, "+x")
    # freeze-ray ad, east upper wall
    ad = framed(2.0, 1.5, canvas=(0.10, 0.14, 0.22))
    ad.extend(box(size=(0.85, 0.30, 0.05), color=P.STEEL_LIGHT,
                  center=(-0.35, 0.0, -0.12)))
    ad.extend(box(size=(1.0, 0.10, 0.05), color=P.COIL_CYAN,
                  center=(0.35, 0.0, -0.13)))
    ad.extend(box(size=(0.25, 0.25, 0.05), color=(0.75, 0.9, 0.98),
                  center=(0.85, 0.0, -0.14)))
    hang_art(z, ad, 35.5, 12.5, 6.0, "-x")


def _wall_dressing(z: ZoneBuild):
    """Lab clutter for the blank upper-shell walls: pipe runs with valve
    wheels, slatted vents, junction boxes with indicator lights, vertical
    conduits, and a couple of round gauges. All flush decor (<=0.5 proud),
    no colliders — same rule as the GRU sign."""
    def pipe_z(x, y, z0, z1, r, color):
        z.add(box(size=(r * 2, r * 2, z1 - z0), color=color,
                  center=(x, y, (z0 + z1) / 2)))

    def pipe_xrun(x0, x1, y, zz, r, color):
        z.add(box(size=(x1 - x0, r * 2, r * 2), color=color,
                  center=((x0 + x1) / 2, y, zz)))

    def valve(x, y, zz, axis):
        ring = torus(radius=0.42, tube_radius=0.07, segments=10,
                     sides=6, color=(0.75, 0.18, 0.15))
        rot = rotate_y(math.pi / 2) if axis == "x" else rotate_x(0.0)
        z.mesh.extend(ring, transform=compose(translate(x, y, zz), rot))

    def vent(x, y, zz, facing):
        w, h = 1.8, 1.2
        if facing in ("+x", "-x"):
            z.add(box(size=(0.18, h, w), color=P.STEEL_DARK, center=(x, y, zz)))
            for i in range(4):
                z.add(box(size=(0.24, 0.10, w - 0.3), color=P.STEEL_LIGHT,
                          center=(x, y - h / 2 + (i + 1) * h / 5, zz)))
        else:
            z.add(box(size=(w, h, 0.18), color=P.STEEL_DARK, center=(x, y, zz)))
            for i in range(4):
                z.add(box(size=(w - 0.3, 0.10, 0.24), color=P.STEEL_LIGHT,
                          center=(x, y - h / 2 + (i + 1) * h / 5, zz)))

    def junction(x, y, zz, facing):
        if facing in ("+x", "-x"):
            z.add(box(size=(0.3, 1.1, 0.9), color=(0.55, 0.58, 0.45),
                      center=(x, y, zz)))
            d = 0.18 if facing == "+x" else -0.18
            z.add(box(size=(0.1, 0.12, 0.12), color=(0.2, 0.9, 0.3),
                      center=(x + d, y + 0.3, zz - 0.2)))
            z.add(box(size=(0.1, 0.12, 0.12), color=(0.95, 0.2, 0.2),
                      center=(x + d, y + 0.3, zz + 0.2)))
        else:
            z.add(box(size=(0.9, 1.1, 0.3), color=(0.55, 0.58, 0.45),
                      center=(x, y, zz)))
            d = 0.18 if facing == "+z" else -0.18
            z.add(box(size=(0.12, 0.12, 0.1), color=(0.2, 0.9, 0.3),
                      center=(x - 0.2, y + 0.3, zz + d)))
            z.add(box(size=(0.12, 0.12, 0.1), color=(0.95, 0.2, 0.2),
                      center=(x + 0.2, y + 0.3, zz + d)))

    def gauge(x, y, zz, facing):
        rot = {"+x": rotate_y(math.pi / 2), "-x": rotate_y(math.pi / 2),
               "+z": rotate_x(math.pi / 2), "-z": rotate_x(math.pi / 2)}[facing]
        z.mesh.extend(cylinder(radius=0.55, height=0.16, segments=14,
                               color=(0.92, 0.92, 0.88)),
                      transform=compose(translate(x, y, zz), rot))
        z.mesh.extend(box(size=(0.07, 0.4, 0.06), color=(0.85, 0.2, 0.15)),
                      transform=compose(translate(x, y, zz),
                                        rot, translate(0, 0.16, -0.10),
                                        rotate_z(0.5)))

    # --- east & west walls (x = +/-36): double pipe runs + clutter
    for x, fdir, inset in ((-35.55, "+x", 0.45), (35.55, "-x", -0.45)):
        pipe_z(x, 17.4, -14.0, 14.0, 0.28, P.PIPE_GREY)
        pipe_z(x, 16.7, -14.0, 14.0, 0.20, (0.72, 0.45, 0.25))
        valve(x + inset * 0.4, 17.4, -6.0, "x")
        valve(x + inset * 0.4, 17.4, 7.0, "x")
        for zz in (-10.0, 10.0):
            z.add(box(size=(0.5, 1.4, 0.5), color=P.STEEL_DARK,
                      center=(x, 16.0, zz)))   # pipe drop collars
            pipe_z(x, 10.0, zz - 0.25, zz + 0.25, 0.25, P.PIPE_GREY)
            z.add(box(size=(0.45, 6.6, 0.45), color=P.PIPE_GREY,
                      center=(x, 12.8, zz)))   # vertical conduits
        vent(x, 12.5, -4.0, fdir)
        vent(x, 12.5, 4.0, fdir)
        junction(x, 10.6, 0.0, fdir)
        gauge(x + inset * 0.5, 14.8, 0.0, fdir)

    # --- north wall (z=-16): flank the GRU sign
    for x0, x1 in ((-34.0, -11.5), (11.5, 34.0)):
        pipe_xrun(x0, x1, 17.6, -15.55, 0.26, P.PIPE_GREY)
        pipe_xrun(x0, x1, 9.6, -15.55, 0.22, (0.72, 0.45, 0.25))
    for vx in (-24.0, 24.0):
        z.add(box(size=(0.45, 8.0, 0.45), color=P.PIPE_GREY,
                  center=(vx, 13.6, -15.55)))
        valve(vx, 12.0, -15.3, "z")
    vent(-17.0, 12.5, -15.55, "+z")
    vent(17.0, 12.5, -15.55, "+z")
    junction(-29.5, 12.0, -15.55, "+z")
    junction(29.5, 12.0, -15.55, "+z")

    # --- south wall (z=16): flank the jumbotron
    for x0, x1 in ((-34.0, -9.5), (9.5, 34.0)):
        pipe_xrun(x0, x1, 17.6, 15.55, 0.26, P.PIPE_GREY)
    for vx in (-22.0, 22.0):
        z.add(box(size=(0.45, 7.6, 0.45), color=P.PIPE_GREY,
                  center=(vx, 13.8, 15.55)))
    vent(-15.0, 12.5, 15.55, "-z")
    vent(15.0, 12.5, 15.55, "-z")
    gauge(-28.0, 13.0, 15.3, "-z")
    junction(28.0, 12.5, 15.55, "-z")


def _tube_and_rocket(z: ZoneBuild):
    z.add(cylinder(radius=2.45, height=0.6, segments=16,
                   color=P.STEEL_LIGHT, center=(0, 8.2, 0)))
    z.add(cylinder(radius=2.2, height=7.2, segments=16,
                   color=P.WATER_BLUE, center=(0, 12.0, 0), caps=False))
    for (bx, by, bz, r) in [(2.0, 9.6, 0.9, 0.35), (-1.9, 10.8, -1.0, 0.45),
                            (0.6, 12.2, 2.1, 0.3), (-0.9, 13.4, 2.0, 0.4),
                            (2.1, 14.2, -0.5, 0.3), (-2.1, 14.9, 0.6, 0.35)]:
        z.add(icosphere(radius=r, subdivisions=1, color=P.GOGGLE_GLASS,
                        center=(bx, by, bz)))
    for (rx, rz) in [(2.25, 0), (-2.25, 0), (0, 2.25), (0, -2.25)]:
        z.add(box(size=(0.2, 7.2, 0.2), color=P.STEEL_LIGHT,
                  center=(rx, 12.0, rz)))
    z.add(cylinder(radius=2.45, height=0.5, segments=16,
                   color=P.STEEL_LIGHT, center=(0, 15.85, 0)))
    _goggle_face(z)
    # goo tube collision (matches arena.rs)
    z.collide((0.0, 10.0, 0.0), (4.4, 12.0, 4.4))

    # tube-top platform
    _slab(z, -2.8, 16.0, -2.8, 2.8, 16.3, 2.8, color=P.STEEL_DARK)
    for dx, dz, sx, sz in [(0, -2.7, 5.6, 0.25), (0, 2.7, 5.6, 0.25),
                           (-2.7, 0, 0.25, 5.2), (2.7, 0, 0.25, 5.2)]:
        z.add(box(size=(sx, 0.1, sz), color=P.PLATFORM_YELLOW,
                  center=(dx, 16.32, dz)))

    # Gru's rocket
    z.add(cylinder(radius=1.5, height=0.3, segments=14,
                   color=P.STEEL_DARK, center=(0, 16.45, 0)))
    z.add(cylinder(radius=1.25, height=3.2, segments=14,
                   color=P.GOGGLE_SILVER, center=(0, 18.2, 0)))
    z.add(icosphere(radius=0.42, subdivisions=1, color=P.COIL_CYAN,
                    center=(0, 18.7, 1.15)))
    # cone() is CENTER-anchored: lift by half its height so the base
    # seats on the body top instead of sinking halfway into it, and give
    # it a proper Tintin lip over the hull.
    z.mesh.extend(cone(radius=1.45, height=2.6, segments=14,
                       color=P.GRU_ORANGE), transform=translate(0, 19.8 + 1.3, 0))
    z.add(cylinder(radius=1.5, height=0.22, segments=14,
                   color=P.WARN_STRIPE, center=(0, 19.85, 0)))
    for fx, fz, sx, sz in [(1.85, 0, 1.2, 0.18), (-1.85, 0, 1.2, 0.18),
                           (0, 1.85, 0.18, 1.2), (0, -1.85, 0.18, 1.2)]:
        z.add(box(size=(sx, 1.5, sz), color=P.GRU_ORANGE,
                  center=(fx, 17.2, fz)))
    z.collide((0.0, 19.0, 0.0), (2.6, 5.4, 2.6))
    _gru_statue(z)


def _gru_statue(z: ZoneBuild):
    """Gru on the rocket nose, mid "I'm gonna freeze the moon!" — arched
    back, claw hand thrown up at the sky-moon, shrink ray in the other.
    Authored facing -Z with feet at y=0, then yawed toward MOON_POS
    (10, 62, -20) and seated on the cone apex.
    """
    coat = (0.10, 0.10, 0.13)
    skin = (0.93, 0.78, 0.62)
    scarf_a = (0.82, 0.82, 0.84)
    scarf_b = (0.45, 0.45, 0.50)
    pants = (0.22, 0.22, 0.26)
    black = (0.05, 0.05, 0.07)

    g = MeshBuilder(name="gru")
    g.extend(cylinder(radius=0.55, height=0.14, segments=12,
                      color=P.STEEL_DARK, center=(0, 0.07, 0)))
    # spindly legs, wide stance; clown shoes
    for side in (-1, 1):
        g.extend(cylinder(radius=0.09, height=0.85, segments=8, color=pants),
                 transform=compose(translate(side * 0.26, 0.56, 0.0),
                                   rotate_z(-side * 0.16)))
        g.extend(box(size=(0.22, 0.12, 0.40), color=black,
                     center=(side * 0.34, 0.13, -0.05)))
    # coat: narrow hips up to broad chest, leaning back into the pose
    g.extend(cylinder(radius=0.30, height=0.5, segments=10, color=coat,
                      center=(0, 1.18, 0.0)))
    g.extend(cylinder(radius=0.40, height=0.62, segments=10, color=coat),
             transform=compose(translate(0, 1.62, 0.06), rotate_x(0.16)))
    g.extend(box(size=(0.95, 0.36, 0.46), color=coat, center=(0, 1.95, 0.12)))
    # striped scarf: two rings + hanging tail
    g.extend(cylinder(radius=0.21, height=0.09, segments=10, color=scarf_a,
                      center=(0, 2.16, 0.14)))
    g.extend(cylinder(radius=0.20, height=0.09, segments=10, color=scarf_b,
                      center=(0, 2.25, 0.15)))
    for i, c in enumerate([scarf_a, scarf_b, scarf_a]):
        g.extend(box(size=(0.16, 0.15, 0.06), color=c,
                     center=(0.12, 1.98 - i * 0.15, -0.14)))
    # bald head tipped back, long nose aimed at the moon
    g.extend(icosphere(radius=0.27, subdivisions=1, color=skin,
                       center=(0, 2.52, 0.20)))
    g.extend(cone(radius=0.075, height=0.52, segments=8, color=skin),
             transform=compose(translate(0, 2.58, -0.10), rotate_x(-0.95)))
    # his right arm thrown up — palm + splayed claw fingers
    g.extend(cylinder(radius=0.075, height=0.95, segments=8, color=coat),
             transform=compose(translate(-0.66, 2.45, 0.18),
                               rotate_z(0.50), rotate_x(0.22)))
    g.extend(icosphere(radius=0.09, subdivisions=1, color=skin,
                       center=(-0.89, 2.88, 0.28)))
    for k in range(4):
        a = -0.55 + k * 0.36
        g.extend(box(size=(0.04, 0.18, 0.05), color=skin),
                 transform=compose(translate(-0.89, 2.88, 0.28),
                                   rotate_x(0.30), rotate_z(a),
                                   translate(0, 0.14, 0)))
    g.extend(box(size=(0.05, 0.14, 0.04), color=skin),
             transform=compose(translate(-0.89, 2.88, 0.28),
                               rotate_z(1.2), translate(0, 0.11, 0)))
    # his left arm bent, shrink ray in hand
    g.extend(cylinder(radius=0.075, height=0.5, segments=8, color=coat),
             transform=compose(translate(0.62, 1.92, 0.12), rotate_z(-1.15)))
    g.extend(cylinder(radius=0.07, height=0.5, segments=8, color=coat),
             transform=compose(translate(0.86, 2.22, 0.10), rotate_z(-0.22)))
    g.extend(icosphere(radius=0.085, subdivisions=1, color=skin,
                       center=(0.93, 2.48, 0.10)))
    g.extend(box(size=(0.16, 0.12, 0.24), color=(0.16, 0.18, 0.22),
                 center=(0.93, 2.60, 0.05)))
    g.extend(icosphere(radius=0.045, subdivisions=1, color=P.COIL_CYAN,
                       center=(0.93, 2.62, -0.09)))

    # cone apex sits at 19.8 + 1.3 + 1.3
    z.mesh.extend(g, transform=compose(translate(0, 22.4, 0), rotate_y(-0.46)))
    z.collide((0.0, 23.9, 0.0), (1.3, 3.0, 1.3))


def _scaffold_stairs(z: ZoneBuild, x_base, x_top, zc, top_y=DECK_TOP):
    """14 deep treads with solid risers. Tread depth must exceed the
    player's footprint or try_step_up's lift test hits the step after
    next and the stairs are unclimbable (0.59 rise / 0.57 run works;
    the original 0.40/0.45 did not)."""
    n = 14
    rise = top_y / n
    run = (x_top - x_base) / n
    for i in range(n):
        cx = x_base + (i + 0.5) * run
        ty = (i + 1) * rise
        z.add(box(size=(abs(run) + 0.12, rise, 1.6), color=P.STEEL_DARK,
                  center=(cx, ty - rise / 2, zc)))
        z.collide((cx, ty - rise / 2, zc), (abs(run) + 0.12, rise, 1.6))
        if i % 2 == 0:
            z.add(box(size=(abs(run) + 0.1, 0.04, 1.62), color=P.PLATFORM_YELLOW,
                      center=(cx, ty + 0.01, zc)))


def _crate(z: ZoneBuild, x, zp, s=1.2, stacked=False):
    n = 2 if stacked else 1
    for i in range(n):
        y = DECK_TOP + s / 2 + i * s
        ox = 0.15 * i  # slight stagger so stacks read as separate boxes
        z.add(box(size=(s, s, s), color=P.STEEL_LIGHT,
                  center=(x + ox, y, zp)))
        z.add(box(size=(s + 0.04, 0.12, s + 0.04), color=P.GRU_YELLOW,
                  center=(x + ox, y, zp)))
    z.collide((x, DECK_TOP + (n * s) / 2, zp), (s + 0.2, n * s, s + 0.2))


def _goggle_face(z: ZoneBuild):
    """The goo tube wears a minion goggle on its south face. Obviously."""
    y = 12.2
    z.add(cylinder(radius=2.24, height=0.5, segments=16,
                   color=P.GLOVE_BLACK, center=(0, y, 0), caps=False))
    ring = torus(radius=0.95, tube_radius=0.18, segments=18, sides=8,
                 color=P.GOGGLE_SILVER)
    z.mesh.extend(ring, transform=compose(translate(0, y, 2.25),
                                          rotate_x(1.5708)))
    sclera = cylinder(radius=0.78, height=0.12, segments=14,
                      color=P.GOGGLE_GLASS)
    z.mesh.extend(sclera, transform=compose(translate(0, y, 2.3),
                                            rotate_x(1.5708)))
    z.add(icosphere(radius=0.3, subdivisions=1, color=P.PUPIL_BROWN,
                    center=(0, y, 2.42)))
    z.add(icosphere(radius=0.09, subdivisions=0, color=P.GOGGLE_GLASS,
                    center=(0.12, y + 0.12, 2.66)))


def build(cutaway: bool = False):
    """`cutaway=True` (previews only) omits the south + east upper shell
    so the iso camera can see into the hall. Collision is always full."""
    z = ZoneBuild("hall", bounds=((-36.0, 8.0, -16.0), (36.0, 20.0, 16.0)))

    # --- Upper shell, y 8..20, yellow trim at the top
    z.add(wall_z(-16.25, 16.25, -36.0, thick=0.5, height=12.0, y0=8.0))
    z.add(wall_x(-35.75, 35.75, -16.0, thick=0.5, height=12.0, y0=8.0))
    if not cutaway:
        z.add(wall_z(-16.25, 16.25, 36.0, thick=0.5, height=12.0, y0=8.0))
        z.add(wall_x(-35.75, 35.75, 16.0, thick=0.5, height=12.0, y0=8.0))
    z.collide((-36.0, 14.0, 0.0), (0.5, 12.0, 32.5))
    z.collide((36.0, 14.0, 0.0), (0.5, 12.0, 32.5))
    z.collide((0.0, 14.0, -16.0), (71.5, 12.0, 0.5))
    z.collide((0.0, 14.0, 16.0), (71.5, 12.0, 0.5))
    # Roof with the silo hatch OPEN over the tube (x,z in ±7): the rocket
    # noses through the gap and the moon hangs in the night sky above.
    # The two hatch doors are slid outward, parked on top of the roof.
    for cx2, cz2, sx2, sz2 in [
        (0.0, -11.625, 72.5, 9.25),   # north strip
        (0.0, 11.625, 72.5, 9.25),    # south strip
        (-21.625, 0.0, 29.25, 14.0),  # west strip
        (21.625, 0.0, 29.25, 14.0),   # east strip
    ]:
        z.add(box(size=(sx2, 0.3, sz2), color=P.CEILING,
                  center=(cx2, 20.15, cz2)))
        z.collide((cx2, 20.15, cz2), (sx2, 0.3, sz2))
    for door_cx in (-11.0, 11.0):
        z.add(box(size=(8.0, 0.3, 14.0), color=P.STEEL_DARK,
                  center=(door_cx, 20.5, 0.0)))
        z.collide((door_cx, 20.5, 0.0), (8.0, 0.3, 14.0))
        for k in range(3):
            z.add(box(size=(7.6, 0.08, 0.5), color=P.WARN_STRIPE
                      if k % 2 == 0 else P.PLATFORM_YELLOW,
                      center=(door_cx, 20.69, -4.0 + k * 4.0)))
    # hatch rim glow so the opening reads from the atrium floor
    for dx, dz, sx2, sz2 in [(0, -7.2, 15.0, 0.4), (0, 7.2, 15.0, 0.4),
                             (-7.2, 0, 0.4, 14.0), (7.2, 0, 0.4, 14.0)]:
        z.add(box(size=(sx2, 0.2, sz2), color=P.GRU_YELLOW,
                  center=(dx, 20.05, dz)))

    _gru_sign(z)
    _jumbotron(z)
    _hall_posters(z)
    _wall_dressing(z)

    # --- Rooftop decks (y 8..8.3) with hatch holes; light wells |x| 12..22
    west_holes = [(-34.5, -28.6, -15.3, -10.7), (-34.5, -28.6, 10.7, 15.3)]
    east_holes = [(29.6, 35.5, -15.3, -10.7), (29.6, 35.5, 10.7, 15.3)]
    for (h0, h1, d0, d1, e0, e1), holes in [
        ((-34.5, -28.6, -36.0, -34.5, -28.6, -22.0), west_holes),
        ((29.6, 35.5, 35.5, 36.0, 22.0, 29.6), east_holes),
    ]:
        _slab(z, d0, 8.0, -16.0, d1, DECK_TOP, 16.0, color=P.CEILING)
        _slab(z, e0, 8.0, -16.0, e1, DECK_TOP, 16.0, color=P.CEILING)
        _slab(z, h0, 8.0, -16.0, h1, DECK_TOP, -15.3, color=P.CEILING)
        _slab(z, h0, 8.0, -10.7, h1, DECK_TOP, 10.7, color=P.CEILING)
        _slab(z, h0, 8.0, 15.3, h1, DECK_TOP, 16.0, color=P.CEILING)
        # (hole spans h0..h1 in x and ±(10.7..15.3) in z — big enough for
        # the full rim-entry tube of the launch arcs below)

    _deck_checker(z, -36, -22, -16, 0, P.TURF_GREEN, west_holes)
    _deck_checker(z, -36, -22, 0, 16, P.PAJAMA_NAVY, west_holes)
    _deck_checker(z, 22, 36, -16, 0, P.CAB_CREAM, east_holes)
    _deck_checker(z, 22, 36, 0, 16, P.TILE_TEAL, east_holes)
    for hx, hz in [(-31.55, -13), (-31.55, 13), (32.55, -13), (32.55, 13)]:
        _hatch_rim(z, hx, hz)

    # Solid rails along the light-well edges. Gaps at the bridge mouths
    # plus two hazard-striped drop notches per side (z ±12.5) — the
    # intentional ways to drop into the rooms below.
    for x in (-21.6, 21.6):
        _solid_rail_z(z, -15.5, -13.75, x, DECK_TOP)
        _solid_rail_z(z, -11.25, -9.2, x, DECK_TOP)
        _solid_rail_z(z, -6.8, -0.3, x, DECK_TOP)
        _solid_rail_z(z, 0.3, 6.8, x, DECK_TOP)
        _solid_rail_z(z, 9.2, 11.25, x, DECK_TOP)
        _solid_rail_z(z, 13.75, 15.5, x, DECK_TOP)
        edge = x - 0.25 if x > 0 else x + 0.25
        _hazard_lip_z(z, -13.75, -11.25, edge, DECK_TOP)
        _hazard_lip_z(z, 11.25, 13.75, edge, DECK_TOP)

    # --- Bridges, ring catwalk, connectors (collision mirrors arena.rs)
    _slab(z, -22.0, 8.05, -9.0, 22.0, DECK_TOP, -7.0)
    _slab(z, -22.0, 8.05, 7.0, 22.0, DECK_TOP, 9.0)
    for bz in (-8.0, 8.0):
        _hazard_lip_x(z, -22.0, 22.0, bz - 0.93, DECK_TOP)
        _hazard_lip_x(z, -22.0, 22.0, bz + 0.93, DECK_TOP)
    _slab(z, -6.0, 8.05, -6.0, 6.0, DECK_TOP, -4.0)
    _slab(z, -6.0, 8.05, 4.0, -1.8, DECK_TOP, 6.0)
    _slab(z, 1.8, 8.05, 4.0, 6.0, DECK_TOP, 6.0)
    _slab(z, -6.0, 8.05, -4.0, -4.0, DECK_TOP, 4.0)
    _slab(z, 4.0, 8.05, -4.0, 6.0, DECK_TOP, 4.0)
    _slab(z, -1.0, 8.05, -7.5, 1.0, DECK_TOP, -5.5)
    _slab(z, 2.0, 8.05, 5.5, 4.0, DECK_TOP, 7.5)
    _hazard_lip_z(z, -6.0, 6.0, -5.93, DECK_TOP)
    _hazard_lip_z(z, -6.0, 6.0, 5.93, DECK_TOP)

    # --- L2 gantries (south one split around the elevator shaft)
    _slab(z, -3.5, 12.3, -4.4, 3.5, 12.55, -2.7, color=P.STEEL_DARK)
    _slab(z, -3.5, 12.3, 2.7, -1.8, 12.55, 4.4, color=P.STEEL_DARK)
    _slab(z, 1.8, 12.3, 2.7, 3.5, 12.55, 4.4, color=P.STEEL_DARK)
    _hazard_lip_x(z, -3.5, 3.5, -4.33, 12.55)
    _hazard_lip_x(z, -3.5, -1.8, 4.33, 12.55)
    _hazard_lip_x(z, 1.8, 3.5, 4.33, 12.55)
    for sx, gz in [(-1.5, -2.6), (1.5, -2.6)]:
        z.add(box(size=(0.15, 0.15, 1.2), color=P.STEEL_LIGHT,
                  center=(sx, 12.35, gz)))
        z.collide((sx, 12.35, gz), (0.15, 0.15, 1.2))

    # --- Walkable rafter beams, one flat grid at top y 16.9: two
    # east-west girders (z=-6, z=+7) and two north-south girders (x=±18),
    # all placed clear of every jump pad's rise path and the elevator
    # shaft. Entry is the spur from the tube-top platform — a 0.6 m
    # step-up, no pad needed; the quad perch unlocks the rafters.
    BEAM_Y = 16.5  # body center; top 16.9, a clean auto-step from 16.3
    for bz in (-6.0, 7.0):
        z.add(box(size=(71.5, 0.8, 1.4), color=P.STEEL_DARK,
                  center=(0.0, BEAM_Y, bz)))
        z.collide((0.0, BEAM_Y, bz), (71.5, 0.8, 1.4))
        z.add(box(size=(71.5, 0.06, 0.5), color=P.PLATFORM_YELLOW,
                  center=(0.0, 16.93, bz)))
    for bx in (-18.0, 18.0):
        z.add(box(size=(1.4, 0.8, 31.5), color=P.STEEL_DARK,
                  center=(bx, BEAM_Y, 0.0)))
        z.collide((bx, BEAM_Y, 0.0), (1.4, 0.8, 31.5))
        z.add(box(size=(0.5, 0.06, 31.5), color=P.PLATFORM_YELLOW,
                  center=(bx, 16.93, 0.0)))
    # spur: tube-top platform -> the z=-6 girder, at x=-1.6 so the
    # gantry pad's whole rise tube (entries up to x 0.75) stays clear
    z.add(box(size=(1.2, 0.8, 3.4), color=P.STEEL_DARK,
              center=(-1.6, BEAM_Y, -4.3)))
    z.collide((-1.6, BEAM_Y, -4.3), (1.2, 0.8, 3.4))
    z.add(box(size=(0.5, 0.06, 3.4), color=P.PLATFORM_YELLOW,
              center=(-1.6, 16.93, -4.3)))

    # --- Cage lights on the long hall walls
    for lx in (-18.0, 18.0):
        for lz, face in [(-15.55, 1.0), (15.55, -1.0)]:
            z.add(box(size=(1.2, 0.6, 0.25), color=P.GLOW_CORE,
                      center=(lx, 14.0, lz)))
            z.collide((lx, 14.0, lz), (1.2, 0.6, 0.25))
            for k in (-0.35, 0.0, 0.35):
                z.add(box(size=(0.06, 0.7, 0.3), color=P.RIVET,
                          center=(lx + k, 14.0, lz + 0.04 * face)))

    # --- Scaffold stairs in the light wells (sports + bathrooms sides):
    # the guides' rule — pads must not be the only way up. Each runs along
    # a well to the deck drop-notch, which doubles as the stair mouth.
    # base sits 1.8 m off the internal wall so the entrance face is
    # actually reachable (it used to start in a 0.34 m crevice)
    _scaffold_stairs(z, x_base=-14.0, x_top=-22.0, zc=-12.5)
    _scaffold_stairs(z, x_base=14.0, x_top=22.0, zc=12.5)

    # --- Crates on the decks: cover for players landing from the hatches
    _crate(z, -26.5, -4.5, stacked=True)
    _crate(z, -25.0, 10.5)
    _crate(z, 28.5, 3.5)
    _crate(z, 27.5, -8.8, stacked=True)

    _tube_and_rocket(z)

    # --- Elevator guide rails (the platform itself is client-rendered)
    for rx in (-1.45, 1.45):
        z.add(box(size=(0.18, 8.0, 0.18), color=P.STEEL_LIGHT,
                  center=(rx, 4.0, 4.5)))
        z.collide((rx, 4.0, 4.5), (0.18, 8.0, 0.18))
    # call button post at the bottom
    z.add(box(size=(0.6, 1.2, 0.3), color=P.STEEL_DARK,
              center=(2.4, 0.6, 4.5)))
    z.add(icosphere(radius=0.14, subdivisions=0, color=P.BALL_RED,
                    center=(2.4, 1.1, 4.35)))
    z.collide((2.4, 0.6, 4.5), (0.6, 1.2, 0.3))

    # --- Gameplay: connector pads arc onto the gantries, the gantry pad
    # arcs over the platform lip onto the quad perch (high apex clears the
    # edge — it pokes through the open silo hatch on the way)
    z.jump_pad(0.0, DECK_TOP, -6.8, target=(0.0, 12.55, -3.55),
               clearance=1.3, radius=0.8)
    z.jump_pad(3.0, DECK_TOP, 6.8, target=(2.6, 12.55, 3.55),
               clearance=1.3, radius=0.8)
    # lands on the z=-6 rafter girder — 71 m long and aligned with the
    # flight, so steering can't miss it; the spur walks down to the quad
    # no pad up from the gantry: every arc from there either clips the
    # rafter underside or threads a lane narrower than the entry spread.
    # Geometry instead — maintenance rungs along the tube's west side,
    # gantry (12.55) to the platform (16.3), auto-step all the way.
    # x=-3.7: a climbing player (r 0.4) must stay clear of the platform
    # slab's footprint (|x| < 3.2) until the final step up, or the slab
    # underside head-blocks the climb at the third rung.
    for i, (ry, rz) in enumerate([(13.15, -2.3), (13.75, -1.5), (14.35, -0.7),
                                  (14.95, 0.1), (15.55, 0.9), (16.15, 1.7)]):
        z.add(box(size=(0.7, 0.6, 0.8), color=P.STEEL_DARK,
                  center=(-3.7, ry - 0.3, rz)))
        z.collide((-3.7, ry - 0.3, rz), (0.7, 0.6, 0.8))
        z.add(box(size=(0.72, 0.05, 0.82),
                  color=P.PLATFORM_YELLOW if i % 2 == 0 else P.WARN_STRIPE,
                  center=(-3.7, ry + 0.02, rz)))
        # strut tying the rung back to the tube (solid — it's standable)
        z.add(box(size=(1.3, 0.12, 0.2), color=P.STEEL_LIGHT,
                  center=(-2.75, ry - 0.36, rz)))
        z.collide((-2.75, ry - 0.36, rz), (1.3, 0.12, 0.2))
    z.spawn(-29.0, DECK_TOP, -8.0)
    z.spawn(29.0, DECK_TOP, 8.0)
    z.powerup("quad_damage", 1.9, 16.3, 1.9)
    # rocket launcher mid north bridge: contested, and the splash
    # knockback is born for the catwalk layer
    z.powerup("rocket_launcher", 0.0, 8.3, -8.0)
    # PX-41 serum up on the north gantry — go purple, go feral
    z.powerup("serum", -2.5, 12.55, -3.8)
    # health packs on the decks — a reason to hold the high ground
    z.powerup("health", -27.0, 8.3, 2.0)
    z.powerup("health", 27.0, 8.3, -2.0)
    z.powerup("banana", 32.0, 8.3, 0.0)

    return z


def main():
    write_zone(build())


if __name__ == "__main__":
    main()
