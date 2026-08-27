"""Level consistency checker — drift between collision and visuals fails
here instead of reaching a player.

Checks:
  1. No invisible walls: every collision AABB must contain visual
     geometry from its zone's glb (secret is exempt: the client renders
     it as cuboids from the arena data by design).
  2. Jump pads land somewhere: each pad's apex clears a walkable top
     within air-drift range.
  3. Powerups float just above a standable surface.
  4. Elevator shafts are clear: no static AABB intrudes into the swept
     platform volume.

Run after build_all (build_all invokes it automatically):
    PYTHONPATH=tools python3 tools/level/check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import numpy as np
from preview.render import _load_scene

GRAVITY = 20.0
DRIFT_RANGE = 2.5      # air-control reach while falling from a pad apex
NO_VISUAL_EXEMPT = {"secret"}


def load_level():
    scenes = ROOT / "assets" / "scenes"
    manifest = json.loads((scenes / "manifest.json").read_text())
    zones = {}
    for name in manifest["zones"]:
        zones[name] = json.loads((scenes / f"{name}.json").read_text())
    return manifest, zones


def aabb_contains_any(mn, mx, verts, pad=0.25):
    sel = (
        (verts[:, 0] >= mn[0] - pad) & (verts[:, 0] <= mx[0] + pad)
        & (verts[:, 1] >= mn[1] - pad) & (verts[:, 1] <= mx[1] + pad)
        & (verts[:, 2] >= mn[2] - pad) & (verts[:, 2] <= mx[2] + pad)
    )
    return bool(sel.any())


def horizontal_gap(px, pz, mn, mx):
    dx = max(mn[0] - px, 0.0, px - mx[0])
    dz = max(mn[2] - pz, 0.0, pz - mx[2])
    return (dx * dx + dz * dz) ** 0.5


PLAYER_R = 0.4
PLAYER_H = 1.7


def _overlap_xz(x, zp, mn, mx):
    return (x + PLAYER_R > mn[0] and x - PLAYER_R < mx[0]
            and zp + PLAYER_R > mn[2] and zp - PLAYER_R < mx[2])


def _collides(x, y, zp, all_walls):
    for wzn, mn, mx in all_walls:
        if not _overlap_xz(x, zp, mn, mx):
            continue
        if y >= mx[1] - 1e-4 or y + PLAYER_H <= mn[1]:
            continue
        return (wzn, mn, mx)
    return None


def _fly(pad, all_walls, air=(0.0, 0.0), strict=True):
    """One arc, mirroring world.rs integration order exactly: horizontal
    axes swept at the OLD height first (this order is why a borderline
    lip-clip happens in game even when position-then-test says it
    doesn't), then vertical with landing on tops / head-stop on
    undersides. `air` approximates max air control as a constant added
    horizontal velocity. strict=True (the no-input arc) treats any side
    contact as failure; steered variants only fail on head-bumps."""
    px, py, pz = pad["center"]
    vx, vy, vz = pad["velocity"]
    vx, vz = vx + air[0], vz + air[1]
    dt = 1.0 / 64.0
    x, y, zp = px, py, pz
    for step in range(int(6.0 / dt)):
        grace = step < 3  # leaving the pad's own supporting surface
        cand = x + vx * dt
        hit = _collides(cand, y, zp, all_walls)
        if hit is None:
            x = cand
        elif strict and not grace:
            return ("clip", hit, (cand, y, zp), step * dt)
        else:
            vx = 0.0
        cand = zp + vz * dt
        hit = _collides(x, y, cand, all_walls)
        if hit is None:
            zp = cand
        elif strict and not grace:
            return ("clip", hit, (x, y, cand), step * dt)
        else:
            vz = 0.0
        prev_y = y
        y += vy * dt
        vy -= GRAVITY * dt
        if vy <= 0.0 or y < prev_y:
            for wzn, mn, mx in all_walls:
                if not _overlap_xz(x, zp, mn, mx):
                    continue
                if prev_y >= mx[1] - 1e-3 and y <= mx[1]:
                    return ("land", (wzn, mn, mx), (x, mx[1], zp), step * dt)
        else:
            for wzn, mn, mx in all_walls:
                if not _overlap_xz(x, zp, mn, mx):
                    continue
                if prev_y + PLAYER_H <= mn[1] and y + PLAYER_H > mn[1]:
                    if grace:
                        continue
                    return ("head", (wzn, mn, mx), (x, y, zp), step * dt)
        if y < -1.0:
            return ("void", None, (x, y, zp), step * dt)
    return ("lost", None, (x, y, zp), 6.0)


def simulate_arc(zn, pad, all_walls):
    import math
    px, py, pz = pad["center"]
    tx, ty, tz = pad.get("target", (px, py, pz))
    # 1. the whole arc TUBE must fly clean: a player can trigger the pad
    # anywhere on the disc, which translates the entire flight. Fly from
    # the center and 8 rim entry points; every one must land on a top
    # near (target + the same entry offset) without touching anything.
    rim = pad["radius"] - 0.05
    entries = [(0.0, 0.0)] + [
        (rim * math.cos(k * math.tau / 8), rim * math.sin(k * math.tau / 8))
        for k in range(8)
    ]
    for ox, oz in entries:
        moved = dict(pad)
        moved["center"] = [px + ox, py, pz + oz]
        kind, hit, at, t = _fly(moved, all_walls, strict=True)
        if kind != "land":
            return (f"{zn}: pad ({px},{py},{pz}) entry ({ox:+.2f},{oz:+.2f}) "
                    f"{kind.upper()}s {hit[1] if hit else ''}.."
                    f"{hit[2] if hit else ''} at "
                    f"({at[0]:.1f},{at[1]:.1f},{at[2]:.1f}), t={t:.2f}s")
        dx, dz = at[0] - (tx + ox), at[2] - (tz + oz)
        if (dx * dx + dz * dz) ** 0.5 > 0.6 or abs(at[1] - ty) > 0.5:
            return (f"{zn}: pad ({px},{py},{pz}) entry ({ox:+.2f},{oz:+.2f}) "
                    f"lands at ({at[0]:.1f},{at[1]:.1f},{at[2]:.1f}), "
                    f"off the {ty}-level target")
    # 2. steered arcs (max air control in 4 directions) must never
    # head-bump — landing somewhere else is fine, ceilings are not
    for air in [(1.5, 0.0), (-1.5, 0.0), (0.0, 1.5), (0.0, -1.5)]:
        kind, hit, at, t = _fly(pad, all_walls, air=air, strict=False)
        if kind == "head":
            return (f"{zn}: pad ({px},{py},{pz}) head-bumps {hit[0]} "
                    f"collider {hit[1]}..{hit[2]} when steered {air}, "
                    f"t={t:.2f}s")
    return None


def main():
    manifest, zones = load_level()
    errors = []

    all_walls = []
    for zn, z in zones.items():
        for w in z.get("walls", []):
            all_walls.append((zn, w["min"], w["max"]))

    # 1. invisible walls
    for zn, z in zones.items():
        if zn in NO_VISUAL_EXEMPT:
            continue
        glb = ROOT / "assets" / "zones" / f"{zn}.glb"
        V, _, _, _ = _load_scene(str(glb), None)
        for i, w in enumerate(z.get("walls", [])):
            if not aabb_contains_any(w["min"], w["max"], V):
                errors.append(
                    f"{zn}: collider {i} {w['min']}..{w['max']} has no "
                    f"visual geometry (invisible wall)")

    # 2. pad arcs: simulate the full hands-off flight against every
    # collider. The arc must land within 0.6 m of the declared target on
    # a real top surface, touching nothing on the way.
    for zn, z in zones.items():
        for p in z.get("jump_pads", []):
            err = simulate_arc(zn, p, all_walls)
            if err:
                errors.append(err)

    # 3. powerups: never inside a collider, and elevated ones must
    # hover above something standable
    for zn, z in zones.items():
        for pu in z.get("powerups", []):
            cx, cy, cz = pu["center"]
            for wzn, mn, mx in all_walls:
                if (mn[0] - 0.2 < cx < mx[0] + 0.2
                        and mn[2] - 0.2 < cz < mx[2] + 0.2
                        and mn[1] < cy + 1.0 and mx[1] > cy + 0.05):
                    errors.append(
                        f"{zn}: powerup {pu['kind']} at ({cx},{cy},{cz}) is "
                        f"buried in {wzn} collider {mn}..{mx}")
            if cy <= 0.01:
                continue  # on the ground floor
            ok = any(
                abs(mx[1] - cy) < 0.6
                and horizontal_gap(cx, cz, mn, mx) <= 0.01
                for _, mn, mx in all_walls
            )
            if not ok:
                errors.append(f"{zn}: powerup {pu['kind']} at "
                              f"({cx},{cy},{cz}) floats over nothing")

    # 4. elevator shafts clear
    for zn, z in zones.items():
        for e in z.get("elevators", []):
            ex, ez = e["center"]
            hx, hz = e["half"]
            y0, y1 = e["y_bottom"], e["y_top"]
            for wzn, mn, mx in all_walls:
                ix = min(mx[0], ex + hx) - max(mn[0], ex - hx)
                iz = min(mx[2], ez + hz) - max(mn[2], ez - hz)
                iy = min(mx[1], y1) - max(mn[1], y0 - e["thickness"])
                if ix > 0.01 and iz > 0.01 and iy > 0.01:
                    errors.append(
                        f"{zn}: elevator shaft at ({ex},{ez}) blocked by "
                        f"{wzn} collider {mn}..{mx}")

    if errors:
        print(f"FAIL: {len(errors)} consistency error(s)")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    n_walls = len(all_walls)
    n_pads = sum(len(z.get("jump_pads", [])) for z in zones.values())
    print(f"level check ok: {n_walls} colliders visible, {n_pads} pads land, "
          f"powerups seated, elevator shafts clear")


if __name__ == "__main__":
    main()
