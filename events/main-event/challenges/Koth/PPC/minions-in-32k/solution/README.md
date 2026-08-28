# Minions in 32K solution

> Organizer material. Do not ship the reference client or arena credentials with the handout.

## Verification record

- Status: `draft`
- Revision: working tree; freeze only after the committed rsctf layout is reproduced
- Challenge type: `KingOfTheHill`, managed Leaderboard evidence
- Delivery: hybrid shared service and `dist/game_minions-in-32k.zip`
- Handout SHA-256: `6fd7624e24b75b12fc95dcdee18a4ce75b9d54e0509f39a5694f173b3c0c980e`
- Runtime image: local `sha256:39922f87a75befe1870fc61d608570ef4d80ee402949040227a377ccedc6f1b5`
- Backend: local Docker arena; checked 2026-08-28 UTC by Codex; hidden rsctf managed-reporting and gateway rehearsal still required
- Solver command: `python3 solution/solve.py --host TARGET --port UDP_PORT --key ARENA_KEY --cert-hash CERT_SHA256`
- Playtest: no uncontaminated report for this candidate revision

## Summary

Minions in 32K adapts Project Sekai CTF 2026's Minions in 16K into an
objective-aware Leaderboard KotH arena. Protocol v13 adds Banana Confirmed: a frag creates a
short-lived server-owned objective. The killer must collect the yellow confirmation banana,
while the victim can collect the red denial banana.

Visual patches and combat-only automation can still help obtain frags, but they do not
complete the confirmation objective. The intended path combines protocol analysis,
objective-aware navigation, and player-driven combat.

## Player inputs

- The exact `game_minions-in-32k.zip` handout with Linux and Windows clients, notices,
  licenses, and corresponding public source.
- The public challenge description and secure event lobby.
- The team's current KotH capability, passed through the transport gateway and exchanged by
  the managed target for an arena key and pinned certificate hash.
- The gateway's issued UDP endpoint.

The player does not receive the managed reporter credential, internal child-report
authenticator, another team's key, or the organizer reference client. The stateless gateway
has none of those values.

## Walkthrough

1. Extract the handout and inspect the corresponding protocol-v13 source and client options.
2. Start the client, enter the current KotH capability in the secure lobby, and retain the
   issued arena endpoint, arena key, and certificate SHA-256.
3. Observe that ordinary frags create temporary objective markers rather than immediately
   producing the best confirmed score.
4. Track the player role attached to each marker. Route the killer to yellow confirmation
   bananas and, after death, route the victim to red denial bananas before their 12-second
   expiry.
5. Keep combat, movement, and confirmation decisions synchronized with authoritative public
   state. Old protocol clients and combat-only bots leave most scoring value unconfirmed.
6. Sustain the best confirmed-kill result across official scoring legs and verify the final
   target-reported rsctf standing.

## Why it works

Author-side implementation detail: the server owns marker creation, roles, expiry, match
state, and minute deltas. Confirmation changes the scoring evidence only when the correct
role reaches the correct live objective. Client-side visibility or aim changes cannot forge
that authoritative transition.

The target receives a lifecycle-bound rsctf reporter credential and generates a short-lived
TLS identity at startup. Its supervisor authenticates event-scoped KotH capabilities,
derives deterministic per-target arena credentials for the eligible hashes, validates child
reports and replays, excludes NPC rows, and submits signed native wave evidence. The
separately deployed gateway only forwards the capability request and relays encrypted UDP;
it owns no roster or scoring state.

## Solver

[`solve.py`](solve.py) builds or runs the existing protocol-v13 headless client, passes only
the issued endpoint/key/certificate inputs, bounds execution time, and succeeds only after
observing `kill confirmed` in the authoritative client output.

From the package directory:

```sh
python3 solution/solve.py \
  --host TARGET \
  --port UDP_PORT \
  --key ARENA_KEY \
  --cert-hash CERT_SHA256
```

Use `--binary PATH` to skip the local Cargo build. `--insecure` is for isolated local testing
only and must not appear in release evidence.

## Evidence

Verify the exact handout from the package directory:

```console
$ (cd dist && sha256sum --check ../solution/handout.sha256)
game_minions-in-32k.zip: OK
```

A successful reference run ends with genuine text output containing:

```text
kill confirmed
[+] Observed kill confirmed
```

On 2026-08-28 UTC, the rebuilt image reached Docker health under the CI sandbox, the rsctf
checker returned OK through its published HTTP port, and the documented solver observed a
fresh authoritative confirmation through the published UDP port. The writeup remains draft
because that local run is not a committed hidden-staging rehearsal.

The service and solver expose text/network evidence; a screenshot would be decorative and is
not retained.

## Notes

- Keep the writeup `draft` until the exact committed image and handout are reproduced by a
  second maintainer and managed reporting plus the gateway complete a multi-team
  hidden-staging rehearsal, including credential rotation on target reset.
- The headless reference client is organizer regression tooling. Event policy requires a
  player-driven match; unattended automation is not scoring-eligible evidence.
- Based on Project Sekai CTF 2026 `Minions in 16K` at commit
  `e35651f972e1c4f355c18427b3063a9ac98fb2cd`.
- Program code remains AGPL-3.0-only and upstream content/assets remain CC BY-NC-SA 4.0; see
  `src/game/LICENSE`, `src/game/LICENSE.content.md`, and `src/challenge/NOTICE.txt`.
