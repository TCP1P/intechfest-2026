"""Build a standalone Silo Lab v2 blockout GLB for browser review.

This intentionally does not wire the map into the game yet. The output is a
visual/scale prototype plus prop metadata that can become the sim contract for
server-side prop physics.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from pyasset import MeshBuilder, box, cylinder, icosphere, torus, tube  # noqa: E402
from pyasset.builder import compose, rotate_y, rotate_z, translate  # noqa: E402
from pyasset.write import build_glb  # noqa: E402
from zones import palette as P  # noqa: E402

OUT_DIR = TOOLS / "previews" / "map_v2"
GLB_PATH = OUT_DIR / "silo_lab.glb"
META_PATH = OUT_DIR / "silo_lab_props.json"


F0 = 0.0
F1 = 4.0
F2 = 8.0


COLORS = {
    "floor": P.FLOOR_BASE,
    "floor_dark": P.FLOOR_DARK,
    "wall": P.WALL_BASE,
    "trim": P.WALL_TRIM,
    "steel": P.STEEL_DARK,
    "steel_light": P.STEEL_LIGHT,
    "yellow": P.GRU_YELLOW,
    "orange": P.GRU_ORANGE,
    "cyan": P.COIL_CYAN,
    "magenta": P.COIL_MAGENTA,
    "goop": P.GOOP_GREEN,
    "teal": P.TILE_TEAL,
    "pink": P.STALL_PINK,
    "cream": P.CAB_CREAM,
    "white": P.BALL_WHITE,
    "black": P.BALL_BLACK,
    "red": P.BALL_RED,
    "green": P.TURF_GREEN,
    "blue": P.DENIM_BLUE,
    "wood": P.BUNK_WOOD,
}


def add_box(mb: MeshBuilder, size, center, color, name="box"):
    mb.extend(box(size=size, center=center, color=color, name=name))


def add_rot_box(mb: MeshBuilder, size, center, yaw, color, name="box"):
    b = box(size=size, center=(0.0, 0.0, 0.0), color=color, name=name)
    mb.extend(b, transform=compose(translate(*center), rotate_y(yaw)))


def add_cylinder_x(mb: MeshBuilder, radius, length, center, color, segments=16, name="cylinder_x"):
    c = cylinder(radius=radius, height=length, segments=segments, color=color, center=(0.0, 0.0, 0.0), name=name)
    mb.extend(c, transform=compose(translate(*center), rotate_z(-math.pi / 2)))


def floor_slab(mb: MeshBuilder, x0, x1, z0, z1, y, color, name="floor"):
    add_box(
        mb,
        (x1 - x0, 0.24, z1 - z0),
        ((x0 + x1) * 0.5, y - 0.12, (z0 + z1) * 0.5),
        color,
        name,
    )


def wall_x(mb: MeshBuilder, x0, x1, z, y0, y1, color=COLORS["wall"]):
    add_box(mb, (x1 - x0, y1 - y0, 0.5), ((x0 + x1) * 0.5, (y0 + y1) * 0.5, z), color)
    add_box(mb, (x1 - x0, 0.22, 0.6), ((x0 + x1) * 0.5, y1 - 0.11, z), COLORS["trim"])


def wall_z(mb: MeshBuilder, z0, z1, x, y0, y1, color=COLORS["wall"]):
    add_box(mb, (0.5, y1 - y0, z1 - z0), (x, (y0 + y1) * 0.5, (z0 + z1) * 0.5), color)
    add_box(mb, (0.6, 0.22, z1 - z0), (x, y1 - 0.11, (z0 + z1) * 0.5), COLORS["trim"])


def rail_x(mb: MeshBuilder, x0, x1, z, y):
    add_box(mb, (x1 - x0, 0.16, 0.16), ((x0 + x1) * 0.5, y + 0.95, z), COLORS["yellow"])
    add_box(mb, (x1 - x0, 0.12, 0.12), ((x0 + x1) * 0.5, y + 0.45, z), COLORS["steel_light"])
    for x in frange(x0, x1, 4.0):
        add_box(mb, (0.14, 0.9, 0.14), (x, y + 0.45, z), COLORS["steel_light"])


def rail_z(mb: MeshBuilder, z0, z1, x, y):
    add_box(mb, (0.16, 0.16, z1 - z0), (x, y + 0.95, (z0 + z1) * 0.5), COLORS["yellow"])
    add_box(mb, (0.12, 0.12, z1 - z0), (x, y + 0.45, (z0 + z1) * 0.5), COLORS["steel_light"])
    for z in frange(z0, z1, 4.0):
        add_box(mb, (0.14, 0.9, 0.14), (x, y + 0.45, z), COLORS["steel_light"])


def frange(a: float, b: float, step: float):
    x = a
    while x <= b + 0.001:
        yield x
        x += step


def add_floor_tiles(mb: MeshBuilder, x0, x1, z0, z1, y, color_a, color_b, tile=4.0):
    nx = max(1, int((x1 - x0) / tile))
    nz = max(1, int((z1 - z0) / tile))
    sx = (x1 - x0) / nx
    sz = (z1 - z0) / nz
    for ix in range(nx):
        for iz in range(nz):
            color = color_a if (ix + iz) % 2 == 0 else color_b
            add_box(
                mb,
                (sx * 0.96, 0.04, sz * 0.96),
                (x0 + (ix + 0.5) * sx, y + 0.025, z0 + (iz + 0.5) * sz),
                color,
                "tile",
            )


def add_silo(mb: MeshBuilder):
    mb.extend(tube(inner=4.6, outer=5.8, height=14.0, segments=28, color=COLORS["steel"], center=(0.0, 7.0, 0.0)))
    for y, color in [(0.35, COLORS["yellow"]), (3.95, COLORS["cyan"]), (7.95, COLORS["magenta"]), (11.6, COLORS["yellow"])]:
        mb.extend(torus(radius=5.95, tube_radius=0.13, segments=28, sides=8, color=color), transform=translate(0.0, y, 0.0))
    for i in range(12):
        a = i * math.tau / 12.0
        x = math.cos(a) * 6.02
        z = math.sin(a) * 6.02
        add_rot_box(mb, (0.12, 11.2, 0.5), (x, 6.8, z), -a, COLORS["cyan"], "silo_window")
    mb.extend(cylinder(radius=3.0, height=0.28, segments=24, color=COLORS["yellow"], center=(0.0, 0.14, 0.0)))
    mb.extend(cylinder(radius=3.0, height=0.16, segments=24, color=COLORS["cyan"], center=(0.0, 4.08, 0.0)))


def add_room_shells(mb: MeshBuilder):
    # F0 outer walls with wide door gaps into the loop.
    wall_x(mb, -36, -18, -24, 0, 3.2)
    wall_x(mb, 18, 36, -24, 0, 3.2)
    wall_x(mb, -36, -18, 24, 0, 3.2)
    wall_x(mb, 18, 36, 24, 0, 3.2)
    wall_z(mb, -24, -8, -36, 0, 3.2)
    wall_z(mb, 8, 24, -36, 0, 3.2)
    wall_z(mb, -24, -8, 36, 0, 3.2)
    wall_z(mb, 8, 24, 36, 0, 3.2)

    # Upper room back walls.
    for y in (F1, F2):
        wall_x(mb, -34, -20, -23, y, y + 2.7)
        wall_x(mb, 20, 34, -23, y, y + 2.7)
        wall_x(mb, -34, -20, 23, y, y + 2.7)
        wall_x(mb, 20, 34, 23, y, y + 2.7)


def add_catwalks(mb: MeshBuilder):
    # F1 ring and rooms.
    for args in [
        (-24, 24, -15, -7, F1),
        (-24, 24, 7, 15, F1),
        (-24, -7, -15, 15, F1),
        (7, 24, -15, 15, F1),
        (-34, -21, -22, -11, F1),
        (21, 34, -22, -11, F1),
        (-34, -21, 11, 22, F1),
        (21, 34, 11, 22, F1),
    ]:
        floor_slab(mb, *args, COLORS["floor_dark"], "f1_slab")
    rail_x(mb, -24, 24, -15, F1)
    rail_x(mb, -24, 24, 15, F1)
    rail_z(mb, -15, 15, -24, F1)
    rail_z(mb, -15, 15, 24, F1)

    # F2 narrower gantry and observation nooks.
    for args in [
        (-27, 27, -2.0, 2.0, F2),
        (-2.0, 2.0, -17, 17, F2),
        (-12, 12, -22, -14, F2),
        (-12, 12, 14, 22, F2),
    ]:
        floor_slab(mb, *args, COLORS["steel"], "f2_slab")
    rail_x(mb, -27, 27, -2.0, F2)
    rail_x(mb, -27, 27, 2.0, F2)
    rail_z(mb, -17, 17, -2.0, F2)
    rail_z(mb, -17, 17, 2.0, F2)


def add_vertical_routes(mb: MeshBuilder):
    # Step stacks are intentionally chunky so the AABB collision version can
    # approximate them without introducing ramp math.
    for i in range(6):
        add_box(mb, (3.2, 0.35, 2.4), (-16 + i * 2.1, 0.18 + i * 0.66, -6.5), COLORS["yellow"], "stairs_f0_f1")
    for i in range(6):
        add_box(mb, (2.6, 0.35, 2.2), (12 + i * 1.8, 4.18 + i * 0.66, 5.8), COLORS["cyan"], "stairs_f1_f2")
    # Drop chute / slide visual.
    for i in range(12):
        a = 1.1 + i * 0.14
        y = 8.0 - i * 0.55
        r = 8.6
        add_rot_box(mb, (2.0, 0.22, 2.8), (math.cos(a) * r, y, math.sin(a) * r), -a, COLORS["magenta"], "slide_segment")


def add_props(mb: MeshBuilder):
    props = []

    def prop(id_, kind, room, pos, body, trigger, extra=None):
        entry = {
            "id": id_,
            "kind": kind,
            "room": room,
            "position": list(pos),
            "body": list(body),
            "trigger": list(trigger),
            "server_owned": True,
        }
        if extra:
            entry.update(extra)
        props.append(entry)

    # F0 sports: cannon and dynamic balls.
    add_box(mb, (5.8, 0.08, 8.0), (-27.0, 0.04, -16.0), COLORS["green"], "sports_turf")
    add_box(mb, (2.6, 1.1, 1.4), (-32.0, 0.65, -16.0), COLORS["red"], "ball_cannon")
    add_cylinder_x(mb, 0.45, 2.2, (-30.5, 0.95, -16.0), COLORS["black"], segments=12, name="ball_cannon_barrel")
    for n, z in enumerate([-18.0, -16.0, -14.0]):
        mb.extend(icosphere(radius=0.52, subdivisions=1, color=COLORS["white"], center=(-26.0 + n, 0.58, z)))
    prop(
        1,
        "BallCannon",
        "sports",
        (-32.0, 0.65, -16.0),
        (2.6, 1.1, 1.4),
        (4.0, 1.6, 4.0),
        {
            "physics": "spawns_dynamic_ball",
            "ball_radius": 0.52,
            "impulse": [10.0, 4.5, 0.0],
            "cooldown_ticks": 180,
        },
    )

    # F0 kitchen/freezer.
    add_box(mb, (6.2, 0.25, 2.4), (28.0, 0.13, -18.0), COLORS["steel_light"], "conveyor")
    for x in frange(25.3, 30.7, 1.0):
        add_box(mb, (0.12, 0.2, 2.5), (x, 0.35, -18.0), COLORS["black"], "conveyor_roller")
    add_box(mb, (3.6, 2.4, 2.0), (33.0, 1.2, -14.0), COLORS["cream"], "freezer")
    mb.extend(icosphere(radius=0.8, subdivisions=1, color=COLORS["cyan"], center=(31.0, 0.75, -13.3)))
    prop(2, "Conveyor", "kitchen", (28.0, 0.13, -18.0), (6.2, 0.25, 2.4), (6.6, 1.2, 3.0), {"impulse": [-3.0, 0.0, 0.0], "cooldown_ticks": 0})
    prop(3, "FreezeRayLeak", "kitchen", (31.0, 0.75, -13.3), (2.8, 0.5, 2.8), (4.0, 1.0, 4.0), {"effect": "low_friction_or_slow", "cooldown_ticks": 240})

    # F0 dorm: spring bed.
    add_box(mb, (5.0, 0.4, 2.2), (-28.0, 0.35, 16.5), COLORS["wood"], "bunk_frame")
    add_box(mb, (4.5, 0.35, 1.8), (-28.0, 0.75, 16.5), P.MATTRESS_CREAM, "spring_mattress")
    for x in [-29.6, -26.4]:
        for z in [15.7, 17.3]:
            add_box(mb, (0.18, 1.3, 0.18), (x, 0.75, z), COLORS["wood"], "bed_post")
    prop(4, "SpringBed", "dorm", (-28.0, 0.75, 16.5), (4.5, 0.35, 1.8), (5.0, 1.2, 2.4), {"impulse": [0.0, 10.5, 0.0], "cooldown_ticks": 90})

    # F0 bathrooms: hand dryer.
    add_box(mb, (1.0, 1.0, 1.2), (33.0, 1.4, 16.0), COLORS["white"], "hand_dryer")
    add_box(mb, (0.2, 0.35, 5.0), (31.2, 1.1, 16.0), COLORS["cyan"], "wind_stream")
    prop(5, "HandDryer", "bathrooms", (33.0, 1.4, 16.0), (1.0, 1.0, 1.2), (6.0, 2.0, 3.0), {"impulse": [-7.0, 2.5, 0.0], "cooldown_ticks": 45})

    # F1 props.
    add_box(mb, (2.6, 0.7, 2.0), (-28.0, F1 + 0.4, -16.5), COLORS["steel_light"], "rocket_console")
    mb.extend(cylinder(radius=0.45, height=0.28, segments=16, color=COLORS["red"], center=(-28.0, F1 + 0.88, -16.5)))
    prop(6, "RocketButton", "control", (-28.0, F1 + 0.88, -16.5), (2.6, 0.7, 2.0), (3.2, 1.2, 2.6), {"toggles": "silo_vents_or_elevator_route", "cooldown_ticks": 210})

    mb.extend(cylinder(radius=1.3, height=0.35, segments=18, color=COLORS["goop"], center=(27.5, F1 + 0.18, -16.0)))
    for y in [F1 + 0.8, F1 + 1.35]:
        mb.extend(icosphere(radius=0.34, subdivisions=0, color=COLORS["goop"], center=(27.4, y, -15.8)))
    prop(7, "GoopVent", "jelly_lab", (27.5, F1 + 0.18, -16.0), (2.6, 0.35, 2.6), (3.4, 1.0, 3.4), {"effect": "periodic_slow_or_damage", "cooldown_ticks": 120})

    add_box(mb, (0.22, 4.0, 0.22), (-28.0, F1 + 2.0, 16.0), COLORS["steel_light"], "claw_drop")
    add_box(mb, (5.0, 0.2, 0.25), (-28.0, F1 + 4.0, 16.0), COLORS["yellow"], "claw_rail")
    for dx in [-0.35, 0.0, 0.35]:
        add_rot_box(mb, (0.16, 1.0, 0.18), (-28.0 + dx, F1 + 0.9, 16.0), dx * 1.8, COLORS["yellow"], "claw_finger")
    prop(8, "ClawCrane", "storage", (-28.0, F1 + 2.0, 16.0), (5.0, 4.0, 2.0), (5.5, 2.0, 3.0), {"physics": "moving_kinematic_shover", "cooldown_ticks": 160})

    for z in [14.7, 17.3]:
        add_cylinder_x(mb, 0.75, 0.35, (28.0, F1 + 1.2, z), COLORS["cyan"], segments=16, name="fan")
    prop(9, "FanTunnel", "laundry", (28.0, F1 + 1.2, 16.0), (1.0, 1.5, 4.0), (6.0, 2.0, 6.0), {"impulse": [-5.0, 1.0, 0.0], "cooldown_ticks": 0})

    # F2 observation prop.
    add_box(mb, (4.4, 0.18, 2.4), (0.0, F2 + 0.1, -17.6), COLORS["blue"], "blueprint_table")
    add_box(mb, (3.8, 0.05, 1.8), (0.0, F2 + 0.24, -17.6), COLORS["cyan"], "blueprint")

    # Central elevator.
    prop(10, "Elevator", "central_silo", (0.0, 0.14, 0.0), (6.0, 0.28, 6.0), (5.5, 2.0, 5.5), {"path_y": [0.14, 4.08, 8.08], "cooldown_ticks": 300})

    return props


def add_spawn_markers(mb: MeshBuilder):
    for x, y, z in [
        (-18, F0, -18),
        (18, F0, -18),
        (-18, F0, 18),
        (18, F0, 18),
        (-20, F1, -8),
        (20, F1, 8),
        (0, F2, -14),
        (0, F2, 14),
    ]:
        mb.extend(icosphere(radius=0.42, subdivisions=1, color=P.MINION_YELLOW, center=(x, y + 0.5, z)))


def build():
    mb = MeshBuilder(name="silo_lab_v2")
    floor_slab(mb, -36, 36, -24, 24, F0, COLORS["floor"], "f0_floor")
    add_floor_tiles(mb, -36, 36, -24, 24, F0, COLORS["floor"], COLORS["floor_dark"], tile=4.0)
    add_room_shells(mb)
    add_silo(mb)
    add_catwalks(mb)
    add_vertical_routes(mb)
    props = add_props(mb)
    add_spawn_markers(mb)
    return mb, props


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mb, props = build()
    build_glb(mb, str(GLB_PATH), scene_name="silo_lab_v2")
    META_PATH.write_text(json.dumps({"name": "Silo Lab v2 prototype", "props": props}, indent=2) + "\n")
    print(f"wrote {GLB_PATH.relative_to(ROOT)} ({mb.tri_count()} tris)")
    print(f"wrote {META_PATH.relative_to(ROOT)} ({len(props)} props)")


if __name__ == "__main__":
    main()
