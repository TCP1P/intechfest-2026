"""Shared helpers for zone construction.

All zones share one coordinate space. Floor y=0, ground rooms reach y=8,
the v3 hall continues to y=20. Each zone builds its own cell of
floor/walls plus props, at world-space positions.

Conventions:
 - y=0 is the floor surface players stand on.
 - outer walls live at x=+/-36, z=+/-16.
 - internal walls at x=+/-12 (between side zones and machine room),
   and at z=0 in side rows (between sports/sleeping and kitchen/bathrooms).
 - Doorways are GAPS in those walls (z in [-3, 3] at x=+/-12;
   x in [-15, -18] at z=0 on the left, x in [18, 21] at z=0 on the right).

SINGLE SOURCE OF TRUTH: these scripts author the level. Each ZoneBuild
carries the visual mesh AND the authoritative gameplay data — collision
AABBs, jump pads, spawn points, powerups, elevators — and writes both the
.glb and the per-zone JSON under assets/scenes/. The sim embeds those
JSONs at compile time (`Arena::gru_lab_v3()`); nothing is hand-mirrored
in Rust. Run tools/zones/build_all.py after any edit.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure pyasset is importable when scripts run from repo root
_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from pyasset import MeshBuilder, box, hex_to_rgb
from pyasset.builder import Node
from . import palette as P

CEIL_Y = 8.0
FLOOR_Y = 0.0

# Arena bounds per arena.rs
OUTER_MIN_X = -36.0
OUTER_MAX_X = 36.0
OUTER_MIN_Z = -16.0
OUTER_MAX_Z = 16.0


def floor_slab(x0: float, x1: float, z0: float, z1: float, color=P.FLOOR_BASE,
               thickness: float = 0.2, y: float = 0.0, name: str = "floor") -> MeshBuilder:
    cx = (x0 + x1) / 2
    cz = (z0 + z1) / 2
    sx = x1 - x0
    sz = z1 - z0
    return box(size=(sx, thickness, sz), color=color,
               center=(cx, y - thickness / 2, cz), name=name)


def floor_tiles(x0, x1, z0, z1, tile=2.0, c1=P.FLOOR_BASE, c2=P.FLOOR_DARK,
                name="floor_tiles") -> MeshBuilder:
    """Alternating 2-tile checker as a single mesh — cheap pattern."""
    mb = MeshBuilder(name=name)
    nx = max(1, int(round((x1 - x0) / tile)))
    nz = max(1, int(round((z1 - z0) / tile)))
    dx = (x1 - x0) / nx
    dz = (z1 - z0) / nz
    for ix in range(nx):
        for iz in range(nz):
            c = c1 if (ix + iz) % 2 == 0 else c2
            b = box(
                size=(dx * 0.98, 0.08, dz * 0.98),
                color=c,
                center=(x0 + (ix + 0.5) * dx, -0.04, z0 + (iz + 0.5) * dz),
                name="tile",
            )
            mb.extend(b)
    return mb


def ceiling_slab(x0, x1, z0, z1, color=P.CEILING, thickness=0.3,
                 name="ceiling") -> MeshBuilder:
    cx = (x0 + x1) / 2
    cz = (z0 + z1) / 2
    sx = x1 - x0
    sz = z1 - z0
    return box(size=(sx, thickness, sz), color=color,
               center=(cx, CEIL_Y + thickness / 2, cz), name=name)


def wall_x(x0, x1, z, thick=0.5, height=None, y0=0.0, color=P.WALL_BASE,
           trim_color=P.WALL_TRIM, trim_height=0.4,
           name="wall_x") -> MeshBuilder:
    """Wall running along X. Adds a yellow trim strip at the top for silhouette."""
    if height is None:
        height = CEIL_Y - y0
    cx = (x0 + x1) / 2
    cy = y0 + height / 2
    sx = x1 - x0
    mb = MeshBuilder(name=name)
    mb.extend(box(size=(sx, height, thick), color=color, center=(cx, cy, z)))
    # Trim at top — raised 0.02 so its top face encloses the wall's top
    # face instead of z-fighting it (visible from the v3 decks/rafters)
    mb.extend(box(size=(sx, trim_height, thick + 0.05),
                  color=trim_color,
                  center=(cx, y0 + height - trim_height / 2 + 0.02, z)))
    # Base rivet strip
    mb.extend(box(size=(sx, 0.15, thick + 0.02),
                  color=P.RIVET,
                  center=(cx, y0 + 0.075, z)))
    return mb


def wall_z(z0, z1, x, thick=0.5, height=None, y0=0.0, color=P.WALL_BASE,
           trim_color=P.WALL_TRIM, trim_height=0.4,
           name="wall_z") -> MeshBuilder:
    if height is None:
        height = CEIL_Y - y0
    cx = x
    cy = y0 + height / 2
    sz = z1 - z0
    mb = MeshBuilder(name=name)
    mb.extend(box(size=(thick, height, sz), color=color, center=(x, cy, (z0 + z1) / 2)))
    mb.extend(box(size=(thick + 0.05, trim_height, sz),
                  color=trim_color,
                  center=(x, y0 + height - trim_height / 2 + 0.02, (z0 + z1) / 2)))
    mb.extend(box(size=(thick + 0.02, 0.15, sz),
                  color=P.RIVET,
                  center=(x, y0 + 0.075, (z0 + z1) / 2)))
    return mb


def pipe_strip(x0, x1, y, z, radius=0.25, color=P.PIPE_GREY):
    """A horizontal pipe along X (simplified as a thin box)."""
    cx = (x0 + x1) / 2
    return box(size=(x1 - x0, radius * 2, radius * 2), color=color,
               center=(cx, y, z), name="pipe_x")


def vert_pipe(x, z, y0=0.0, y1=None, radius=0.2, color=P.PIPE_GREY):
    if y1 is None:
        y1 = CEIL_Y
    return box(size=(radius * 2, y1 - y0, radius * 2), color=color,
               center=(x, (y0 + y1) / 2, z), name="pipe_y")


class ZoneBuild:
    """Accumulates a zone's visual mesh + the authoritative gameplay data."""

    def __init__(self, name: str, bounds=None):
        self.name = name
        self.mesh = MeshBuilder(name=name)
        self.collision_aabbs: list = []  # list of (min_xyz, max_xyz)
        self.jump_pads: list = []
        self.spawn_points: list = []
        self.powerups: list = []
        self.elevators: list = []
        # extra named glTF nodes (decor the client toggles by Name —
        # e.g. jumbotron slides). Purely visual; never gameplay data.
        self.named_meshes: list = []  # (name, MeshBuilder)
        # ((min_xyz), (max_xyz)) zone metadata bounds
        self.bounds = bounds

    def add(self, mb: MeshBuilder):
        self.mesh.extend(mb)

    def add_named(self, name: str, mb: MeshBuilder):
        self.named_meshes.append((name, mb))

    def collide(self, center, size):
        cx, cy, cz = center
        sx, sy, sz = size
        self.collision_aabbs.append((
            (cx - sx / 2, cy - sy / 2, cz - sz / 2),
            (cx + sx / 2, cy + sy / 2, cz + sz / 2),
        ))

    def solid_wall_x(self, x0, x1, z, thick=0.4, height=None, y0=0.0,
                     color=P.WALL_BASE):
        """Wall visual + exactly matching collision in one call."""
        if height is None:
            height = CEIL_Y - y0
        self.add(wall_x(x0, x1, z, thick=thick, height=height, y0=y0,
                        color=color))
        self.collide(((x0 + x1) / 2, y0 + height / 2, z),
                     (x1 - x0, height, thick))

    def solid_wall_z(self, z0, z1, x, thick=0.4, height=None, y0=0.0,
                     color=P.WALL_BASE):
        if height is None:
            height = CEIL_Y - y0
        self.add(wall_z(z0, z1, x, thick=thick, height=height, y0=y0,
                        color=color))
        self.collide((x, y0 + height / 2, (z0 + z1) / 2),
                     (thick, height, z1 - z0))

    def solid_pipe_x(self, x0, x1, y, z, radius=0.25, color=P.PIPE_GREY):
        """Walkable pipe: visual + collision. Players WILL try to walk
        every pipe they can see, so every pipe they can see is solid."""
        self.add(pipe_strip(x0, x1, y, z, radius=radius, color=color))
        self.collide(((x0 + x1) / 2, y, z),
                     (x1 - x0, radius * 2, radius * 2))

    def jump_pad(self, x, y, z, target, clearance=1.5, radius=1.1):
        """Quake-style launch pad: computes the full ballistic velocity so
        a player who touches the trigger lands ON `target` hands-off — no
        air steering needed (Q3 trigger_push + AimAtTarget, except we aim
        at the landing spot, not the apex). `clearance` is how far the
        arc's apex rises above the higher of pad/target.

        The arc is the gameplay contract: tools/level/check.py simulates
        it against every collider and fails the build if it clips."""
        import math
        from pyasset import cylinder, box

        g = 20.0  # sim GRAVITY
        tx, ty, tz = target
        apex = max(y, ty) + clearance
        vy = math.sqrt(2.0 * g * (apex - y))
        t_total = vy / g + math.sqrt(2.0 * (apex - ty) / g)
        vx = (tx - x) / t_total
        vz = (tz - z) / t_total
        self.jump_pads.append({
            "center": [x, y, z], "radius": radius,
            "velocity": [round(vx, 4), round(vy, 4), round(vz, 4)],
            # tooling-only: the sim ignores this; check.py asserts the arc
            "target": [tx, ty, tz],
        })

        self.add(cylinder(radius=radius, height=0.12, segments=12,
                          color=P.COIL_CYAN, center=(x, y + 0.06, z)))
        self.add(cylinder(radius=radius * 0.5, height=0.18, segments=10,
                          color=P.GLOW_CORE, center=(x, y + 0.09, z)))
        # direction chevrons on the puck so the arc reads before you step on
        h = math.hypot(vx, vz)
        if h > 0.05:
            ux, uz = vx / h, vz / h
            for k in (0.45, 0.75):
                self.add(box(size=(0.34, 0.05, 0.34), color=P.GLOW_CORE,
                             center=(x + ux * k * radius,
                                     y + 0.14, z + uz * k * radius)))

    def spawn(self, x, y, z):
        self.spawn_points.append([x, y, z])

    def powerup(self, kind: str, x, y, z, radius=0.75):
        self.powerups.append({
            "kind": kind, "center": [x, y, z], "radius": radius,
        })

    def elevator(self, center_x, center_z, half_x, half_z, thickness,
                 y_bottom, y_top, speed, dwell_s):
        self.elevators.append({
            "center": [center_x, center_z], "half": [half_x, half_z],
            "thickness": thickness, "y_bottom": y_bottom, "y_top": y_top,
            "speed": speed, "dwell_s": dwell_s,
        })

    def zone_json(self) -> dict:
        bounds = self.bounds
        if bounds is None:
            raise ValueError(f"zone '{self.name}' has no bounds set")
        return {
            "name": self.name,
            "bounds": {"min": list(bounds[0]), "max": list(bounds[1])},
            "walls": [
                {"min": list(mn), "max": list(mx)}
                for mn, mx in self.collision_aabbs
            ],
            "jump_pads": self.jump_pads,
            "powerups": self.powerups,
            "spawn_points": self.spawn_points,
            "elevators": self.elevators,
            "props": [],
        }

    def tri_count(self) -> int:
        return self.mesh.tri_count()


def write_zone(z: ZoneBuild, glb: bool = True, unlit: bool = False):
    """Write a zone's glb + collision glb + scene JSON. The one exit path
    every zone script uses, so visuals and gameplay can't be saved apart."""
    import json
    from pyasset.write import build_glb, build_scene_glb, write_collision_glb

    root = Path(__file__).resolve().parents[2]
    if glb:
        zones_dir = root / "assets" / "zones"
        zones_dir.mkdir(parents=True, exist_ok=True)
        if z.named_meshes:
            nodes = [Node(name=z.name, mesh=z.mesh)]
            nodes += [Node(name=n, mesh=mb) for n, mb in z.named_meshes]
            build_scene_glb(nodes, str(zones_dir / f"{z.name}.glb"),
                            scene_name=z.name)
        else:
            build_glb(z.mesh, str(zones_dir / f"{z.name}.glb"), scene_name=z.name,
                      unlit=unlit)
        if z.collision_aabbs:
            write_collision_glb(z.collision_aabbs,
                                str(zones_dir / f"{z.name}.collision.glb"))
    scenes_dir = root / "assets" / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    with open(scenes_dir / f"{z.name}.json", "w") as f:
        json.dump(z.zone_json(), f, indent=2)
        f.write("\n")
    print(f"{z.name}: {z.tri_count()} tris, {len(z.collision_aabbs)} "
          f"colliders, {len(z.jump_pads)} pads, {len(z.spawn_points)} spawns")


# ---------------------------------------------------------------- wall art
#
# Paintings/posters are authored in LOCAL space facing -Z (origin at the
# canvas centre) and hung with `hang_art`, which rotates them onto a wall.
# Flush wall decor needs no collider (same as the GRU sign): it protrudes
# less than the wall's own collision slab tolerance.

from pyasset import cylinder, cone, icosphere  # noqa: E402
from pyasset.builder import compose, rotate_x, rotate_y, translate  # noqa: E402
import math as _math  # noqa: E402


def framed(w: float, h: float, canvas=(0.92, 0.89, 0.82),
           frame=(0.32, 0.20, 0.10)) -> MeshBuilder:
    mb = MeshBuilder(name="art")
    mb.extend(box(size=(w + 0.28, h + 0.28, 0.12), color=frame, center=(0, 0, 0.02)))
    mb.extend(box(size=(w, h, 0.08), color=canvas, center=(0, 0, -0.05)))
    return mb


def _disc(mb: MeshBuilder, x, y, r, color, z=-0.12, h=0.05):
    mb.extend(cylinder(radius=r, height=h, segments=14, color=color),
              transform=compose(translate(x, y, z), rotate_x(_math.pi / 2)))


def art_minion_portrait() -> MeshBuilder:
    mb = framed(1.6, 2.0, canvas=(0.55, 0.72, 0.92))
    mb.extend(box(size=(1.0, 1.3, 0.05), color=P.MINION_YELLOW,
                  center=(0, -0.05, -0.12)))
    _disc(mb, 0, 0.45, 0.52, P.MINION_YELLOW)          # head
    _disc(mb, 0, 0.45, 0.26, (0.75, 0.76, 0.78), z=-0.15)  # goggle ring
    _disc(mb, 0, 0.45, 0.15, (0.96, 0.96, 0.92), z=-0.18)  # lens
    _disc(mb, 0, 0.45, 0.06, (0.35, 0.22, 0.10), z=-0.20)  # pupil
    mb.extend(box(size=(0.44, 0.08, 0.04), color=(0.30, 0.18, 0.10),
                  center=(0, 0.02, -0.18)))           # smile
    mb.extend(box(size=(1.0, 0.55, 0.05), color=P.PAJAMA_NAVY,
                  center=(0, -0.62, -0.14)))          # dungarees
    return mb


def art_banana() -> MeshBuilder:
    mb = framed(1.8, 1.3, canvas=(0.20, 0.16, 0.22))
    for i, a in enumerate((-0.5, 0.0, 0.5)):
        mb.extend(box(size=(0.62, 0.20, 0.05), color=(0.95, 0.82, 0.18)),
                  transform=compose(translate(a * 0.52, -0.12 + 0.20 * (1 - abs(a)),
                                              -0.12),
                                    rotate_y(0.0)))
    mb.extend(box(size=(0.10, 0.10, 0.05), color=(0.35, 0.22, 0.08),
                  center=(-0.80, 0.02, -0.13)))
    mb.extend(box(size=(0.10, 0.10, 0.05), color=(0.35, 0.22, 0.08),
                  center=(0.80, 0.02, -0.13)))
    return mb


def art_moon() -> MeshBuilder:
    mb = framed(1.7, 1.7, canvas=(0.07, 0.09, 0.18))
    _disc(mb, 0.05, 0.10, 0.52, (0.84, 0.82, 0.68))
    _disc(mb, -0.10, 0.22, 0.12, (0.72, 0.70, 0.56), z=-0.15)
    _disc(mb, 0.22, -0.08, 0.09, (0.72, 0.70, 0.56), z=-0.15)
    for sx, sy in ((-0.62, 0.6), (0.55, 0.62), (-0.5, -0.55), (0.62, -0.45)):
        mb.extend(box(size=(0.06, 0.06, 0.04), color=(0.95, 0.95, 0.85),
                      center=(sx, sy, -0.12)))
    return mb


def art_duck() -> MeshBuilder:
    mb = framed(1.5, 1.2, canvas=(0.62, 0.86, 0.92))
    _disc(mb, -0.08, -0.18, 0.34, (0.98, 0.85, 0.15))   # body
    _disc(mb, 0.22, 0.18, 0.20, (0.98, 0.85, 0.15))     # head
    mb.extend(box(size=(0.22, 0.08, 0.05), color=(0.95, 0.45, 0.10),
                  center=(0.42, 0.14, -0.13)))          # beak
    mb.extend(box(size=(0.05, 0.05, 0.04), color=(0.1, 0.1, 0.1),
                  center=(0.27, 0.25, -0.15)))          # eye
    return mb


def art_egg() -> MeshBuilder:
    mb = framed(1.4, 1.4, canvas=(0.30, 0.55, 0.45))
    _disc(mb, 0, -0.02, 0.46, (0.96, 0.95, 0.90))
    _disc(mb, 0.06, 0.04, 0.18, (0.99, 0.75, 0.10), z=-0.15)
    return mb


def art_trophy() -> MeshBuilder:
    mb = framed(1.4, 1.8, canvas=(0.28, 0.20, 0.30))
    mb.extend(cylinder(radius=0.26, height=0.42, segments=10,
                       color=(0.95, 0.78, 0.18), center=(0, 0.22, -0.14)))
    mb.extend(box(size=(0.34, 0.10, 0.05), color=(0.95, 0.78, 0.18),
                  center=(0, -0.06, -0.14)))
    mb.extend(box(size=(0.46, 0.14, 0.05), color=(0.40, 0.26, 0.12),
                  center=(0, -0.42, -0.13)))
    mb.extend(box(size=(0.55, 0.12, 0.04), color=(0.85, 0.20, 0.15),
                  center=(0, 0.62, -0.12)))            # ribbon
    return mb


def hang_art(zb, art: MeshBuilder, x: float, y: float, z: float, facing: str):
    """Mount local -Z-facing art onto a wall. facing = direction the art
    looks INTO the room: one of '+x', '-x', '+z', '-z'."""
    angle = {"-z": 0.0, "+x": -_math.pi / 2, "-x": _math.pi / 2,
             "+z": _math.pi}[facing]
    zb.mesh.extend(art, transform=compose(translate(x, y, z), rotate_y(angle)))
