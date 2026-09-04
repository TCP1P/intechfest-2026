# Minions in 32K solution

> Organizer material. Do not ship the reference client or arena credentials with the handout.

## Verification record

- Status: `draft`
- Revision: working tree; freeze only after the committed rsctf layout is reproduced
- Challenge type: `KingOfTheHill`, managed Leaderboard evidence
- Delivery: hybrid shared service and `dist/game_minions-in-32k.zip`
- Handout SHA-256: `bb46e59ae3e222d91df10843b1937bc0a8ae6b612384a7fb44808de3a0e9dcdc`
- Runtime image: local `sha256:1e8b334a532c875ce27cc699bb41e9872699af5c4722e7677e039e114bb2bd76`
- Backend: local Docker arena; direct TCP/UDP verified 2026-09-04 UTC by Codex; hidden-staging rehearsal still required
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
  and licenses.
- The public challenge description and secure event lobby.
- The current private target IPv4 address shown by RSCTF and the event VPN profile.
- The team's current KotH capability, exchanged directly with the managed target for an
  arena key and pinned certificate hash.

The player does not receive the managed reporter credential, internal child-report
authenticator, another team's key, or the organizer reference client.

## Walkthrough

1. Extract the handout and inspect the protocol-v13 client options. Organizer-side source
   remains under `src/game/` and is not part of the player archive.
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
reports and replays, excludes NPC rows, and submits signed native wave evidence. Its HTTP
session route and QUIC game server use TCP and UDP on the same declared port; RSCTF admits
that exact private target endpoint through the event VPN.

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

On 2026-09-04 UTC, the rebuilt image reached Docker health with TCP and UDP both published on
port 8080. The rsctf checker returned OK through HTTP, and the documented solver observed a
fresh authoritative confirmation through QUIC/UDP on the same port. The writeup remains
draft because that local run is not a committed hidden-staging rehearsal.

The service and solver expose text/network evidence; a screenshot would be decorative and is
not retained.

## Notes

- Keep the writeup `draft` until the exact committed image and handout are reproduced by a
  second maintainer and direct private TCP/UDP managed reporting completes a multi-team
  hidden-staging rehearsal, including credential rotation on target reset.
- The headless reference client is organizer regression tooling. Event policy requires a
  player-driven match; unattended automation is not scoring-eligible evidence.
- Based on Project Sekai CTF 2026 `Minions in 16K` at commit
  `e35651f972e1c4f355c18427b3063a9ac98fb2cd`.
- Program code remains AGPL-3.0-only and upstream content/assets remain CC BY-NC-SA 4.0; see
  `src/game/LICENSE`, `src/game/LICENSE.content.md`, and `src/challenge/NOTICE.txt`.
