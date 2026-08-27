#!/bin/bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: startup-smoke.sh <Rythme-linux-executable> <profile-directory>" >&2
  exit 2
fi

APP="$(realpath "$1")"
PROFILE="$(realpath -m "$2")"
OUTPUT="$PROFILE/stdout.log"

for command in xvfb-run xdotool; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "error: $command is required for the real lobby startup smoke test" >&2
    exit 1
  fi
done

mkdir -p "$PROFILE/home" "$PROFILE/data"

set +e
xvfb-run -a -s '-screen 0 1920x1080x24' bash -c '
  set -u
  app="$1"
  profile="$2"
  export HOME="$profile/home"
  export XDG_DATA_HOME="$profile/data"
  export DOTNET_ROLL_FORWARD=Major

  # Begin with a held button outside the future window. This exercises the
  # Windows failure mode where a position event arrives without a matching
  # button-down input queue.
  xdotool mousemove 1900 1050 mousedown 1
  "$app" >"$profile/stdout.log" 2>&1 &
  app_pid=$!

  window=""
  tries=0
  while [ "$tries" -lt 150 ]; do
    window=$(xdotool search --onlyvisible --pid "$app_pid" 2>/dev/null | head -1)
    [ -n "$window" ] && break
    kill -0 "$app_pid" 2>/dev/null || break
    sleep 0.1
    tries=$((tries + 1))
  done

  if [ -z "$window" ]; then
    xdotool mouseup 1 >/dev/null 2>&1 || true
    wait "$app_pid"
    exit $?
  fi

  xdotool mousemove --window "$window" 80 80
  xdotool mousemove_relative -- 60 40
  xdotool mouseup 1
  xdotool key --window "$window" alt+Return >/dev/null 2>&1 || true

  # Repeat the outside-window gesture while the menu and lobby transition.
  cycle=0
  while [ "$cycle" -lt 6 ]; do
    kill -0 "$app_pid" 2>/dev/null || break
    xdotool mousemove 1900 1050
    xdotool mousedown 1
    xdotool mousemove --window "$window" 100 100
    xdotool mousemove_relative -- 45 35
    xdotool mouseup 1
    cycle=$((cycle + 1))
    sleep 0.25
  done

  entered=0
  tries=0
  while [ "$tries" -lt 300 ]; do
    kill -0 "$app_pid" 2>/dev/null || break
    if grep -Rqs "entered RythmeLobbyScreen" "$profile/data/rythme/logs" 2>/dev/null; then
      entered=1
      break
    fi
    sleep 0.1
    tries=$((tries + 1))
  done

  if [ "$entered" -eq 1 ]; then
    sleep 3
  fi

  if kill -0 "$app_pid" 2>/dev/null; then
    kill "$app_pid"
    wait "$app_pid" >/dev/null 2>&1
    app_result=124
  else
    wait "$app_pid"
    app_result=$?
  fi

  [ "$entered" -eq 1 ] && [ "$app_result" -eq 124 ]
' _ "$APP" "$PROFILE"
result=$?
set -e

if [ "$result" -ne 0 ]; then
  echo "error: Rythme exited before the clean-profile lobby smoke test completed" >&2
  tail -120 "$OUTPUT" >&2 || true
  exit 1
fi

if grep -REq \
  'System\.(ArgumentNullException|NullReferenceException|ObjectDisposedException)|Rythme startup failure|unrecoverable error' \
  "$PROFILE/data/rythme/logs" "$OUTPUT" 2>/dev/null; then
  echo "error: the lobby smoke test recorded a fatal startup exception" >&2
  grep -REn \
    'System\.(ArgumentNullException|NullReferenceException|ObjectDisposedException)|Rythme startup failure|unrecoverable error' \
    "$PROFILE/data/rythme/logs" "$OUTPUT" >&2 || true
  exit 1
fi

echo "clean-profile lobby and orphaned-mouse startup smoke test passed"
