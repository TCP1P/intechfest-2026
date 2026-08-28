# Player evidence ledger

This file tracks author-side writeup evidence. It is not included in the
player handout.

| Boundary reached | Newly visible evidence | Acquisition path | Still unavailable |
| --- | --- | --- | --- |
| Initial service | Host and port, Crown owner, heat phase, hidden selection commitment, per-wave scoring rule, 700,000 raw floor, 2,100,000 exploit target, selected exact map after reveal | Platform target, public frontend, SSE, supplied ZIP | Client internals, channel format, cue tokens, manifest fields, server implementation |
| Extracted client | Managed assembly, native Rust DLL, endpoint strings, embedded 21-map pool, exact `.osz` parser, P-256/HKDF channel, chained request format, cue polling, modifier envelope, capsule return fields, manifest canonicalization, one opaque native export, anti-debug imports, release-specific dispatcher IDs, policy VM, split proof-key shares, integrity checks | Extract the issued .NET single-file bundle, decompile `Rythme.dll`, and inspect the PE export/import tables, strings, and xrefs in `rythme_native.dll` | Coordinator source, capsule-signing key, managed rsctf reporter credential |
| Fresh scoring wave | Narrow bearer, channel keys, heat ticket, BeatmapSetID, BeatmapID, archive hash, chart digest, native mode, start time, cue windows, accepted input digest, raw basis | Reproduce onboarding and protected routes, download the revealed archive, send fresh cue-bound input | Final Crown until all performers settle |
| Boosted divergent run | First modifier profile locked at ready, last profile emitted by the native VM, accepted capsule-bound proof, settled score, rotation progress | Send a duplicate profile TLV, preserve both server capsules, and reconstruct the native proof | Other teams' results until wave finalization |
| Finalized wave | Official score and Crown owner identity | Complete one fresh live wave; repeat on later waves to keep scoring | Nothing required for the KOTH objective |

# Screenshot capture ledger

The prior screenshots were moved to
`brainstorm/retired-ui-captures/`. They document the upstream lazer shell and
the original room flow, but their text and thresholds predate protocol v5. Any
screenshot used in the final writeup must be recaptured from the final EXE and
current coordinator.

| Filename | Required state | Fact to prove | Status |
| --- | --- | --- | --- |
| `main-menu-ui.png` | Clean final client | Real lazer menu, settings, Play, Edit, Browse, Argon skin | Recapture after final build |
| `crown-menu-ui.png` | Play, then Multi | Crown Arena is added to the upstream menu | Recapture after final build |
| `login-ui.png` | Not authenticated | Coordinator URL and event-token onboarding | Recapture after final build |
| `post-login-ui.png` | Wave enrollment | Team roster, hidden map, rotation index and wave | Recapture after final build |
| `download-ui.png` | Reveal with clean beatmap storage | Automatic exact `.osz` transfer, validation, and import | Recapture after final build |
| `mod-select-ui.png` | Revealed map imported | Native Mod Select and compatibility behavior | Recapture after final build |
| `mod-selected-ui.png` | Fixed-rate profile selected | Playback rate and native multiplier before lock | Recapture after final build |
| `countdown-ui.png` | Ready on a hard map | Shared absolute start and selected mode | Recapture after final build |
| `gameplay-ui.png` | Running final client | Native real-map gameplay and default skin | Recapture after final build |
| `frontend-ui.png` | Public current service | Per-wave rule, 2.1M exploit target, 700k floor, current room | Recapture after final build |

For each recapture, record the exact release SHA-256, server seed, local/event
authentication mode, display size, capture command, identity, and file
SHA-256. Do not reuse prior hashes or claim a screenshot proves protocol v5
until it has been reproduced.
