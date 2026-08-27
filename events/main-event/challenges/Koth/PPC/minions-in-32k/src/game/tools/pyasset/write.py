"""Low-level glTF/GLB assembly. Vertex colors only — no images, no UVs.

Each MeshBuilder becomes one mesh + one primitive + one material. A "scene"
can pack many meshes under named nodes.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from typing import Iterable, Sequence
import numpy as np

import pygltflib as g

from .builder import MeshBuilder, Node


def _pad4(n: int) -> int:
    return (4 - (n & 3)) & 3


@dataclass
class _Accum:
    """Accumulates binary data and emits bufferViews/accessors."""

    data: bytearray
    bufferViews: list
    accessors: list

    def add(self, arr: np.ndarray, target: int | None, component: int,
            accessor_type: str, with_bounds: bool = False):
        # Align to 4 bytes
        pad = _pad4(len(self.data))
        self.data.extend(b"\0" * pad)
        offset = len(self.data)
        raw = arr.tobytes()
        self.data.extend(raw)
        bv = g.BufferView(buffer=0, byteOffset=offset, byteLength=len(raw))
        if target is not None:
            bv.target = target
        bv_idx = len(self.bufferViews)
        self.bufferViews.append(bv)

        acc = g.Accessor(
            bufferView=bv_idx,
            byteOffset=0,
            componentType=component,
            count=int(arr.shape[0]),
            type=accessor_type,
        )
        if with_bounds:
            acc.min = arr.min(axis=0).tolist()
            acc.max = arr.max(axis=0).tolist()
        acc_idx = len(self.accessors)
        self.accessors.append(acc)
        return acc_idx


def _material(name: str, double_sided: bool = True, unlit: bool = False) -> g.Material:
    # Minimal material: use vertex colors, matte finish. Bevy respects vertex colors.
    # Default double-sided=true: our procedural primitives don't always have
    # consistent winding (and Blender exports can flip a few), so backface
    # culling otherwise punches holes through chunks of the minion / arena.
    mat = g.Material(
        name=name,
        pbrMetallicRoughness=g.PbrMetallicRoughness(
            baseColorFactor=[1.0, 1.0, 1.0, 1.0],
            metallicFactor=0.0,
            roughnessFactor=0.95,
        ),
        doubleSided=double_sided,
        alphaMode="OPAQUE",
    )
    if unlit:
        # KHR_materials_unlit — Bevy and three.js both honor it. Used for
        # the sky so scene lighting never dims the night backdrop.
        mat.extensions = {"KHR_materials_unlit": {}}
    return mat


def _add_mesh(gltf: g.GLTF2, acc: _Accum, mb: MeshBuilder,
              material_idx: int) -> int:
    pos, nor, col, idx = mb.to_arrays()
    if pos.size == 0:
        raise ValueError(f"empty mesh: {mb.name}")
    a_pos = acc.add(pos, g.ARRAY_BUFFER, g.FLOAT, "VEC3", with_bounds=True)
    a_nor = acc.add(nor, g.ARRAY_BUFFER, g.FLOAT, "VEC3")
    a_col = acc.add(col, g.ARRAY_BUFFER, g.FLOAT, "VEC4")
    a_idx = acc.add(idx, g.ELEMENT_ARRAY_BUFFER, g.UNSIGNED_INT, "SCALAR")

    prim = g.Primitive(
        attributes=g.Attributes(POSITION=a_pos, NORMAL=a_nor, COLOR_0=a_col),
        indices=a_idx,
        material=material_idx,
        mode=g.TRIANGLES,
    )
    mesh = g.Mesh(name=mb.name, primitives=[prim])
    mesh_idx = len(gltf.meshes)
    gltf.meshes.append(mesh)
    return mesh_idx


def _finalize(gltf: g.GLTF2, acc: _Accum, out_path: str):
    # Pad final buffer to multiple of 4 (required for GLB chunks)
    while len(acc.data) % 4 != 0:
        acc.data.append(0)

    gltf.buffers = [g.Buffer(byteLength=len(acc.data))]
    gltf.bufferViews = acc.bufferViews
    gltf.accessors = acc.accessors

    # Use set_binary_blob (pygltflib API): attach the binary blob and save as GLB
    gltf.set_binary_blob(bytes(acc.data))
    gltf.save_binary(out_path)


def build_glb(mb: MeshBuilder, out_path: str, scene_name: str = "scene",
              unlit: bool = False):
    """Single-mesh visual GLB."""
    gltf = g.GLTF2(asset=g.Asset(version="2.0", generator="minions-in-16k/pyasset"))
    gltf.meshes = []
    gltf.materials = [_material("default", unlit=unlit)]
    if unlit:
        gltf.extensionsUsed = ["KHR_materials_unlit"]
    gltf.nodes = []
    gltf.scenes = [g.Scene(name=scene_name, nodes=[0])]
    gltf.scene = 0
    acc = _Accum(data=bytearray(), bufferViews=[], accessors=[])
    mesh_idx = _add_mesh(gltf, acc, mb, material_idx=0)
    gltf.nodes = [g.Node(name=mb.name or "mesh", mesh=mesh_idx)]
    _finalize(gltf, acc, out_path)


def build_scene_glb(nodes: Sequence[Node], out_path: str, scene_name: str = "scene",
                    materials: Sequence[str] | None = None):
    """Multi-node scene where each node may carry a MeshBuilder. Each node's
    mesh is stored as its own glTF mesh + primitive. Each mesh uses its own
    material (index matches node order) so the renderer can batch cleanly.

    `materials` optionally names materials per node (for debugging); otherwise
    each node gets a generic material named after it.
    """
    gltf = g.GLTF2(asset=g.Asset(version="2.0", generator="minions-in-16k/pyasset"))
    gltf.meshes = []
    gltf.materials = []
    gltf.nodes = []
    acc = _Accum(data=bytearray(), bufferViews=[], accessors=[])

    def emit(node: Node) -> int:
        mesh_idx = None
        if node.mesh is not None and node.mesh.vcount() > 0:
            mat_name = f"{node.name}_mat"
            mat_idx = len(gltf.materials)
            gltf.materials.append(_material(mat_name))
            mesh_idx = _add_mesh(gltf, acc, node.mesh, material_idx=mat_idx)
        child_idxs = [emit(c) for c in node.children]
        n = g.Node(name=node.name, translation=list(node.translation),
                   rotation=list(node.rotation), scale=list(node.scale))
        if mesh_idx is not None:
            n.mesh = mesh_idx
        if child_idxs:
            n.children = child_idxs
        idx = len(gltf.nodes)
        gltf.nodes.append(n)
        return idx

    root_idxs = [emit(n) for n in nodes]
    gltf.scenes = [g.Scene(name=scene_name, nodes=root_idxs)]
    gltf.scene = 0
    _finalize(gltf, acc, out_path)


def write_collision_glb(aabbs: Iterable, out_path: str,
                        color=(0.5, 0.5, 0.5)):
    """Collision: write every AABB as a unit-colored box, one mesh total.

    `aabbs` is a sequence of (min_xyz, max_xyz) tuples. We also include a
    floor quad so the server has something to stand on if it ever reads this.
    """
    from .prims import box
    import numpy as np

    mb = MeshBuilder(name="collision")
    for (mn, mx) in aabbs:
        cx = (mn[0] + mx[0]) / 2
        cy = (mn[1] + mx[1]) / 2
        cz = (mn[2] + mx[2]) / 2
        sx = mx[0] - mn[0]
        sy = mx[1] - mn[1]
        sz = mx[2] - mn[2]
        b = box(size=(sx, sy, sz), color=color, center=(cx, cy, cz))
        mb.extend(b)
    build_glb(mb, out_path, scene_name="collision")


def write_skinned_glb(
    nodes: Sequence[Node],
    skin_joints: Sequence[int],
    inverse_bind: np.ndarray,
    part_groups: Sequence,  # list of (name: str, mesh: MeshBuilder, joints: ndarray, weights: ndarray)
    animations: list,  # list of dict {name, channels:[{joint, path, times, values}]}
    out_path: str,
    skin_node_idx: int,
    scene_name: str = "minion",
):
    """Emit a skinned mesh + skeleton + animations into a GLB.

    Each entry in `part_groups` becomes its own glTF Primitive with a named
    Material inside a single Mesh, so tools like Blender pick them up as
    distinct selectable material slots (goggles, dungaree_bib, gun, …)
    without the authoring pipeline pre-merging everything into an opaque
    blob.

    `nodes` is the flat node list in the order they'll appear in the glTF.
    `skin_joints` is the list of node indices that act as joints.
    `inverse_bind` is an (N, 4, 4) float32 array of inverseBindMatrices.
    `part_groups` is an ordered list of `(name, mesh, joints, weights)`:
      - `mesh` has POSITION/NORMAL/COLOR_0
      - `joints` is (V, 4) u8 indexing into `skin_joints`
      - `weights` is (V, 4) f32 summing to 1 per row
    `animations` is the list of clips; see minion script for format.
    `skin_node_idx` is the index of the node that carries the Mesh + Skin.
    """
    gltf = g.GLTF2(asset=g.Asset(version="2.0", generator="minions-in-16k/pyasset"))
    gltf.meshes = []
    gltf.materials = []
    gltf.nodes = []
    acc = _Accum(data=bytearray(), bufferViews=[], accessors=[])

    # The flat `nodes` list — we'll write them in order. Children references must
    # use the same indices.
    gltf.nodes = [g.Node(name=n.name) for n in nodes]
    for idx, n in enumerate(nodes):
        gn = gltf.nodes[idx]
        gn.translation = list(n.translation)
        gn.rotation = list(n.rotation)
        gn.scale = list(n.scale)
        if n.children:
            gn.children = list(n.children)

    # One primitive per logical group. Each primitive gets its own material
    # named for the group, so Blender / any glTF editor sees `goggles`,
    # `dungaree_bib`, `gun`, etc. as distinct slots.
    primitives: list[g.Primitive] = []
    for gname, mesh, joints_per_vertex, weights_per_vertex in part_groups:
        pos, nor, col, idx = mesh.to_arrays()
        a_pos = acc.add(pos, g.ARRAY_BUFFER, g.FLOAT, "VEC3", with_bounds=True)
        a_nor = acc.add(nor, g.ARRAY_BUFFER, g.FLOAT, "VEC3")
        a_col = acc.add(col, g.ARRAY_BUFFER, g.FLOAT, "VEC4")
        a_j = acc.add(joints_per_vertex.astype(np.uint8), g.ARRAY_BUFFER,
                      g.UNSIGNED_BYTE, "VEC4")
        a_w = acc.add(weights_per_vertex.astype(np.float32), g.ARRAY_BUFFER,
                      g.FLOAT, "VEC4")
        a_idx = acc.add(idx, g.ELEMENT_ARRAY_BUFFER, g.UNSIGNED_INT, "SCALAR")
        mat_idx = len(gltf.materials)
        gltf.materials.append(_material(gname))
        primitives.append(g.Primitive(
            attributes=g.Attributes(
                POSITION=a_pos, NORMAL=a_nor, COLOR_0=a_col,
                JOINTS_0=a_j, WEIGHTS_0=a_w,
            ),
            indices=a_idx,
            material=mat_idx,
            mode=g.TRIANGLES,
        ))
    mesh = g.Mesh(name="minion", primitives=primitives)
    gltf.meshes.append(mesh)
    gltf.nodes[skin_node_idx].mesh = 0

    # Skin
    # glTF wants inverseBindMatrices as column-major 4x4 floats
    ibm = np.ascontiguousarray(np.transpose(inverse_bind, (0, 2, 1)), dtype=np.float32)
    a_ibm = acc.add(ibm.reshape(-1, 16), None, g.FLOAT, "MAT4")
    # We need the MAT4 accessor count to equal number of joints, not 16*N
    acc.accessors[a_ibm].count = len(skin_joints)
    acc.accessors[a_ibm].min = None
    acc.accessors[a_ibm].max = None

    skin = g.Skin(name="minion_skin", joints=list(skin_joints),
                  inverseBindMatrices=a_ibm)
    gltf.skins = [skin]
    gltf.nodes[skin_node_idx].skin = 0

    # Animations
    gltf.animations = []
    for clip in animations:
        samplers = []
        channels = []
        for ch in clip["channels"]:
            times = np.ascontiguousarray(ch["times"], dtype=np.float32)
            vals = np.ascontiguousarray(ch["values"], dtype=np.float32)
            a_t = acc.add(times.reshape(-1, 1), None, g.FLOAT, "SCALAR",
                          with_bounds=True)
            # Rotation → VEC4 quat, translation/scale → VEC3
            if ch["path"] == "rotation":
                a_v = acc.add(vals, None, g.FLOAT, "VEC4")
            else:
                a_v = acc.add(vals, None, g.FLOAT, "VEC3")
            # scalar count is fine; vec accessor count should match row count
            acc.accessors[a_t].count = int(times.shape[0])
            acc.accessors[a_v].count = int(vals.shape[0])
            sampler = g.AnimationSampler(
                input=a_t, output=a_v, interpolation="LINEAR"
            )
            s_idx = len(samplers)
            samplers.append(sampler)
            channels.append(g.AnimationChannel(
                sampler=s_idx,
                target=g.AnimationChannelTarget(node=ch["joint"], path=ch["path"]),
            ))
        gltf.animations.append(g.Animation(
            name=clip["name"], samplers=samplers, channels=channels
        ))

    # Scene root = the mesh node (which parents the joint hierarchy).
    gltf.scenes = [g.Scene(name=scene_name, nodes=[skin_node_idx])]
    gltf.scene = 0
    _finalize(gltf, acc, out_path)


def write_skinned_variant_glb(
    variants: Sequence[dict],
    out_path: str,
):
    """Emit several skinned minion variants into one GLB.

    Each variant is a complete scene with its own mesh, skin, joint hierarchy,
    and copy of the animation clips. The first variant keeps the legacy
    material and animation names for compatibility with existing tooling.
    """
    gltf = g.GLTF2(asset=g.Asset(version="2.0", generator="minions-in-16k/pyasset"))
    gltf.meshes = []
    gltf.materials = []
    gltf.nodes = []
    gltf.skins = []
    gltf.animations = []
    gltf.scenes = []
    acc = _Accum(data=bytearray(), bufferViews=[], accessors=[])

    for variant_idx, variant in enumerate(variants):
        name = variant["name"]
        nodes = variant["nodes"]
        node_offset = len(gltf.nodes)

        for n in nodes:
            gn = g.Node(name=n.name)
            gn.translation = list(n.translation)
            gn.rotation = list(n.rotation)
            gn.scale = list(n.scale)
            if n.children:
                gn.children = [node_offset + int(c) for c in n.children]
            gltf.nodes.append(gn)

        def emit_primitives(groups) -> list[g.Primitive]:
            prims: list[g.Primitive] = []
            for gname, mesh, joints_per_vertex, weights_per_vertex in groups:
                pos, nor, col, idx = mesh.to_arrays()
                a_pos = acc.add(pos, g.ARRAY_BUFFER, g.FLOAT, "VEC3", with_bounds=True)
                a_nor = acc.add(nor, g.ARRAY_BUFFER, g.FLOAT, "VEC3")
                a_col = acc.add(col, g.ARRAY_BUFFER, g.FLOAT, "VEC4")
                a_j = acc.add(joints_per_vertex.astype(np.uint8), g.ARRAY_BUFFER,
                              g.UNSIGNED_BYTE, "VEC4")
                a_w = acc.add(weights_per_vertex.astype(np.float32), g.ARRAY_BUFFER,
                              g.FLOAT, "VEC4")
                a_idx = acc.add(idx, g.ELEMENT_ARRAY_BUFFER, g.UNSIGNED_INT, "SCALAR")

                mat_idx = len(gltf.materials)
                mat_name = gname if variant_idx == 0 else f"{name}_{gname}"
                gltf.materials.append(_material(mat_name))
                prims.append(g.Primitive(
                    attributes=g.Attributes(
                        POSITION=a_pos, NORMAL=a_nor, COLOR_0=a_col,
                        JOINTS_0=a_j, WEIGHTS_0=a_w,
                    ),
                    indices=a_idx,
                    material=mat_idx,
                    mode=g.TRIANGLES,
                ))
            return prims

        primitives = emit_primitives(variant["part_groups"])
        mesh_idx = len(gltf.meshes)
        gltf.meshes.append(g.Mesh(name=name, primitives=primitives))

        skin_node_idx = node_offset + int(variant["mesh_node_idx"])
        gltf.nodes[skin_node_idx].mesh = mesh_idx

        ibm = np.ascontiguousarray(
            np.transpose(variant["inverse_bind"], (0, 2, 1)),
            dtype=np.float32,
        )
        a_ibm = acc.add(ibm.reshape(-1, 16), None, g.FLOAT, "MAT4")
        skin_joints = [node_offset + int(j) for j in variant["skin_joints"]]
        acc.accessors[a_ibm].count = len(skin_joints)
        acc.accessors[a_ibm].min = None
        acc.accessors[a_ibm].max = None
        skin_idx = len(gltf.skins)
        gltf.skins.append(g.Skin(
            name=f"{name}_skin",
            joints=skin_joints,
            inverseBindMatrices=a_ibm,
        ))
        gltf.nodes[skin_node_idx].skin = skin_idx

        # Carryable weapons: separate named nodes sharing the skin, so the
        # client can match the visible weapon to the one actually held.
        weapon_node_indices: list[int] = []
        for wname, wgroups in variant.get("weapon_nodes", []):
            w_mesh_idx = len(gltf.meshes)
            gltf.meshes.append(g.Mesh(name=f"{name}_{wname}",
                                      primitives=emit_primitives(wgroups)))
            wn = g.Node(name=wname)
            wn.mesh = w_mesh_idx
            wn.skin = skin_idx
            weapon_node_indices.append(len(gltf.nodes))
            gltf.nodes.append(wn)

        for clip in variant["animations"]:
            samplers = []
            channels = []
            for ch in clip["channels"]:
                times = np.ascontiguousarray(ch["times"], dtype=np.float32)
                vals = np.ascontiguousarray(ch["values"], dtype=np.float32)
                a_t = acc.add(times.reshape(-1, 1), None, g.FLOAT, "SCALAR",
                              with_bounds=True)
                if ch["path"] == "rotation":
                    a_v = acc.add(vals, None, g.FLOAT, "VEC4")
                else:
                    a_v = acc.add(vals, None, g.FLOAT, "VEC3")
                acc.accessors[a_t].count = int(times.shape[0])
                acc.accessors[a_v].count = int(vals.shape[0])
                sampler_idx = len(samplers)
                samplers.append(g.AnimationSampler(
                    input=a_t,
                    output=a_v,
                    interpolation="LINEAR",
                ))
                channels.append(g.AnimationChannel(
                    sampler=sampler_idx,
                    target=g.AnimationChannelTarget(
                        node=node_offset + int(ch["joint"]),
                        path=ch["path"],
                    ),
                ))
            clip_name = clip["name"] if variant_idx == 0 else f"{name}_{clip['name']}"
            gltf.animations.append(g.Animation(
                name=clip_name,
                samplers=samplers,
                channels=channels,
            ))

        scene_name = variant.get("scene_name", name)
        gltf.scenes.append(g.Scene(name=scene_name,
                                   nodes=[skin_node_idx, *weapon_node_indices]))

    gltf.scene = 0
    _finalize(gltf, acc, out_path)
