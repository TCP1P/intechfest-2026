# Minions in 32K

Minions in 32K is a mechanically distinct RSCTF adaptation of Project Sekai
CTF 2026 **Minions in 16K**. It retains the Rust/QUIC arena foundation, Gru's
Lab, minion assets, replays, and 256×128 (32,768-pixel) presentation. Its
protocol-v13 scoring primitive is Banana Confirmed: frags create short-lived,
server-owned objectives that the killer confirms and the victim can deny.

## Why the 16K solution no longer solves 32K

The public 16K solution family raised resolution, patched glTF materials for
transparent walls/high-contrast targets, added ESP or screen-color aim
assistance, and eventually rebuilt a protocol bot. Those techniques remain
possible client-side, but are no longer sufficient:

| Legacy technique | 32K result |
| --- | --- |
| Run the v11/v12 binary or bot | Join rejected by protocol v13 |
| Transparent walls, target recoloring, higher resolution | Cosmetic only |
| ESP or perfect aim | Helps obtain frags, but does not collect confirmation objectives |
| Port an old combat-only bot to v13 | Moves and fires, but leaves score on the floor |

The intended 32K progression is protocol analysis plus player-driven,
semi-automated combat. Tools may interpret public state, highlight targets,
recommend routes, and assist actions, but the player must remain in the loop
and drive the match. Sustained unattended combat is not eligible for official
KotH evidence. A banana remains live for 12 seconds. Only its killer can
confirm it and only its victim can deny it. The headless NPC implementation is
test and exhibition infrastructure, not a player reference solution; role
isolation, expiry, and bounded-state behavior are covered by simulation tests.

The RSCTF integration is deliberately outside the gameplay trust boundary. An
HTTPS gateway exchanges an event-stable `koth_…` capability for an arena-only
key and pinned certificate hash. Encrypted UDP is relayed without inspection.
After each official minute, the authoritative upstream server reports that
minute's confirmed-kill/frag/death deltas and presence. The gateway turns each leg into
private KotH evidence, then verifies a cumulative final reconciliation at
match end.

The production arena image contains no roster, arena credential, reporting
secret, certificate, or private key. RSCTF injects one random internal flag only
at container creation; the supervisor derives the internal report authenticator
from it and removes the seed from child-process environments. The supervisor
generates a short-lived TLS identity at startup. After authenticating a player,
the trusted bridge sends the event roster over the authenticated internal start
route and returns the live certificate pin to that player. A new image can
therefore serve a new event without rebuilding secrets into an image layer.

## Player flow

```powershell
.\minions-in-32k.exe
```

```sh
./minions-in-32k
```

Run the binary without arguments, copy the stable KotH token from the RSCTF
challenge panel, and paste it into the masked secure event lobby. The explicit
`--token koth_…` option remains supported for test automation and scripted
launchers. The production gateway defaults to
`https://minions-private.tcp.1pc.tf`; use `--gateway` only for an explicitly
trusted test deployment. Use `--demo` for the offline bot match.

In the arena, collect yellow bananas to confirm your own frags. After
respawning, collect red bananas to deny the opponent who killed you. Confirmed
kills decide rank; total frags and then fewer deaths break ties. The client
shows final standings without exiting and can return to the secure lobby with
the same in-memory KotH token.

The official arena always runs at eight occupied slots. Any slot not assigned
to a human is filled by a clearly named `NPC_*` client during the staging
period. NPCs use the public protocol, resolve their own confirmation objectives, and use an
adaptive human-like combat profile: each has a bounded reaction time and skill
level, sees only through line of sight and a field of view, remembers recently
seen opponents, navigates around cover, prioritizes health when wounded, leads
rockets, and dodges visible projectiles. Their aim still accelerates, drifts,
hesitates, and makes mistakes instead of snapping like an aimbot. They are
exhibition-only: their results remain in the in-game standings but cannot
authenticate to RSCTF, change the KotH normalization ceiling, or receive the
Crown.

## Source layout

- `game/` — adapted upstream challenge source and assets.
- `supervisor/` — demand-starts one authoritative match and its network bots.
- `htb/solver.py` — author-side automated regression runner (not a scoring-eligible
  player and not shipped in the player archive).
- `Dockerfile` — builds the server, headless client, and supervisor image.
- `build-release.sh` — builds Linux/Windows clients and packages corresponding
  source, notices, and licenses.
- `deploy/` — versioned production bridge, checker, Compose definition, and
  target-reconciliation service.

## Attribution and licenses

Based on
[Project Sekai CTF 2026, Minions in 16K](https://github.com/project-sekai-ctf/sekaictf-2026/tree/main/game/minions-in-16k/challenge)
at commit `e35651f972e1c4f355c18427b3063a9ac98fb2cd`.

- Program code: GNU Affero General Public License v3.0 only (`game/LICENSE`).
- Upstream content/assets: CC BY-NC-SA 4.0 (`game/LICENSE.content.md`).
- RSCTF adaptation changes are distributed under the same applicable terms.

See `challenge/NOTICE.txt` for the modification notice.
