#!/usr/bin/env python3
"""Tiny stdin dashboard for server tracing output."""

from __future__ import annotations

import collections
import re
import shutil
import sys
import time


PATTERNS = {
    "joins": re.compile(r"player joined"),
    "leaves": re.compile(r"player left"),
    "flag_flips": re.compile(r"feature flag flip"),
    "connection_drops": re.compile(r"connection dropped"),
    "rtt_drops": re.compile(r"RTT above limit|rtt too high"),
    "broadcast_lag": re.compile(r"broadcast lag"),
    "oversized_or_rate": re.compile(r"oversized message|input rate exceeded"),
    "replays": re.compile(r"replay writer open"),
}


def clear() -> None:
    sys.stdout.write("\033[2J\033[H")


def render(counts: collections.Counter[str], recent: collections.deque[str]) -> None:
    clear()
    width = shutil.get_terminal_size((96, 24)).columns
    print("minions-in-16k telemetry".ljust(width, "="))
    for key in PATTERNS:
        print(f"{key:18} {counts[key]:>8}")
    print("-" * width)
    for line in recent:
        print(line[-width:])
    sys.stdout.flush()


def main() -> int:
    counts: collections.Counter[str] = collections.Counter()
    recent: collections.deque[str] = collections.deque(maxlen=12)
    last_render = 0.0
    for raw in sys.stdin:
        line = raw.rstrip()
        if not line:
            continue
        recent.append(line)
        for key, pattern in PATTERNS.items():
            if pattern.search(line):
                counts[key] += 1
        now = time.monotonic()
        if now - last_render > 0.25:
            render(counts, recent)
            last_render = now
    render(counts, recent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
