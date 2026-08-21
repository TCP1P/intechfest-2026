![img](assets/banner.png)

<img src='assets/htb.png' style='zoom: 80%;' align=left /> <font size='10'>Rythme</font>

2<sup>nd</sup> August 2026

Prepared By: `dimasma0305`

Challenge Author(s): `dimasma0305`

Difficulty: <font color='orange'>Hard</font>

<br><br>

# Synopsis

Rythme is a black-box Windows reversing challenge built from osu!lazer. The player extracts a managed client and a stripped Rust DLL, reconstructs an authenticated binary arena protocol, and finds inconsistent selector arithmetic in the modifier policy. The client predownloads the pinned event pool before enrollment. Every heat produces an independent Leaderboard KotH score; the four-mode rotation keeps play varied but does not delay scoring until all four modes are complete.

## Description

Rythme is Syncore's live Crown qualifier, where every crew receives the same surprise track, discipline, and synchronized start. The final intake is closing, the current owner is banking bracket points, and settlement is minutes away. You are an external integrity crew. Take the Crown before the cutoff to control the qualifier, or miss it and let the incumbent lock in another payout.

## Skills Required

- Static analysis of managed and native Windows code
- PE and .NET single-file bundle inspection
- P-256 ECDH, HKDF-SHA256, and HMAC-SHA256
- Binary protocol reconstruction
- osu! beatmap parsing and real-time input scheduling

## Skills Learned

- Extracting managed and native components from a self-contained .NET executable
- Recovering an opaque native dispatcher from managed call sites
- Reproducing a chained authenticated transport
- Identifying signed and unsigned arithmetic differences across a trust boundary
- Automating fresh server-timed rhythm-game waves without submitting a score value

# Enumeration

The issued archive contains the Windows client, a short player guide, the osu! licence, and third-party notices. It has no source or solver. After authentication, the client predownloads the pinned pool directly from its HTTPS mirror, with the coordinator as a verified fallback, before entering a heat; the exact selection remains hidden until reveal.

```console
$ sha256sum reversing_rythme.zip
91f378e90010349718074072859706813838af6e281006528a19f941283d9c24  reversing_rythme.zip
$ unzip -Z1 reversing_rythme.zip
reversing_rythme/
reversing_rythme/OSU-LICENCE.txt
reversing_rythme/THIRD-PARTY-NOTICES.txt
reversing_rythme/README.txt
reversing_rythme/Rythme.exe
```

The public configuration and state routes disclose the release identifier, native modes, pool size, score floors, and per-wave rule. They do not disclose a modifier envelope or a score-submission route.

```console
$ curl -s http://TARGET:PORT/api/v1/config |
    jq '{client_build,protocol,pool_schema,pool_song_count,pool_source,rulesets,transport}'
{
  "client_build": "r6-0f3d3c3-8f21",
  "protocol": "rythme-secure-channel-v3",
  "pool_schema": "rythme-pool-v2",
  "pool_song_count": 21,
  "pool_source": "organizer-curated-real-osu-beatmaps",
  "rulesets": [
    "osu",
    "taiko",
    "fruits",
    "mania"
  ],
  "transport": "authenticated arena channel"
}
$ curl -s http://TARGET:PORT/api/v1/state |
    jq '{crown_threshold,raw_score_floor,series_rule}'
{
  "crown_threshold": 2100000,
  "raw_score_floor": 700000,
  "series_rule": "every finalized heat scores independently; a fresh completed run is required; an exact top-score tie awards no Crown"
}
```

Launching `Rythme.exe` shows the normal lazer start screen. `PLAY`, `MULTI`, and `CROWN ARENA` lead to the token login for the preconfigured production coordinator. After authentication, the lobby verifies or downloads the complete local pool from the pinned mirror before enabling enrollment; the coordinator is an automatic fallback. Players save one native Mod Select loadout for each ruleset and arm one persistent auto-start control for future heats. Because the next ruleset is hidden, the client applies only the matching saved loadout after reveal. On reveal, the verified local map becomes the lobby background and music preview, then the playfield opens immediately after ready confirmation. Manual Mod Select and ready controls remain available. Normal play confirms that the coordinator reveals the exact selection only after enrollment and that the client never asks the player for a score value.

The executable is a .NET single-file bundle. Extracting it exposes one challenge assembly and one stripped native policy library. The DLL has a single semantic-free export.

```console
$ mkdir extracted
$ DOTNET_ROLL_FORWARD=Major sfextract Rythme.exe -o extracted
Entry point: Rythme.dll
Bundle version: 6.0
Extracted 368 files to "extracted"
$ file extracted/Rythme.dll extracted/rythme_native.dll
extracted/Rythme.dll: PE32+ executable (GUI) x86-64 Mono/.Net assembly, for MS Windows, 2 sections
extracted/rythme_native.dll: PE32+ executable (DLL) (console) x86-64 (stripped to external PDB), for MS Windows, 11 sections
$ x86_64-w64-mingw32-objdump -p extracted/rythme_native.dll |
    sed -n '/\[Ordinal\/Name Pointer\] Table/,+2p'
[Ordinal/Name Pointer] Table
        [   0] r4_gate
```

The managed assembly retains the ordinary arena workflow, while selected policy methods have generated names. UTF-16 strings expose only the session routes, public state, one binary arena route, the channel version, and the client build.

```console
$ strings -el extracted/Rythme.dll |
    grep -E '/api/v[23]/|rythme-secure-channel|r6-'
/api/v2/session/local
/api/v2/session/rctf
/api/v3/frame
rythme-secure-channel-v3
r6-0f3d3c3-8f21
```

The executable checks its signed image and protected managed methods during startup. The native library checks its extracted image, loaded code pages, and common debugger states. These checks raise the cost of live patching. Static extraction still exposes the information required by the official client.

# Solution

## Player knowledge ledger

| Boundary reached | Newly visible evidence | Acquisition path | Still unavailable |
| --- | --- | --- | --- |
| Initial access | Target, handout ZIP, client hash, four-mode rotation, score floors, pinned pool archives | Platform copy, player guide, public configuration and predownload routes | Protocol frames, future selections, policy constants |
| Client extracted | Session targets, `/api/v3/frame`, channel call sites, dispatcher ABI, embedded pool metadata | Issued `Rythme.exe` | Coordinator implementation, future nonces, server capsule key |
| Heat revealed | Exact BeatmapSetID, BeatmapID, mode, archive digest, chart digest, nonce, shared start | Authenticated state response | Future waves and cue tokens |
| Ready and result observed | Locked applied profile, opaque ready capsule, server judgment, opaque judge capsule | Authenticated arena frames | Server capsule key |
| Wave finalized | Official settled score, team ownership, and recurring Crown result | Authenticated arena and Crown responses | Other teams' secrets and coordinator internals |

No coordinator source, host filesystem access, or server signing key is required.

## Finding the vulnerability

#### Black-box discovery

Session creation uses a fresh P-256 key pair and two nonces. The managed call sites show that the client verifies the server's P-256 proof, derives 96 bytes with HKDF-SHA256, and splits the result into request, response, and local sealing material. The HKDF context binds the protocol name, team ID, channel ID, both public keys, and both nonces.

Every protected request increments a sequence number. Its HMAC covers the protocol name, channel ID, method, exact target, timestamp, body digest, and previous request MAC. The response HMAC binds the status and response body to the accepted request. Reimplementing this chain allows a replacement client to use the same public session routes as the official executable.

All competitive operations pass through `POST /api/v3/frame`. Reverse engineering the managed frame reader and writer gives the release-specific header and action values:

| Field | Value |
| --- | ---: |
| Magic | `29 9e d9 46` |
| Version | `01` |
| Enroll | `5c` |
| Ready | `12` |
| Cues | `3b` |
| Input | `21` |
| Result | `55` |
| Finish | `31` |

The first six bytes are the magic, version, and action. Variable fields use a little-endian `uint16` length followed by the raw bytes. Input batches bind the heat ticket, ruleset, sequence, note index, cue token, action bits, and optional osu! pointer coordinates.

Managed call sites also disclose the operation IDs passed to `r4_gate`:

| Operation | ID |
| --- | ---: |
| ABI | `0x79a5df25` |
| Initialize | `0xdd57bd91` |
| Health | `0xfb159d9b` |
| Open channel | `0xdf9fef7b` |
| Create request | `0x4993e9cd` |
| Verify response | `0xe7498545` |
| Verify handshake | `0xb9db2f11` |
| Build envelope | `0xab9941f7` |
| Finalize | `0x07799fc3` |
| Close | `0x055f2771` |

Following `Build envelope` into the native dispatcher shows that the envelope layout changes with the revealed nonce. The release contains two XOR-split 32-byte values needed by a replacement client. Recombining the shares produces these keys:

```text
variant key: 76505bdbc56782425f3d162a2b6b28496a1891f921f7974c37f2dee73b26fceb
proof key:   6e2c211ee477e9b498e24cd01b58db436072ffee67372d9506d8bc311eb72ec7
```

The variant key HMACs the four-byte frame magic followed by the revealed nonce. Bytes from that digest derive a per-heat envelope magic, profile tag, selector, and stride. The selector always has its high bit set. A valid version 2 envelope is a list of tagged modifier profiles using those derived values.

The coordinator and native policy VM select different entries from the same list. The coordinator treats the selector as an unsigned byte. The native VM casts it to a signed byte before adding the stride. With three profiles, subtracting 256 changes the index because 256 is not divisible by 3.

The player can therefore place the aggressive legal profile at the coordinator's unsigned index, `HT` at the native signed index, and `NM` in the remaining slot. Ready-up locks the aggressive profile and its actual playback rate. The native policy later emits `HT` from the exact same signed capsule. The exploitable value is the selector interpretation. The server still ignores declared scores and rejects old transcripts.

The ready and judge capsules remain opaque. The final operation needs both exact capsule byte strings and proof of a policy claim. Native analysis shows that the proof is HMAC-SHA256 over the frame magic, finish action, SHA-256 of each capsule, claim acronym, rate, and settings hash. The recovered proof key is sufficient to reproduce this operation without recovering the server's capsule key.

The settlement uses the ratio between the applied and attested native multipliers. `HT` has a 0.300x multiplier. For example, taiko with a 1.384x applied profile produces a 4.613x settlement ratio. A high raw basis can then cross the 2,100,000 exploit target while the ready profile and final native claim remain different. RSCTF still admits a completed lower score; exploitation raises the official native result rather than acting as a binary platform-scoring gate.

Gameplay remains required. The coordinator calculates hit count, accuracy, input digest, coverage, and raw basis from fresh input receipts. Each cue is tied to the current ticket, note index, time window, and action. A fresh run is required for every wave the team wants to score; modes rotate in four-wave bags.

## Exploitation

The complete player-developed implementation is [htb/solver.py](htb/solver.py). It contains the session exchange, chained request authentication, binary frame codec, exact `.osz` parser, mode-specific input generation, dynamic policy envelope, completion proof, and repeated-wave loop.

The policy envelope builder below is a byte-for-byte excerpt from that solver:

```python
def modifier_envelope(nonce, applied):
    magic, tag, selector, stride = policy_spec(nonce)
    profiles = [[], [], []]
    server_index = (selector + stride) % len(profiles)
    signed_selector = selector - 256 if selector >= 128 else selector
    native_index = (signed_selector + stride) % len(profiles)
    if server_index == native_index:
        raise RuntimeError("release selector did not create two policy views")
    profiles[server_index] = applied
    profiles[native_index] = ["HT"]

    raw = bytearray(magic)
    raw.extend((MODIFIER_ENVELOPE_VERSION, len(profiles)))
    for profile in profiles:
        value = bytearray((len(profile),))
        for acronym in profile:
            encoded = acronym.encode("ascii")
            if not encoded or len(encoded) > 8:
                raise RuntimeError("invalid modifier acronym")
            value.append(len(encoded))
            value.extend(encoded)
        raw.append(tag)
        raw.extend(struct.pack("<H", len(value)))
        raw.extend(value)
    return bytes(raw)
```

### Connecting to the server

Install the two Python dependencies and supply either local team fields or an event token:

```console
$ python3 -m pip install -r htb/requirements.txt
$ python3 htb/solver.py --host 127.0.0.1 --port 1337 \
    --team patch-crew --name "Patch Crew"
```

```console
$ python3 htb/solver.py --host 1pc.tf --port PORT --scheme https \
    --token "$TEAM_TOKEN"
```

For each leg, the solver performs the following player-visible actions:

1. Enrolls and waits for the exact map and mode reveal.
2. Downloads the pinned `.osz`, checks its SHA-256, and parses the `.osu` file with the revealed BeatmapID.
3. Builds the nonce-specific three-profile envelope and locks the legal aggressive profile.
4. Measures server clock offset, waits for the authenticated running phase, and requests short-lived cues.
5. Sends down and up input frames near every chart target at the locked playback rate.
6. Retrieves the server judgment and judge capsule, builds the `HT` completion proof, and submits the binary finish frame.

Some pinned maps have a scoreable object at chart time zero. Waiting for the authenticated `running` phase avoids racing the coordinator's reveal transition. The solver also requests cues in small windows so expired tokens are never reused.

### Taking the Crown

The final verified run used real pinned maps and fresh server-receipt timing for every object. The output below is trimmed only to remove repeated wait and progress lines.

```console
$ python3 htb/solver.py --host 127.0.0.1 --port 17341 \
    --team patch-crew --name "Patch Crew"
[+] Judge: 520/526 hits, 97.129% accuracy, raw basis 971292
[+] Wave Crown captured with official score 3,032,598
```

The Crown remains team-bound. A declared score, old input transcript, or local-only result cannot enter the wave. Every later wave requires new server-timed play; completing one wave does not carry its score forward.
