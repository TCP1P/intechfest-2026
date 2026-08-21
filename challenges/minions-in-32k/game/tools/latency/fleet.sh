#!/bin/bash
# 10 parallel matches: 8 equal elite bots, one-way latency ladder
# 25..200ms in 25ms steps (no 0ms tier — unrealistic). 5 minutes.
cd /home/mixy/projects/minions-in-16k
pkill -x server 2>/dev/null; pkill -x headless-client 2>/dev/null; pkill -x latency-proxy 2>/dev/null
sleep 1
pkill -9 -x server 2>/dev/null; pkill -9 -x headless-client 2>/dev/null; pkill -9 -x latency-proxy 2>/dev/null
sleep 3
rm -rf replays/fleet; mkdir -p replays/fleet

pids=()
for i in $(seq 0 9); do
  sport=$((5000+i))
  mkdir -p "replays/fleet/m$i"
  ./target/release/server --bind 127.0.0.1:$sport --wt-bind 127.0.0.1:$((5100+i)) \
      --arena gru-lab-v3 --seed $i --replay-dir "replays/fleet/m$i" \
      > /tmp/fleet-srv-$i.log 2>&1 &
  pids+=($!)
done
sleep 2

for i in $(seq 0 9); do
  sport=$((5000+i))
  for k in $(seq 1 8); do
    d=$((k*25))
    pport=$((6000+i*10+k))
    ./target/release/latency-proxy --listen 127.0.0.1:$pport --upstream 127.0.0.1:$sport \
        --delay-ms $d > /dev/null 2>&1 &
    pids+=($!)
  done
done
sleep 1

for i in $(seq 0 9); do
  for k in $(seq 1 8); do
    d=$((k*25))
    pport=$((6000+i*10+k))
    ./target/release/headless-client --remote 127.0.0.1:$pport --count 1 --elite-count 1 \
        --elite-name lat$(printf %03d $d) > /dev/null 2>&1 &
    pids+=($!)
  done
done

sleep 300
kill "${pids[@]}" 2>/dev/null
sleep 3
echo FLEET-DONE
