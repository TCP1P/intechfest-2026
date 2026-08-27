# rCTF patch — Minions instancer

The matchmaking backend in this repo is driven by rCTF via a custom instancer
provider. rCTF lives in a separate repo (we don't commit there), so the changes
are vendored here. Apply them to an rCTF checkout to wire the challenge up.

## Files

- `minions-instancer.ts` — the instancer provider. It forwards queue intake to
  this repo's backend (`/api/queue/{join,status,leave}`), signs every call with
  the per-challenge `queueSecret` (`Authorization: Bearer …`), binds
  `team_uuid = user.id`, forwards the optional `startDate`/`endDate` event
  window, and tolerates a team holding multiple concurrent instances (per-team
  concurrency ramp).
- `index.ts.patch` — registers the provider as `instancer/minions-instancer`.

## Apply

```sh
# from the rctf checkout root
cp /path/to/minions-in-16k/rctf-patch/minions-instancer.ts \
   apps/api/src/providers/instancer/minions-instancer.ts
git apply /path/to/minions-in-16k/rctf-patch/index.ts.patch
```

## Configure

1. **`rctf.d/00-*.yaml`** — register the instancer and make it the default:

   ```yaml
   instancers:
     minions:
       name: 'instancer/minions-instancer'
   defaultInstancer: minions
   ```

2. **Challenge** (`PUT /api/v2/admin/challs/<id>`) — DYNAMIC scoring + instancer
   config. The `queueSecret` must equal the backend's `QUEUE_SECRET`; the
   `scoring.source.secret` must equal the backend's `RCTF_SECRET`:

   ```jsonc
   {
     "data": {
       "scoring": { "kind": "dynamic",
         "source": { "transport": "webhook", "secret": "<RCTF_SECRET>" } },
       "instancerConfig": { "instancer": "minions", "challengeIntegrationId": "minions",
         "config": { "apiUrl": "http://<backend>:8080", "udprouterAddr": "<host:port>",
                     "pods": [], "queueSecret": "<QUEUE_SECRET>",
                     "startDate": "2026-07-01T00:00:00Z", "endDate": "2026-07-03T00:00:00Z" } }
     }
   }
   ```

   Leave `flag` empty for a dynamic challenge (a non-empty flag re-enables the
   flag-submit box, which competes with webhook scoring).

   `startDate` / `endDate` (RFC3339, or unix seconds) bound the event window.
   The backend refuses to serve any instance that wouldn't finish ~20 s before
   `endDate`, and won't serve before `startDate`; in-progress matches always
   finish. Both are optional — omit either side to leave it unconstrained.
   These are swappable mid-event like `udprouterAddr` (forwarded per request).

## Backend side (this repo)

```sh
RCTF_SECRET=<…> QUEUE_SECRET=<…> cargo run -p backend -- \
  --rctf-url http://<rctf>:3000 --instancer local \
  --event-hours 48 --max-concurrent-per-team 4 \
  --replay-bucket /srv/minions/replays
```

See the backend `--help` for the full flag set (calibration/decay are built in;
`--event-hours`/`--max-concurrent-per-team` drive the per-team concurrency ramp;
unset `--event-hours` keeps the cap pinned at 1).
