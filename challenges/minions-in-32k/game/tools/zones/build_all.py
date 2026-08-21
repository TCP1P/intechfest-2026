"""Build the whole level: every zone's glb + scene JSON + the manifest.

This is the single entry point after editing any zone script:

    PYTHONPATH=tools python3 tools/zones/build_all.py

The generated assets/scenes/*.json are the authoritative arena — the sim
embeds them at compile time (`Arena::gru_lab_v3()`), so run `cargo build`
afterwards to pick up gameplay changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zones import (
    sports, sleeping, machine_room, kitchen, bathrooms, hall, secret, sky,
)
from zones.common import write_zone

ZONE_MODULES = [sports, sleeping, machine_room, kitchen, bathrooms, hall,
                secret, sky]

FLOOR_Y = 0.0
CEILING_Y = 20.0


def main():
    root = Path(__file__).resolve().parents[2]
    names = []
    for m in ZONE_MODULES:
        z = m.build()
        write_zone(z, glb=getattr(m, "EMIT_GLB", True),
                   unlit=getattr(m, "UNLIT", False))
        names.append(z.name)

    scenes = root / "assets" / "scenes"
    with open(scenes / "manifest.json", "w") as f:
        json.dump({"floor_y": FLOOR_Y, "ceiling_y": CEILING_Y,
                   "zones": names}, f, indent=2)
        f.write("\n")
    # drop zone files that are no longer in the manifest
    for stale in scenes.glob("*.json"):
        if stale.stem != "manifest" and stale.stem not in names:
            stale.unlink()
            print(f"removed stale {stale.name}")
    print(f"manifest: {len(names)} zones")

    from level import check
    check.main()


if __name__ == "__main__":
    main()
