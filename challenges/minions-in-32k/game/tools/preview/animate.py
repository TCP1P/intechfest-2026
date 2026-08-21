"""Animated preview of a skinned GLB.

Samples an animation clip at a given time, applies linear-blend skinning in
numpy, and hands the deformed vertices to the existing software rasterizer
in `tools/preview/render.py`.

Two entrypoints:

    # single frame
    python3 tools/preview/animate.py assets/minion/minion.glb out.png \
        --clip run --t 0.5 --view hero

    # contact sheet: every clip × N sampled frames
    python3 tools/preview/animate.py assets/minion/minion.glb sheet.png \
        --sheet --frames 6 --view hero
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pygltflib as g

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from preview.render import (  # noqa: E402
    _accessor_array,
    _camera_matrix,
    rasterize,
)

COMP_DTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16,
              5123: np.uint16, 5125: np.uint32, 5126: np.float32}
TYPE_COMPS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _quat_to_mat(q):
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    R = np.eye(4, dtype=np.float32)
    R[0, 0] = 1 - 2 * (yy + zz)
    R[0, 1] = 2 * (x * y - z * w)
    R[0, 2] = 2 * (x * z + y * w)
    R[1, 0] = 2 * (x * y + z * w)
    R[1, 1] = 1 - 2 * (xx + zz)
    R[1, 2] = 2 * (y * z - x * w)
    R[2, 0] = 2 * (x * z - y * w)
    R[2, 1] = 2 * (y * z + x * w)
    R[2, 2] = 1 - 2 * (xx + yy)
    return R


def _compose_trs(t, r, s):
    T = np.eye(4, dtype=np.float32)
    T[:3, 3] = t
    R = _quat_to_mat(r)
    S = np.diag([s[0], s[1], s[2], 1.0]).astype(np.float32)
    return T @ R @ S


def _nlerp_quat(q0, q1, a):
    # Flip one end if dot < 0 so we take the short arc
    if np.dot(q0, q1) < 0:
        q1 = -q1
    q = (1 - a) * q0 + a * q1
    n = np.linalg.norm(q)
    return q / (n if n > 1e-9 else 1.0)


def _sample_channel(times, values, path, t):
    """LINEAR interpolation per glTF spec. Quaternion path uses nlerp + flip."""
    if t <= times[0]:
        return values[0]
    if t >= times[-1]:
        return values[-1]
    i = int(np.searchsorted(times, t) - 1)
    i = max(0, min(i, len(times) - 2))
    t0, t1 = times[i], times[i + 1]
    a = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
    v0, v1 = values[i], values[i + 1]
    if path == "rotation":
        return _nlerp_quat(v0, v1, a)
    return (1 - a) * v0 + a * v1


class SkinnedGLB:
    def __init__(self, path: str):
        self.gltf = g.GLTF2().load(path)
        self.blob = self.gltf.binary_blob()
        self._find_mesh_node()
        self._load_skin()
        self._load_primitives()
        self._index_animations()

    def _find_mesh_node(self):
        for i, n in enumerate(self.gltf.nodes):
            if n.mesh is not None:
                self.mesh_node_idx = i
                self.mesh = self.gltf.meshes[n.mesh]
                return
        raise RuntimeError("no mesh node found")

    def _load_skin(self):
        skin = self.gltf.skins[0]
        self.joint_nodes = list(skin.joints)
        self.ibm_col = _accessor_array(
            self.gltf, self.blob, skin.inverseBindMatrices
        ).astype(np.float32).reshape(-1, 4, 4)
        # glTF stores IBMs as column-major; convert to row-major
        self.ibm = np.transpose(self.ibm_col, (0, 2, 1)).copy()
        self.node_to_joint = {n: i for i, n in enumerate(self.joint_nodes)}

    def _load_primitives(self):
        """Load per-primitive vertex data + skinning attributes."""
        self.prims = []
        for prim in self.mesh.primitives:
            a = prim.attributes
            pos = _accessor_array(self.gltf, self.blob, a.POSITION).astype(np.float32)
            nor = _accessor_array(self.gltf, self.blob, a.NORMAL).astype(np.float32)
            col = _accessor_array(self.gltf, self.blob, a.COLOR_0).astype(np.float32)
            if col.shape[1] == 4:
                col = col[:, :3]
            joints = _accessor_array(self.gltf, self.blob, a.JOINTS_0).astype(np.int32)
            weights = _accessor_array(self.gltf, self.blob, a.WEIGHTS_0).astype(np.float32)
            idx = _accessor_array(self.gltf, self.blob, prim.indices).astype(np.int32).reshape(-1, 3)
            self.prims.append({
                "pos": pos, "nor": nor, "col": col,
                "joints": joints, "weights": weights, "indices": idx,
            })

    def _index_animations(self):
        self.anims = {}
        for anim in self.gltf.animations or []:
            channels = []
            max_t = 0.0
            for ch in anim.channels:
                sampler = anim.samplers[ch.sampler]
                times = _accessor_array(self.gltf, self.blob, sampler.input).astype(np.float32)
                if times.ndim > 1:
                    times = times[:, 0]
                vals = _accessor_array(self.gltf, self.blob, sampler.output).astype(np.float32)
                channels.append({
                    "node": ch.target.node,
                    "path": ch.target.path,
                    "times": times,
                    "values": vals,
                })
                if len(times) > 0:
                    max_t = max(max_t, float(times[-1]))
            self.anims[anim.name] = {"channels": channels, "duration": max_t}

    def _rest_local(self, node_idx: int):
        node = self.gltf.nodes[node_idx]
        t = np.array(node.translation or [0, 0, 0], dtype=np.float32)
        r = np.array(node.rotation or [0, 0, 0, 1], dtype=np.float32)
        s = np.array(node.scale or [1, 1, 1], dtype=np.float32)
        return t, r, s

    def joint_world_matrices(self, clip: str | None, t: float) -> np.ndarray:
        """Return (N_joints, 4, 4) world-space joint transforms."""
        N = len(self.joint_nodes)
        # Build per-node local TRS, start from rest
        locals_trs = {}
        for joint_node_idx in self.joint_nodes:
            locals_trs[joint_node_idx] = self._rest_local(joint_node_idx)
        # Overlay animation channels
        if clip and clip in self.anims:
            for ch in self.anims[clip]["channels"]:
                if ch["node"] not in locals_trs:
                    continue
                val = _sample_channel(ch["times"], ch["values"], ch["path"], t)
                tt, rr, ss = locals_trs[ch["node"]]
                if ch["path"] == "translation":
                    tt = np.asarray(val, dtype=np.float32)
                elif ch["path"] == "rotation":
                    rr = np.asarray(val, dtype=np.float32)
                elif ch["path"] == "scale":
                    ss = np.asarray(val, dtype=np.float32)
                locals_trs[ch["node"]] = (tt, rr, ss)

        # Resolve parent relationships among joint nodes. glTF parents come
        # from the node.children lists.
        parent_of = {n: -1 for n in self.joint_nodes}
        for i, node in enumerate(self.gltf.nodes):
            for ch in node.children or []:
                if ch in parent_of and i in parent_of:
                    parent_of[ch] = i
        # Compute world matrices in topological order
        world = {}
        # Simple iterative pass until all resolved
        remaining = set(self.joint_nodes)
        while remaining:
            progressed = False
            for n in list(remaining):
                p = parent_of[n]
                if p < 0 or p in world:
                    local = _compose_trs(*locals_trs[n])
                    world[n] = (world[p] if p >= 0 else np.eye(4, dtype=np.float32)) @ local
                    remaining.remove(n)
                    progressed = True
            if not progressed:
                raise RuntimeError("cycle or missing joint")
        # Assemble into (N, 4, 4) ordered by joint index
        out = np.zeros((N, 4, 4), dtype=np.float32)
        for i, n in enumerate(self.joint_nodes):
            out[i] = world[n]
        return out

    def skin(self, clip: str | None, t: float):
        """Apply skinning to every primitive; return (V, N, C, F)."""
        world = self.joint_world_matrices(clip, t)
        # Precompute world @ ibm per joint: (N, 4, 4)
        skin_mats = np.einsum("nij,njk->nik", world, self.ibm)
        all_v, all_n, all_c, all_f = [], [], [], []
        voff = 0
        for prim in self.prims:
            pos = prim["pos"]
            nor = prim["nor"]
            col = prim["col"]
            joints = prim["joints"]
            weights = prim["weights"]
            # Blend per vertex: M_vert = sum_i w_i * skin_mats[joint_i]
            # Shape acrobatics to avoid per-vertex loops:
            # skin_mats[joints] has shape (V, 4, 4, 4) — the 4 influences
            per_inf = skin_mats[joints]  # (V, 4, 4, 4)
            w = weights[:, :, None, None]  # (V, 4, 1, 1)
            M = (per_inf * w).sum(axis=1)  # (V, 4, 4)
            ones = np.ones((pos.shape[0], 1), dtype=np.float32)
            ph = np.hstack([pos, ones])  # (V, 4)
            # multiply M @ ph.T → (V, 4, 1); use einsum
            posw = np.einsum("vij,vj->vi", M, ph)[:, :3]
            # Normal: use rotation-only part (M[:3,:3])
            norw = np.einsum("vij,vj->vi", M[:, :3, :3], nor)
            norw /= (np.linalg.norm(norw, axis=1, keepdims=True) + 1e-9)
            all_v.append(posw.astype(np.float32))
            all_n.append(norw.astype(np.float32))
            all_c.append(col.astype(np.float32))
            all_f.append((prim["indices"] + voff).astype(np.int32))
            voff += pos.shape[0]
        V = np.vstack(all_v) if all_v else np.zeros((0, 3), np.float32)
        N = np.vstack(all_n) if all_n else np.zeros((0, 3), np.float32)
        C = np.vstack(all_c) if all_c else np.zeros((0, 3), np.float32)
        F = np.vstack(all_f) if all_f else np.zeros((0, 3), np.int32)
        return V, N, C, F


def render_frame(skg: SkinnedGLB, clip: str | None, t: float,
                 W: int, H: int, view: str,
                 target, radius: float) -> Image.Image:
    V, N, C, F = skg.skin(clip, t)
    # Aim the camera at the character.
    cam_radius = radius * 2.5
    eye, fwd, up, right = _camera_matrix(view, np.asarray(target, dtype=np.float32), cam_radius)
    half_w = radius
    aspect = W / H
    half_h = half_w / aspect
    return rasterize(V, N, C, F, eye, fwd, up, right, W, H, half_w, half_h)


def render_clip_to_mp4(skg: SkinnedGLB, clip: str, out_path: str,
                       view: str, W: int, H: int, fps: int,
                       target, radius: float, loop_seconds: float = 0.0):
    """Render `clip` as an MP4 via an ffmpeg pipe.

    `loop_seconds`: if > 0, keep rendering past the clip's natural duration,
    wrapping time with modulo. Useful for the naturally-looping clips
    (idle/run/strafe) so the video is long enough to watch."""
    import subprocess

    anim = skg.anims[clip]
    dur = anim["duration"] or 1.0
    total_dur = max(dur, loop_seconds)
    nframes = max(2, int(round(total_dur * fps)))
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "image2pipe", "-framerate", str(fps),
        "-vcodec", "png", "-i", "-",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        # ensure even dimensions (yuv420 requirement)
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for f in range(nframes):
            t = (f / fps) % dur if dur > 0 else 0.0
            img = render_frame(skg, clip, t, W, H, view, target, radius)
            img.save(proc.stdin, format="PNG")
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exit {proc.returncode}")
    print(f"wrote {out_path} — {clip}: {nframes} frames @ {fps}fps "
          f"({total_dur:.2f}s, loop={dur:.2f}s)")


def contact_sheet(skg: SkinnedGLB, out_path: str, frames: int,
                  view: str, cell_w: int, cell_h: int,
                  target, radius: float):
    clip_names = list(skg.anims.keys())
    cols = frames
    rows = len(clip_names)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h + 24 * rows), (36, 40, 48))
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    for r, name in enumerate(clip_names):
        dur = skg.anims[name]["duration"] or 1.0
        for c in range(cols):
            t = (c / max(cols - 1, 1)) * dur if cols > 1 else 0.0
            img = render_frame(skg, name, t, cell_w, cell_h, view,
                               target, radius)
            y = r * (cell_h + 24)
            sheet.paste(img, (c * cell_w, y + 24))
        d = ImageDraw.Draw(sheet)
        label = f"{name} ({dur:.2f}s)"
        d.text((6, r * (cell_h + 24) + 4), label, fill=(240, 240, 240), font=font)
    sheet.save(out_path)
    print(f"wrote {out_path} ({cols}x{rows} cells, {cols * cell_w}x{rows * (cell_h + 24)} px)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("out")
    ap.add_argument("--clip", default=None, help="animation name (default: rest pose)")
    ap.add_argument("--t", type=float, default=0.0, help="time in seconds")
    ap.add_argument("--view", default="hero")
    ap.add_argument("--w", type=int, default=320)
    ap.add_argument("--h", type=int, default=320)
    ap.add_argument("--fov", type=float, default=38.0)
    ap.add_argument("--target", type=float, nargs=3, default=(0.0, 1.0, 0.0))
    ap.add_argument("--radius", type=float, default=1.6)
    ap.add_argument("--sheet", action="store_true",
                    help="render a contact sheet of all clips × N frames")
    ap.add_argument("--frames", type=int, default=6,
                    help="frames per clip in sheet mode")
    ap.add_argument("--mp4", action="store_true",
                    help="render clip(s) as mp4 via ffmpeg. If --clip is "
                         "given, writes `out`. Otherwise, `out` is treated "
                         "as a directory and one mp4 per clip is written.")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--loop-seconds", type=float, default=3.0,
                    help="min video duration; loops clip until reached")
    args = ap.parse_args()

    skg = SkinnedGLB(args.glb)
    if args.mp4:
        from pathlib import Path
        if args.clip:
            render_clip_to_mp4(skg, args.clip, args.out, args.view,
                               args.w, args.h, args.fps, args.target,
                               args.radius, args.loop_seconds)
        else:
            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in skg.anims:
                render_clip_to_mp4(skg, name, str(out_dir / f"{name}.mp4"),
                                   args.view, args.w, args.h, args.fps,
                                   args.target, args.radius, args.loop_seconds)
    elif args.sheet:
        contact_sheet(skg, args.out, args.frames, args.view,
                      args.w, args.h, args.target, args.radius)
    else:
        img = render_frame(skg, args.clip, args.t, args.w, args.h, args.view,
                           args.target, args.radius)
        img.save(args.out)
        print(f"wrote {args.out} (clip={args.clip} t={args.t})")


if __name__ == "__main__":
    main()
