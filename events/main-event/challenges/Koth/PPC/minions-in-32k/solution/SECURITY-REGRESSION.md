# Minions in 32K scoring-boundary audit

## Baseline

The comparison target is Project Sekai CTF 2026 `game/minions-in-16k/challenge`
at commit `e35651f972e1c4f355c18427b3063a9ac98fb2cd`. The original solution families
changed rendering, exposed geometry, improved aim, or automated the public game
protocol. Player-driven assistance remains intended, but sustained unattended
combat cannot write official RSCTF evidence.

## Protocol-v13 boundary

Every opponent frag creates one server-owned confirmation at the victim's
authoritative death position. The invariant is:

```text
opponent frag
  -> banana exists for 12 seconds
  -> only killer may confirm
  -> only victim may deny
  -> everyone else is ignored
  -> unresolved objective expires
```

The simulation caps live confirmations at 64. The game server derives
`confirmed_kills`, frags, deaths, and private participation-integrity status
from authoritative state. The bridge rejects any ordered prefix whose
cumulative confirmations exceed cumulative frags, counters cannot reconcile,
identities differ, ranks disagree with the authoritative ordering, or a final
report differs from its one-minute legs. A private unattended-combat status is
sticky across later legs and excluded from activity, objective, normalization,
and Crown evidence.

| Regression case | Expected result |
| --- | --- |
| Protocol v11/v12 executable or bot | Rejected during `Join` |
| Combat-only v13 bot | Can frag, but leaves its confirmation unscored |
| Third party touches a banana | No state change |
| Killer touches its banana | One `KillConfirmed` and removal |
| Respawned victim touches its banana | One `KillDenied` and removal |
| Deadline passes | One `ConfirmationExpired` and removal |
| More than 64 unresolved drops | Oldest state expires; memory remains bounded |
| Forged cumulative `confirmed_kills > kills` prefix | Bridge rejects the report |
| Confirmation crosses a one-minute boundary | Accepted when the cumulative prefix remains valid |
| Sustained unattended combat | Private report status; zero activity, objective, and Crown evidence |
| Human-driven assistance without unattended firing | Remains scoring eligible |

## Reproduction

```sh
cd game
cargo test -p sim
cargo test -p headless-client
cargo test -p server
cd ../../../../rsctf-production/intechfest/minions32/bridge
go test ./...
```

Run `python3 solution/solve.py --host 127.0.0.1 --port 4433 --key LOCAL_KEY --insecure`
against a local
server only as an automated regression actor. It is not a scoring-eligible
player reference.
