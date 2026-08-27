# latency testing

`fleet.sh` runs 10 parallel matches of 8 equal elite bots on a one-way
latency ladder (25..200 ms in 25 ms steps, one latency-proxy per bot)
for 5 minutes each; `aggregate.py` averages the per-replay leaderboards
into a per-tier table. `cargo run -p replay --example fire_audit`
breaks a replay down into inputs / fire presses / granted shots per
player — this is what isolated the tick-boundary input-loss bug.

Findings live in the git log: lag compensation halved the latency kill
gap; the fire-press input buffer + same-tick press merge fixed tiers
whose RTT phase grazed the server tick boundary.
