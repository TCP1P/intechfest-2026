#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="minions-in-32k:local"
CONTAINER="minions-in-32k-local"
HTTP_PORT="${MINIONS_HTTP_PORT:-32032}"
UDP_PORT="${MINIONS_UDP_PORT:-4433}"

if [[ "${1:-}" == "stop" ]]; then
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    echo "[+] Local arena stopped"
    exit 0
fi

echo "[*] Building the official upstream arena image"
docker build --tag "${IMAGE}" "${ROOT}"

RUNTIME_SEED="flag{local-minions-$(openssl rand -hex 24)}"
REPORT_TOKEN="$(
    printf '%s' 'minions-in-32k/report-token/v1' |
        openssl dgst -sha256 -mac HMAC -macopt "key:${RUNTIME_SEED}" -r |
        awk '{print $1}'
)"
ALPHA="$(openssl rand -hex 24)"
BETA="$(openssl rand -hex 24)"
GAMMA="$(openssl rand -hex 24)"
DELTA="$(openssl rand -hex 24)"
ECHO="$(openssl rand -hex 24)"
FOXTROT="$(openssl rand -hex 24)"
GOLF="$(openssl rand -hex 24)"
HOTEL="$(openssl rand -hex 24)"
START_BODY="$(printf '{"players":[{"uuid":"alpha","key":"%s","name":"alpha","bot":false},{"uuid":"npc-1","key":"%s","name":"NPC_1","bot":true},{"uuid":"npc-2","key":"%s","name":"NPC_2","bot":true},{"uuid":"npc-3","key":"%s","name":"NPC_3","bot":true},{"uuid":"npc-4","key":"%s","name":"NPC_4","bot":true},{"uuid":"npc-5","key":"%s","name":"NPC_5","bot":true},{"uuid":"npc-6","key":"%s","name":"NPC_6","bot":true},{"uuid":"npc-7","key":"%s","name":"NPC_7","bot":true}]}' "${ALPHA}" "${BETA}" "${GAMMA}" "${DELTA}" "${ECHO}" "${FOXTROT}" "${GOLF}" "${HOTEL}")"

docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
docker run -d \
    --name "${CONTAINER}" \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=128m,uid=32032,gid=32032,mode=700 \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 128 \
    --memory 512m \
    --cpus 2 \
    -p "${HTTP_PORT}:8080/tcp" \
    -p "${UDP_PORT}:4433/udp" \
    -e "RSCTF_FLAG=${RUNTIME_SEED}" \
    -e "REPORT_URL=http://127.0.0.1:9/report" \
    "${IMAGE}" >/dev/null

for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz" >/dev/null; then
        break
    fi
    sleep 1
done
CERT_HASH="$(curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz" | jq -er '.certHash')"
printf '%s' "${START_BODY}" | curl -fsS -X POST \
    -H "Authorization: Bearer ${REPORT_TOKEN}" \
    -H 'Content-Type: application/json' \
    --data-binary @- \
    "http://127.0.0.1:${HTTP_PORT}/internal/start" >/dev/null
curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz" |
    jq -e '.status == "running" and .players == 8 and .npcs == 7' >/dev/null

echo "[+] Local arena running"
echo "    Health: http://127.0.0.1:${HTTP_PORT}/healthz"
echo "    Client: game/target/release/client --remote 127.0.0.1:${UDP_PORT} --name alpha --key ${ALPHA} --cert-hash ${CERT_HASH}"
