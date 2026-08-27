# Trusted Leaderboard KotH infrastructure

Both main-event challenges use rsctf's signed API claim source. Their trusted observer or
bridge runs outside the player-facing `src/` image, reads bounded challenge evidence, filters
it against rsctf's current eligible capability hashes, and submits an HMAC-authenticated wave
snapshot. It never submits points; rsctf validates the snapshot and computes scoring.

Read the package-specific deployment contract before operating either service:

- [Rythme observer and coordinator](../events/main-event/challenges/Koth/Reverse/rythme/observer/README.md)
- [Minions bridge](../events/main-event/challenges/Koth/PPC/minions-in-32k/observer/README.md)

## Security boundary

The observer secret is a one-time operational credential. Never put it in `challenge.yaml`,
`src/`, a player archive, a browser, an image layer, Git, or logs. Run trusted infrastructure
under a dedicated non-root identity with:

- network access only to rsctf and the private challenge evidence route;
- a read-only filesystem plus narrowly scoped persistent state;
- the secret mounted read-only from a restricted secret store;
- synchronized time, bounded requests, and bounded retained evidence;
- health monitoring for stalled polling, feed gaps, context conflicts, and rejected rows; and
- an immutable reviewed image identity.

HMAC authenticates the submitting process. It does not prove that the process evaluated the
game honestly. Keep the evidence endpoint and observer runtime outside player-controlled
workloads.

## rsctf setup

For each challenge on hidden staging:

1. Import the exact commit and keep the game hidden and challenge disabled.
2. Open **A&D / KotH operations**, select the hill, and enable Leaderboard/API observation.
3. Record the imported game and challenge IDs.
4. Copy the one-time observer secret directly into the deployment's secret store.
5. Start the official lifecycle while scoring is paused.
6. Deploy the matching observer or bridge against the active target and immutable runtime
   image.
7. Confirm a healthy empty/current snapshot, one recognized team action, one rejected or
   ineligible action, and exact context/row counts.
8. Exercise a complete multi-team wave, reset/rotation, observer restart, and stale-target
   failure before resuming scoring.

The claim source is frozen when official scoring starts. Rehearse rescan-induced challenge ID
changes away from the live event.

## Rythme observer

Rythme's [observer.py](../events/main-event/challenges/Koth/Reverse/rythme/observer/observer.py)
is a standard-library Python service. It reads the coordinator's private finalized-wave feed
and submits one `official-score` objective. Its required runtime values are:

| Variable | Purpose |
| --- | --- |
| `RSCTF_GAME_ID` | imported game ID |
| `RSCTF_CHALLENGE_ID` | imported challenge ID |
| `RSCTF_URL` | private rsctf origin |
| `RYTHME_ARENA_URL` | private coordinator origin with access to `/api/v1/scoring` |
| `RSCTF_OBSERVER_SECRET_FILE` | read-only file containing the one-time API secret |
| `RYTHME_OBSERVER_HEARTBEAT_FILE` | optional health heartbeat path |

Use the checked-in
[production Compose file](../events/main-event/challenges/Koth/Reverse/rythme/observer/compose.production.yml)
as the topology contract. The public gateway must block the scoring feed. The observer must
reach that feed only on its private network. A successful cycle refreshes the heartbeat used
by the container health check.

Rythme validates the exact settlement window, eligible hashes, objective schema, bounded
wave/team counts, and unique-Crown rule before signing. A feed or context mismatch fails the
cycle; it does not fabricate partial scoring.

## Minions bridge

Minions uses a trusted Go bridge because the same process owns capability exchange, pinned
UDP relay, target reporting, and signed scoring evidence. Build it from
[`observer/bridge/`](../events/main-event/challenges/Koth/PPC/minions-in-32k/observer/bridge/)
and deploy only the immutable image ID.

The bridge secret is a mode-`0400` JSON file owned by UID/GID 32033. Its exact schema and
report-token derivation are in the package [observer README](../events/main-event/challenges/Koth/PPC/minions-in-32k/observer/README.md).
The runtime additionally needs the active rsctf game/challenge IDs, exact target container
identity, numeric upstream address, private health URL, player-visible UDP endpoint, state
directory, and private rsctf origin.

Use [reconcile.sh](../events/main-event/challenges/Koth/PPC/minions-in-32k/observer/reconcile.sh)
to bind the bridge to the current platform-managed target. It rejects an unexpected target
network or malformed identity, removes the bridge when no active target exists, and replaces
an unhealthy or stale binding. Review its database/container authority before installing it;
it is organizer operations code, not part of Repository Bindings.

## Verification

Before release, require all of the following on the exact candidate:

- observer/bridge unit tests pass;
- secret and state files have the documented ownership and permissions;
- the player ingress cannot reach private evidence or internal report routes;
- an empty wave submits explicit bounded evidence without awarding points;
- recognized and unknown capability hashes are handled correctly;
- ties, zero results, and the unique-Crown invariant match rsctf;
- stale context, cursor/feed gaps, invalid target identity, and rsctf rejection fail closed;
- restart does not duplicate accepted evidence or lose finalized state; and
- recent logs contain no secret, capability, player credential, or unbounded payload.

The repository checker remains independent. It verifies ordinary challenge function and must
not inspect or mutate this observer/bridge control source.
