#!/bin/bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: capture-lobby.sh <Rythme-linux-executable> <output-directory>" >&2
  exit 2
fi

app="$(realpath "$1")"
output="$(realpath -m "$2")"
mkdir -p "$output"

for command in xvfb-run xdotool ffmpeg; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: $command is required for lobby visual QA" >&2
    exit 1
  fi
done

capture() {
  local fixture="$1"
  local width="$2"
  local height="$3"
  local destination="$output/lobby-$fixture-${width}x${height}.png"
  local profile
  profile="$(mktemp -d /tmp/rythme-lobby-capture.XXXXXX)"

  if ! xvfb-run -a -s "-screen 0 ${width}x${height}x24" bash -c '
    set -eu
    app="$1"
    profile="$2"
    fixture="$3"
    width="$4"
    height="$5"
    destination="$6"
    export HOME="$profile/home"
    export XDG_DATA_HOME="$profile/data"
    export DOTNET_ROLL_FORWARD=Major
    export RYTHME_UI_CAPTURE="$fixture"
    mkdir -p "$HOME" "$XDG_DATA_HOME"

    "$app" >"$profile/stdout.log" 2>&1 &
    pid=$!
    trap "kill $pid 2>/dev/null || true" EXIT

    window=""
    attempt=0
    while [ "$attempt" -lt 300 ]; do
      window=$(xdotool search --onlyvisible --pid "$pid" 2>/dev/null | head -1 || true)
      [ -n "$window" ] && break
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
      attempt=$((attempt + 1))
    done
    test -n "$window"
    xdotool windowmove "$window" 0 0
    xdotool windowsize --sync "$window" "$width" "$height"

    attempt=0
    while [ "$attempt" -lt 400 ]; do
      grep -Rqs "entered RythmeLobbyScreen" "$profile/data/rythme/logs" 2>/dev/null && break
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
      attempt=$((attempt + 1))
    done
    grep -Rqs "entered RythmeLobbyScreen" "$profile/data/rythme/logs"
    sleep 3
    ffmpeg -loglevel error -y -f x11grab -video_size "${width}x${height}" \
      -i "$DISPLAY" -frames:v 1 "$destination"
  ' _ "$app" "$profile" "$fixture" "$width" "$height" "$destination"; then
    tail -120 "$profile/stdout.log" >&2 || true
    rm -rf "$profile"
    return 1
  fi

  if grep -REq \
    'System\.(ArgumentNullException|NullReferenceException|ObjectDisposedException)|Rythme startup failure|unrecoverable error' \
    "$profile/data/rythme/logs" "$profile/stdout.log" 2>/dev/null; then
    echo "error: $fixture ${width}x${height} visual QA recorded a fatal exception" >&2
    rm -rf "$profile"
    return 1
  fi

  rm -rf "$profile"
  echo "captured $destination"
}

for fixture in login reveal; do
  capture "$fixture" 1024 768
  capture "$fixture" 1366 768
  capture "$fixture" 1920 1080
done
