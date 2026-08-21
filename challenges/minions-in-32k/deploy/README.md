# Minions production deployment

The platform launches the shared arena target from the immutable base image.
The target must receive a unique, random `RSCTF_FLAG` through RSCTF's normal
static-flag injection; do not set roster, report, or TLS values in the image.

The bridge secret file is JSON owned by UID/GID 32033 with mode 0400:

```json
{
  "observerSecret": "koth_api_…",
  "reportToken": "64 lowercase hex characters derived from RSCTF_FLAG",
  "teams": [
    {
      "name": "Team name",
      "arenaUuid": "stable event pseudonym",
      "arenaKey": "random per-event arena credential",
      "scoringEligible": true
    }
  ]
}
```

Derive `reportToken` as HMAC-SHA256 with the exact `RSCTF_FLAG` bytes as the
key and `minions-in-32k/report-token/v1` as the message. Backfill NPC entries use
`scoringEligible: false`. The bridge converts that field to the supervisor's
private `bot` marker; it never admits those entries through the public capability
exchange.

`compose.production.yml` requires a full bridge image ID and the active target
binding. `reconcile.sh` validates that binding against Postgres, accepts only the
private `10.13.40.0/24` service range, replaces an unhealthy or stale bridge, and
removes the bridge when the target is no longer active.
