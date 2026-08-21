"""Scan the current arena for pairs of walls whose faces are coplanar and
overlap — classic z-fighting candidates. Reads the JSON dumped by
`cargo run -p sim --example dump_arena`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EPS = 1e-4


def main():
    out = subprocess.run(
        ["cargo", "run", "--release", "-p", "sim", "--example", "dump_arena"],
        cwd=str(ROOT),
        capture_output=True,
        check=True,
    )
    arena = json.loads(out.stdout.decode())
    walls = arena["walls"]
    print(f"scanning {len(walls)} walls for coplanar face overlaps ...")

    # Each face is (axis, sign, coord, other_axis_min, other_axis_max,
    #               third_axis_min, third_axis_max, wall_index)
    # axis: 0=x, 1=y, 2=z.  sign: -1 or +1.
    faces = []
    for wi, w in enumerate(walls):
        mn, mx = w["min"], w["max"]
        # x faces
        faces.append((0, -1, mn[0], mn[1], mx[1], mn[2], mx[2], wi))
        faces.append((0, +1, mx[0], mn[1], mx[1], mn[2], mx[2], wi))
        # y faces
        faces.append((1, -1, mn[1], mn[0], mx[0], mn[2], mx[2], wi))
        faces.append((1, +1, mx[1], mn[0], mx[0], mn[2], mx[2], wi))
        # z faces
        faces.append((2, -1, mn[2], mn[0], mx[0], mn[1], mx[1], wi))
        faces.append((2, +1, mx[2], mn[0], mx[0], mn[1], mx[1], wi))

    real_zfights: list[str] = []
    back_to_back: int = 0
    hidden_bottoms: list[str] = []
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            a = faces[i]
            b = faces[j]
            if a[0] != b[0]:
                continue  # different axes
            if a[7] == b[7]:
                continue  # same wall, front+back face, fine
            if abs(a[2] - b[2]) > EPS:
                continue  # different plane coordinate
            # Check overlap on the two perpendicular axes.
            ox = max(a[3], b[3])
            oy = min(a[4], b[4])
            pz = max(a[5], b[5])
            pw = min(a[6], b[6])
            if ox + EPS >= oy or pz + EPS >= pw:
                continue  # empty overlap or edge-touch only
            axis_name = "xyz"[a[0]]
            summary = (
                f"  {axis_name}={a[2]:+.3f}  walls #{a[7]} <-> #{b[7]} "
                f"overlap area {(oy - ox):.2f} x {(pw - pz):.2f}  signs {a[1]:+d} vs {b[1]:+d}"
            )
            # Opposite-sign coplanar faces are back-to-back surfaces —
            # each is only visible from one side so they don't z-fight.
            if a[1] != b[1]:
                back_to_back += 1
                continue
            # Same-sign bottom faces at y=0 face downward and sit on top
            # of the floor plane, which always wins the depth test.
            if a[0] == 1 and abs(a[2] - 0.0) < EPS and a[1] == -1:
                hidden_bottoms.append(summary)
                continue
            real_zfights.append(summary)

    print(f"back-to-back coplanar pairs (not z-fighting): {back_to_back}")
    print(f"hidden-by-floor bottom pairs (not visible):    {len(hidden_bottoms)}")
    if not real_zfights:
        print("VISIBLE z-fighting: none — arena is clean")
        return 0
    print(f"VISIBLE z-fighting: {len(real_zfights)}")
    for c in real_zfights:
        print(c)
    return 1


if __name__ == "__main__":
    sys.exit(main())
