#!/bin/sh
# rsctf Attack & Defense — self-hosted setup for "Yani-netlink"
# Run it:  sh setup.sh        (needs docker + docker compose)
set -e
umask 077
DIR='rsctf-byoc-35-128'
if [ -L "$DIR" ]; then echo 'Refusing a symlinked BYOC setup directory.' >&2; exit 1; fi
if [ -e "$DIR" ] && [ ! -d "$DIR" ]; then echo 'The BYOC setup path exists but is not a directory.' >&2; exit 1; fi
mkdir -p "$DIR"
if [ "$(stat -c '%u' "$DIR" 2>/dev/null)" != "$(id -u)" ]; then echo 'The BYOC setup directory belongs to another user.' >&2; exit 1; fi
chmod 700 "$DIR"
cd "$DIR"
rm -f docker-compose.yml rsctf-ad.conf

SERVICE_IMAGE=""
echo '[1/4] Fetching the real service image from the game server (best-effort)...'
REVIEWED_IMAGE='rsctf-byoc-35-128-service:reviewed-07cb2e7b8d213693'
if LOAD_OUTPUT=$(curl -fSL 'https://intechfest.1pc.tf/api/stateful/Game/35/Ad/Byoc/Image/378/128/2f976873fb13f28da71ac4cbbe73dbf8c1d22437bd2c3428a980771ceeabf8ac' 2>/dev/null | docker load 2>/dev/null); then
  LOADED_REF=$(printf '%s\n' "$LOAD_OUTPUT" | sed -n -e 's/^Loaded image ID: //p' -e 's/^Loaded image: //p' | tail -n 1)
  if [ -n "$LOADED_REF" ] && LOADED_ID=$(docker image inspect --format '{{.Id}}' "$LOADED_REF" 2>/dev/null) && [ -n "$LOADED_ID" ] && docker image tag "$LOADED_ID" "$REVIEWED_IMAGE" 2>/dev/null; then
    SERVICE_IMAGE="$REVIEWED_IMAGE"
    echo "  loaded and pinned $SERVICE_IMAGE"
  else
    echo '  the archive loaded but its immutable local identity could not be verified — using the pinned placeholder.'
  fi
else
  echo '  image pull unavailable on this server — using a placeholder that serves the rotating flag.'
fi

echo '[2/4] Writing docker-compose.yml...'
if [ -n "$SERVICE_IMAGE" ]; then
  printf '%s' 'bmFtZTogcnNjdGYtYnlvYy0zNS0xMjgKc2VydmljZXM6CiAgIyBUaGUgdnVsbmVyYWJsZSBzZXJ2aWNlLiBQYXRjaCBpdCB0byBkZWZlbmQ7IGl0IHJlYWRzIGl0cwogICMgcm90YXRpbmcgZmxhZyBmcm9tIFJTQ1RGX0ZMQUdfRklMRSAod2UgZGVsaXZlciBpdCB0byAvc2hhcmVkL2ZsYWcpLgogIHNlcnZpY2U6CiAgICBpbWFnZTogInJzY3RmLWJ5b2MtMzUtMTI4LXNlcnZpY2U6cmV2aWV3ZWQtMDdjYjJlN2I4ZDIxMzY5MyIKICAgIHB1bGxfcG9saWN5OiBuZXZlcgogICAgY29udGFpbmVyX25hbWU6IHJzY3RmLWJ5b2MtMzUtMTI4LXNlcnZpY2UKICAgIHJlc3RhcnQ6IHVubGVzcy1zdG9wcGVkCiAgICBlbnZpcm9ubWVudDoKICAgICAgUlNDVEZfRkxBR19GSUxFOiAvc2hhcmVkL2ZsYWcKICAgIHZvbHVtZXM6CiAgICAgIC0gZmxhZzovc2hhcmVkOnJvCiAgIyBUaGUgdHVubmVsIGFnZW50IOKAlCBwdWJsaWMgaW1hZ2UsIHRva2VuIGJha2VkIGluLiBEb24ndCBlZGl0LgogIHJzY3RmLWFnZW50OgogICAgaW1hZ2U6ICJnaGNyLmlvL2RpbWFzbWEwMzA1L3JzY3RmLWJ5b2MtYWdlbnRAc2hhMjU2OjlhNDZkYjE0ZThlNjFmMTc1MGM5MGI2ZTM1OGU4NDY1NDdjZDM2MGRmNmVlOTUyNWVmMWJhY2I4ZGY1ZThiYzUiCiAgICByZXN0YXJ0OiAib24tZmFpbHVyZTo1IgogICAgcmVhZF9vbmx5OiB0cnVlCiAgICBjYXBfZHJvcDoKICAgICAgLSBBTEwKICAgIHNlY3VyaXR5X29wdDoKICAgICAgLSBuby1uZXctcHJpdmlsZWdlczp0cnVlCiAgICBwaWRzX2xpbWl0OiAxMjgKICAgIGVudmlyb25tZW50OgogICAgICBSU0NURl9CWU9DX01PREU6IGFnZW50CiAgICAgIFJTQ1RGX0JZT0NfVFVOTkVMX1VSTDogIndzczovL2ludGVjaGZlc3QuMXBjLnRmL2FwaS9zdGF0ZWZ1bC9HYW1lLzM1L0FkL0J5b2MvQWdlbnQvMzc4LzEyOC8xOTZkNjcyNTIyMWI3ZWEzZWQ1ZTAzYzA1ZjdmOWFhM2RiMzhiNDIwMGI2N2U0OTBiOTEwOGNmYzNmZWQwNDYwIgogICAgICBSU0NURl9CWU9DX1NFUlZJQ0U6ICdzZXJ2aWNlOjMxMzM3JwogICAgICBSU0NURl9CWU9DX0ZMQUdfRklMRTogL3NoYXJlZC9mbGFnCiAgICAgIFJTQ1RGX0JZT0NfU0VSVklDRV9DT05UQUlORVI6ICdyc2N0Zi1ieW9jLTM1LTEyOC1zZXJ2aWNlJwogICAgdm9sdW1lczoKICAgICAgLSBmbGFnOi9zaGFyZWQKICAgICAgIyBPcHRpb25hbCBCWU9DIFNTSC9hZG1pbiBzaGVsbCAoZGlzYWJsZWQgYnkgZGVmYXVsdCk6IHVuY29tbWVudGluZwogICAgICAjIHRoZSBuZXh0IGxpbmUgZ3JhbnRzIHRoaXMgYWdlbnQgcm9vdC1lcXVpdmFsZW50IERvY2tlciBhY2Nlc3Mgb24KICAgICAgIyBUSElTIGhvc3QuIFNlcnZpY2UgYW5kIGZsYWcgc3RyZWFtcyB3b3JrIHdpdGhvdXQgdGhlIHNvY2tldC4KICAgICAgIyAtIC92YXIvcnVuL2RvY2tlci5zb2NrOi92YXIvcnVuL2RvY2tlci5zb2NrCiAgICBkZXBlbmRzX29uOgogICAgICAtIHNlcnZpY2UKdm9sdW1lczoKICBmbGFnOgo=' | base64 -d > 'docker-compose.yml' && chmod 600 'docker-compose.yml'
else
  printf '%s' 'bmFtZTogcnNjdGYtYnlvYy0zNS0xMjgKc2VydmljZXM6CiAgIyBUaGUgdnVsbmVyYWJsZSBzZXJ2aWNlLiBQYXRjaCBpdCB0byBkZWZlbmQ7IGl0IHJlYWRzIGl0cwogICMgcm90YXRpbmcgZmxhZyBmcm9tIFJTQ1RGX0ZMQUdfRklMRSAod2UgZGVsaXZlciBpdCB0byAvc2hhcmVkL2ZsYWcpLgogIHNlcnZpY2U6CiAgICBpbWFnZTogZG9ja2VyLmlvL2FscGluZS9zb2NhdEBzaGEyNTY6NGU2MjVhNjJjOWVhNDBjY2JjZTkzYjlhNGZjYzZiNDE3NDBhOWYzMDgzODljMjE2ZjM0Yzg4Y2UzYWJiMjc1YgogICAgY29tbWFuZDogWyJUQ1AtTElTVEVOOjMxMzM3LGZvcmsscmV1c2VhZGRyIiwgIlNZU1RFTTpjYXQgL3NoYXJlZC9mbGFnIDI+L2Rldi9udWxsIl0KICAgIGNvbnRhaW5lcl9uYW1lOiByc2N0Zi1ieW9jLTM1LTEyOC1zZXJ2aWNlCiAgICByZXN0YXJ0OiB1bmxlc3Mtc3RvcHBlZAogICAgZW52aXJvbm1lbnQ6CiAgICAgIFJTQ1RGX0ZMQUdfRklMRTogL3NoYXJlZC9mbGFnCiAgICB2b2x1bWVzOgogICAgICAtIGZsYWc6L3NoYXJlZDpybwogICMgVGhlIHR1bm5lbCBhZ2VudCDigJQgcHVibGljIGltYWdlLCB0b2tlbiBiYWtlZCBpbi4gRG9uJ3QgZWRpdC4KICByc2N0Zi1hZ2VudDoKICAgIGltYWdlOiAiZ2hjci5pby9kaW1hc21hMDMwNS9yc2N0Zi1ieW9jLWFnZW50QHNoYTI1Njo5YTQ2ZGIxNGU4ZTYxZjE3NTBjOTBiNmUzNThlODQ2NTQ3Y2QzNjBkZjZlZTk1MjVlZjFiYWNiOGRmNWU4YmM1IgogICAgcmVzdGFydDogIm9uLWZhaWx1cmU6NSIKICAgIHJlYWRfb25seTogdHJ1ZQogICAgY2FwX2Ryb3A6CiAgICAgIC0gQUxMCiAgICBzZWN1cml0eV9vcHQ6CiAgICAgIC0gbm8tbmV3LXByaXZpbGVnZXM6dHJ1ZQogICAgcGlkc19saW1pdDogMTI4CiAgICBlbnZpcm9ubWVudDoKICAgICAgUlNDVEZfQllPQ19NT0RFOiBhZ2VudAogICAgICBSU0NURl9CWU9DX1RVTk5FTF9VUkw6ICJ3c3M6Ly9pbnRlY2hmZXN0LjFwYy50Zi9hcGkvc3RhdGVmdWwvR2FtZS8zNS9BZC9CeW9jL0FnZW50LzM3OC8xMjgvMTk2ZDY3MjUyMjFiN2VhM2VkNWUwM2MwNWY3ZjlhYTNkYjM4YjQyMDBiNjdlNDkwYjkxMDhjZmMzZmVkMDQ2MCIKICAgICAgUlNDVEZfQllPQ19TRVJWSUNFOiAnc2VydmljZTozMTMzNycKICAgICAgUlNDVEZfQllPQ19GTEFHX0ZJTEU6IC9zaGFyZWQvZmxhZwogICAgICBSU0NURl9CWU9DX1NFUlZJQ0VfQ09OVEFJTkVSOiAncnNjdGYtYnlvYy0zNS0xMjgtc2VydmljZScKICAgIHZvbHVtZXM6CiAgICAgIC0gZmxhZzovc2hhcmVkCiAgICAgICMgT3B0aW9uYWwgQllPQyBTU0gvYWRtaW4gc2hlbGwgKGRpc2FibGVkIGJ5IGRlZmF1bHQpOiB1bmNvbW1lbnRpbmcKICAgICAgIyB0aGUgbmV4dCBsaW5lIGdyYW50cyB0aGlzIGFnZW50IHJvb3QtZXF1aXZhbGVudCBEb2NrZXIgYWNjZXNzIG9uCiAgICAgICMgVEhJUyBob3N0LiBTZXJ2aWNlIGFuZCBmbGFnIHN0cmVhbXMgd29yayB3aXRob3V0IHRoZSBzb2NrZXQuCiAgICAgICMgLSAvdmFyL3J1bi9kb2NrZXIuc29jazovdmFyL3J1bi9kb2NrZXIuc29jawogICAgZGVwZW5kc19vbjoKICAgICAgLSBzZXJ2aWNlCnZvbHVtZXM6CiAgZmxhZzoK' | base64 -d > 'docker-compose.yml' && chmod 600 'docker-compose.yml'
fi

echo '[3/4] Writing bundled WireGuard config (rsctf-ad.conf)...'
printf '%s' 'IyBXaXJlR3VhcmQgY29uZmlnIGZvciBkaW1hc3Mg4oCUIEEmRCBnYW1lIDM1CiMgR2VuZXJhdGVkIDIwMjYtMDktMDQgMTc6MzA6MTUgVVRDCgpbSW50ZXJmYWNlXQpQcml2YXRlS2V5ID0gL1ZPZEtoSkdnVStyaFlDUnY1RUprcVVMTUZVRDVuOUtGQXJLOVBzdnZqOD0KQWRkcmVzcyA9IDEwLjEzLjQyLjEyNy8zMgpETlMgPSAxLjEuMS4xCgpbUGVlcl0KUHVibGljS2V5ID0ga29zaUlxUE1jRnZtNFlQWlVZVC9xZWdORFBoT3MrN05ab3g5OTVMamJVMD0KRW5kcG9pbnQgPSBpbnRlY2hmZXN0LjFwYy50Zjo1MTgyMQpBbGxvd2VkSVBzID0gMTAuMTMuNDEuMC8yNCwgMTAuMTMuNDIuMC8yNApQZXJzaXN0ZW50S2VlcGFsaXZlID0gMjUK' | base64 -d > 'rsctf-ad.conf' && chmod 600 'rsctf-ad.conf'
echo '  if the tunnel agent cannot connect, bring the VPN up:  wg-quick up ./rsctf-ad.conf'

echo '[4/4] Starting...'
COMPOSE_PROJECT_NAME='rsctf-byoc-35-128' docker compose --env-file /dev/null -f docker-compose.yml up -d
echo 'Done — watch the platform; your status should go green within a tick.'
