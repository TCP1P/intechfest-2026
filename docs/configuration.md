# Repository manifest reference

This page describes the configuration that the current rsctf Repository Bindings importer
actually consumes. The manifests under `events/` are the source of truth. Copy the closest
compatible package only when its delivery and runtime model match; change only what the new
challenge needs. For directory ownership and minimal layouts by challenge type, use the
[challenge authoring contract](authoring-contract.md).

## Repository layout

One `.gzevent` creates one game and owns every `challenge.yaml` or `challenge.yml` below its
directory. This repository has two sibling roots and uses this convention inside each:

```text
events/<event>/.gzevent
events/<event>/challenges/AD/<category>/<challenge>/challenge.yaml
events/<event>/challenges/Koth/<category>/<challenge>/challenge.yaml
events/<event>/challenges/Jeopardy/<category>/<challenge>/challenge.yaml
```

`<event>` is `warmup` or `main-event`. Do not put a `.gzevent` at the repository root or
inside a challenge package.

The category directory must be one of `Misc`, `Crypto`, `Pwn`, `Web`,
`Reverse`, `Blockchain`, `Forensics`, `Hardware`, `Mobile`, `PPC`, `AI`,
`Pentest`, or `OSINT`.

## Event manifest

Event settings seed a game only on its first import, so later Admin game edits persist.
Challenges are reconciled by their binding-scoped manifest path: a same-path challenge is
updated in place with its ID and accumulated state preserved. A missing manifest is guarded
and disabled as a tombstone rather than deleted; a rename creates a new identity and sends
the old path through that removal flow. A challenge type cannot change at the same path.

| `.gzevent` key | Default | Notes |
| --- | --- | --- |
| `title` | required | Must be non-empty; give every imported event a stable, unique title |
| `start` | import time + 1 day | ISO-8601 timestamp |
| `end` | import time + 30 days | ISO-8601 timestamp |
| `hidden` | `false` | Use `true` until review and staging are complete |
| `summary`, `content` | empty | Player-facing Markdown content |
| `acceptWithoutReview` | `false` | Whether new team joins are accepted automatically |
| `inviteCode` | absent | Optional event join code |
| `teamMemberCountLimit` | `0` | `0` means the normal unlimited/default behavior |
| `containerCountLimit` | `3` | Per-team normal-container limit |
| `practiceMode` | `true` | Allows supported post-event practice behavior |
| `writeupRequired` | `false` | Require a writeup submission |
| `writeupDeadline` | import time + 30 days | ISO-8601 timestamp |
| `writeupNote` | empty | Writeup instructions |
| `bloodBonus` | platform packed default | Internal packed value; configure percentages in Admin |

`poster` and `organizations` are accepted by the parser but are not currently
applied when the game is created. Do not rely on them in a repository template.

The event-wide `ad:` block supports:

| Key | Purpose |
| --- | --- |
| `tickSeconds` | Round cadence |
| `flagLifetimeTicks` | Number of rounds a flag remains valid (`1..50`) |
| `warmupSeconds` | Delay before round/scoring activity starts |
| `resetCooldownMinutes` | Cooldown between managed-service resets |
| `allowSnapshotDownload` | Allow managed-service snapshot downloads |
| `snapshotRetentionDays` | Expire downloadable service snapshots this many days after the event ends; omission means no expiry |
| `getflagWindowFraction` | Bound the randomized checker-start jitter after flag publication as a fraction of the tick, subject to remaining runway |
| `minGracePeriodSeconds` | Delay checker starts after flag publication and reserve enough runway to avoid a clipped terminal round |

See the [warmup](../events/warmup/.gzevent) and
[main-event](../events/main-event/.gzevent) manifests for safe hidden staging
configurations.

### Warmup KotH crown-cycle settings

The current Repository Bindings schema does not import KotH crown-cycle settings. Configure
the warmup game in Admin before enabling either hill:

| Setting | Required value |
| --- | ---: |
| KotH epoch ticks | `12` |
| KotH cycle ticks | `6` |
| Previous-champion cooldown ticks | `1` |
| Claim-confirmation ticks | `2` |

This is a required runtime contract, not optional tuning. Draft Control becomes available to
players only after the new cycle target passes readiness. A three-tick cycle can reset the
target before a newly written marker receives two complete confirmation observations. The
six-tick cycle leaves a bounded acquisition window and still divides the 12-tick epoch.
Apply these values only before official KotH scoring starts; rsctf correctly locks them after
the official configuration is snapshotted.

## Common challenge keys

| Key | Default | Notes |
| --- | --- | --- |
| `name` | required | Display name |
| `author` | absent | Rendered above the description |
| `description` | empty | Markdown/HTML challenge text |
| `type` | required | A supported type shown in the copyable-examples table below |
| `category` | inferred/`Misc` | Explicit category should match its directory |
| `minScoreRate` | `0.01` | Dynamic-score floor: 10 points from the default 1000, validated in `0..1` |
| `difficulty` | `5` | Positive dynamic-score decay factor |
| `submissionLimit` | `0` | `0` means no explicit attempt limit |
| `hints` | absent | String list |
| `flags` | absent | Static flag rows for attachment/container challenges; imported `DynamicAttachment` rows are not yet assigned per team; do not use for A&D or KotH |
| `flagTemplate` | absent | Flag generator for `DynamicContainer` and A&D; use `[TEAM_HASH]` and, when rotation is needed, `[GUID]` |
| `provide` | `dist/` convention | Attachment file or directory relative to the manifest |
| `disableBloodBonus` | `true` | Disable first-solve bonuses for this challenge; set `false` only for a deliberate bonus |
| `ignore` | `false` | `true` prevents creation and is rejected by this catalog validator |
| `variantMode` | `Disabled` | `PerParticipation` asks rsctf to derive one deterministic variant per accepted participation before the event |
| `variantGeneratorImage` | absent | Optional registry fallback ending in `@sha256:<64 lowercase hex>`; omit when `generator/Dockerfile` is present |
| `variantGeneratorDigest` | absent | Matching digest for the registry fallback; supply both image fields or neither |
| `solveReceiptMode` | `Disabled` | `Optional` accepts a trusted proof when supplied; `Required` rejects solves without one |
| `receiptVerifierIdentity` | absent | Stable verifier name recorded in solve receipts; required with `Optional` or `Required` |

The examples omit `minScoreRate`, `difficulty`, and `submissionLimit` so rsctf owns
their defaults. Add a key only when the event deliberately overrides that setting.
These fields do not apply to Attack & Defense or King of the Hill, whose scores come
from their mode-specific engines.

Validate authored overrides with `rsctf challenge check`: unlike the compatibility
importer, the offline CLI reports unknown keys so a typo cannot silently turn into an
omitted setting. It also rejects non-finite/out-of-range scoring values and negative
submission limits rather than accepting a different effective policy.

Do not add `value`: the repository importer always creates a 1000-point Standard
challenge and ignores unknown keys. Only `minScoreRate` and `difficulty` control
the imported jeopardy score curve. Other misleading, ignored keys include
`scoreCurve`, `originalScore`, `deadline`, `networkMode`, `isEnabled`, and
`adScoringWeight`.

With `variantMode: PerParticipation`, omit both image fields and include a
regular `generator/Dockerfile`; a trusted scan builds and checks it
automatically. Supplying exactly one image field is invalid. Explicit matching
image and digest fields remain the fallback for deployments that cannot share
a trusted Docker daemon. When every provenance key and the generator directory
are omitted, Repository Bindings preserves policy set in Admin. Provenance is
currently Jeopardy-only and becomes immutable when the event starts. A variant
generator may derive only the participation's flag, content, and hints; it does
not create or replace attachment files. Configure
`RSCTF_EVENT_VPN_CREDENTIAL_KEY` before generation.
Any enabled receipt mode additionally needs
`RSCTF_SOLVE_RECEIPT_ISSUER_TOKEN` on the control surface. See
[`provenance.md`](provenance.md) for the runtime contract and automation calls.

## Attachment packaging

`provide` is relative to the directory containing `challenge.yaml`:

- A single file is attached as-is.
- A directory containing one file attaches that file as-is.
- A directory containing multiple files is packaged as a ZIP.
- If `provide` is omitted, rsctf looks for a `dist/` directory; these examples name
  the path explicitly so the delivery contract is obvious.
- Absolute paths, `..` traversal, and symlinks outside the package are rejected.

Treat `dist/` as the exact player handout. It must not contain a real flag, private
writeup, intended solver, repository history, organizer notes, or source unless source
is intentionally part of the public challenge contract.

## Container block

`StaticContainer`, `DynamicContainer`, `AttackDefense`, and `KingOfTheHill` use
`container:`:

| Key | Behavior |
| --- | --- |
| `containerImage` | Omit to build `src/Dockerfile` (then package-root `Dockerfile`); a concrete reference switches to registry pull/pin behavior |
| `flagTemplate` | Container-local override for the top-level template |
| `memoryLimit` | Memory limit in MiB; defaults to 64 for Jeopardy/KotH and 256 for managed A&D |
| `cpuCount` | Backend-specific CPU limit; use `1` for the portable examples |
| `storageLimit` | Writable-layer limit in MiB: Kubernetes enforces ephemeral storage; Docker enforces an XFS project quota when supported and otherwise reports an unbounded fallback |
| `exposePort` | Service port inside the challenge container |
| `enableTrafficCapture` | Effective only for platform-hosted A&D services today; omit it elsewhere |
| `enableSharedContainer` | Effective only for `StaticContainer`; all teams share one instance |

This repository deliberately omits every `containerImage`, so Repository
Bindings builds the checked-in source. Local archive builds require the Docker
backend and one daemon shared by the builder and container owner. Kubernetes or
independent node-local daemons need a prebuilt immutable registry image instead.

Every long-running example image includes a Docker `HEALTHCHECK` against its normal
service protocol. This helps local Docker and CI detect a process that is running but
not responding. It does not replace an A&D/KotH checker, and Kubernetes does not use
Dockerfile health checks; validate those runtimes through their actual backend too.

## A&D and KotH block

`AttackDefense` and `KingOfTheHill` use `ad:`:

| Key | Import default | Behavior |
| --- | --- | --- |
| `checkerImage` | absent | Omit it; concrete checker-container references are rejected, while an adjacent `checker/run.py` entry point and its sibling source files are prepared automatically |
| `allowEgress` | `false` | Controls outbound networking for platform-managed A&D and KotH containers, not checker or BYOC-host egress; this repository sets it to `true` explicitly, and the deployment must use a backend that can enforce it |
| `allowSelfReset` | `true` | Player reset for managed A&D; unavailable for BYOC |
| `sshRequiresFlag` | `false` | Persisted but not currently enforced by SSH authorization; omit it from runnable templates |
| `selfHosted` | `false` | `true` selects BYOC and is valid only for `AttackDefense` |

Put a `checker/` directory beside every A&D or KotH manifest. Copy the complete
directory from the closest example: `lib.py` supplies the protocol-neutral
contexts, verdicts, `@checker` registry, and `run_ad_checker()` /
`run_koth_checker()` entry points; the legacy `@ad_checker` and `@koth_checker`
wrappers remain available. `run.py` owns the challenge protocol and assertions.
An optional `requirements.txt` beside `run.py` may contain only simple, exactly
pinned PyPI packages such as
`pwntools==4.15.0`; URLs, local paths, editable installs, pip options, and
unpinned or ranged versions are rejected. Repository Bindings installs these
packages and their dependencies into the immutable checker environment using
wheels only, so preparation fails when a compatible wheel cannot be resolved.

The current warmup KotH checkers use the Python standard library and need no dependency file.
Future dependencies require PyPI access during a trusted repository scan or admin approval;
review the repository commit and exact pins before preparing them. See
[`checkers.md`](checkers.md).

The examples register focused checks whose execution order is cryptographically
and independently shuffled for each checker process. A fresh shuffle may repeat
an earlier order. Every registered function is attempted once; none is randomly
skipped, and source-registration order is not execution order.
Failures do not skip later checks; the final priority is InternalError, Offline,
Mumble, then OK.
The A&D suite as a whole must cover full service health and the current flag.
Checks must be read-only and order-independent. Order and fingerprint variation
is only defense-in-depth: it does not hide the checker source IP or by itself
prevent source-IP allowlisting.

Use the current `Parcel Panic` package for the self-hosted/BYOC shape. A future
platform-hosted package uses the corresponding managed settings:

```yaml
# Platform-hosted: rsctf launches one service container per accepted team.
ad:
  allowEgress: true
  allowSelfReset: true
  selfHosted: false
```

```yaml
# Self-hosted/BYOC: each team runs the downloaded service behind a relay.
ad:
  selfHosted: true
```

For KotH, use only settings that affect the platform-owned hill:

```yaml
ad:
  allowEgress: true
```

The import default remains `false`, so repository packages opt in explicitly. Set it back
to `false` only when isolation is part of the reviewed challenge contract. Private managed
callbacks do not depend on general Internet access. rsctf's current Docker backend rejects
competitive `true` workloads; use a policy-capable backend for these manifests.

The repository manifest does not contain a reporter secret or claim-source switch. For a
managed Leaderboard hill, import the ordinary `KingOfTheHill` manifest, configure rsctf's
private `RSCTF_KOTH_REPORTER_BASE_URL`, then enable **Leaderboard** for that hill in the
A&D / KotH operator view before the official snapshot is created. rsctf injects a
lifecycle-bound credential and exact callback URLs into the managed target. It freezes the
selected Marker or managed Leaderboard claim source when official scoring starts; the target
reports bounded native evidence, never a platform score. Challenge packages in this
repository do not use a separately deployed scoring process.

Even in BYOC mode, keep a local `src/Dockerfile`: rsctf builds it during import
and streams the immutable challenge-service image to authorized teams. The BYOC
tunnel must be routed to a network-capable rsctf role. The generated bundle also
uses the separately configured BYOC relay-agent image; mirror that platform
dependency and all base images for a completely Docker-Hub-free deployment.

## Current copyable examples

| Type/mode | Example |
| --- | --- |
| self-hosted/BYOC `AttackDefense` | [`AD/Web/parcel-panic`](../events/warmup/challenges/AD/Web/parcel-panic/) |
| self-hosted/BYOC `AttackDefense`, built kernel image | [`AD/Pwn/yani-netlink`](../events/main-event/challenges/AD/Pwn/yani-netlink/) |
| marker/Boot2Root `KingOfTheHill` | [`Koth/Web/draft-control`](../events/warmup/challenges/Koth/Web/draft-control/) |
| managed-reporting `KingOfTheHill`, direct browser/API game | [`Koth/PPC/serpent-circuit`](../events/warmup/challenges/Koth/PPC/serpent-circuit/) |
| managed-reporting `KingOfTheHill`, direct private TCP coordinator | [`Koth/Reverse/rythme`](../events/main-event/challenges/Koth/Reverse/rythme/) |
| managed-reporting `KingOfTheHill`, direct private TCP/UDP arena | [`Koth/PPC/minions-in-32k`](../events/main-event/challenges/Koth/PPC/minions-in-32k/) |

The field reference above covers other rsctf types, but these packages remain specific to
their delivery and claim contracts. For deterministic variants and
trusted solve receipts, continue with [`provenance.md`](provenance.md). For custom functional
checks, continue with [`checkers.md`](checkers.md).
