#!/bin/sh

set -eu

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

compose_file=/root/rsctf-production/intechfest/rythme/compose.yml
active_event=/root/rsctf-production/intechfest/rythme/active-event.env

[ -r "$active_event" ] || {
  printf 'Rythme reconcile: active event configuration is unavailable\n' >&2
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
case "${RYTHME_IMAGE:-}" in
  sha256:????????????????????????????????????????????????????????????????) ;;
  *) exit 1 ;;
esac

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

coordinator_image=$(docker inspect rsctf-rythme-coordinator --format '{{.Image}}' 2>/dev/null || :)
observer_game=$(environment_value rsctf-rythme-observer RSCTF_GAME_ID)
observer_challenge=$(environment_value rsctf-rythme-observer RSCTF_CHALLENGE_ID)

if [ "$coordinator_image" = "$RYTHME_IMAGE" ] &&
  [ "$observer_game" = "$RSCTF_GAME_ID" ] &&
  [ "$observer_challenge" = "$RSCTF_CHALLENGE_ID" ] &&
  container_healthy rsctf-rythme-coordinator &&
  container_healthy rsctf-rythme-observer &&
  container_healthy rsctf-rythme-gateway; then
  exit 0
fi

printf 'Rythme reconcile: restoring persistent private arena\n'
docker compose \
  --env-file "$active_event" \
  --project-name rsctf-rythme \
  --file "$compose_file" \
  up -d --remove-orphans --wait --wait-timeout 90
