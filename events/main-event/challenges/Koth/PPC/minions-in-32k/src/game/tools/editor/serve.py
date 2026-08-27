"""Tiny HTTP server for the browser-based arena editor.
Run:
    python3 tools/editor/serve.py [--port 8080]

Then open http://localhost:8080/

Endpoints:
    GET /                 → the editor HTML shell
    GET /editor.js, etc.  → static assets next to this file
    GET /arena.json       → runs `cargo run --release -p sim --example dump_arena`
                            and returns the JSON. Cached per-request.
    POST /save            → writes the JSON body to
                            `tools/editor/edits.json` (for later review/apply
                            into the sim-side scene files) and responds with
                            the written path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EDITS_PATH = HERE / "edits.json"
SCENES_DIR = ROOT / "assets" / "scenes"
STATIC = {
    "/": "index.html",
    "/index.html": "index.html",
    "/editor.js": "editor.js",
    "/style.css": "style.css",
}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def classify_wall(w):
    """Mirror of sim::Arena::classify_wall. Returns the themed zone name."""
    if "zone" in w:
        return w["zone"]
    cx = (w["min"][0] + w["max"][0]) * 0.5
    cz = (w["min"][2] + w["max"][2]) * 0.5
    if cz > 16.5:
        return "secret"
    if abs(cx) >= 35.5 or abs(cz) >= 15.5:
        return "outer"
    if cx < -12:
        return "sports" if cz < 0 else "sleeping"
    if cx > 12:
        return "kitchen" if cz < 0 else "bathrooms"
    return "machine_room"


def classify_point(p):
    """Point-classify: build a degenerate AABB and reuse classify_wall."""
    return classify_wall({
        "min": [p[0] - 0.01, p[1], p[2] - 0.01],
        "max": [p[0] + 0.01, p[1], p[2] + 0.01],
    })


ALL_ZONES = ("machine_room", "sports", "bathrooms", "sleeping", "kitchen", "secret", "outer")
ZONE_BOUNDS = {
    "machine_room": ([-12.0, 0.0, -16.0], [ 12.0, 8.0, 16.0]),
    "sports":       ([-36.0, 0.0, -16.0], [-12.0, 8.0,  0.0]),
    "sleeping":     ([-36.0, 0.0,   0.0], [-12.0, 8.0, 16.0]),
    "kitchen":      ([ 12.0, 0.0, -16.0], [ 36.0, 8.0,  0.0]),
    "bathrooms":    ([ 12.0, 0.0,   0.0], [ 36.0, 8.0, 16.0]),
    "secret":       ([-12.0, 0.0,  16.0], [ 12.0, 8.0, 26.0]),
    "outer":        ([-36.0, 0.0, -16.0], [ 36.0, 8.0, 16.0]),
}


def save_per_zone(arena):
    """Split the flat arena dict into assets/scenes/<zone>.json files."""
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    buckets = {z: {
        "name": z,
        "bounds": {"min": ZONE_BOUNDS[z][0], "max": ZONE_BOUNDS[z][1]},
        "walls": [],
        "jump_pads": [],
        "spawn_points": [],
        "props": [],
    } for z in ALL_ZONES}
    for w in arena["walls"]:
        z = classify_wall(w)
        buckets.setdefault(z, {
            "name": z, "bounds": {"min": [0, 0, 0], "max": [0, 0, 0]},
            "walls": [], "jump_pads": [], "spawn_points": [], "props": []
        })
        buckets[z]["walls"].append({"min": w["min"], "max": w["max"]})
    for p in arena["jump_pads"]:
        z = classify_point(p["center"])
        buckets[z]["jump_pads"].append({
            "center": p["center"],
            "radius": p["radius"],
            "velocity": p["velocity"],
        })
    for s in arena["spawn_points"]:
        z = classify_point(s)
        buckets[z]["spawn_points"].append(list(s))

    written = []
    manifest = {
        "floor_y": arena["floor_y"],
        "ceiling_y": arena["ceiling_y"],
        "zones": list(ALL_ZONES),
    }
    mpath = SCENES_DIR / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2) + "\n")
    written.append(mpath)
    for z in ALL_ZONES:
        zpath = SCENES_DIR / f"{z}.json"
        zpath.write_text(json.dumps(buckets[z], indent=2) + "\n")
        written.append(zpath)
    return written


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: D401
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, ctype: str = "text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in STATIC:
            name = STATIC[path]
            target = HERE / name
            if not target.is_file():
                return self._send(404, f"missing: {target}".encode())
            data = target.read_bytes()
            return self._send(200, data, MIME[target.suffix])
        if path == "/arena.json":
            return self._serve_arena()
        if path.startswith("/assets/"):
            # Serve repo assets (zone glbs etc.) so the editor can render
            # the same themed meshes the game client loads.
            rel = path[len("/assets/"):]
            target = (ROOT / "assets" / rel).resolve()
            if not str(target).startswith(str((ROOT / "assets").resolve())):
                return self._send(403, b"forbidden")
            if not target.is_file():
                return self._send(404, f"missing: {rel}".encode())
            ctype = {
                ".glb": "model/gltf-binary",
                ".gltf": "model/gltf+json",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".bin": "application/octet-stream",
            }.get(target.suffix, "application/octet-stream")
            return self._send(200, target.read_bytes(), ctype)
        return self._send(404, f"no route: {path}".encode())

    def do_POST(self):  # noqa: N802
        if self.path == "/save":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as e:
                return self._send(400, f"bad json: {e}".encode())
            for key in ("walls", "jump_pads", "spawn_points", "floor_y", "ceiling_y"):
                if key not in parsed:
                    return self._send(400, f"missing {key!r}".encode())
            # Full-arena backup: always keep a flat edits.json so you can diff
            # a round-trip. Per-zone split is the authoritative write path.
            EDITS_PATH.write_text(json.dumps(parsed, indent=2) + "\n")
            try:
                written = save_per_zone(parsed)
            except Exception as e:  # noqa: BLE001
                return self._send(500, f"zone split failed: {e}".encode())
            rels = [str(p.relative_to(ROOT)) for p in written]
            return self._send(
                200, ("\n".join(rels)).encode(), "text/plain; charset=utf-8"
            )
        return self._send(404, f"no route: {self.path}".encode())

    def _serve_arena(self):
        try:
            out = subprocess.run(
                ["cargo", "run", "--release", "-p", "sim", "--example", "dump_arena"],
                cwd=str(ROOT),
                capture_output=True,
                check=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as e:
            msg = (e.stderr or b"").decode() or f"cargo failed: {e}"
            return self._send(500, msg.encode())
        # The binary may also print compile output; locate the first `{` as
        # the JSON document start.
        data = out.stdout
        idx = data.find(b"{")
        if idx < 0:
            return self._send(500, b"no JSON in dump_arena output")
        return self._send(200, data[idx:], MIME[".json"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"editor up at http://{args.host}:{args.port}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", file=sys.stderr)


if __name__ == "__main__":
    main()
