"""Secret showcase room behind the south doorway.

Collision + zone metadata only — no glb. The client deliberately renders
this room as plain cuboids from the arena walls (it's the podium room for
the minion animation showcase), so there is nothing visual to bake.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zones.common import ZoneBuild, write_zone

EMIT_GLB = False


def build():
    z = ZoneBuild("secret", bounds=((-12.0, 0.0, 16.0), (12.0, 8.0, 26.0)))
    z.collide((0.0, 4.0, 26.0), (23.5, 8.0, 0.5))    # back wall
    z.collide((-12.0, 4.0, 21.25), (0.5, 8.0, 10.5))  # west wall
    z.collide((12.0, 4.0, 21.25), (0.5, 8.0, 10.5))   # east wall
    # mega health gives the showcase dead-end a reason to exist
    z.powerup("mega_health", 0.0, 0.0, 21.0)
    return z


def main():
    write_zone(build(), glb=EMIT_GLB)


if __name__ == "__main__":
    main()
