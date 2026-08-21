#!/usr/bin/env python3
"""Produce the metadata-only pool embedded in the black-box player client."""

from __future__ import annotations

import json
import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: clientpool.py <server-pool.json> <client-pool.json>", file=sys.stderr)
        return 2

    source = pathlib.Path(sys.argv[1])
    destination = pathlib.Path(sys.argv[2])
    pool = json.loads(source.read_text(encoding="utf-8"))
    songs = pool.get("songs")
    if not isinstance(songs, list) or not songs:
        raise ValueError("server pool has no songs")
    for song in songs:
        if not isinstance(song, dict) or not isinstance(song.get("notes"), list) or not song["notes"]:
            raise ValueError("server pool song has no hit objects")
        song.pop("notes")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(pool, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
