# Tower of Babel solution

> Organizer material. Keep this directory out of player delivery and service images.

## Verification record

- Status: `draft`
- Revision: `<pending user-authorized git inspection>`
- Challenge type: `KingOfTheHill`, managed Leaderboard evidence
- Delivery: `hybrid` (shared TCP target plus `dist/tower-of-babel.zip`)
- Artifact or image: `sha256:1b5a024cd1b2b8bbf1fdc67f491ab3174f4abe30f1e91f9df0273d8a269e82d2` (local slim UI-test image)
- Handout SHA-256: `50a9013fdf7a91b58840151c2a295d1dac5a17755a7b5bcc3dbb7c45f558d7dc`
- Tested at: `2026-09-03T17:50:47Z` (UTC)
- Tested by: `Codex`
- Command: `python3 solution/solve.py --host <target> --port 1094 --token <current-capability>`
- Result: `exit 0; mastery depth=4, payloadBytes=<bounded>, randomized wave (local capability; no live flag)`

## Summary

The player joins the current wave with an rsctf KotH capability and receives a
randomized order of four guest architectures. A nested, position-independent
shellcode relay reads one secret per stage and passes the next stage as hex to
the next executor. The server scores the verified stage depth first and outer
payload size second; the first authenticated full relay owns a bounded patch
window whose candidate is canary-tested before it becomes the ward.

## Player inputs

- Description: the manifest description and the exact `dist/tower-of-babel.zip`
  handout, including the player-owned terminal lobby and short PRTS briefing.
- Target: the issued TCP host and port `1094`.
- Credentials or rules: the current `koth_…` capability; `PING`, `INFO`, `RELAY`,
  `AUTH`, `PATCH`, `STATUS`, and `QUIT` are the public commands.
- Files: `dist/tower-of-babel.zip` (contains `README.txt` and `client.py`).
- Not supplied: service source, runner implementation, reporter credential,
  generated secrets, and organizer APIs.

## Walkthrough

### 1. Open the player lobby and read the public wave contract

- Action: extract `tower-of-babel.zip`, run `python3 client.py --host TARGET --port 1094`,
  and choose `Wave briefing` (or use the protocol console's `INFO`).
- Observation: `INFO` returns four ordered stages, each architecture, secret
  path, executor path, syscall numbers, payload limit, `instanceId`, and the
  active ward's status/revision/fallback fields. It also publishes the bounded
  2,000-team wave roster limit.
- Hypothesis: each stage must use its own syscall ABI and embed the next
  executor path and payload.
- Action: assemble position-independent stage shellcode from the visible paths,
  building from the leaf back to the first stage.
- Result: one outer payload no larger than 32768 bytes.
- Capability gained: a relay that can be submitted to the current wave.

### 2. Cross the four guest stages

- Observation: each successful `RELAY` response prints a `STAGE` secret and a
  `RELAY_RESULT` depth.
- Hypothesis: the printed values are the ordered authentication material, not a
  static flag.
- Action: send the outer payload as lowercase hex.
- Result: four `STAGE` lines, `depth: 4`, and `AUTH_REQUIRED`.
- Capability gained: the current wave's authentication sequence.

### 3. Authenticate and own the patch window

- Observation: the four secrets concatenate in build order.
- Action: send `AUTH <secret1><secret2><secret3><secret4>`. If this is the
  first verified full relay in the wave, submit up to three JSON `PATCH`
  candidates containing 2–8-byte patterns that do not contain a mandatory
  syscall opcode. The three-attempt budget is shared by the wave and survives
  reconnects.
- Result: `RELAY_ACCEPTED` identifies the first-solver patch window. A
  `PATCH_ACCEPTED` response means the server replay-canary passed; a
  `PATCH_REJECTED` response means the previous known-good ward remains active
  and `INFO` exposes the fallback status.
- Capability gained: a native scoring row and, for the first solver, a safe
  defense that applies immediately to later attempts and future waves.

## Why it works

Author-only implementation detail: the target creates a fresh wave descriptor
on an epoch-aligned 60-second grid. The x86 `babel-runner` emulates each guest
with Unicorn and implements only the five stage syscalls (`open`/`openat`,
`read`, `write`, `close`, and `execve`) plus `exit`. It accepts a secret read
only when the path matches the current stage, accepts an `execve` only for the
next visible runner path, and recursively emulates the hex argument. The
runner has a code-size and instruction limit and never executes player bytes
natively.

The coordinator retains only SHA-256 capability hashes and the best bounded
attempt per team and wave, while retaining one zero-progress roster row for
every authenticated team that joined the wave. The first authenticated full
relay in a wave is recorded separately as the only patch owner. A submitted
candidate is replayed against that known-good payload under the candidate ward;
failure never replaces the active ward. Its in-process reporter filters those
hashes through the active rsctf context and submits one `babel-mastery`
objective per active team with `activity = 1/1`; it signs the exact native
evidence body with the lifecycle reporter credential. Checkers use only
unauthenticated `PING`, `INFO`, and rejection paths, so they never create a
session or touch scoring state.

## Solver

Run the tracked [`solve.py`](solve.py) with a current capability:

```console
$ python3 solution/solve.py --host <issued-host> --port 1094 --token <current-capability>
mastery depth=4 payloadBytes=<bounded> wave=babel-<epoch-ms>
```

The solver uses `pwntools==4.15.0` plus the target architecture binutils for
RISC-V, AArch64, and MIPS64 assembly. It deliberately does not submit a patch;
the patch owner and canary/fallback behavior are exercised separately so the
reference solve remains a clean initial-wave path.

## Evidence

Evidence is text-only: the terminal lobby and protocol output are the player-visible state.

Retained checks cover ZIP contents and hash, story/UI smoke, image health, checker exit 0,
mixed-architecture `depth=4`, ranking, patch ownership, canary fallback/activation,
reconnect limits, strict syscall validation, and restart reset.

## Notes

- Cleanup or reset behavior: sessions and unfinalized attempts are in memory;
  a target restart starts a new instance with a safe empty ward, and rsctf reset
  rotates the reporter credential. Finalized native snapshots are bounded to the
  reporter context.
- Important negative checks: malformed hex, oversized payloads, wrong executor
  targets, mandatory-syscall ward patterns, unauthenticated relay/patch, exact
  score ties, stale capabilities, and child environments containing any
  `RSCTF_KOTH_*` value.
- Known limitations: release status remains `draft` until the matching rsctf image,
  hidden managed-Leaderboard lifecycle, fresh blind playtest, and exact revision identity
  are recorded. Event-root scans pass; the whole-tree scan still includes the legacy
  root-level `tower-of-babel-jeop/challenge.yml`.
