#!/bin/sh

set -eu

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

database_container=rsctf-db-1
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
compose_file=${RYTHME_COMPOSE_FILE:-$script_dir/compose.production.yml}
active_event=${RYTHME_ACTIVE_EVENT:-$script_dir/active-event.env}

[ -r "$compose_file" ] || { printf 'Rythme reconcile: Compose configuration is unavailable\n' >&2; exit 1; }
[ -r "$active_event" ] || { printf 'Rythme reconcile: active event configuration is unavailable\n' >&2; exit 1; }
set -a
. "$active_event"
set +a

case "${RSCTF_GAME_ID:-}" in ''|*[!0-9]*) exit 1 ;; esac
case "${RSCTF_CHALLENGE_ID:-}" in ''|*[!0-9]*) exit 1 ;; esac
gateway_digest=${RYTHME_GATEWAY_IMAGE#sha256:}
[ "${#gateway_digest}" -eq 64 ] || exit 1
case "$gateway_digest" in *[!0-9a-f]*) exit 1 ;; esac
game_id=$RSCTF_GAME_ID
challenge_id=$RSCTF_CHALLENGE_ID

database_user=$(docker inspect "$database_container" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^POSTGRES_USER=//p')
database_name=$(docker inspect "$database_container" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^POSTGRES_DB=//p')
[ -n "$database_user" ] && [ -n "$database_name" ] || { printf 'Rythme reconcile: database identity is unavailable\n' >&2; exit 1; }

target=$(docker exec "$database_container" psql -U "$database_user" -d "$database_name" -qAtc \
  "SELECT target.host || '|' || target.container_id || '|' || COALESCE(cycle.phase, 'Warm')
     FROM \"KothTargets\" target
     LEFT JOIN LATERAL (
       SELECT current_cycle.phase FROM \"KothCrownCycles\" current_cycle
        WHERE current_cycle.replacement_container_id = target.container_id
        ORDER BY current_cycle.cycle_number DESC LIMIT 1
     ) cycle ON true
    WHERE target.game_id = $game_id AND target.challenge_id = $challenge_id
    ORDER BY target.id DESC LIMIT 1")

target_host=${target%%|*}
remaining=${target#*|}
target_container=${remaining%%|*}
target_phase=${remaining#*|}

deactivate_gateway() {
  if docker inspect rsctf-rythme-gateway >/dev/null 2>&1; then
    printf 'Rythme reconcile: removing gateway without an active target\n'
    docker compose --env-file "$active_event" --project-name rsctf-rythme --file "$compose_file" down --remove-orphans
  fi
}

[ -n "$target" ] || { deactivate_gateway; exit 0; }
case "$target_host" in 10.13.40.*) ;; *) printf 'Rythme reconcile: refusing unexpected target host\n' >&2; exit 1 ;; esac
case "$target_phase" in Active|Warm) ;; *) deactivate_gateway; exit 0 ;; esac
[ "${#target_container}" -eq 64 ] || exit 1
case "$target_container" in *[!0-9a-f]*) exit 1 ;; esac
[ "$(docker inspect "$target_container" --format '{{.State.Running}}' 2>/dev/null || :)" = true ] || { deactivate_gateway; exit 0; }

environment_value() {
  docker inspect "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | sed -n "s/^$2=//p"
}
current_upstream=$(environment_value rsctf-rythme-gateway RYTHME_UPSTREAM)
current_target=$(environment_value rsctf-rythme-gateway RYTHME_TARGET_ID)
current_image=$(docker inspect rsctf-rythme-gateway --format '{{.Image}}' 2>/dev/null || :)
current_health=$(docker inspect rsctf-rythme-gateway --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || :)
if [ "$current_upstream" = "http://$target_host:1337" ] && [ "$current_target" = "$target_container" ] &&
  [ "$current_image" = "$RYTHME_GATEWAY_IMAGE" ] && [ "$current_health" = healthy ]; then
  exit 0
fi

temporary_event=${active_event}.$$.tmp
trap 'rm -f "$temporary_event"' 0 HUP INT TERM
{
  printf 'RSCTF_GAME_ID=%s\n' "$game_id"
  printf 'RSCTF_CHALLENGE_ID=%s\n' "$challenge_id"
  printf 'RYTHME_UPSTREAM=%s\n' "$target_host"
  printf 'RYTHME_TARGET_ID=%s\n' "$target_container"
  printf 'RYTHME_GATEWAY_IMAGE=%s\n' "$RYTHME_GATEWAY_IMAGE"
} > "$temporary_event"
chmod 600 "$temporary_event"
mv "$temporary_event" "$active_event"
trap - 0 HUP INT TERM

RYTHME_UPSTREAM=$target_host
RYTHME_TARGET_ID=$target_container
export RYTHME_UPSTREAM RYTHME_TARGET_ID
printf 'Rythme reconcile: activating target %.12s\n' "$target_container"
docker compose --env-file "$active_event" --project-name rsctf-rythme --file "$compose_file" \
  up -d --force-recreate --remove-orphans --wait --wait-timeout 120 gateway
