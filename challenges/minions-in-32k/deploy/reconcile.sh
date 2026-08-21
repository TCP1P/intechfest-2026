#!/bin/sh

set -eu

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

database_container=rsctf-db-1
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ -n "${MINIONS_COMPOSE_FILE:-}" ]; then
  compose_file=$MINIONS_COMPOSE_FILE
elif [ -r "$script_dir/compose.production.yml" ]; then
  compose_file=$script_dir/compose.production.yml
else
  compose_file=$script_dir/compose.yml
fi
active_event=${MINIONS_ACTIVE_EVENT:-$script_dir/active-event.env}

[ -r "$compose_file" ] || {
  printf 'Minions reconcile: Compose configuration is unavailable\n' >&2
  exit 1
}
[ -r "$active_event" ] || {
  printf 'Minions reconcile: active event configuration is unavailable\n' >&2
  exit 1
}
set -a
. "$active_event"
set +a

case "${RSCTF_GAME_ID:-}" in
  ''|*[!0-9]*) exit 1 ;;
esac
case "${RSCTF_CHALLENGE_ID:-}" in
  ''|*[!0-9]*) exit 1 ;;
esac
bridge_digest=${MINIONS_BRIDGE_IMAGE#sha256:}
[ "${#bridge_digest}" -eq 64 ] || exit 1
case "$bridge_digest" in
  *[!0-9a-f]*) exit 1 ;;
esac
game_id=$RSCTF_GAME_ID
challenge_id=$RSCTF_CHALLENGE_ID

database_user=$(docker inspect "$database_container" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' |
  sed -n 's/^POSTGRES_USER=//p')
database_name=$(docker inspect "$database_container" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' |
  sed -n 's/^POSTGRES_DB=//p')

[ -n "$database_user" ] && [ -n "$database_name" ] || {
  printf 'Minions reconcile: database identity is unavailable\n' >&2
  exit 1
}

target=$(docker exec "$database_container" psql \
  -U "$database_user" -d "$database_name" -qAtc \
  "SELECT target.host || '|' || target.container_id || '|' || COALESCE(cycle.phase, 'Warm')
     FROM \"KothTargets\" target
     LEFT JOIN LATERAL (
       SELECT current_cycle.phase
         FROM \"KothCrownCycles\" current_cycle
        WHERE current_cycle.replacement_container_id = target.container_id
        ORDER BY current_cycle.cycle_number DESC
        LIMIT 1
     ) cycle ON true
    WHERE target.game_id = $game_id
      AND target.challenge_id = $challenge_id
    ORDER BY target.id DESC
    LIMIT 1")

target_host=${target%%|*}
remaining=${target#*|}
target_container=${remaining%%|*}
target_phase=${remaining#*|}

deactivate_bridge() {
  if docker inspect rsctf-minions-bridge >/dev/null 2>&1; then
    printf 'Minions reconcile: removing bridge without an active target\n'
    docker compose \
      --env-file "$active_event" \
      --project-name rsctf-minions \
      --file "$compose_file" \
      down --remove-orphans
  fi
}

[ -n "$target" ] || {
  deactivate_bridge
  exit 0
}

case "$target_host" in
  10.13.40.*) ;;
  *)
    printf 'Minions reconcile: refusing unexpected target host\n' >&2
    exit 1
    ;;
esac
case "$target_phase" in
  Active|Warm) ;;
  *)
    deactivate_bridge
    exit 0
    ;;
esac
[ "${#target_container}" -eq 64 ] || exit 1
case "$target_container" in
  *[!0-9a-f]*) exit 1 ;;
esac
if [ "$(docker inspect "$target_container" --format '{{.State.Running}}' 2>/dev/null || :)" != true ]; then
  deactivate_bridge
  exit 0
fi

container_running() {
  [ "$(docker inspect "$1" --format '{{.State.Running}}' 2>/dev/null || :)" = true ]
}

container_healthy() {
  [ "$(docker inspect "$1" --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || :)" = healthy ]
}

environment_value() {
  docker inspect "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null |
    sed -n "s/^$2=//p"
}

current_host=$(environment_value rsctf-minions-bridge MINIONS_UPSTREAM)
current_target=$(environment_value rsctf-minions-bridge MINIONS_TARGET_ID)
current_game=$(environment_value rsctf-minions-bridge RSCTF_GAME_ID)
current_challenge=$(environment_value rsctf-minions-bridge RSCTF_CHALLENGE_ID)
current_image=$(docker inspect rsctf-minions-bridge \
  --format '{{.Image}}' 2>/dev/null || :)
current_project=$(docker inspect rsctf-minions-bridge \
  --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || :)

if [ "$current_host" = "$target_host:4433" ] &&
  [ "$current_target" = "$target_container" ] &&
  [ "$current_game" = "$game_id" ] &&
  [ "$current_challenge" = "$challenge_id" ] &&
  [ "$current_image" = "$MINIONS_BRIDGE_IMAGE" ] &&
  [ "$current_project" = rsctf-minions ] &&
  container_running rsctf-minions-bridge; then
  if container_healthy rsctf-minions-bridge; then
    exit 0
  fi
  printf 'Minions reconcile: replacing unhealthy bridge for target %s\n' \
    "$(printf '%s' "$target_container" | cut -c1-12)"
else
  printf 'Minions reconcile: activating target %s\n' \
    "$(printf '%s' "$target_container" | cut -c1-12)"
fi

temporary_event="${active_event}.$$.tmp"
trap 'rm -f "$temporary_event"' 0 HUP INT TERM
{
  printf 'RSCTF_GAME_ID=%s\n' "$game_id"
  printf 'RSCTF_CHALLENGE_ID=%s\n' "$challenge_id"
  printf 'MINIONS_UPSTREAM=%s\n' "$target_host"
  printf 'MINIONS_TARGET_ID=%s\n' "$target_container"
  printf 'MINIONS_BRIDGE_IMAGE=%s\n' "$MINIONS_BRIDGE_IMAGE"
} > "$temporary_event"
chmod 600 "$temporary_event"
mv "$temporary_event" "$active_event"
trap - 0 HUP INT TERM

# Values sourced at startup take precedence over --env-file interpolation in
# Docker Compose. Publish the newly selected target into this process as well,
# otherwise the first reconciliation after a crown rotation recreates the
# bridge against the previous target and a second timer pass is required.
MINIONS_UPSTREAM=$target_host
MINIONS_TARGET_ID=$target_container
export MINIONS_UPSTREAM MINIONS_TARGET_ID

docker compose \
  --env-file "$active_event" \
  --project-name rsctf-minions \
  --file "$compose_file" \
  up -d --force-recreate --remove-orphans --wait --wait-timeout 120 bridge
