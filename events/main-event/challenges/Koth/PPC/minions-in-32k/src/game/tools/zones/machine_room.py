"""Machine room — the money shot.

Footprint: x in [-12, 12], z in [-16, 16]. Zone center (0, 0, 0).
Central 4x4 tower (the cylinder-ish reactor column) rising full height, with
four orbiting platforms at y=2 (centers sqrt(2)*6 = 4.24, 4.24 along diagonals,
size 2x0.3x2 per arena.rs).

Hero vibe: industrial steel, hot magenta coils wrapped around the core,
cyan pipework, a glowing core visible through slotted vents on the tower,
hazard-stripe trim on the platforms, side machines that look clickable.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyasset import MeshBuilder, box, cylinder, icosphere, torus, cone
from pyasset.builder import rotate_y, translate, compose
from pyasset.write import build_glb, write_collision_glb

from zones import palette as P
from zones.common import (
    ZoneBuild, write_zone, wall_x, wall_z,
    pipe_strip, vert_pipe,
)


def build():
    z = ZoneBuild("machine_room",
                  bounds=((-12.0, 0.0, -16.0), (12.0, 20.0, 16.0)))
    x0, x1, z0, z1 = -12.0, 12.0, -16.0, 16.0
    cx, cz = 0.0, 0.0

    # --- Checkered steel floor
    tile = 2.0
    for ix in range(int((x1 - x0) / tile)):
        for iz in range(int((z1 - z0) / tile)):
            c = P.STEEL_LIGHT if (ix + iz) % 2 == 0 else P.STEEL_DARK
            z.add(box(size=(tile * 0.98, 0.08, tile * 0.98),
                      color=c,
                      center=(x0 + (ix + 0.5) * tile, -0.04,
                              z0 + (iz + 0.5) * tile)))

    # --- Internal walls at x = +/- 12 (machine_room is the single owner;
    # side zones no longer duplicate them), doorways at z in [-3, 3]
    for wx in (-12.0, 12.0):
        z.solid_wall_z(-16.0, -3.0, wx, thick=0.4, color=P.WALL_BASE)
        z.solid_wall_z(3.0, 16.0, wx, thick=0.4, color=P.WALL_BASE)

    # --- Machine room's share of the outer N/S walls. These had collision
    # in arena.rs but NO visual mesh anywhere — the hole was masked by the
    # big machines. South side keeps the doorway to the secret room.
    z.solid_wall_x(-12.0, 12.0, -16.0, thick=0.5, color=P.WALL_BASE)
    z.solid_wall_x(-12.0, -3.0, 16.0, thick=0.5, color=P.WALL_BASE)
    z.solid_wall_x(3.0, 12.0, 16.0, thick=0.5, color=P.WALL_BASE)

    # v3: the machine room is the open atrium of the hall — no ceiling.

    # --- Central tower (matches the 4x4x8 AABB)
    core_r = 2.0
    # Cylinder shell
    z.add(cylinder(radius=core_r, height=8.0, segments=16,
                   color=P.STEEL_DARK, center=(0.0, 4.0, 0.0),
                   name="core_shell"))
    # Hazard stripes at base (yellow/black rings)
    for i in range(6):
        c = P.PLATFORM_YELLOW if i % 2 == 0 else P.WARN_STRIPE
        z.add(cylinder(radius=core_r + 0.05, height=0.4, segments=16,
                       color=c, center=(0.0, 0.25 + i * 0.4, 0.0),
                       caps=False))
    # Coils (magenta tori wrapping)
    for (cy, c) in [(3.0, P.COIL_MAGENTA), (4.5, P.COIL_CYAN),
                    (6.0, P.COIL_MAGENTA)]:
        t = torus(radius=core_r + 0.25, tube_radius=0.25, segments=20,
                  sides=8, color=c)
        z.mesh.extend(t, transform=translate(0.0, cy, 0.0))
    # v3: no cone cap — the goo tube (hall.py) continues the core upward.
    # Full-height core collision (the old AABB spanned y -4..4, leaving
    # the visible upper half of the tower hollow).
    z.collide((0.0, 4.0, 0.0), (4.4, 8.0, 4.4))

    # --- Orbiting platforms (4), at diagonals r=6, y=2, size 2x0.3x2
    for i in range(4):
        a = (i / 4) * math.tau + math.pi / 4
        px = math.cos(a) * 6.0
        pz = math.sin(a) * 6.0
        # platform slab
        z.add(box(size=(2.0, 0.3, 2.0), color=P.PLATFORM_YELLOW,
                  center=(px, 2.0, pz)))
        # hazard stripe along edges
        for edge_off in [(1.05, 0, 0), (-1.05, 0, 0), (0, 0, 1.05), (0, 0, -1.05)]:
            ex, _, ez = edge_off
            z.add(box(size=(0.1 if ex == 0 else 0.1,
                            0.06, 0.1 if ez == 0 else 0.1),
                      color=P.WARN_STRIPE,
                      center=(px + ex, 2.17, pz + ez)))
        # Diagonal stripes on top
        for k in range(-2, 3):
            z.add(box(size=(0.18, 0.03, 2.2), color=P.WARN_STRIPE,
                      center=(px + k * 0.45, 2.17, pz)))
        # Support cable up to the L1 ring catwalk
        z.add(box(size=(0.08, 5.9, 0.08), color=P.RIVET,
                  center=(px, 5.1, pz)))
        # Grating panel below
        z.add(box(size=(1.8, 0.05, 1.8), color=P.STEEL_DARK,
                  center=(px, 1.9, pz)))
        z.collide((px, 2.0, pz), (2.0, 0.3, 2.0))

    # --- Side machines along east/west walls (interactable vibes).
    # Inset by 0.3 m so the body sits inside the wall's interior face
    # (wall x=±12 thick 0.4, interior face at x=±11.8; body half-width 0.4).
    for wx, wz, name, accent in [
        (-11.3, -8.0, "console_a", P.COIL_MAGENTA),
        (-11.3, 8.0, "console_b", P.COIL_CYAN),
        (11.3, -8.0, "console_c", P.PLATFORM_YELLOW),
        (11.3, 8.0, "console_d", P.COIL_MAGENTA),
    ]:
        _machine_console(z, wx, wz, accent)

    # --- Machines at the short ends (north/south)
    _big_machine(z, -5.0, -14.5, P.COIL_MAGENTA)
    _big_machine(z, 5.0, -14.5, P.COIL_CYAN)
    _big_machine(z, -5.0, 14.5, P.PLATFORM_YELLOW)
    _big_machine(z, 5.0, 14.5, P.COIL_CYAN)

    # --- Ceiling pipe network (walkable — drop on from the bridges).
    # North outer pipe hugs the end wall; the south one was removed — it
    # ran straight across the jumbotron's lower half as seen from the
    # floor. Clear of the atrium pads' rise tubes.
    for z_at in [-14.2, -6.0, 6.0]:
        z.solid_pipe_x(x0 + 1, x1 - 1, 7.2, z_at, radius=0.25,
                       color=P.COIL_MAGENTA)
    # Conduit beams: chunky, and WALKABLE — drop onto them from the L1
    # bridges (1.05 m below the bridge tops) and run the atrium under fire.
    for x_at in [-9.5, 9.5]:  # off the atrium pads' rise-tube lane
        z.add(box(size=(0.7, 0.7, z1 - z0 - 2),
                  color=P.COIL_CYAN,
                  center=(x_at, 7.0, 0.0)))
        z.collide((x_at, 7.0, 0.0), (0.7, 0.7, z1 - z0 - 2))

    # --- Gameplay: atrium jump pads beside the bridge edges, ground
    # spawns, and the elevator (platform itself is client-rendered).
    # x=±6, not ±8: the walkable conduits own the ±8 lane and the arcs
    # must not clip them (check.py simulates every arc).
    # r=0.8 at z=±12.3: the corridor between the big machines' fronts
    # (z ±13.65) and the bridge rise-clearance can't fit a 1.1 m pad
    for px, pz in [(-6.0, -12.3), (6.0, -12.3), (-6.0, 12.3), (6.0, 12.3)]:
        bridge_z = -8.0 if pz < 0 else 8.0
        z.jump_pad(px, 0.0, pz, target=(px, 8.3, bridge_z), clearance=3.0,
                   radius=0.8)
    z.spawn(0.0, 0.0, -12.0)
    z.spawn(0.0, 0.0, 12.0)
    z.powerup("armor", 0.0, 0.0, 8.0)
    z.powerup("banana", 0.0, 0.0, -8.0)
    z.elevator(center_x=0.0, center_z=4.5, half_x=1.25, half_z=1.25,
               thickness=0.3, y_bottom=0.25, y_top=16.3, speed=2.0,
               dwell_s=2.5)

    # --- Warning chevrons on floor around the core
    for i in range(12):
        a = i * math.tau / 12
        r0 = 2.6
        x = math.cos(a) * r0
        z_p = math.sin(a) * r0
        c = P.PLATFORM_YELLOW if i % 2 == 0 else P.WARN_STRIPE
        # small chevron
        b = box(size=(0.6, 0.03, 0.2), color=c, center=(x, 0.01, z_p))
        M = translate(x, 0.01, z_p) @ rotate_y(a + math.pi / 2) @ translate(-x, -0.01, -z_p)
        z.mesh.extend(b, transform=M)

    return z


def _machine_console(z: ZoneBuild, x: float, zp: float, accent):
    """Wall-mounted console: base + screen + blinking lights.

    `toward_room` is the +X/-X direction the console faces. Previously the
    sign was inverted, so the screen and accent bezel ended up *inside* the
    gray side wall and z-fought it (the pink/blue/orange flicker the player
    saw in the lab middle).
    """
    toward_room = 1.0 if x < 0 else -1.0  # +X if left wall, -X if right wall
    # Base
    z.add(box(size=(0.8, 1.4, 2.4), color=P.STEEL_DARK,
              center=(x, 0.7, zp)))
    # Solid: body + screen depth (consoles were walk-through before)
    z.collide((x + 0.15 * toward_room, 1.2, zp), (1.2, 2.4, 2.4))
    # Screen — sits on the room-facing side of the body
    screen_x = x + 0.45 * toward_room
    z.add(box(size=(0.05, 0.9, 1.6), color=P.GLOW_CORE,
              center=(screen_x, 1.6, zp)))
    # Colored bezel just outboard of the screen
    z.add(box(size=(0.06, 0.95, 1.7), color=accent,
              center=(screen_x + 0.03 * toward_room, 1.6, zp)))
    # Dials further out so they don't z-fight the screen
    for dz in (-0.7, -0.3, 0.1, 0.5):
        z.add(cylinder(radius=0.12, height=0.08, segments=8,
                       color=P.PLATFORM_YELLOW,
                       center=(screen_x + 0.08 * toward_room, 0.7, zp + dz)))
    # Handle on top
    z.add(box(size=(0.08, 0.12, 0.5), color=P.GRU_ORANGE,
              center=(screen_x + 0.05 * toward_room, 2.3, zp)))


def _big_machine(z: ZoneBuild, x: float, zp: float, accent):
    """End-wall big machine with antenna and blinkers.

    Body half-depth is 0.6 and the accent panel is 0.1 thick, so the panel
    centre must be ≥ 0.65 m off the body centre to clear the body's front
    face. Previously 0.55, which placed the panel back face exactly on the
    body's front face → pink/cyan/orange flickering.
    """
    toward_room = 1.0 if zp < 0 else -1.0  # away from end wall, into the room
    # Main body
    z.add(box(size=(3.0, 3.0, 1.2), color=P.STEEL_DARK,
              center=(x, 1.5, zp)))
    # Solid: body + front panel (big machines were walk-through before)
    z.collide((x, 1.5, zp + 0.1 * toward_room), (3.0, 3.0, 1.5))
    front_z = zp + 0.70 * toward_room
    # Front panel accent
    z.add(box(size=(2.6, 2.6, 0.1), color=accent,
              center=(x, 1.5, front_z)))
    # Tiny screen — 0.07 in front of accent so back face clears panel front face
    z.add(box(size=(1.2, 0.6, 0.05), color=P.GLOW_CORE,
              center=(x, 1.8, front_z + 0.08 * toward_room)))
    # Knobs
    for dx in (-1.0, 0.0, 1.0):
        z.add(cylinder(radius=0.15, height=0.1, segments=8,
                       color=P.PLATFORM_YELLOW,
                       center=(x + dx, 1.0, front_z + 0.08 * toward_room)))
    # Antenna + bulb
    z.add(box(size=(0.1, 1.6, 0.1), color=P.RIVET,
              center=(x, 3.8, zp)))
    z.add(icosphere(radius=0.18, subdivisions=0, color=accent,
                    center=(x, 4.6, zp)))
    # Top grate
    for i in range(5):
        z.add(box(size=(2.8, 0.06, 0.15), color=P.RIVET,
                  center=(x, 3.05, zp - 0.4 + i * 0.2)))


def main():
    write_zone(build())


if __name__ == "__main__":
    main()
