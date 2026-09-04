# Rythme solution

> Organizer material. Do not expose this directory or a current KotH capability to players.

## Verification record

- Status: `draft`
- Revision: working tree; freeze only after the committed rsctf layout is reproduced
- Challenge type: `KingOfTheHill`, managed Leaderboard evidence
- Delivery: hybrid shared service and `dist/reversing_rythme.zip`
- Handout SHA-256: `0435b353a2342f1b1e9855b97a78224fef647e06d4dbbfd2407fcb0db9a32c18`
- Runtime image: local `sha256:3387a1e56f2bc8d562ef1efddfad649a22f34723a3ab2210e6848f0bc5cadbdd`
- Backend: local Docker coordinator; direct private target and health verified 2026-09-04 UTC by Codex; hidden rsctf staging still required
- Solver command: `python3 solution/solve.py --host TARGET --port PORT --scheme http --token CURRENT_TOKEN`
- Playtest: no uncontaminated report for this candidate revision

## Summary

Rythme is a Windows reversing challenge built from osu!lazer. The player extracts the
managed client and stripped Rust policy library, reconstructs the authenticated binary arena
protocol, and finds a signed-versus-unsigned selector mismatch in the modifier policy. A
replacement client applies an aggressive legal modifier profile while the native completion
proof attests `HT`, increasing the server-settled score enough to take a fresh wave's Crown.

The exploit never submits a chosen score. It still plays the revealed map with current cue
tokens and server-receipt timing.

## Player inputs

- The exact `reversing_rythme.zip` handout containing `Rythme.exe`, its player guide, and
  notices.
- The current private coordinator IPv4 address and port shown by RSCTF, reached through the
  event VPN.
- The team's current event-scoped KotH capability.
- Public `/api/v1/config` and `/api/v1/state` responses.

The player does not receive coordinator source, managed reporter state, signing secrets, future map
selections, or another team's capability.

## Walkthrough

1. Hash and extract the handout. The executable is a .NET single-file bundle containing the
   challenge assembly and one stripped native DLL with the single export `r4_gate`.
2. Inspect the public configuration and state routes. They disclose the secure-channel
   version, four rulesets, pool identity, score floors, wave timing, and the fact that every
   finalized heat scores independently.
3. Follow the managed session call sites. Reproduce the P-256 ECDH exchange, HKDF-SHA256 key
   split, request sequence, previous-request MAC chaining, and response authentication.
4. Reconstruct the `/api/v3/frame` codec. Frames start with one release-specific magic,
   version, and action byte; variable fields are little-endian length-prefixed values.
5. Trace the managed `Build envelope` call into the native dispatcher. Recombine the
   player-recoverable split constants and derive the heat-specific profile tag, selector,
   stride, and completion-proof input.
6. Build three modifier profiles. Put the aggressive legal profile at the coordinator's
   unsigned selector index and `HT` at the native policy's signed selector index.
7. Enroll in a fresh heat, wait for reveal, download and verify the selected `.osz`, and parse
   the exact `.osu` difficulty.
8. Ready with the crafted profile list. Request short-lived cues in bounded windows and send
   down/up input frames near each chart target at the locked playback rate.
9. Retrieve the server judgment and opaque capsules. Produce the `HT` completion proof and
   submit the finish frame before the submission window closes.
10. Wait for finalization and confirm that the returned team identity owns the wave Crown.

## Why it works

Author-side implementation detail: the coordinator indexes the profile list with an unsigned
selector. The native policy converts that same byte to a signed value before adding the
stride. With three profiles, subtracting 256 changes the modulo result because 256 is not
divisible by three. Both components accept the same envelope but choose different profiles.

The coordinator locks the aggressive profile and playback rate during ready-up. The native
policy later proves `HT`, whose lower multiplier raises the settlement ratio. The server
still derives hits, accuracy, coverage, raw basis, and the input digest from fresh receipts,
so replaying an old transcript or sending a declared score does not work.

The coordinator's in-process managed reporter submits the finalized wave as signed native
evidence. rsctf performs the final relative normalization and Crown attribution; the solver
never calls an organizer endpoint.

The player protocol is served directly from the current RSCTF private target. Only the
declared TCP port is admitted through the event VPN; no public transport gateway or scoring
route is involved.

## Solver

[`solve.py`](solve.py) is the small CLI and error-handling layer. The protocol implementation
is isolated in [`exploit.py`](exploit.py) because this challenge genuinely requires the
authenticated transport, binary frame codec, `.osz` parser, policy envelope, and timed input
loop.

Install the two exact direct dependencies, then use only issued player inputs:

```sh
python3 -m venv .solution-venv
.solution-venv/bin/python -m pip install --only-binary=:all: -r solution/requirements.txt
.solution-venv/bin/python solution/solve.py \
  --host TARGET \
  --port PORT \
  --scheme http \
  --token CURRENT_TOKEN
```

Local mode may use `--scheme http --team solver-crew --name 'Patch Crew'` without a token.

## Evidence

Verify the exact handout from the package directory:

```console
$ (cd dist && sha256sum --check ../solution/handout.sha256)
reversing_rythme.zip: OK
```

A successful run ends with fresh server-derived evidence similar to:

```text
[+] Judge: 520/526 hits, 97.129% accuracy, raw basis 971292
[+] Wave Crown captured with official score 3,032,598
settled score: 3032598
```

These are text protocol and scoring results; a screenshot would not prove more than the
retained command output.

On 2026-09-04 UTC, the rebuilt coordinator reached Docker health through its direct player
port and reported the expected `2026.09.04.1` build. The full reference exploit
was not rerun against a committed hidden-staging heat, so the example scoring transcript is
not freeze evidence for this candidate.

## Notes

- Keep the writeup `draft` until a second maintainer reproduces the exact committed image and
  handout and the solver succeeds on a committed hidden-staging revision.
- Every later wave requires fresh server-timed input; one completed heat does not carry its
  score forward.
- The signed selector and proof constants are recoverable from the issued executable and are
  not production secrets.
- Never retain a real KotH token, managed reporter secret, or production target in this folder.
