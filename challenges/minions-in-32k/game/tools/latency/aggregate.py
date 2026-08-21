#!/usr/bin/env python3
"""Aggregate the latency-ladder fleet: mean stats per latency tier."""
import glob
import re
import statistics as st
import subprocess

rows = {}  # name -> list of (rank, kills, deaths, shots, acc)
replays = sorted(glob.glob("replays/fleet/m*/*.mrpl"))
print(f"{len(replays)} replays")
for path in replays:
    out = subprocess.run(
        ["./target/release/spectator", "leaderboard", path],
        capture_output=True, text=True).stdout
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(lat\d+)\s+(\d+)\s+(\d+)\s+[\d.]+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", line)
        if m:
            rank, name, kills, deaths, dmgp, dmgm, shots, acc = m.groups()
            rows.setdefault(name, []).append(
                (int(rank), int(kills), int(deaths), int(shots), int(acc)))

print(f"{'tier':8} {'n':>2} {'rank':>5} {'kills':>6} {'deaths':>6} {'shots':>6} {'acc%':>5}")
for name in sorted(rows):
    r = rows[name]
    print(f"{name:8} {len(r):>2} "
          f"{st.mean(x[0] for x in r):>5.1f} "
          f"{st.mean(x[1] for x in r):>6.1f} "
          f"{st.mean(x[2] for x in r):>6.1f} "
          f"{st.mean(x[3] for x in r):>6.1f} "
          f"{st.mean(x[4] for x in r):>5.1f}")
