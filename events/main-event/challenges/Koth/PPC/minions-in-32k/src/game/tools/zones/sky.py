"""Night sky — the lab's silo doors are open and the moon is RIGHT THERE.

A giant interior-faced night box around the whole lab (gradient bands,
star field, one shooting star) and the moon Gru is going to steal,
parked where the rocket points through the open silo doors. Unlit
(KHR_materials_unlit) so scene lighting never dims it. Render-only:
no collision, no gameplay.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyasset import box, icosphere, hex_to_rgb
from zones.common import ZoneBuild, write_zone

EMIT_GLB = True
UNLIT = True

NIGHT_LOW = hex_to_rgb("#232a55")
NIGHT_MID = hex_to_rgb("#161b40")
NIGHT_HIGH = hex_to_rgb("#0b0e2a")
STAR = hex_to_rgb("#f2ecd9")
STAR_DIM = hex_to_rgb("#9aa0c8")
MOON = hex_to_rgb("#f2e9c4")
CRATER = hex_to_rgb("#cdbf92")

X, Z, TOP = 130.0, 110.0, 88.0
MOON_POS = (10.0, 62.0, -20.0)
MOON_R = 10.0


def _dome(z: ZoneBuild):
    bands = [(0.0, 30.0, NIGHT_LOW), (30.0, 60.0, NIGHT_MID),
             (60.0, TOP, NIGHT_HIGH)]
    for y0, y1, c in bands:
        cy, h = (y0 + y1) / 2, y1 - y0
        z.add(box(size=(0.5, h, 2 * Z), color=c, center=(-X, cy, 0)))
        z.add(box(size=(0.5, h, 2 * Z), color=c, center=(X, cy, 0)))
        z.add(box(size=(2 * X, h, 0.5), color=c, center=(0, cy, -Z)))
        z.add(box(size=(2 * X, h, 0.5), color=c, center=(0, cy, Z)))
    z.add(box(size=(2 * X, 0.5, 2 * Z), color=NIGHT_HIGH, center=(0, TOP, 0)))


def _stars(z: ZoneBuild):
    rng = random.Random(16)  # deterministic builds
    for _ in range(90):
        face = rng.choice(["top", "n", "s", "w", "e"])
        s = rng.uniform(0.35, 0.9)
        c = STAR if rng.random() < 0.6 else STAR_DIM
        if face == "top":
            p = (rng.uniform(-X + 5, X - 5), TOP - 0.6, rng.uniform(-Z + 5, Z - 5))
        elif face == "n":
            p = (rng.uniform(-X + 5, X - 5), rng.uniform(28, TOP - 4), -Z + 0.6)
        elif face == "s":
            p = (rng.uniform(-X + 5, X - 5), rng.uniform(28, TOP - 4), Z - 0.6)
        elif face == "w":
            p = (-X + 0.6, rng.uniform(28, TOP - 4), rng.uniform(-Z + 5, Z - 5))
        else:
            p = (X - 0.6, rng.uniform(28, TOP - 4), rng.uniform(-Z + 5, Z - 5))
        z.add(box(size=(s, s, s), color=c, center=p))
    # one shooting star, north-east, streaking down-left
    sx, sy, sz_ = 60.0, 74.0, -Z + 1.0
    for i in range(4):
        s = 1.0 - i * 0.22
        z.add(box(size=(s, s, 0.4), color=STAR,
                  center=(sx + i * 2.2, sy + i * 1.5, sz_)))


def _moon(z: ZoneBuild, pos=MOON_POS, r=MOON_R):
    mx, my, mz = pos
    scale = r / MOON_R
    z.add(icosphere(radius=r, subdivisions=2, color=MOON,
                    center=(mx, my, mz)))
    # craters on the lab-facing side (down and slightly south)
    for dx, dy, dz, cr in [(-2.5, -8.6, 3.5, 2.2), (4.0, -8.0, 4.0, 1.5),
                           (0.5, -9.4, -1.5, 1.8), (-5.5, -7.0, 4.5, 1.2),
                           (6.0, -6.5, -4.5, 1.4), (-1.0, -6.8, 7.0, 1.0)]:
        z.add(icosphere(radius=cr * scale, subdivisions=1, color=CRATER,
                        center=(mx + dx * scale, my + dy * 0.98 * scale,
                                mz + dz * scale)))


def build(preview: bool = False):
    """`preview=True` keeps only the moon + shooting star (no dome bands)
    so the software-renderer previews aren't wrapped in a giant box."""
    z = ZoneBuild("sky", bounds=((-X, 0.0, -Z), (X, TOP + 2, Z)))
    if not preview:
        _dome(z)
        _stars(z)
        _moon(z)
    else:
        sx, sy, sz_ = 24.0, 34.0, -28.0
        for i in range(4):
            s = 1.0 - i * 0.22
            z.add(box(size=(s, s, 0.4), color=STAR,
                      center=(sx + i * 2.2, sy + i * 1.5, sz_)))
        # closer, smaller stand-in so the preview camera stays framed
        _moon(z, pos=(20.0, 42.0, -26.0), r=6.0)
    return z


def main():
    write_zone(build(), unlit=UNLIT)


if __name__ == "__main__":
    main()
