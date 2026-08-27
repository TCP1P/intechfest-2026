"""Minimal procedural glTF pipeline for minions-in-16k.

The client renders at 160x100 then upscales, so every surface becomes a handful
of pixels on screen. That means:
  - Vertex colors beat textures (saves bytes, looks the same at that res).
  - Chunky, readable silhouettes matter more than geometric detail.
  - Everything lives in MATERIALs per-zone so the shader is uniform.

Public API:
  MeshBuilder  -- accumulates triangles with per-vertex colors.
  build_glb    -- write a single-mesh visual GLB.
  build_scene_glb -- write a multi-node scene (visual).
  write_collision_glb -- write a flat list of AABBs as a single mesh.
"""

from .builder import MeshBuilder, Node
from .write import build_glb, build_scene_glb, write_collision_glb
from .prims import (
    box, cylinder, icosphere, capsule, cone, tube, plane, torus, extrude_poly,
)
from .color import hex_to_rgb, rgb

__all__ = [
    "MeshBuilder",
    "Node",
    "build_glb",
    "build_scene_glb",
    "write_collision_glb",
    "box",
    "cylinder",
    "icosphere",
    "capsule",
    "cone",
    "tube",
    "plane",
    "torus",
    "extrude_poly",
    "hex_to_rgb",
    "rgb",
]
