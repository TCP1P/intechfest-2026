#!/bin/bash
# Build and run the single Rythme coordinator used by local challenge validation.

set -euo pipefail
cd "$(dirname "$0")"

IMAGE_NAME="rythme"
CONTAINER_NAME="rythme-container"
HOST_PORT="${RYTHME_HOST_PORT:-1337}"
PORT_MAPPING="$HOST_PORT:1337"

stop() {
  echo "[*] stopping $CONTAINER_NAME"
  docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
}

if [ "${1:-}" = "stop" ]; then
  stop
  echo "[+] application stopped"
  exit 0
fi

echo "[*] building $IMAGE_NAME"
docker build --tag "$IMAGE_NAME" --file Dockerfile .

stop

echo "[*] starting $CONTAINER_NAME on http://127.0.0.1:$HOST_PORT"
DOCKER_ARGS=(
  --detach
  --name "$CONTAINER_NAME"
  --publish "$PORT_MAPPING"
  --env RYTHME_LOCAL_AUTH=true
  --env RYTHME_ENROLLMENT_DURATION=8s
  --env RYTHME_READY_DURATION=30s
  --env RYTHME_SUBMISSION_DURATION=5s
  --env RYTHME_RESULTS_DURATION=5s
)

if [ -n "${RYTHME_BEATMAP_CACHE:-}" ]; then
  if [ ! -d "$RYTHME_BEATMAP_CACHE" ]; then
    echo "error: RYTHME_BEATMAP_CACHE is not a directory: $RYTHME_BEATMAP_CACHE" >&2
    exit 1
  fi
  BEATMAP_CACHE_PATH="$(cd "$RYTHME_BEATMAP_CACHE" && pwd -P)"
  echo "[*] mounting organizer beatmap cache from $BEATMAP_CACHE_PATH"
  DOCKER_ARGS+=(
    --mount "type=bind,src=$BEATMAP_CACHE_PATH,dst=/app/beatmaps,readonly"
  )
fi

docker run "${DOCKER_ARGS[@]}" \
  "$IMAGE_NAME" >/dev/null

echo "[+] arena started"
