"""Render all five zones + minion into a single contact sheet for review."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def run(args):
    subprocess.check_call([sys.executable] + args)


def render_all(preview_dir: Path):
    preview_dir.mkdir(parents=True, exist_ok=True)
    zones = {
        "sports": ("sports.glb", "-24,2,-8", 16.0),
        "sleeping": ("sleeping.glb", "-24,2,8", 16.0),
        "machine_room": ("machine_room.glb", "0,3,0", 20.0),
        "kitchen": ("kitchen.glb", "24,2,-8", 16.0),
        "bathrooms": ("bathrooms.glb", "24,2,8", 16.0),
    }
    shots = []
    for zone, (glb, target, radius) in zones.items():
        iso = preview_dir / f"{zone}_iso.png"
        top = preview_dir / f"{zone}_top.png"
        run([
            str(ROOT / "tools/preview/render.py"),
            str(ROOT / f"assets/zones/{glb}"),
            str(iso),
            "--view", "iso", "--w", "640", "--h", "480",
            f"--target={target}", "--radius", str(radius),
            "--y-cut", "7.0",
        ])
        run([
            str(ROOT / "tools/preview/render.py"),
            str(ROOT / f"assets/zones/{glb}"),
            str(top),
            "--view", "top", "--w", "640", "--h", "480",
            f"--target={target.replace(',2,', ',0,')}",
            "--radius", str(radius * 0.9),
            "--y-cut", "6.0",
        ])
        shots.append((zone, iso, top))

    # Minion 3-up
    mfront = preview_dir / "minion_front.png"
    miso = preview_dir / "minion_iso.png"
    mhero = preview_dir / "minion_hero.png"
    for out, view in ((mfront, "front"), (miso, "iso"), (mhero, "hero")):
        run([
            str(ROOT / "tools/preview/render.py"),
            str(ROOT / "assets/minion/minion.glb"),
            str(out),
            "--view", view, "--w", "480", "--h", "640",
            f"--target=0,1,0", "--radius", "1.6",
        ])

    # Stitch a big grid
    tile_w, tile_h = 640, 480
    sheet = Image.new("RGB", (tile_w * 2, tile_h * 5 + 640),
                      (20, 22, 28))
    for i, (zone, iso, top) in enumerate(shots):
        sheet.paste(Image.open(iso), (0, i * tile_h))
        sheet.paste(Image.open(top), (tile_w, i * tile_h))

    # Minions at the bottom
    minion_row = Image.new("RGB", (tile_w * 2, 640), (20, 22, 28))
    for i, p in enumerate([mfront, miso, mhero]):
        im = Image.open(p).resize((480, 640))
        minion_row.paste(im, (i * 420 + 30, 0))
    sheet.paste(minion_row, (0, tile_h * 5))

    out = preview_dir / "contact_sheet.png"
    sheet.save(out)
    print(f"contact sheet -> {out}")


if __name__ == "__main__":
    render_all(ROOT / "tools/previews")
