"""Merge a Blender-exported minion-modified.glb back into minion.glb.

Blender's glTF export path drops the skeleton, the skin weights, and the
animations when you export only the mesh. This script patches the new
vertex positions / normals / colors back into the classic minion scene so
we keep the rig + animation clips while picking up the artist's mesh edits,
then regenerates the additional minion variant scenes.

Matches primitives by material base name (ignoring Blender's .002 suffix).
For groups whose vertex count matches, we reuse the original's
JOINTS_0/WEIGHTS_0 and indices. For groups whose vertex count changed
(the user actually remodelled that part), we keep the modified geometry
and reassign skinning by snapping each vertex to the nearest rest-pose
joint.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
import pygltflib as g

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
MINION_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(MINION_DIR))
from pyasset.builder import MeshBuilder  # noqa: E402
from pyasset.write import write_skinned_variant_glb  # noqa: E402
from pyasset.builder import Node  # noqa: E402
from minion import VARIANTS, build as build_variant  # noqa: E402

COMP_DTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16,
              5123: np.uint16, 5125: np.uint32, 5126: np.float32}
TYPE_COMPS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
              "MAT2": 4, "MAT3": 9, "MAT4": 16}

# Joint positions / parents as authored in minion.py build_rig().
# Must match or the skinning gets interpreted relative to a different rig.
JOINT_NAMES = ["root", "hips", "spine", "head", "arm_l", "arm_r",
               "leg_l", "leg_r", "gun"]
JOINT_POSITIONS = np.array([
    [0, 0, 0],            # root
    [0, 0.60, 0],         # hips
    [0, 1.10, 0],         # spine
    [0, 1.55, 0],         # head
    [0.55, 1.15, 0],      # arm_l
    [-0.55, 1.15, 0],     # arm_r
    [0.22, 0.35, 0],      # leg_l
    [-0.22, 0.35, 0],     # leg_r
    [-0.55, 1.05, -0.4],  # gun
], dtype=np.float32)
JOINT_PARENTS = [-1, 0, 1, 2, 2, 2, 1, 1, 5]

# Every modified part falls under one of these "home" joints if we have to
# auto-assign skinning. Keys are group names, values are indices into
# JOINT_NAMES. "split" groups get per-vertex nearest-joint assignment among
# the listed candidates.
GROUP_HOMES: dict[str, int | tuple[str, list[int]]] = {
    "body":               2,  # spine
    "hair":               3,  # head
    "goggles":            3,  # head
    "mouth":              3,  # head
    "dungaree_pants":     1,  # hips
    "dungaree_belt":      2,  # spine
    "dungaree_bib":       2,  # spine
    "dungaree_straps":    2,  # spine
    "dungaree_pocket":    2,  # spine
    "dungaree_buttons":   2,  # spine
    "legs":               ("nearest", [6, 7]),   # leg_l, leg_r
    "boots":              ("nearest", [6, 7]),
    "arms":               ("nearest", [4, 5]),   # arm_l, arm_r
    "hands":              ("nearest", [4, 5]),
    "gun":                8,  # gun
}


def accessor_array(gltf: g.GLTF2, blob: bytes, idx: int) -> np.ndarray:
    a = gltf.accessors[idx]
    bv = gltf.bufferViews[a.bufferView]
    dtype = COMP_DTYPE[a.componentType]
    comps = TYPE_COMPS[a.type]
    offset = (bv.byteOffset or 0) + (a.byteOffset or 0)
    length = a.count * comps
    arr = np.frombuffer(blob, dtype=dtype, count=length, offset=offset)
    return arr.reshape(-1, comps) if comps > 1 else arr.copy()


def strip_suffix(name: str) -> str:
    """Strip Blender's `.001`, `.002` etc. suffix that gets added on re-import."""
    if name is None:
        return ""
    base = name.rsplit(".", 1)
    if len(base) == 2 and base[1].isdigit():
        return base[0]
    return name


def build_mod_by_basename(mod: g.GLTF2) -> dict:
    out = {}
    mesh = mod.meshes[0]
    for prim in mesh.primitives:
        name = strip_suffix(mod.materials[prim.material].name)
        out[name] = prim
    return out


def nearest_joint_weights(positions: np.ndarray,
                          candidate_indices: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Per vertex, snap to nearest joint among `candidate_indices`.

    Returns (joints (V, 4) u8, weights (V, 4) f32) — single-influence rigid skinning.
    """
    candidates = JOINT_POSITIONS[candidate_indices]
    # (V, C) distances
    d = np.linalg.norm(positions[:, None, :] - candidates[None, :, :], axis=2)
    winner = np.argmin(d, axis=1)
    joints = np.zeros((len(positions), 4), dtype=np.uint8)
    joints[:, 0] = np.array(candidate_indices, dtype=np.uint8)[winner]
    weights = np.zeros((len(positions), 4), dtype=np.float32)
    weights[:, 0] = 1.0
    return joints, weights


def rigid_joint_weights(vcount: int, joint_idx: int) -> tuple[np.ndarray, np.ndarray]:
    j = np.zeros((vcount, 4), dtype=np.uint8)
    j[:, 0] = joint_idx
    w = np.zeros((vcount, 4), dtype=np.float32)
    w[:, 0] = 1.0
    return j, w


def build_nodes():
    """Rebuild the node hierarchy that the skinned GLB writer expects."""
    N = len(JOINT_NAMES)
    local_tr = JOINT_POSITIONS.copy()
    for i in range(N):
        if JOINT_PARENTS[i] >= 0:
            local_tr[i] = JOINT_POSITIONS[i] - JOINT_POSITIONS[JOINT_PARENTS[i]]
    child_map = {i: [] for i in range(N)}
    for i, p in enumerate(JOINT_PARENTS):
        if p >= 0:
            child_map[p].append(i)
    nodes = []
    for i, name in enumerate(JOINT_NAMES):
        nodes.append(Node(
            name=name,
            translation=(float(local_tr[i, 0]), float(local_tr[i, 1]),
                         float(local_tr[i, 2])),
            rotation=(0.0, 0.0, 0.0, 1.0),
            scale=(1.0, 1.0, 1.0),
            children=list(child_map[i]),
        ))
    mesh_node_idx = len(nodes)
    nodes.append(Node(name="minion_mesh", mesh=None, children=[0]))
    return nodes, mesh_node_idx


def read_animations(orig: g.GLTF2, blob: bytes, limit: int | None = None) -> list[dict]:
    """Re-extract animation clips from the original GLB in the format
    write_skinned_variant_glb expects."""
    clips = []
    for anim in (orig.animations or [])[:limit]:
        channels = []
        for ch in anim.channels:
            sampler = anim.samplers[ch.sampler]
            times = accessor_array(orig, blob, sampler.input).astype(np.float32)
            if times.ndim > 1:
                times = times[:, 0]
            vals = accessor_array(orig, blob, sampler.output).astype(np.float32)
            channels.append({
                "joint": ch.target.node,
                "path": ch.target.path,
                "times": times,
                "values": vals,
            })
        clips.append({"name": anim.name, "channels": channels})
    return clips


def extract_inverse_bind(orig: g.GLTF2, blob: bytes) -> np.ndarray:
    """Return (N, 4, 4) row-major inverse-bind matrices."""
    skin = orig.skins[0]
    ibm = accessor_array(orig, blob, skin.inverseBindMatrices).astype(np.float32)
    ibm = ibm.reshape(-1, 4, 4)
    # glTF stores column-major; convert to row-major for our writer
    return np.transpose(ibm, (0, 2, 1)).copy()


def mb_from_arrays(name, pos, nor, col, idx):
    mb = MeshBuilder(name=name)
    # make sure color is 4-channel
    if col is None:
        col = np.ones((len(pos), 4), dtype=np.float32)
    elif col.shape[1] == 3:
        col = np.hstack([col, np.ones((len(col), 1), dtype=np.float32)])
    if col.dtype != np.float32:
        if col.dtype == np.uint8:
            col = col.astype(np.float32) / 255.0
        elif col.dtype == np.uint16:
            col = col.astype(np.float32) / 65535.0
        else:
            col = col.astype(np.float32)
    mb.positions = [tuple(v) for v in pos.astype(np.float32)]
    mb.normals = [tuple(v) for v in nor.astype(np.float32)]
    mb.colors = [tuple(v) for v in col.astype(np.float32)]
    mb.indices = list(idx.astype(np.uint32).reshape(-1))
    return mb


def main(orig_path: str, mod_path: str, out_path: str):
    orig = g.GLTF2().load(orig_path)
    mod = g.GLTF2().load(mod_path)
    orig_blob = orig.binary_blob()
    mod_blob = mod.binary_blob()

    mod_prims = build_mod_by_basename(mod)

    part_groups = []
    for prim in orig.meshes[0].primitives:
        gname = strip_suffix(orig.materials[prim.material].name)
        mp = mod_prims.get(gname)
        if mp is None:
            print(f"  !! {gname}: missing in modified glb, keeping original", file=sys.stderr)
            # Fall through to copy original
            pos = accessor_array(orig, orig_blob, prim.attributes.POSITION)
            nor = accessor_array(orig, orig_blob, prim.attributes.NORMAL)
            col = accessor_array(orig, orig_blob, prim.attributes.COLOR_0)
            idx = accessor_array(orig, orig_blob, prim.indices).reshape(-1)
            joints = accessor_array(orig, orig_blob, prim.attributes.JOINTS_0)
            weights = accessor_array(orig, orig_blob, prim.attributes.WEIGHTS_0).astype(np.float32)
        else:
            m_pos = accessor_array(mod, mod_blob, mp.attributes.POSITION)
            m_nor = accessor_array(mod, mod_blob, mp.attributes.NORMAL)
            m_col_attr = getattr(mp.attributes, "COLOR_0", None)
            if m_col_attr is not None:
                m_col = accessor_array(mod, mod_blob, m_col_attr)
            else:
                # fall back to original's colors if count matches
                m_col = None
            m_idx = accessor_array(mod, mod_blob, mp.indices).reshape(-1)

            o_vcount = accessor_array(orig, orig_blob, prim.attributes.POSITION).shape[0]
            m_vcount = m_pos.shape[0]

            if m_vcount == o_vcount:
                # Same topology — keep the original skinning + indices, swap in
                # the modified positions/normals/colors.
                pos = m_pos
                nor = m_nor
                col = m_col if m_col is not None else accessor_array(orig, orig_blob, prim.attributes.COLOR_0)
                idx = accessor_array(orig, orig_blob, prim.indices).reshape(-1)
                joints = accessor_array(orig, orig_blob, prim.attributes.JOINTS_0)
                weights = accessor_array(orig, orig_blob, prim.attributes.WEIGHTS_0).astype(np.float32)
                note = "same topology, kept original skinning"
            else:
                # Topology changed — take modified geometry wholesale and
                # auto-assign skinning by nearest joint.
                pos = m_pos
                nor = m_nor
                col = m_col if m_col is not None else np.ones((m_vcount, 4), dtype=np.float32)
                idx = m_idx
                home = GROUP_HOMES.get(gname, 2)  # default to spine
                if isinstance(home, tuple) and home[0] == "nearest":
                    joints, weights = nearest_joint_weights(pos.astype(np.float32), home[1])
                else:
                    joints, weights = rigid_joint_weights(m_vcount, int(home))
                note = f"vertex count changed {o_vcount}→{m_vcount}, reassigned to {gname}'s joint"
            print(f"  {gname:22s}  verts={m_vcount:5d}  — {note}")

        mb = mb_from_arrays(gname, pos, nor, col[:, :4] if col.ndim == 2 else col,
                            idx)
        part_groups.append((gname, mb, joints.astype(np.uint8),
                            weights.astype(np.float32)))

    nodes, mesh_node_idx = build_nodes()
    ibm = extract_inverse_bind(orig, orig_blob)
    animations = read_animations(orig, orig_blob, limit=8)

    classic = {
        "name": VARIANTS[0].name,
        "scene_name": VARIANTS[0].scene_name,
        "nodes": nodes,
        "skin_joints": list(range(len(JOINT_NAMES))),
        "inverse_bind": ibm,
        "part_groups": part_groups,
        "animations": animations,
        "mesh_node_idx": mesh_node_idx,
    }
    generated_variants = [build_variant(variant) for variant in VARIANTS[1:]]
    write_skinned_variant_glb(
        variants=[classic, *generated_variants],
        out_path=out_path,
    )
    print(f"wrote {out_path}")


if __name__ == "__main__":
    orig = ROOT / "assets" / "minion" / "minion.glb"
    mod = ROOT / "assets" / "minion" / "minion-modified.glb"
    out = ROOT / "assets" / "minion" / "minion.glb"
    main(str(orig), str(mod), str(out))
