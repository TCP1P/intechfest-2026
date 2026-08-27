"""MeshBuilder: accumulate positions/normals/colors/indices for a mesh.

A single MeshBuilder becomes a single glTF primitive — one draw call. Per-vertex
COLOR_0 carries all surface color; no textures. One MATERIAL per builder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np


@dataclass
class MeshBuilder:
    name: str = "mesh"
    positions: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    colors: list = field(default_factory=list)
    indices: list = field(default_factory=list)

    def vcount(self) -> int:
        return len(self.positions)

    def add_vertex(self, p, n, c):
        self.positions.append((float(p[0]), float(p[1]), float(p[2])))
        self.normals.append((float(n[0]), float(n[1]), float(n[2])))
        self.colors.append((float(c[0]), float(c[1]), float(c[2]), 1.0))

    def add_tri(self, a: int, b: int, c: int):
        self.indices.extend([int(a), int(b), int(c)])

    def add_quad(self, a, b, c, d):
        self.indices.extend([int(a), int(b), int(c), int(a), int(c), int(d)])

    def extend(self, other: "MeshBuilder", transform=None):
        """Append `other`'s geometry, optionally applying a 4x4 transform."""
        base = self.vcount()
        if transform is None:
            for p in other.positions:
                self.positions.append(p)
            for n in other.normals:
                self.normals.append(n)
        else:
            M = np.asarray(transform, dtype=np.float32).reshape(4, 4)
            rot = M[:3, :3]
            for p in other.positions:
                v = M @ np.array([p[0], p[1], p[2], 1.0], dtype=np.float32)
                self.positions.append((float(v[0]), float(v[1]), float(v[2])))
            # normal matrix — assume uniform scale here (we only use TRS w/ uniform scale)
            for n in other.normals:
                nv = rot @ np.array([n[0], n[1], n[2]], dtype=np.float32)
                nrm = float(np.linalg.norm(nv))
                if nrm > 1e-8:
                    nv = nv / nrm
                self.normals.append((float(nv[0]), float(nv[1]), float(nv[2])))
        self.colors.extend(other.colors)
        for idx in other.indices:
            self.indices.append(idx + base)

    def to_arrays(self):
        pos = np.array(self.positions, dtype=np.float32)
        nor = np.array(self.normals, dtype=np.float32)
        col = np.array(self.colors, dtype=np.float32)
        idx = np.array(self.indices, dtype=np.uint32)
        return pos, nor, col, idx

    def tri_count(self) -> int:
        return len(self.indices) // 3

    def transform_in_place(self, transform):
        """Mutate this mesh by a 4x4 transform. Normals are re-rotated; translation only moves positions."""
        M = np.asarray(transform, dtype=np.float32).reshape(4, 4)
        rot = M[:3, :3]
        new_pos = []
        for p in self.positions:
            v = M @ np.array([p[0], p[1], p[2], 1.0], dtype=np.float32)
            new_pos.append((float(v[0]), float(v[1]), float(v[2])))
        self.positions = new_pos
        new_nor = []
        for n in self.normals:
            nv = rot @ np.array([n[0], n[1], n[2]], dtype=np.float32)
            mag = float(np.linalg.norm(nv))
            if mag > 1e-8:
                nv = nv / mag
            new_nor.append((float(nv[0]), float(nv[1]), float(nv[2])))
        self.normals = new_nor


@dataclass
class Node:
    name: str
    mesh: MeshBuilder | None = None
    translation: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    scale: tuple = (1.0, 1.0, 1.0)
    children: list = field(default_factory=list)


def translate(tx, ty, tz):
    M = np.eye(4, dtype=np.float32)
    M[0, 3] = tx
    M[1, 3] = ty
    M[2, 3] = tz
    return M


def scale_mat(sx, sy=None, sz=None):
    if sy is None:
        sy = sx
    if sz is None:
        sz = sx
    M = np.eye(4, dtype=np.float32)
    M[0, 0] = sx
    M[1, 1] = sy
    M[2, 2] = sz
    return M


def rotate_y(theta):
    c = np.cos(theta)
    s = np.sin(theta)
    M = np.eye(4, dtype=np.float32)
    M[0, 0] = c
    M[0, 2] = s
    M[2, 0] = -s
    M[2, 2] = c
    return M


def rotate_x(theta):
    c = np.cos(theta)
    s = np.sin(theta)
    M = np.eye(4, dtype=np.float32)
    M[1, 1] = c
    M[1, 2] = -s
    M[2, 1] = s
    M[2, 2] = c
    return M


def rotate_z(theta):
    c = np.cos(theta)
    s = np.sin(theta)
    M = np.eye(4, dtype=np.float32)
    M[0, 0] = c
    M[0, 1] = -s
    M[1, 0] = s
    M[1, 1] = c
    return M


def compose(*mats):
    M = np.eye(4, dtype=np.float32)
    for m in mats:
        M = M @ m
    return M
