"""Headless preview renderer for GLBs.

Software rasterizer (no OpenGL, no GPU). Reads a GLB via pygltflib, flattens
the scene graph, flat-shades with a simple directional light, and writes a PNG.

Resolution defaults are small — we preview for the same eye that plays the
game at 160x100.

Usage:
    python3 tools/preview/render.py assets/zones/sports.glb out.png \\
        --w 640 --h 480 --view top
"""

from __future__ import annotations

import argparse
import sys
import struct
from pathlib import Path
import numpy as np
from PIL import Image
import pygltflib as g

_COMPONENT_DTYPE = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_TYPE_COMPONENTS = {
    "SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
    "MAT2": 4, "MAT3": 9, "MAT4": 16,
}


def _accessor_array(gltf: g.GLTF2, blob: bytes, acc_idx: int) -> np.ndarray:
    acc = gltf.accessors[acc_idx]
    bv = gltf.bufferViews[acc.bufferView]
    dtype = _COMPONENT_DTYPE[acc.componentType]
    comps = _TYPE_COMPONENTS[acc.type]
    offset = (bv.byteOffset or 0) + (acc.byteOffset or 0)
    length = acc.count * comps
    arr = np.frombuffer(blob, dtype=dtype, count=length, offset=offset)
    if comps > 1:
        arr = arr.reshape(-1, comps)
    return np.array(arr)  # copy


def _node_transform(node) -> np.ndarray:
    M = np.eye(4, dtype=np.float32)
    if getattr(node, "matrix", None):
        M = np.array(node.matrix, dtype=np.float32).reshape(4, 4).T
        return M
    if getattr(node, "translation", None):
        t = np.array(node.translation, dtype=np.float32)
        T = np.eye(4, dtype=np.float32)
        T[:3, 3] = t
        M = T @ M
    if getattr(node, "rotation", None):
        q = np.array(node.rotation, dtype=np.float32)
        x, y, z, w = q
        R = np.eye(4, dtype=np.float32)
        R[0, 0] = 1 - 2 * (y * y + z * z)
        R[0, 1] = 2 * (x * y - z * w)
        R[0, 2] = 2 * (x * z + y * w)
        R[1, 0] = 2 * (x * y + z * w)
        R[1, 1] = 1 - 2 * (x * x + z * z)
        R[1, 2] = 2 * (y * z - x * w)
        R[2, 0] = 2 * (x * z - y * w)
        R[2, 1] = 2 * (y * z + x * w)
        R[2, 2] = 1 - 2 * (x * x + y * y)
        M = M @ R
    if getattr(node, "scale", None):
        s = node.scale
        S = np.diag([s[0], s[1], s[2], 1.0]).astype(np.float32)
        M = M @ S
    # Order: matrix OR (T * R * S) applied to local
    # Standard glTF composition: M = T * R * S
    # Rebuild properly:
    T = np.eye(4, dtype=np.float32)
    R = np.eye(4, dtype=np.float32)
    S = np.eye(4, dtype=np.float32)
    if getattr(node, "translation", None):
        T[:3, 3] = np.array(node.translation, dtype=np.float32)
    if getattr(node, "rotation", None):
        q = np.array(node.rotation, dtype=np.float32)
        x, y, z, w = q
        R[0, 0] = 1 - 2 * (y * y + z * z)
        R[0, 1] = 2 * (x * y - z * w)
        R[0, 2] = 2 * (x * z + y * w)
        R[1, 0] = 2 * (x * y + z * w)
        R[1, 1] = 1 - 2 * (x * x + z * z)
        R[1, 2] = 2 * (y * z - x * w)
        R[2, 0] = 2 * (x * z - y * w)
        R[2, 1] = 2 * (y * z + x * w)
        R[2, 2] = 1 - 2 * (x * x + y * y)
    if getattr(node, "scale", None):
        s = node.scale
        S = np.diag([s[0], s[1], s[2], 1.0]).astype(np.float32)
    return T @ R @ S


def _load_scene(path: str, scene_index: int | None = None):
    gltf = g.GLTF2().load(path)
    blob = gltf.binary_blob()
    all_v, all_n, all_c, all_f = [], [], [], []
    voff = 0

    def visit(node_idx: int, parent_M: np.ndarray):
        nonlocal voff
        node = gltf.nodes[node_idx]
        M = parent_M @ _node_transform(node)
        if node.mesh is not None:
            mesh = gltf.meshes[node.mesh]
            for prim in mesh.primitives:
                attrs = prim.attributes
                pos = _accessor_array(gltf, blob, attrs.POSITION)
                if attrs.NORMAL is not None:
                    nor = _accessor_array(gltf, blob, attrs.NORMAL)
                else:
                    nor = np.zeros_like(pos)
                    nor[:, 1] = 1.0
                col_attr = getattr(attrs, "COLOR_0", None)
                if col_attr is not None:
                    col = _accessor_array(gltf, blob, col_attr)
                    if col.shape[1] == 4:
                        col = col[:, :3]
                    if col.dtype != np.float32:
                        if col.dtype == np.uint8:
                            col = col.astype(np.float32) / 255.0
                        elif col.dtype == np.uint16:
                            col = col.astype(np.float32) / 65535.0
                        else:
                            col = col.astype(np.float32)
                else:
                    col = np.ones_like(pos) * 0.75
                if prim.indices is not None:
                    idx = _accessor_array(gltf, blob, prim.indices).reshape(-1, 3)
                else:
                    idx = np.arange(pos.shape[0]).reshape(-1, 3)
                # Transform positions and normals
                ones = np.ones((pos.shape[0], 1), dtype=np.float32)
                pos_h = np.hstack([pos.astype(np.float32), ones])
                pos_w = (M @ pos_h.T).T[:, :3]
                rot = M[:3, :3]
                # Approx normal transform
                nor_w = (rot @ nor.astype(np.float32).T).T
                nl = np.linalg.norm(nor_w, axis=1, keepdims=True) + 1e-9
                nor_w = nor_w / nl
                all_v.append(pos_w.astype(np.float32))
                all_n.append(nor_w.astype(np.float32))
                all_c.append(col.astype(np.float32))
                all_f.append((idx + voff).astype(np.int32))
                voff += pos.shape[0]
        if node.children:
            for ch in node.children:
                visit(ch, M)

    scene_idx = (gltf.scene or 0) if scene_index is None else scene_index
    if scene_idx < 0 or scene_idx >= len(gltf.scenes or []):
        raise ValueError(f"scene {scene_idx} out of range for {path}")
    scene = gltf.scenes[scene_idx]
    for n_idx in scene.nodes:
        visit(n_idx, np.eye(4, dtype=np.float32))

    V = np.vstack(all_v) if all_v else np.zeros((0, 3), np.float32)
    N = np.vstack(all_n) if all_n else np.zeros((0, 3), np.float32)
    C = np.vstack(all_c) if all_c else np.zeros((0, 3), np.float32)
    F = np.vstack(all_f) if all_f else np.zeros((0, 3), np.int32)
    return V, N, C, F


def _camera_matrix(view: str, target: np.ndarray, radius: float):
    t = target
    if view == "top":
        eye = t + np.array([0.01, radius, 0.01])
        up = np.array([0, 0, -1], dtype=np.float32)
    elif view == "iso":
        d = radius * 0.7
        eye = t + np.array([d, d * 1.1, d])
        up = np.array([0, 1, 0], dtype=np.float32)
    elif view == "front":
        # Minions face -Z (glTF convention), so camera sits at -Z looking +Z.
        eye = t + np.array([0, radius * 0.2, -radius])
        up = np.array([0, 1, 0], dtype=np.float32)
    elif view == "side":
        eye = t + np.array([radius, radius * 0.2, 0])
        up = np.array([0, 1, 0], dtype=np.float32)
    elif view == "hero":
        eye = t + np.array([radius * 0.6, radius * 0.5, -radius * 0.85])
        up = np.array([0, 1, 0], dtype=np.float32)
    else:
        raise ValueError(view)
    fwd = t - eye
    fwd = fwd / (np.linalg.norm(fwd) + 1e-9)
    right = np.cross(fwd, up)
    right = right / (np.linalg.norm(right) + 1e-9)
    up = np.cross(right, fwd)
    return eye.astype(np.float32), fwd.astype(np.float32), up.astype(np.float32), right.astype(np.float32)


def rasterize(V, N, C, F,
              eye, fwd, up, right,
              width: int, height: int,
              half_w: float, half_h: float,
              bg=(0.08, 0.08, 0.11),
              light_dir=(-0.5, -1.0, -0.3)) -> Image.Image:
    """Software rasterize `(V, N, C, F)` through a given camera frame.

    Shared by the static `render` pipeline and the animation sampler in
    `animate.py`.
    """
    v_rel = V - eye
    xs = v_rel @ right
    ys = v_rel @ up
    zs = v_rel @ fwd

    sx = (xs / half_w * 0.5 + 0.5) * (width - 1)
    sy = (1.0 - (ys / half_h * 0.5 + 0.5)) * (height - 1)

    img = np.zeros((height, width, 3), dtype=np.float32)
    img[:, :, 0] = bg[0]
    img[:, :, 1] = bg[1]
    img[:, :, 2] = bg[2]
    zbuf = np.full((height, width), np.inf, dtype=np.float32)

    L = np.array(light_dir, dtype=np.float32)
    L = -L / (np.linalg.norm(L) + 1e-9)

    # Sort far-to-near for stable painter's fallback
    face_z = zs[F].mean(axis=1)
    order = np.argsort(-face_z)
    F = F[order]

    for tri in F:
        i0, i1, i2 = tri
        if zs[i0] <= 0 and zs[i1] <= 0 and zs[i2] <= 0:
            continue
        v0 = (sx[i0], sy[i0], zs[i0])
        v1 = (sx[i1], sy[i1], zs[i1])
        v2 = (sx[i2], sy[i2], zs[i2])

        n_avg = (N[i0] + N[i1] + N[i2]) / 3
        n_avg = n_avg / (np.linalg.norm(n_avg) + 1e-9)
        c_avg = (C[i0] + C[i1] + C[i2]) / 3
        diffuse = max(0.15, float(np.dot(n_avg, L)))
        rim = max(0.0, 1.0 - abs(float(np.dot(n_avg, fwd))))  # cheap rim
        shade = c_avg * (0.30 + 0.7 * diffuse) + 0.05 * rim

        x0i = int(max(0, np.floor(min(v0[0], v1[0], v2[0]))))
        x1i = int(min(width - 1, np.ceil(max(v0[0], v1[0], v2[0]))))
        y0i = int(max(0, np.floor(min(v0[1], v1[1], v2[1]))))
        y1i = int(min(height - 1, np.ceil(max(v0[1], v1[1], v2[1]))))
        if x1i < x0i or y1i < y0i:
            continue
        xs_grid, ys_grid = np.meshgrid(np.arange(x0i, x1i + 1), np.arange(y0i, y1i + 1))

        def edge(ax, ay, bx, by, px, py):
            return (bx - ax) * (py - ay) - (by - ay) * (px - ax)

        w0 = edge(v1[0], v1[1], v2[0], v2[1], xs_grid, ys_grid)
        w1 = edge(v2[0], v2[1], v0[0], v0[1], xs_grid, ys_grid)
        w2 = edge(v0[0], v0[1], v1[0], v1[1], xs_grid, ys_grid)
        area = edge(v0[0], v0[1], v1[0], v1[1], v2[0], v2[1])
        if abs(area) < 1e-6:
            continue
        inside = ((w0 >= 0) & (w1 >= 0) & (w2 >= 0)) | ((w0 <= 0) & (w1 <= 0) & (w2 <= 0))
        if not np.any(inside):
            continue
        bw0 = w0 / area
        bw1 = w1 / area
        bw2 = w2 / area
        depth = bw0 * zs[i0] + bw1 * zs[i1] + bw2 * zs[i2]
        tile_z = zbuf[y0i:y1i + 1, x0i:x1i + 1]
        draw = inside & (depth < tile_z) & (depth > 0)
        if not np.any(draw):
            continue
        tile_z[draw] = depth[draw]
        zbuf[y0i:y1i + 1, x0i:x1i + 1] = tile_z
        for ch in range(3):
            tile = img[y0i:y1i + 1, x0i:x1i + 1, ch]
            tile[draw] = shade[ch]
            img[y0i:y1i + 1, x0i:x1i + 1, ch] = tile

    img = np.clip(img, 0, 1)
    enc = np.where(img <= 0.0031308, 12.92 * img, 1.055 * np.power(img, 1 / 2.4) - 0.055)
    out = (np.clip(enc, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(out)


def render(glb_path: str, out_path: str, width: int = 640, height: int = 480,
           view: str = "iso", bg=(0.08, 0.08, 0.11), light_dir=(-0.5, -1.0, -0.3),
           fov_scale: float = 1.0, target: tuple | None = None, radius: float | None = None,
           y_cut: float | None = None, scene: int | None = None):
    V, N, C, F = _load_scene(glb_path, scene)
    if V.shape[0] == 0:
        print(f"no geometry in {glb_path}")
        return
    if y_cut is not None:
        face_y = V[F, 1].mean(axis=1)
        F = F[face_y < y_cut]
    center = (V.max(0) + V.min(0)) * 0.5
    extent = float(np.linalg.norm(V.max(0) - V.min(0))) * 0.55
    if target is not None:
        center = np.array(target, dtype=np.float32)
    if radius is not None:
        extent = radius
    eye, fwd, up, right = _camera_matrix(view, center, extent * 2.5)
    half_w = extent * fov_scale
    aspect = width / height
    half_h = half_w / aspect
    img = rasterize(V, N, C, F, eye, fwd, up, right, width, height,
                    half_w, half_h, bg=bg, light_dir=light_dir)
    img.save(out_path)
    print(f"rendered {glb_path} -> {out_path} ({width}x{height} view={view})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("out")
    ap.add_argument("--w", type=int, default=640)
    ap.add_argument("--h", type=int, default=480)
    ap.add_argument("--view", default="iso",
                    choices=["top", "iso", "front", "side", "hero"])
    ap.add_argument("--fov", type=float, default=1.0)
    ap.add_argument("--target", type=str, default=None,
                    help="x,y,z override for camera target")
    ap.add_argument("--radius", type=float, default=None)
    ap.add_argument("--scene", type=int, default=None,
                    help="scene index to render; defaults to the GLB default scene")
    ap.add_argument("--y-cut", type=float, default=None,
                    help="drop faces whose mean y is above this (cutaway)")
    args = ap.parse_args()
    tgt = None
    if args.target:
        tgt = tuple(float(x) for x in args.target.split(","))
    render(args.glb, args.out, args.w, args.h, args.view,
           fov_scale=args.fov, target=tgt, radius=args.radius,
           y_cut=args.y_cut, scene=args.scene)


if __name__ == "__main__":
    main()
