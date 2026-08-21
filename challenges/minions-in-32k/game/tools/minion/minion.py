"""Minion character — yellow capsule, denim overalls, goggles, gun.

Rigged with a small flat skeleton (one bone per visible limb) using rigid
skinning — each vertex binds to exactly one joint with weight 1. That's all
the animation system needs for this chunky style and the joint count stays
tiny, which keeps the GLB small.

Joint layout (Y-up, forward = -Z, right = +X):
    0  root      (at ground, y=0)
    1  hips      (parent: root)     — pelvis, y~=0.7; drives body capsule + overalls
    2  spine     (parent: hips)     — upper body pivot, y~=1.1
    3  head      (parent: spine)    — head + goggles + eyes
    4  arm_l     (parent: spine)    — left glove
    5  arm_r     (parent: spine)    — right glove + gun
    6  leg_l     (parent: hips)     — left boot
    7  leg_r     (parent: hips)     — right boot
    8  gun       (parent: arm_r)    — gun mesh, child of right arm

Skinning rule of thumb:
    body capsule -> spine
    head cap     -> head
    goggles/eyes -> head
    pajama strap & overalls upper -> spine
    overalls lower & hip band -> hips
    left arm geom  -> arm_l
    right arm geom -> arm_r
    boots          -> leg_l / leg_r
    gun mesh       -> gun

Height target: ~1.6m (classic minion proportion — 1.1m body + head cap ~0.3m + boots).

Output:
    assets/minion/minion.glb  (four scenes: classic + three variants)
    assets/minion/animations.json  (manifest)
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pygltflib as gltf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyasset import MeshBuilder, box, cylinder, icosphere, capsule, torus, cone
from pyasset.builder import Node, translate, rotate_y, rotate_x, rotate_z
from pyasset.write import write_skinned_variant_glb

from zones import palette as P


# --- Joint indices
J_ROOT = 0
J_HIPS = 1
J_SPINE = 2
J_HEAD = 3
J_ARM_L = 4
J_ARM_R = 5
J_LEG_L = 6
J_LEG_R = 7
J_GUN = 8

JOINT_NAMES = ["root", "hips", "spine", "head", "arm_l", "arm_r",
               "leg_l", "leg_r", "gun"]


@dataclass(frozen=True)
class MinionVariant:
    name: str
    scene_name: str
    body_color: tuple[float, float, float] = P.MINION_YELLOW
    denim_blue: tuple[float, float, float] = P.DENIM_BLUE
    denim_dark: tuple[float, float, float] = P.DENIM_DARK
    glove_color: tuple[float, float, float] = P.GLOVE_BLACK
    boot_color: tuple[float, float, float] = P.BOOT_BLACK
    hair_color: tuple[float, float, float] = (0.12, 0.1, 0.08)
    eye_count: int = 2
    pupil_colors: tuple[tuple[float, float, float], ...] = (P.PUPIL_BROWN,)
    hair_style: str = "tufts"
    accessory: str | None = None
    y_scale: float = 1.0


VARIANTS = [
    MinionVariant(name="classic", scene_name="classic"),
    MinionVariant(
        name="bob",
        scene_name="bob",
        denim_blue=(0.28, 0.58, 0.86),
        denim_dark=(0.16, 0.36, 0.64),
        pupil_colors=((0.27, 0.55, 0.22), P.PUPIL_BROWN),
        hair_style="bare",
        accessory="bear",
        y_scale=0.96,
    ),
    MinionVariant(
        name="kevin",
        scene_name="kevin",
        denim_blue=(0.32, 0.62, 0.92),
        denim_dark=(0.16, 0.38, 0.68),
        hair_style="kevin",
        accessory="hardhat",
        y_scale=1.04,
    ),
    MinionVariant(
        name="stuart",
        scene_name="stuart",
        eye_count=1,
        hair_style="sprout",
        accessory="beacon",
    ),
]

COMP_DTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16,
              5123: np.uint16, 5125: np.uint32, 5126: np.float32}
TYPE_COMPS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
              "MAT2": 4, "MAT3": 9, "MAT4": 16}


@dataclass
class Rig:
    # positions in rest pose (world space since all joints are in the same skeleton)
    positions: np.ndarray  # (N, 3)
    parents: list          # parent joint index, or -1 for root

    def world_rest(self):
        return self.positions.copy()


def build_rig() -> Rig:
    """Rest pose positions + parent chain."""
    P = np.zeros((len(JOINT_NAMES), 3), dtype=np.float32)
    P[J_ROOT] = [0, 0, 0]
    P[J_HIPS] = [0, 0.60, 0]
    P[J_SPINE] = [0, 1.10, 0]
    P[J_HEAD] = [0, 1.55, 0]
    P[J_ARM_L] = [0.55, 1.15, 0]
    P[J_ARM_R] = [-0.55, 1.15, 0]
    P[J_LEG_L] = [0.22, 0.35, 0]
    P[J_LEG_R] = [-0.22, 0.35, 0]
    P[J_GUN] = [-0.55, 1.05, -0.4]
    parents = [-1, J_ROOT, J_HIPS, J_SPINE, J_SPINE, J_SPINE,
               J_HIPS, J_HIPS, J_ARM_R]
    return Rig(positions=P, parents=parents)


def skinned_body(rig: Rig, variant: MinionVariant = VARIANTS[0]):
    """Build the minion, grouped by logical part.

    Returns a list of ``(group_name, merged_mesh, joints_arr, weights_arr)``
    — one entry per editable component (body, goggles, dungaree, …). The
    writer emits each as its own named primitive/material in the GLB, so
    the parts are individually selectable in Blender or any other glTF
    editor without the authoring pipeline fighting the tool.
    """
    groups: dict[str, list[tuple[MeshBuilder, int]]] = {}

    def add(group_name: str, mb: MeshBuilder, joint_idx: int):
        groups.setdefault(group_name, []).append((mb, joint_idx))

    # ========== BODY ==========
    # Yellow capsule — skinned to SPINE.
    body = capsule(radius=0.5, cyl_height=0.55, segments=18, rings=6,
                   color=variant.body_color,
                   center=(0.0, 1.2, 0.0), name="body")
    add("body", body, J_SPINE)

    # ========== HAIR ==========
    if variant.hair_style == "bare":
        hair_points = []
    elif variant.hair_style == "sprout":
        hair_points = [(0.0, 0.08, 0.09, 0.28)]
    elif variant.hair_style == "kevin":
        hair_points = [(-0.18, -0.02, 0.045, 0.30),
                       (-0.09, 0.04, 0.055, 0.36),
                       (0.0, 0.07, 0.060, 0.40),
                       (0.09, 0.04, 0.055, 0.36),
                       (0.18, -0.02, 0.045, 0.30)]
    elif variant.hair_style == "crest":
        hair_points = [(-0.10, -0.03, 0.055, 0.23),
                       (0.0, 0.02, 0.065, 0.27),
                       (0.10, -0.03, 0.055, 0.23)]
    else:
        hair_points = [(-0.1, -0.05, 0.07, 0.22),
                       (0.05, 0.0, 0.07, 0.22),
                       (-0.02, 0.08, 0.07, 0.22)]
    for dx, dz, radius, height in hair_points:
        tuft = cone(radius=radius, height=height, segments=6,
                    color=variant.hair_color,
                    center=(dx, 1.83, dz), name="hair")
        add("hair", tuft, J_HEAD)

    # ========== GOGGLES ==========
    # Strap wraps the head. One-eyed goggles need a tighter front gap so the
    # strap visibly meets the single frame.
    STRAP_DARK = (0.10, 0.10, 0.12)
    strap_gap = 55.0 if variant.eye_count == 1 else 70.0
    strap = _ring_band(y=1.62, radius=0.56, thickness=0.12,
                       color=STRAP_DARK, gap_deg=strap_gap)
    add("goggles", strap, J_HEAD)
    if variant.eye_count == 2:
        bridge = box(size=(0.14, 0.06, 0.08), color=STRAP_DARK,
                     center=(0.0, 1.62, -0.46), name="goggle_bridge")
        add("goggles", bridge, J_HEAD)

    # Silver rings + white lenses + brown pupils. Eye primitives are built
    # at origin, tipped so their axis points -Z, then translated into place.
    import numpy as np
    R_tip = np.eye(4, dtype=np.float32)
    R_tip[:3, :3] = np.array([[1, 0, 0],
                              [0, 0, 1],
                              [0, -1, 0]], dtype=np.float32)  # -90° around X

    def _eye_piece(mb, cx, cy, cz):
        T = np.eye(4, dtype=np.float32)
        T[:3, 3] = (cx, cy, cz)
        mb.transform_in_place(T @ R_tip)
        return mb

    eye_offsets = (0.0,) if variant.eye_count == 1 else (-0.22, 0.22)
    eye_radius = 0.22 if variant.eye_count == 1 else 0.17
    lens_radius = 0.18 if variant.eye_count == 1 else 0.14
    pupil_radius = 0.075 if variant.eye_count == 1 else 0.065
    for eye_idx, eye_x in enumerate(eye_offsets):
        ring = torus(radius=eye_radius, tube_radius=0.05, segments=16, sides=8,
                     color=P.GOGGLE_SILVER)
        add("goggles", _eye_piece(ring, eye_x, 1.62, -0.46), J_HEAD)
        lens = cylinder(radius=lens_radius, height=0.04, segments=14,
                        color=P.GOGGLE_GLASS, name="lens")
        add("goggles", _eye_piece(lens, eye_x, 1.62, -0.47), J_HEAD)
        pupil_color = variant.pupil_colors[min(eye_idx, len(variant.pupil_colors) - 1)]
        pupil = cylinder(radius=pupil_radius, height=0.05, segments=10,
                         color=pupil_color)
        add("goggles", _eye_piece(pupil, eye_x, 1.62, -0.49), J_HEAD)
        hl = icosphere(radius=0.025, subdivisions=0, color=(1.0, 1.0, 1.0),
                       center=(eye_x - 0.03, 1.65, -0.52))
        add("goggles", hl, J_HEAD)

    # ========== MOUTH ==========
    SMILE_DARK = (0.22, 0.08, 0.08)
    smile_arc_cx = 0.0
    smile_arc_cy = 1.33
    smile_radius = 0.11
    smile_z = -0.52
    smile_start_deg = 215.0
    smile_sweep_deg = 110.0
    smile_segments = 6
    for i in range(smile_segments):
        t = (i + 0.5) / smile_segments
        ang = math.radians(smile_start_deg + t * smile_sweep_deg)
        px = smile_arc_cx + math.cos(ang) * smile_radius
        py = smile_arc_cy + math.sin(ang) * smile_radius
        dot = box(size=(0.034, 0.028, 0.03), color=SMILE_DARK,
                  center=(px, py, smile_z), name=f"smile_{i}")
        add("mouth", dot, J_HEAD)

    # ========== DUNGAREE — PANTS ==========
    # Denim cylinder wrapping the hips from above the legs up to the waist,
    # plus a crotch band and leg-hole seams at the bottom.
    pants = cylinder(radius=0.55, height=0.52, segments=18,
                     color=variant.denim_blue,
                     center=(0, 0.68, 0.0), name="pants", caps=True)
    add("dungaree_pants", pants, J_HIPS)
    band = cylinder(radius=0.45, height=0.18, segments=12,
                    color=variant.denim_dark,
                    center=(0, 0.44, 0.0), caps=False)
    add("dungaree_pants", band, J_HIPS)
    for dx in (-0.22, 0.22):
        seam = torus(radius=0.14, tube_radius=0.03, segments=12, sides=6,
                     color=variant.denim_dark,
                     center=(dx, 0.40, 0))
        add("dungaree_pants", seam, J_HIPS)

    # ========== DUNGAREE — BELT ==========
    # Darker denim ring wrapping the body at the waist; the horizontal strap
    # tying the front bib to the back bib along the sides.
    belt = _ring_band(y=0.95, radius=0.56, thickness=0.08,
                      color=variant.denim_dark, gap_deg=0.0)
    add("dungaree_belt", belt, J_SPINE)

    # ========== DUNGAREE — BIB ==========
    # Flat front and back panels above the belt. Yellow face is visible
    # above the bib top between the two shoulder straps.
    bib_front = box(size=(0.52, 0.28, 0.04),
                    color=variant.denim_blue,
                    center=(0, 1.06, -0.56), name="bib_front")
    add("dungaree_bib", bib_front, J_SPINE)
    bib_back = box(size=(0.52, 0.28, 0.04),
                   color=variant.denim_blue,
                   center=(0, 1.06, 0.56), name="bib_back")
    add("dungaree_bib", bib_back, J_SPINE)

    # ========== DUNGAREE — SHOULDER STRAPS ==========
    for dx in (-0.14, 0.14):
        shoulder_x = dx * 1.55
        if variant.name == "classic":
            shoulder_front = (shoulder_x, 1.30, -0.40)
            shoulder_back = (shoulder_x, 1.30, 0.40)
            fstrap = _strap_between(
                (dx, 1.18, -0.58),
                shoulder_front,
                width=0.09,
                thickness=0.045,
                color=variant.denim_blue,
                name="strap_front",
            )
            add("dungaree_straps", fstrap, J_SPINE)
            bstrap = _strap_between(
                shoulder_back,
                (dx, 1.18, 0.58),
                width=0.09,
                thickness=0.045,
                color=variant.denim_blue,
                name="strap_back",
            )
            add("dungaree_straps", bstrap, J_SPINE)
            continue

        strap = _strap_path(
            [
                (dx, 1.18, -0.61),
                (dx, 1.23, -0.60),
                (shoulder_x, 1.42, -0.49),
                (shoulder_x, 1.57, -0.16),
                (shoulder_x, 1.57, 0.16),
                (shoulder_x, 1.42, 0.49),
                (dx, 1.23, 0.60),
                (dx, 1.18, 0.61),
            ],
            width=0.10,
            thickness=0.05,
            color=variant.denim_blue,
            name="suspender_strap",
        )
        add("dungaree_straps", strap, J_SPINE)

        front_reinforcement = _strap_between(
            (dx, 1.14, -0.625),
            (dx, 1.23, -0.625),
            width=0.055,
            thickness=0.018,
            color=variant.denim_dark,
            name="strap_buttonhole",
        )
        add("dungaree_straps", front_reinforcement, J_SPINE)

    # ========== DUNGAREE — POCKET ==========
    pocket = box(size=(0.22, 0.18, 0.02),
                 color=variant.denim_dark,
                 center=(0, 1.04, -0.59), name="pocket")
    add("dungaree_pocket", pocket, J_SPINE)
    stitch = box(size=(0.24, 0.015, 0.02),
                 color=(0.18, 0.42, 0.72),
                 center=(0, 1.13, -0.60))
    add("dungaree_pocket", stitch, J_SPINE)
    logo = box(size=(0.14, 0.14, 0.02),
               color=P.GRU_YELLOW,
               center=(0, 1.04, -0.60), name="g_logo")
    add("dungaree_pocket", logo, J_SPINE)
    gnotch = box(size=(0.06, 0.05, 0.02),
                 color=variant.denim_dark,
                 center=(0.02, 1.04, -0.61))
    add("dungaree_pocket", gnotch, J_SPINE)

    # ========== DUNGAREE — BUTTONS ==========
    # Silver buttons at the strap tops + yellow accents on the hips.
    for dx in (-0.14, 0.14):
        btn = icosphere(radius=0.035, subdivisions=0,
                        color=P.GOGGLE_SILVER,
                        center=(dx, 1.19, -0.59))
        add("dungaree_buttons", btn, J_SPINE)
        if variant.name != "classic":
            back_btn = icosphere(radius=0.035, subdivisions=0,
                                 color=P.GOGGLE_SILVER,
                                 center=(dx, 1.19, 0.59))
            add("dungaree_buttons", back_btn, J_SPINE)
    for dx in (-0.6, 0.6):
        side_btn = icosphere(radius=0.05, subdivisions=0,
                             color=P.GRU_YELLOW,
                             center=(dx, 0.95, 0))
        add("dungaree_buttons", side_btn, J_SPINE)

    # ========== LEGS ==========
    for side, jnt in ((0.22, J_LEG_L), (-0.22, J_LEG_R)):
        leg = cylinder(radius=0.13, height=0.2, segments=10,
                       color=variant.body_color,
                       center=(side, 0.38, 0.0))
        add("legs", leg, jnt)

    # ========== BOOTS ==========
    for side, jnt in ((0.22, J_LEG_L), (-0.22, J_LEG_R)):
        boot_body = cylinder(radius=0.18, height=0.35, segments=12,
                             color=variant.boot_color,
                             center=(side, 0.175, 0.02))
        add("boots", boot_body, jnt)
        toe = icosphere(radius=0.2, subdivisions=1, color=variant.boot_color,
                        center=(side, 0.15, -0.12))
        add("boots", toe, jnt)

    # ========== ARMS & HANDS ==========
    for side, jnt in ((0.55, J_ARM_L), (-0.55, J_ARM_R)):
        sleeve = cylinder(radius=0.12, height=0.32, segments=10,
                          color=variant.body_color,
                          center=(side, 0.95, 0.0))
        add("arms", sleeve, jnt)
        glove = box(size=(0.28, 0.18, 0.3),
                    color=variant.glove_color,
                    center=(side, 0.72, -0.05))
        add("hands", glove, jnt)
        thumb = box(size=(0.1, 0.1, 0.14), color=variant.glove_color,
                    center=(side + (0.14 if side > 0 else -0.14), 0.73, -0.1))
        add("hands", thumb, jnt)
        for i in range(3):
            fdx = -0.09 + i * 0.09
            finger = box(size=(0.07, 0.07, 0.12), color=variant.glove_color,
                         center=(side + fdx, 0.67, -0.18))
            add("hands", finger, jnt)

    # ========== GUN ==========
    gx, gy, gz = -0.55, 1.05, -0.4
    gb = box(size=(0.28, 0.22, 0.7),
             color=(0.18, 0.2, 0.23),
             center=(gx, gy - 0.1, gz - 0.25))
    add("gun", gb, J_GUN)
    barrel2 = MeshBuilder(name="barrel")
    src = cylinder(radius=0.08, height=0.5, segments=10,
                   color=(0.1, 0.12, 0.15))
    M = translate(gx, gy - 0.1, gz - 0.75) @ rotate_x(-math.pi / 2)
    barrel2.extend(src, transform=M)
    add("gun", barrel2, J_GUN)
    muzz = MeshBuilder(name="muzzle")
    src = cone(radius=0.1, height=0.15, segments=10, color=(0.9, 0.4, 0.1))
    M = translate(gx, gy - 0.1, gz - 1.08) @ rotate_x(-math.pi / 2)
    muzz.extend(src, transform=M)
    add("gun", muzz, J_GUN)
    grip = box(size=(0.12, 0.35, 0.18),
               color=(0.4, 0.25, 0.1),
               center=(gx, gy - 0.35, gz + 0.02))
    add("gun", grip, J_GUN)
    sight = box(size=(0.08, 0.1, 0.15),
                color=P.COIL_CYAN,
                center=(gx, gy + 0.08, gz - 0.5))
    add("gun", sight, J_GUN)
    drum = cylinder(radius=0.15, height=0.18, segments=10,
                    color=P.GRU_ORANGE,
                    center=(gx, gy + 0.02, gz + 0.05))
    add("gun", drum, J_GUN)

    # ========== PICKUP WEAPONS ==========
    # One group per carryable weapon, gripped at the rail's anchor and
    # skinned to J_GUN. build() splits these out into their own named
    # scene nodes so the client can show exactly the one being held.
    flam = MeshBuilder(name="weapon_flamer")
    flam.extend(box(size=(0.26, 0.2, 0.5), color=(0.18, 0.2, 0.23),
                    center=(gx, gy - 0.1, gz - 0.15)))
    src = cylinder(radius=0.10, height=0.5, segments=10, color=(0.1, 0.12, 0.15))
    flam.extend(src, transform=translate(gx, gy - 0.1, gz - 0.6) @ rotate_x(-math.pi / 2))
    src = cone(radius=0.15, height=0.2, segments=10, color=(0.85, 0.3, 0.08))
    flam.extend(src, transform=translate(gx, gy - 0.1, gz - 0.95) @ rotate_x(-math.pi / 2))
    flam.extend(cylinder(radius=0.09, height=0.32, segments=10,
                         color=(0.78, 0.12, 0.10),
                         center=(gx, gy + 0.13, gz + 0.06)))
    flam.extend(box(size=(0.12, 0.3, 0.16), color=(0.4, 0.25, 0.1),
                    center=(gx, gy - 0.33, gz + 0.02)))
    add("weapon_flamer", flam, J_GUN)

    shot = MeshBuilder(name="weapon_shotgun")
    for bx in (-0.055, 0.055):
        src = cylinder(radius=0.055, height=0.9, segments=10,
                       color=(0.12, 0.13, 0.16))
        shot.extend(src, transform=translate(gx + bx, gy - 0.06, gz - 0.5)
                    @ rotate_x(-math.pi / 2))
        src = cylinder(radius=0.068, height=0.08, segments=10,
                       color=(0.22, 0.24, 0.28))
        shot.extend(src, transform=translate(gx + bx, gy - 0.06, gz - 0.92)
                    @ rotate_x(-math.pi / 2))
    shot.extend(box(size=(0.22, 0.2, 0.3), color=(0.25, 0.27, 0.3),
                    center=(gx, gy - 0.08, gz - 0.02)))
    shot.extend(box(size=(0.16, 0.16, 0.42), color=(0.45, 0.27, 0.1),
                    center=(gx, gy - 0.14, gz + 0.3)))
    shot.extend(box(size=(0.14, 0.1, 0.26), color=(0.45, 0.27, 0.1),
                    center=(gx, gy - 0.2, gz - 0.55)))
    shot.extend(box(size=(0.12, 0.3, 0.16), color=(0.4, 0.25, 0.1),
                    center=(gx, gy - 0.32, gz + 0.05)))
    add("weapon_shotgun", shot, J_GUN)

    baz = MeshBuilder(name="weapon_bazooka")
    src = cylinder(radius=0.13, height=1.25, segments=12, color=(0.16, 0.3, 0.2))
    baz.extend(src, transform=translate(gx, gy, gz - 0.25) @ rotate_x(-math.pi / 2))
    src = cone(radius=0.17, height=0.22, segments=12, color=(0.12, 0.22, 0.15))
    baz.extend(src, transform=translate(gx, gy, gz + 0.42) @ rotate_x(math.pi / 2))
    src = cone(radius=0.09, height=0.16, segments=10, color=P.GRU_ORANGE)
    baz.extend(src, transform=translate(gx, gy, gz - 0.92) @ rotate_x(-math.pi / 2))
    baz.extend(box(size=(0.07, 0.1, 0.14), color=P.COIL_CYAN,
                   center=(gx, gy + 0.17, gz - 0.3)))
    baz.extend(box(size=(0.12, 0.3, 0.16), color=(0.4, 0.25, 0.1),
                   center=(gx, gy - 0.32, gz - 0.05)))
    add("weapon_bazooka", baz, J_GUN)

    # ========== VARIANT ACCESSORIES ==========
    if variant.accessory == "bear":
        bear = MeshBuilder(name="bear")
        bear.extend(icosphere(radius=0.11, subdivisions=0, color=(0.50, 0.30, 0.14),
                              center=(0.77, 0.72, -0.18)))
        bear.extend(icosphere(radius=0.075, subdivisions=0, color=(0.56, 0.34, 0.18),
                              center=(0.77, 0.84, -0.18)))
        for ex in (0.72, 0.82):
            bear.extend(icosphere(radius=0.028, subdivisions=0, color=(0.42, 0.24, 0.12),
                                  center=(ex, 0.90, -0.18)))
        for lx in (0.68, 0.86):
            bear.extend(icosphere(radius=0.045, subdivisions=0, color=(0.42, 0.24, 0.12),
                                  center=(lx, 0.68, -0.18)))
        add("accessories", bear, J_ARM_L)
    elif variant.accessory == "hardhat":
        brim = cylinder(radius=0.42, height=0.055, segments=18,
                        color=P.GRU_YELLOW,
                        center=(0.0, 1.78, -0.02), name="hardhat_brim")
        add("accessories", brim, J_HEAD)
        crown = capsule(radius=0.34, cyl_height=0.08, segments=18, rings=3,
                        color=P.GRU_YELLOW,
                        center=(0.0, 1.82, -0.02), name="hardhat_crown")
        add("accessories", crown, J_HEAD)
        ridge = box(size=(0.10, 0.08, 0.60), color=P.GRU_ORANGE,
                    center=(0.0, 1.91, -0.02), name="hardhat_ridge")
        add("accessories", ridge, J_HEAD)
    elif variant.accessory == "beacon":
        base = cylinder(radius=0.13, height=0.06, segments=12,
                        color=(0.10, 0.12, 0.14),
                        center=(0.0, 1.86, 0.0), name="beacon_base")
        add("accessories", base, J_HEAD)
        dome = icosphere(radius=0.12, subdivisions=1,
                         color=(0.95, 0.08, 0.06),
                         center=(0.0, 1.94, 0.0), name="beacon_dome")
        add("accessories", dome, J_HEAD)

    # Merge per-group so each group has its own continuous vertex buffer.
    result: list[tuple[str, MeshBuilder, np.ndarray, np.ndarray]] = []
    for gname, entries in groups.items():
        merged = MeshBuilder(name=gname)
        joints: list[tuple[int, int, int, int]] = []
        weights: list[tuple[float, float, float, float]] = []
        for mb, jnt in entries:
            vstart = merged.vcount()
            merged.extend(mb)
            vend = merged.vcount()
            for _ in range(vend - vstart):
                joints.append((jnt, 0, 0, 0))
                weights.append((1.0, 0.0, 0.0, 0.0))
        result.append((
            gname,
            merged,
            np.array(joints, dtype=np.uint8),
            np.array(weights, dtype=np.float32),
        ))
    return result


def _arc_tube_yz(
    center: tuple,
    ring_radius: float,
    tube_radius: float,
    start_deg: float,
    sweep_deg: float,
    color,
    segments: int = 18,
    sides: int = 6,
    name: str = "arc",
) -> MeshBuilder:
    """A partial torus lying in the YZ plane (tube axis is X). Used for the
    over-shoulder arc; endpoints are at (center + R*sin(start), R*cos(start))
    in YZ space."""
    mb = MeshBuilder(name=name)
    cx, cy, cz = center
    start = math.radians(start_deg)
    sweep = math.radians(sweep_deg)
    for i in range(segments):
        a0 = start + (i / segments) * sweep
        a1 = start + ((i + 1) / segments) * sweep
        for j in range(sides):
            b0 = (j / sides) * math.tau
            b1 = ((j + 1) / sides) * math.tau

            def v(a, b):
                r_ = ring_radius + tube_radius * math.cos(b)
                x = cx + tube_radius * math.sin(b)
                y = cy + r_ * math.sin(a)
                z = cz + r_ * math.cos(a)
                return (x, y, z)

            def n(a, b):
                return (math.sin(b), math.cos(b) * math.sin(a), math.cos(b) * math.cos(a))

            base = mb.vcount()
            mb.add_vertex(v(a0, b0), n(a0, b0), color)
            mb.add_vertex(v(a1, b0), n(a1, b0), color)
            mb.add_vertex(v(a1, b1), n(a1, b1), color)
            mb.add_vertex(v(a0, b1), n(a0, b1), color)
            mb.add_quad(base, base + 1, base + 2, base + 3)
    return mb


def _strap_between(
    p0: tuple[float, float, float],
    p1: tuple[float, float, float],
    width: float,
    thickness: float,
    color,
    name: str = "strap",
) -> MeshBuilder:
    """Flat-shaded rectangular strap from p0 to p1, with width along X."""
    x0, y0, z0 = p0
    x1, y1, z1 = p1
    dy = y1 - y0
    dz = z1 - z0
    length = math.sqrt(dy * dy + dz * dz) or 1.0
    ny = -dz / length * (thickness / 2)
    nz = dy / length * (thickness / 2)
    wx = width / 2
    verts = [
        (x0 - wx, y0 - ny, z0 - nz),
        (x0 + wx, y0 - ny, z0 - nz),
        (x0 + wx, y0 + ny, z0 + nz),
        (x0 - wx, y0 + ny, z0 + nz),
        (x1 - wx, y1 - ny, z1 - nz),
        (x1 + wx, y1 - ny, z1 - nz),
        (x1 + wx, y1 + ny, z1 + nz),
        (x1 - wx, y1 + ny, z1 + nz),
    ]
    faces = [
        (0, 1, 5, 4),
        (3, 7, 6, 2),
        (0, 4, 7, 3),
        (1, 2, 6, 5),
        (0, 3, 2, 1),
        (4, 5, 6, 7),
    ]
    mb = MeshBuilder(name=name)
    for i0, i1, i2, i3 in faces:
        p_a = np.array(verts[i0], dtype=np.float32)
        p_b = np.array(verts[i1], dtype=np.float32)
        p_c = np.array(verts[i2], dtype=np.float32)
        n = np.cross(p_b - p_a, p_c - p_a)
        n = n / (np.linalg.norm(n) + 1e-9)
        base = mb.vcount()
        for idx in (i0, i1, i2, i3):
            mb.add_vertex(verts[idx], n, color)
        mb.add_quad(base, base + 1, base + 2, base + 3)
    return mb


def _strap_path(
    points: list[tuple[float, float, float]],
    width: float,
    thickness: float,
    color,
    name: str = "strap_path",
) -> MeshBuilder:
    mb = MeshBuilder(name=name)
    for p0, p1 in zip(points, points[1:]):
        mb.extend(_strap_between(p0, p1, width, thickness, color, name=name))
    return mb


def _ring_band(
    y: float,
    radius: float,
    thickness: float,
    color,
    *,
    gap_deg: float = 0.0,
) -> MeshBuilder:
    """Horizontal band at height y. If gap_deg > 0 an arc is cut out of the
    front (centered on -Z), so e.g. the goggle strap wraps sides+back and
    leaves the face unblocked. Uses cylinder's x=cos(a)r, z=sin(a)r
    convention; -Z (front of head) sits at a = -90°."""
    import math as _m
    mb = MeshBuilder(name="band")
    hy = thickness / 2
    segments = 22
    full = _m.tau
    gap = _m.radians(gap_deg)
    sweep = full - gap
    start = _m.radians(-90.0) + gap / 2.0  # just past the gap, going CCW
    for i in range(segments):
        a0 = start + (i / segments) * sweep
        a1 = start + ((i + 1) / segments) * sweep
        x0, z0 = _m.cos(a0) * radius, _m.sin(a0) * radius
        x1, z1 = _m.cos(a1) * radius, _m.sin(a1) * radius
        nx, nz = _m.cos((a0 + a1) / 2), _m.sin((a0 + a1) / 2)
        nrm = (nx, 0, nz)
        base = mb.vcount()
        mb.add_vertex((x0, y - hy, z0), nrm, color)
        mb.add_vertex((x1, y - hy, z1), nrm, color)
        mb.add_vertex((x1, y + hy, z1), nrm, color)
        mb.add_vertex((x0, y + hy, z0), nrm, color)
        mb.add_quad(base, base + 1, base + 2, base + 3)
    return mb


def _accessor_array(doc: gltf.GLTF2, blob: bytes, idx: int) -> np.ndarray:
    accessor = doc.accessors[idx]
    view = doc.bufferViews[accessor.bufferView]
    dtype = COMP_DTYPE[accessor.componentType]
    comps = TYPE_COMPS[accessor.type]
    offset = (view.byteOffset or 0) + (accessor.byteOffset or 0)
    arr = np.frombuffer(blob, dtype=dtype, count=accessor.count * comps, offset=offset)
    return arr.reshape(-1, comps) if comps > 1 else arr.copy()


def _strip_blender_suffix(name: str | None) -> str:
    if name is None:
        return ""
    base = name.rsplit(".", 1)
    if len(base) == 2 and base[1].isdigit():
        return base[0]
    return name


def _mesh_from_arrays(name: str,
                      positions: np.ndarray,
                      normals: np.ndarray,
                      colors: np.ndarray | None,
                      indices: np.ndarray) -> MeshBuilder:
    if colors is None:
        colors = np.ones((len(positions), 4), dtype=np.float32)
    elif colors.shape[1] == 3:
        colors = np.hstack([
            colors,
            np.ones((len(colors), 1), dtype=colors.dtype),
        ])
    if colors.dtype != np.float32:
        colors = colors.astype(np.float32)
    mb = MeshBuilder(name=name)
    mb.positions = [tuple(v) for v in positions.astype(np.float32)]
    mb.normals = [tuple(v) for v in normals.astype(np.float32)]
    mb.colors = [tuple(v) for v in colors[:, :4].astype(np.float32)]
    mb.indices = list(indices.astype(np.uint32).reshape(-1))
    return mb


def _modified_group_mesh(group_name: str, variant: MinionVariant) -> MeshBuilder | None:
    """Load one polished group from minion-modified.glb and recolor denim."""
    mod_path = Path(__file__).resolve().parents[2] / "assets" / "minion" / "minion-modified.glb"
    if not mod_path.exists():
        return None

    doc = gltf.GLTF2().load(str(mod_path))
    blob = doc.binary_blob()
    for prim in doc.meshes[0].primitives:
        if _strip_blender_suffix(doc.materials[prim.material].name) != group_name:
            continue
        positions = _accessor_array(doc, blob, prim.attributes.POSITION)
        normals = _accessor_array(doc, blob, prim.attributes.NORMAL)
        color_attr = getattr(prim.attributes, "COLOR_0", None)
        colors = _accessor_array(doc, blob, color_attr) if color_attr is not None else None
        indices = _accessor_array(doc, blob, prim.indices)

        if colors is not None:
            if colors.shape[1] == 3:
                colors = np.hstack([
                    colors,
                    np.ones((len(colors), 1), dtype=colors.dtype),
                ])
            colors = colors.astype(np.float32)
            denim_blue = np.array((*P.DENIM_BLUE, 1.0), dtype=np.float32)
            denim_dark = np.array((*P.DENIM_DARK, 1.0), dtype=np.float32)
            blue_mask = np.linalg.norm(colors - denim_blue, axis=1) < 0.02
            dark_mask = np.linalg.norm(colors - denim_dark, axis=1) < 0.02
            colors[blue_mask] = np.array((*variant.denim_blue, 1.0), dtype=np.float32)
            colors[dark_mask] = np.array((*variant.denim_dark, 1.0), dtype=np.float32)

        return _mesh_from_arrays(group_name, positions, normals, colors, indices)
    return None


def _rigid_group(name: str, mesh: MeshBuilder, joint_idx: int):
    joints = np.zeros((mesh.vcount(), 4), dtype=np.uint8)
    joints[:, 0] = joint_idx
    weights = np.zeros((mesh.vcount(), 4), dtype=np.float32)
    weights[:, 0] = 1.0
    return name, mesh, joints, weights


def _apply_modified_dungarees(
    part_groups: list[tuple[str, MeshBuilder, np.ndarray, np.ndarray]],
    variant: MinionVariant,
) -> list[tuple[str, MeshBuilder, np.ndarray, np.ndarray]]:
    homes = {
        "dungaree_pants": J_HIPS,
        "dungaree_belt": J_SPINE,
        "dungaree_bib": J_SPINE,
        "dungaree_straps": J_SPINE,
        "dungaree_pocket": J_SPINE,
        "dungaree_buttons": J_SPINE,
    }
    out = []
    for gname, mesh, joints, weights in part_groups:
        if gname not in homes:
            out.append((gname, mesh, joints, weights))
            continue
        replacement = _modified_group_mesh(gname, variant)
        if replacement is None:
            out.append((gname, mesh, joints, weights))
        else:
            out.append(_rigid_group(gname, replacement, homes[gname]))
    return out


def _apply_modified_classic(
    part_groups: list[tuple[str, MeshBuilder, np.ndarray, np.ndarray]],
) -> list[tuple[str, MeshBuilder, np.ndarray, np.ndarray]]:
    """Use the artist-polished classic mesh while keeping procedural skinning."""
    mod_path = Path(__file__).resolve().parents[2] / "assets" / "minion" / "minion-modified.glb"
    if not mod_path.exists():
        return part_groups

    doc = gltf.GLTF2().load(str(mod_path))
    blob = doc.binary_blob()
    mod_prims = {
        _strip_blender_suffix(doc.materials[prim.material].name): prim
        for prim in doc.meshes[0].primitives
    }

    patched = []
    for gname, mesh, joints, weights in part_groups:
        prim = mod_prims.get(gname)
        if prim is None:
            patched.append((gname, mesh, joints, weights))
            continue

        positions = _accessor_array(doc, blob, prim.attributes.POSITION)
        if len(positions) != len(joints):
            patched.append((gname, mesh, joints, weights))
            continue

        normals = _accessor_array(doc, blob, prim.attributes.NORMAL)
        color_attr = getattr(prim.attributes, "COLOR_0", None)
        colors = _accessor_array(doc, blob, color_attr) if color_attr is not None else None
        indices = _accessor_array(doc, blob, prim.indices)
        patched.append((
            gname,
            _mesh_from_arrays(gname, positions, normals, colors, indices),
            joints,
            weights,
        ))
    return patched


def _scale_part_groups(
    part_groups: list[tuple[str, MeshBuilder, np.ndarray, np.ndarray]],
    y_scale: float,
) -> list[tuple[str, MeshBuilder, np.ndarray, np.ndarray]]:
    if y_scale == 1.0:
        return part_groups
    scaled = []
    for gname, mesh, joints, weights in part_groups:
        out = MeshBuilder(name=mesh.name)
        out.positions = [
            (float(x), float(y * y_scale), float(z))
            for x, y, z in mesh.positions
        ]
        out.normals = list(mesh.normals)
        out.colors = list(mesh.colors)
        out.indices = list(mesh.indices)
        scaled.append((gname, out, joints, weights))
    return scaled


# ------------------------------------------------------------------------
# Animation authoring
# ------------------------------------------------------------------------

def _quat_rotate_axis(axis: np.ndarray, angle: float) -> np.ndarray:
    a = axis / (np.linalg.norm(axis) + 1e-9)
    s = math.sin(angle / 2)
    return np.array([a[0] * s, a[1] * s, a[2] * s, math.cos(angle / 2)],
                    dtype=np.float32)


QUAT_IDENTITY = np.array([0, 0, 0, 1], dtype=np.float32)


def _eul_to_quat(rx: float, ry: float, rz: float) -> np.ndarray:
    """XYZ euler (intrinsic) -> quaternion (x, y, z, w)."""
    cx, sx = math.cos(rx / 2), math.sin(rx / 2)
    cy, sy = math.cos(ry / 2), math.sin(ry / 2)
    cz, sz = math.cos(rz / 2), math.sin(rz / 2)
    x = sx * cy * cz + cx * sy * sz
    y = cx * sy * cz - sx * cy * sz
    z = cx * cy * sz + sx * sy * cz
    w = cx * cy * cz - sx * sy * sz
    return np.array([x, y, z, w], dtype=np.float32)


@dataclass
class Channel:
    joint: int
    path: str              # "rotation" | "translation" | "scale"
    times: np.ndarray      # (K,) float32
    values: np.ndarray     # (K, 3) for T/S or (K, 4) for R (xyzw)


def _clip(name: str, channels: list[Channel]):
    return {
        "name": name,
        "channels": [
            {"joint": c.joint, "path": c.path,
             "times": c.times, "values": c.values}
            for c in channels
        ],
    }


def anim_idle():
    """Gentle breathing: whole character bobs + head tilts.

    Bobs ROOT rather than SPINE so `dungaree_pants` (parented to HIPS)
    moves with the body and no waist gap opens. Also drives J_ROOT
    rotation back to identity — when transitioning out of the die clip
    (which leaves root tipped -π/2 around X), this resets the pose on
    respawn instead of leaving the minion face-down on the floor.
    """
    t = np.linspace(0.0, 2.0, 9, dtype=np.float32)
    root_y = 0.015 * np.sin(t * math.pi)
    tr = np.stack([np.zeros_like(root_y), root_y, np.zeros_like(root_y)], axis=1)
    hrot = np.stack([_eul_to_quat(math.sin(ti * math.pi) * 0.05,
                                   math.sin(ti * math.pi * 0.5) * 0.1,
                                   0) for ti in t])
    root_rot = np.tile(QUAT_IDENTITY, (len(t), 1))
    # Spine identity + root scale=1 act as explicit resets so transitions
    # out of Hit (flexed spine) or Crouch (squashed root scale) snap back
    # to standing upright instead of leaving the minion stuck mid-pose.
    spine_rot = np.tile(QUAT_IDENTITY, (len(t), 1))
    root_scale = np.tile(np.array([1.0, 1.0, 1.0], dtype=np.float32), (len(t), 1))
    return _clip("idle", [
        Channel(J_ROOT, "translation", t, tr),
        Channel(J_ROOT, "rotation", t, root_rot),
        Channel(J_ROOT, "scale", t, root_scale),
        Channel(J_SPINE, "rotation", t, spine_rot),
        Channel(J_HEAD, "rotation", t, hrot),
    ])


def anim_run():
    """Arms swing opposite to legs, bob up/down, slight forward lean."""
    t = np.linspace(0.0, 0.6, 9, dtype=np.float32)
    omega = 2 * math.pi / 0.6  # one cycle
    # hip bob
    hips_y = 0.60 + 0.06 * np.abs(np.sin(t * omega))
    hips_tr = np.stack([np.zeros_like(hips_y), hips_y, np.zeros_like(hips_y)], axis=1)
    # legs swing around X
    leg_l_rot = np.stack([_eul_to_quat(math.sin(ti * omega) * 0.6, 0, 0) for ti in t])
    leg_r_rot = np.stack([_eul_to_quat(-math.sin(ti * omega) * 0.6, 0, 0) for ti in t])
    # arms swing opposite
    arm_l_rot = np.stack([_eul_to_quat(-math.sin(ti * omega) * 0.5, 0, 0) for ti in t])
    arm_r_rot = np.stack([_eul_to_quat(math.sin(ti * omega) * 0.5, 0, 0) for ti in t])
    # Slight forward lean. glTF/Bevy is right-handed +Y up with -Z forward,
    # so positive rotation around X tips the head BACKWARD (toward +Z); a
    # forward run lean needs negative pitch.
    spine_rot = np.stack([_eul_to_quat(-0.2, 0, math.sin(ti * omega) * 0.05) for ti in t])
    root_rot = np.tile(QUAT_IDENTITY, (len(t), 1))
    # Reset root scale so transitioning Crouch→Run un-squashes the minion.
    root_scale = np.tile(np.array([1.0, 1.0, 1.0], dtype=np.float32), (len(t), 1))
    return _clip("run", [
        Channel(J_ROOT, "rotation", t, root_rot),
        Channel(J_ROOT, "scale", t, root_scale),
        Channel(J_HIPS, "translation", t, hips_tr),
        Channel(J_LEG_L, "rotation", t, leg_l_rot),
        Channel(J_LEG_R, "rotation", t, leg_r_rot),
        Channel(J_ARM_L, "rotation", t, arm_l_rot),
        Channel(J_ARM_R, "rotation", t, arm_r_rot),
        Channel(J_SPINE, "rotation", t, spine_rot),
    ])


def anim_strafe():
    """Sidestep — lateral weight shift, legs cross slightly."""
    t = np.linspace(0.0, 0.8, 9, dtype=np.float32)
    omega = 2 * math.pi / 0.8
    hips_x = 0.0 + 0.08 * np.sin(t * omega)
    hips_tr = np.stack([hips_x, np.full_like(hips_x, 0.62), np.zeros_like(hips_x)], axis=1)
    # Legs swing sideways (around Z axis)
    leg_l_rot = np.stack([_eul_to_quat(0, 0, math.sin(ti * omega) * 0.3) for ti in t])
    leg_r_rot = np.stack([_eul_to_quat(0, 0, -math.sin(ti * omega) * 0.3) for ti in t])
    # Arms out slightly for balance
    arm_l_rot = np.stack([_eul_to_quat(0, 0, -0.3) for _ in t])
    arm_r_rot = np.stack([_eul_to_quat(0, 0, 0.2) for _ in t])
    root_rot = np.tile(QUAT_IDENTITY, (len(t), 1))
    return _clip("strafe", [
        Channel(J_ROOT, "rotation", t, root_rot),
        Channel(J_HIPS, "translation", t, hips_tr),
        Channel(J_LEG_L, "rotation", t, leg_l_rot),
        Channel(J_LEG_R, "rotation", t, leg_r_rot),
        Channel(J_ARM_L, "rotation", t, arm_l_rot),
        Channel(J_ARM_R, "rotation", t, arm_r_rot),
    ])


def anim_jump():
    """Crouch → launch → apex → land. Short one-shot."""
    t = np.array([0.0, 0.15, 0.35, 0.55, 0.75], dtype=np.float32)
    hips_y = np.array([0.60, 0.48, 0.80, 0.90, 0.58], dtype=np.float32)
    hips_tr = np.stack([np.zeros_like(hips_y), hips_y, np.zeros_like(hips_y)], axis=1)
    # Legs tuck at apex
    leg_l_rot = np.stack([_eul_to_quat(-0.1, 0, 0),
                          _eul_to_quat(0.5, 0, 0),
                          _eul_to_quat(1.1, 0, 0),
                          _eul_to_quat(0.7, 0, 0),
                          _eul_to_quat(-0.1, 0, 0)])
    leg_r_rot = leg_l_rot.copy()
    # Arms windup -> up
    arm_l_rot = np.stack([_eul_to_quat(0.2, 0, -0.3),
                          _eul_to_quat(0.6, 0, -0.4),
                          _eul_to_quat(-1.2, 0, -0.2),
                          _eul_to_quat(-0.8, 0, -0.2),
                          _eul_to_quat(0.0, 0, -0.2)])
    arm_r_rot = arm_l_rot.copy()
    root_rot = np.tile(QUAT_IDENTITY, (len(t), 1))
    return _clip("jump", [
        Channel(J_ROOT, "rotation", t, root_rot),
        Channel(J_HIPS, "translation", t, hips_tr),
        Channel(J_LEG_L, "rotation", t, leg_l_rot),
        Channel(J_LEG_R, "rotation", t, leg_r_rot),
        Channel(J_ARM_L, "rotation", t, arm_l_rot),
        Channel(J_ARM_R, "rotation", t, arm_r_rot),
    ])


def anim_fire():
    """Quick recoil: gun barrel kicks up, spine jolts back, arm absorbs.

    Right-handed +Y up / -Z forward: positive X rotation tips a -Z vector
    toward +Y. The barrel sits along -Z relative to the gun joint, so a
    POSITIVE pitch on J_GUN raises the muzzle — recoil. Same for spine:
    positive pitch leans the upper body BACKWARD which is what a kick
    should look like.
    """
    t = np.array([0.0, 0.05, 0.15, 0.30], dtype=np.float32)
    arm_r_rot = np.stack([_eul_to_quat(0, 0, 0),
                          _eul_to_quat(-0.6, -0.2, 0),
                          _eul_to_quat(-0.3, -0.1, 0),
                          _eul_to_quat(0, 0, 0)])
    gun_rot = np.stack([_eul_to_quat(0, 0, 0),
                        _eul_to_quat(0.4, 0, 0),
                        _eul_to_quat(0.2, 0, 0),
                        _eul_to_quat(0, 0, 0)])
    spine_rot = np.stack([_eul_to_quat(0, 0, 0),
                          _eul_to_quat(0.15, 0, 0.08),
                          _eul_to_quat(0.05, 0, 0.04),
                          _eul_to_quat(0, 0, 0)])
    return _clip("fire", [
        Channel(J_ARM_R, "rotation", t, arm_r_rot),
        Channel(J_GUN, "rotation", t, gun_rot),
        Channel(J_SPINE, "rotation", t, spine_rot),
    ])


def anim_hit():
    """Flinch: spine bends back quickly, head snaps back, arms flail."""
    t = np.array([0.0, 0.08, 0.25, 0.45], dtype=np.float32)
    spine_rot = np.stack([_eul_to_quat(0, 0, 0),
                          _eul_to_quat(0.5, 0, 0.2),
                          _eul_to_quat(0.3, 0, 0.15),
                          _eul_to_quat(0, 0, 0)])
    head_rot = np.stack([_eul_to_quat(0, 0, 0),
                         _eul_to_quat(-0.5, 0, 0),
                         _eul_to_quat(-0.2, 0, 0),
                         _eul_to_quat(0, 0, 0)])
    arm_l_rot = np.stack([_eul_to_quat(0, 0, 0),
                          _eul_to_quat(-0.8, 0, -0.6),
                          _eul_to_quat(-0.4, 0, -0.3),
                          _eul_to_quat(0, 0, 0)])
    arm_r_rot = np.stack([_eul_to_quat(0, 0, 0),
                          _eul_to_quat(-0.8, 0, 0.6),
                          _eul_to_quat(-0.4, 0, 0.3),
                          _eul_to_quat(0, 0, 0)])
    return _clip("hit", [
        Channel(J_SPINE, "rotation", t, spine_rot),
        Channel(J_HEAD, "rotation", t, head_rot),
        Channel(J_ARM_L, "rotation", t, arm_l_rot),
        Channel(J_ARM_R, "rotation", t, arm_r_rot),
    ])


def anim_crouch():
    """Held crouch pose.

    Squashes the whole minion vertically by scaling J_ROOT.y to 0.57 —
    1.5 m eye * 0.57 ≈ 0.85 m (matches EYE_HEIGHT_CROUCH in the sim).
    Feet stay at y=0 because root sits at origin, so the scale compresses
    everything toward the floor uniformly instead of just bending the
    body cylinder down onto the pants.
    """
    t = np.array([0.0, 0.5], dtype=np.float32)
    n = len(t)
    root_scale = np.tile(np.array([1.0, 0.57, 1.0], dtype=np.float32), (n, 1))
    root_rot = np.tile(QUAT_IDENTITY, (n, 1))
    # A gentle forward lean reads as "I'm crouched" rather than "I shrank".
    spine_rot = np.tile(_eul_to_quat(-0.15, 0, 0), (n, 1))
    head_rot = np.tile(_eul_to_quat(0.10, 0, 0), (n, 1))
    arm_l_rot = np.tile(_eul_to_quat(-0.2, 0, -0.2), (n, 1))
    arm_r_rot = np.tile(_eul_to_quat(-0.2, 0, 0.2), (n, 1))
    return _clip("crouch", [
        Channel(J_ROOT, "rotation", t, root_rot),
        Channel(J_ROOT, "scale", t, root_scale),
        Channel(J_SPINE, "rotation", t, spine_rot),
        Channel(J_HEAD, "rotation", t, head_rot),
        Channel(J_ARM_L, "rotation", t, arm_l_rot),
        Channel(J_ARM_R, "rotation", t, arm_r_rot),
    ])


def anim_die():
    """Pratfall: minion topples over forward, then still."""
    t = np.array([0.0, 0.2, 0.45, 0.7, 1.1], dtype=np.float32)
    # root tips forward (rotate around X at root)
    root_rot = np.stack([_eul_to_quat(0, 0, 0),
                         _eul_to_quat(-0.3, 0, 0.1),
                         _eul_to_quat(-0.9, 0, 0.15),
                         _eul_to_quat(-math.pi / 2, 0, 0.2),
                         _eul_to_quat(-math.pi / 2, 0, 0.2)])
    root_tr = np.stack([np.array([0, 0, 0]),
                        np.array([0, 0.0, 0.1]),
                        np.array([0, -0.05, 0.3]),
                        np.array([0, -0.35, 0.7]),
                        np.array([0, -0.35, 0.7])]).astype(np.float32)
    arm_l_rot = np.stack([_eul_to_quat(0, 0, 0),
                          _eul_to_quat(-0.5, 0, -0.4),
                          _eul_to_quat(-1.2, 0, -0.6),
                          _eul_to_quat(-1.5, 0, -0.5),
                          _eul_to_quat(-1.5, 0, -0.5)])
    arm_r_rot = np.stack([_eul_to_quat(0, 0, 0),
                          _eul_to_quat(-0.5, 0, 0.4),
                          _eul_to_quat(-1.2, 0, 0.6),
                          _eul_to_quat(-1.5, 0, 0.5),
                          _eul_to_quat(-1.5, 0, 0.5)])
    return _clip("die", [
        Channel(J_ROOT, "rotation", t, root_rot),
        Channel(J_ROOT, "translation", t, root_tr),
        Channel(J_ARM_L, "rotation", t, arm_l_rot),
        Channel(J_ARM_R, "rotation", t, arm_r_rot),
    ])


# ------------------------------------------------------------------------
# Assembly
# ------------------------------------------------------------------------

def build(variant: MinionVariant = VARIANTS[0]):
    rig = build_rig()
    part_groups = skinned_body(rig, variant)
    if variant.name == "classic":
        part_groups = _apply_modified_classic(part_groups)
    else:
        part_groups = _apply_modified_dungarees(part_groups, variant)
        part_groups = _scale_part_groups(part_groups, variant.y_scale)

    # Carryable weapons become their own named scene nodes (sharing the
    # skin) so the client can toggle visibility to match the held weapon.
    # "gun" keeps its group/material name for the modified-mesh pipeline
    # but rides on the weapon_rail node.
    weapon_homes = {"gun": "weapon_rail", "weapon_flamer": "weapon_flamer",
                    "weapon_shotgun": "weapon_shotgun",
                    "weapon_bazooka": "weapon_bazooka"}
    body_groups, weapon_nodes = [], {}
    for grp in part_groups:
        node = weapon_homes.get(grp[0])
        if node is None:
            body_groups.append(grp)
        else:
            weapon_nodes.setdefault(node, []).append(grp)
    part_groups = body_groups

    # Compute inverse bind matrices: inverse of each joint's rest world transform.
    # With all-identity rotations, the rest transform is just a translation to
    # the joint position -> inverse is a translation by -position.
    N = len(JOINT_NAMES)
    ibm = np.zeros((N, 4, 4), dtype=np.float32)
    for i in range(N):
        ibm[i] = np.eye(4, dtype=np.float32)
        # world-space rest positions
        ibm[i, 0, 3] = -rig.positions[i, 0]
        ibm[i, 1, 3] = -rig.positions[i, 1]
        ibm[i, 2, 3] = -rig.positions[i, 2]

    # Build the node list: joints 0..8 (hierarchy via LOCAL translations: child - parent)
    # plus ONE extra node that holds the skinned mesh (to keep its own transform
    # clean). The mesh node parents to the scene root.
    # Compute local translations (child - parent)
    local_tr = np.zeros_like(rig.positions)
    for i in range(N):
        if rig.parents[i] < 0:
            local_tr[i] = rig.positions[i]
        else:
            local_tr[i] = rig.positions[i] - rig.positions[rig.parents[i]]

    # Joints become glTF nodes 0..N-1. Each holds a translation; rotation is
    # identity at rest (animations drive them).
    nodes = []
    # Place "children" indices at a fixed location — we'll build via parent->children map
    child_map = {i: [] for i in range(N)}
    for i, p in enumerate(rig.parents):
        if p >= 0:
            child_map[p].append(i)

    # Create N joint nodes
    for i, name in enumerate(JOINT_NAMES):
        nodes.append(Node(
            name=name,
            translation=(float(local_tr[i, 0]), float(local_tr[i, 1]),
                         float(local_tr[i, 2])),
            rotation=(0.0, 0.0, 0.0, 1.0),
            scale=(1.0, 1.0, 1.0),
            children=list(child_map[i]),  # plain ints for the GLB writer
        ))
    # Add a mesh node that is NOT in the joint hierarchy; it's the root of the
    # scene. It holds the skin reference.
    mesh_node_idx = len(nodes)
    nodes.append(Node(
        name="minion_mesh",
        mesh=None,  # filled by the GLB writer
        children=[0],  # the root joint
    ))

    # Re-root: scene has the mesh node, which has J_ROOT as its only child
    # in glTF. But children must be integer indices — we've set them that way.
    # The joint hierarchy lives under the mesh node in the scene graph.

    # Collect clips
    clips = [
        anim_idle(),
        anim_run(),
        anim_strafe(),
        anim_jump(),
        anim_fire(),
        anim_hit(),
        anim_die(),
        anim_crouch(),
    ]

    return {
        "name": variant.name,
        "scene_name": variant.scene_name,
        "nodes": nodes,
        "skin_joints": list(range(N)),
        "inverse_bind": ibm,
        "part_groups": part_groups,
        "weapon_nodes": [(n, weapon_nodes[n]) for n in
                         ("weapon_rail", "weapon_flamer", "weapon_shotgun",
                          "weapon_bazooka") if n in weapon_nodes],
        "animations": clips,
        "mesh_node_idx": mesh_node_idx,
    }


def main():
    out_dir = Path(__file__).resolve().parents[2] / "assets" / "minion"
    out_dir.mkdir(parents=True, exist_ok=True)

    variants = [build(variant) for variant in VARIANTS]

    write_skinned_variant_glb(
        variants=variants,
        out_path=str(out_dir / "minion.glb"),
    )

    # Write animations.json manifest
    clip_meta = []
    for clip in variants[0]["animations"]:
        # Duration = max time across all channels
        max_t = 0.0
        for ch in clip["channels"]:
            max_t = max(max_t, float(np.max(ch["times"])))
        loop = clip["name"] in ("idle", "run", "strafe", "crouch")
        clip_meta.append({
            "name": clip["name"],
            "duration": round(max_t, 3),
            "loop": loop,
        })
    manifest = {
        "variants": [
            {
                "name": variant["name"],
                "scene": i,
                "animation_offset": i * len(clip_meta),
            }
            for i, variant in enumerate(variants)
        ],
        "animations": clip_meta,
        "gun_mount_joint": "gun",
        "notes": "All clips author root at origin. Translation for locomotion is server-authoritative; these clips express pose only.",
    }
    with open(out_dir / "animations.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"minion: {len(variants)} variants, "
          f"{len(variants[0]['skin_joints'])} joints each, "
          f"{len(variants[0]['animations'])} clips per variant")
    for variant in variants:
        total_tris = sum(mesh.tri_count() for _, mesh, _, _ in variant["part_groups"])
        print(f"  {variant['name']}: {total_tris} tris across "
              f"{len(variant['part_groups'])} named parts")
        for gname, mesh, _, _ in variant["part_groups"]:
            print(f"    - {gname}: {mesh.tri_count()} tris")


if __name__ == "__main__":
    main()
