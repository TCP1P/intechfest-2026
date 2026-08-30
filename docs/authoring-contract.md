# Challenge authoring contract

This is the canonical layout and ownership contract for challenges in this
repository. Use it when creating a package, deciding where a file belongs, or
reviewing whether a package is complete. The existing packages are runnable examples;
this page explains the rules that connect them.

The central rule is simple: every file has one owner and one audience. Do not copy a
file between author, player, runtime, checker, or organizer surfaces for convenience.

## Repository layout

```text
.
├── .agents/                         # automatic AI skill workflows and references
│   └── skills/rsctf-challenge-playtest/
│       └── playtest/                # ignored temporary clean-room preparation
├── .github/workflows/validate.yml   # official rsctf validation and dynamic builds
├── AGENTS.md                        # AI-agent entry point
├── Makefile                         # short local rsctf commands
├── docs/                            # human documentation, templates, and images
├── events/
│   ├── warmup/
│   │   ├── .gzevent                 # owns only warmup manifests below it
│   │   └── challenges/<mode>/<category>/<slug>/
│   └── main-event/
│       ├── .gzevent                 # owns only main-event manifests below it
│       └── challenges/<mode>/<category>/<slug>/
└── README.md                        # short repository entry point
```

The repository contains exactly two sibling event roots: `events/warmup/` and
`events/main-event/`. Each `.gzevent` owns only the challenge manifests below its event
directory. Never add a root `.gzevent`, nest one event below another, or move a package
between events without an explicit event decision. The warmup is intentionally empty until a
real warmup challenge exists.

Human maintainer documentation belongs in `docs/`. Agent instructions belong in
`AGENTS.md` and `.agents/`. Each event's `challenges/` tree contains package inputs, player
artifacts, and the tracked organizer-only `solution/` material described below.

## Canonical package layout

```text
events/<event>/challenges/<mode>/<category>/<slug>/
├── challenge.yaml       # required: rsctf import contract
├── dist/                # optional: exact files issued to players
├── src/                 # optional: service Docker build context
│   └── Dockerfile
├── checker/             # required for A&D and KotH
│   ├── lib.py
│   ├── run.py
│   └── requirements.txt # optional: exact wheel-installable PyPI pins
├── generator/           # optional: deterministic variant build context
│   └── Dockerfile
├── gateway/             # optional: stateless transport adapter only
└── solution/            # required tracked organizer material
    ├── README.md        # concise writeup in the standard format
    └── solve.py         # simple reference solver
```

Delete unused directories. Empty placeholders make reviews harder and can accidentally
change importer behavior. `solution/README.md` and `solution/solve.py` are universal and must
contain challenge-specific content rather than copied placeholders. New packages use
`src/Dockerfile`; the platform supports a package-root Dockerfile as a compatibility fallback,
but this repository does not use that fallback for new work.

### File ownership

| Path | Audience | Owns | Must not contain |
| --- | --- | --- | --- |
| `challenge.yaml` | rsctf and players | metadata, public copy, challenge type, delivery and runtime policy | unknown fields, live credentials, private notes |
| `dist/` | players | byte-for-byte downloadable handout | solver, solution, hidden source, real production flag, repository history |
| `src/` | service build and runtime | attackable service plus in-process managed reporting when Leaderboard scoring is used | author notes, committed reporter secret, solution, unrelated tooling |
| `checker/` | trusted rsctf checker sandbox | bounded functional checks against one target IP and port | service implementation, stateful attacks, shell-based dependencies |
| `generator/` | trusted variant runner | deterministic content, hints, and flag derivation | network dependencies, mutable inputs, unrelated service code |
| `gateway/` | organizer-operated transport adapter | unavoidable TLS/protocol/UDP adaptation to the current target | reporter secrets, roster/scoring state, score decisions |
| `solution/` | repository readers and organizers; never players by delivery | intended path, simple solver, reviewed evidence, limitations | live credentials, production flags, unreferenced captures, player-delivery files |

Component-specific unit tests stay with the component and follow that language's normal
layout. Add a package-level test directory only when a real cross-component test cannot be
owned by `src/`, `checker/`, `generator/`, or `gateway/`. Test code is not a substitute
for the organizer reference solver and must not enter `dist/`.

## Naming rules

- `<event>` is exactly `warmup` or `main-event`.
- `<mode>` is exactly `AD`, `Jeopardy`, or `Koth`.
- `<category>` is exactly one supported manifest category: `Misc`, `Crypto`, `Pwn`,
  `Web`, `Reverse`, `Blockchain`, `Forensics`, `Hardware`, `Mobile`, `PPC`, `AI`,
  `Pentest`, or `OSINT`.
- `<slug>` uses lowercase ASCII kebab-case, describes one challenge, and is stable after
  the first import. Use `managed-leaderboard-hill`, not `Managed Leaderboard Hill` or
  `managed_leaderboard_hill`.
- `challenge.yaml` uses the canonical filename even though rsctf accepts
  `challenge.yml` for compatibility.
- Challenge display names must be unique within the event. Directory slugs should also
  be unique after punctuation and whitespace normalization so generated Docker tags do
  not collide.
- Use descriptive lowercase filenames inside a package. Follow the language ecosystem
  only where casing is meaningful.

Renaming a package path does not preserve an imported challenge row. A rescan recreates
challenge records, so coordinate path or display-name changes with staging and any
automation that records challenge IDs.

## Minimal layout by challenge type

Every row below includes universal `challenge.yaml`, `solution/README.md`, and
`solution/solve.py` files.
Start with the nearest example and retain only the other applicable components.

| Challenge contract | Required package components | Optional public delivery | Notes |
| --- | --- | --- | --- |
| shared static handout | `challenge.yaml`, `dist/` | none beyond `dist/` | use `StaticAttachment` and static `flags` |
| deterministic per-team variant | `challenge.yaml`, `dist/`, `generator/` | shared explanatory files in `dist/` | use `StaticAttachment` plus `variantMode: PerParticipation` |
| dynamic handout | `challenge.yaml`, `dist/` | exact declared files | schema-only in the current importer; do not enable |
| shared static service | `challenge.yaml`, `src/` | optional `dist/` for a deliberate hybrid challenge | use `StaticContainer`; static flags are server-side |
| isolated dynamic service | `challenge.yaml`, `src/` | optional `dist/` for a deliberate hybrid challenge | use `DynamicContainer` and `flagTemplate` |
| managed Attack & Defense | `challenge.yaml`, `src/`, `checker/` | optional deliberate handout | rsctf runs one service per accepted team |
| self-hosted/BYOC Attack & Defense | `challenge.yaml`, `src/`, `checker/` | platform-generated bundle and image | keep `ad.selfHosted: true`; do not commit a generated team bundle |
| marker King of the Hill | `challenge.yaml`, `src/`, `checker/` | optional deliberate handout | service writes the current control token to `/koth/king` |
| managed Leaderboard King of the Hill | `challenge.yaml`, `src/`, `checker/` | optional deliberate handout; `gateway/` only for a required transport adapter | target reports native evidence with a lifecycle-bound rsctf credential |

`dist/` is optional for a service unless players truly receive files. A source tree in
Git is not automatically a player handout. Conversely, source intentionally supplied to
players belongs under `dist/`, even when an author copy also exists under `src/`; generate
the public archive deliberately and review it as a release artifact rather than assuming
the two trees remain equivalent.

## Decide the player contract first

Before implementation, write down one delivery mode and its exact inputs:

| Mode | Player receives |
| --- | --- |
| `service` | public challenge copy and one issued URL or host/port |
| `handout` | the exact artifact produced from `dist/` |
| `hybrid` | the exact service target and exact handout |
| `byoc` | public copy, team-specific setup flow, immutable service image, and the controls available on the team's own host |

Then record the intended trust-boundary transitions. For each transition, identify:

1. the capability the player has before the step;
2. the player-visible clue that justifies the next experiment;
3. the action or exploit primitive;
4. the observable result;
5. the new capability gained; and
6. the boundary-skipping alternatives that must fail.

Keep this design material in the organizer-only `solution/README.md` using the
[solution format](solutions.md). The public description states the objective and any
intended rules, but it must not disclose the exploit chain merely to repair a missing clue.

## Challenge design standard

A complete design has one intentional objective, an explainable player path, and a clear
reason each component exists. Before polishing UI or story, prove the vulnerable mechanism
and flag/control path in the smallest functional implementation.

Record these decisions in the organizer solution while designing:

- **Objective:** the concrete capability or information the player must obtain.
- **Initial access:** every target, file, credential, rule, and tool assumption supplied at
  the start.
- **Intended boundaries:** the ordered identities, trust zones, or cryptographic/runtime
  properties the player must cross.
- **Clue map:** the literal public or earned evidence that motivates each non-obvious step.
- **Exploit contract:** behavior that is intentionally vulnerable and behavior that must
  remain secure.
- **Flag/control path:** how rsctf creates, injects, rotates, grades, or attributes the final
  result.
- **Failure model:** expected behavior for malformed input, retries, concurrent players,
  resets, timeouts, and exhausted resources.
- **Difficulty target:** assumed prerequisite skills, intended solve time, and the specific
  reasoning or execution cost behind the estimate.
- **Non-goals:** tempting mechanics, decorative features, and deployment capabilities that
  are explicitly outside scope.

Difficulty must come from the intended reasoning and execution, not from an undisclosed
route, a brittle race, an accidental outage, or a required guess. A hard challenge can
require advanced exploitation; it still needs observable evidence connecting its stages.

Treat every unintended vulnerability outside the designed exploit contract as an ordinary
security defect. Fix it according to severity even when it also reaches the flag. Do not
stack accidental bugs and call the result a multi-step challenge.

## Implementation quality

Challenge code is intentionally vulnerable at a reviewed boundary, but the surrounding
implementation should still be small, legible, and predictable.

- Keep one source of truth for data, protocol constants, and build configuration.
- Prefer ordinary language/project layouts inside each owning component; include lockfiles
  or exact pins where the ecosystem supports them.
- Remove unused packages, endpoints, sample credentials, debug modes, generated caches, and
  copied template text.
- Validate and bound all input not deliberately part of the exploit. Bound output, retries,
  recursion, decompression, subprocesses, file sizes, and concurrency.
- Make startup, shutdown, reset, and error behavior deterministic enough to rehearse.
- Keep timestamps, randomness, and external services out of an intended deterministic
  contract unless they are injected and reproducible.
- Write focused tests for important invariants, the intended vulnerable behavior, and every
  fixed unintended path. Do not add a repository-wide script when the component's normal
  test command owns the behavior.
- Document a surprising implementation constraint in the owning source or organizer solution,
  not in a new package README.

For a player-facing UI, use semantic controls, visible keyboard focus, labeled forms, useful
error messages, and sufficient contrast. It must remain usable without horizontal overflow
at 320 px and respect reduced-motion preferences. Every visual element should support the
application's function, clue path, or event theme; remove decorative controls that look
interactive but do nothing. Test the actual player route with keyboard and assistive
technology before release.

## Flags, credentials, and secrets

Choose the flag mechanism from the challenge type; never invent a second delivery path for
the solver.

- Static attachment/container examples use declared static `flags`. Any committed value is
  permanently disclosed and must be replaced outside this public catalog for a real event.
- Dynamic containers read the rsctf-injected `RSCTF_FLAG` according to the service contract.
- Managed A&D reads `RSCTF_FLAG_FILE` at request time. BYOC uses the relay-managed shared
  flag file. The checker receives the matching expected value.
- Deterministic variants return a server-side flag in their generated manifest. The flag is
  not a player attachment.
- KotH uses the selected marker or managed Leaderboard evidence source and receives no checker
  flag.

Do not use a real event flag in source tests, Docker layers, health checks, logs, screenshots,
solution evidence, or CI. Use unmistakable local values such as `rsctf{local_test}` and
redact retained output to `rsctf{...}`. Store reporter secrets, receipt issuer tokens, admin
JWTs, repository tokens, SSH keys, and production endpoints only in the deployment's
restricted secret/configuration stores.

Review Git history before release. Replacing a leaked secret in the latest tree requires
rotation; rewriting or deleting a file does not make the old value safe again.

## Dependency and artifact policy

- Use the fewest dependencies needed to express the challenge clearly.
- Pin direct dependencies and commit the ecosystem lockfile when it is part of a
  reproducible build. Review transitive packages and base images for the target
  architecture.
- Checker requirements follow the stricter exact-PyPI-pin and wheel-only contract in
  [Checker development](checkers.md).
- Do not vendor package caches, virtual environments, node modules, compiler output, or
  downloaded tools into a package unless the exact bytes are intentionally issued to the
  player and cannot be reproduced safely.
- Record the final handout hash and material image identity in the organizer solution. Build
  and test on the event architecture; a build on a different architecture is supporting
  evidence only.
- Treat an external URL, registry tag, package index, and base-image tag as mutable unless
  the release process pins and verifies an immutable identity.

## Manifest ownership

`challenge.yaml` is the only authored platform contract. Copy a complete example and
remove inapplicable keys. Do not invent metadata aliases or CI-only fields.

- Keep `name`, `type`, and `category` explicit. The category must match its path.
- Keep player copy in `description`; use `hints` only when they are deliberate event
  content.
- Omit `minScoreRate`, `difficulty`, and `submissionLimit` to inherit rsctf's current
  defaults. Add them only for a reviewed Jeopardy scoring override. They do not apply to
  A&D or KotH scoring.
- Use `flags` only for supported Jeopardy static flag rows. Use `flagTemplate` for
  dynamic containers and A&D. KotH uses its control-source contract instead of flags.
- Set `ad.allowEgress: true` for managed A&D and KotH packages so targets can reach the
  internet. Use `false` only for a deliberate reviewed isolation requirement.
- Use a relative `provide` path and keep the target inside the package. This repository
  names `dist` explicitly when a handout exists.
- Omit `containerImage` when Repository Bindings should build the adjacent
  `src/Dockerfile`. When a distributed deployment requires a registry, use the immutable
  image policy agreed with the organizer.
- Never set `ignore: true` in an active catalog. The strict CLI rejects it because no
  challenge would be imported.

The complete field and default behavior remains in the
[manifest reference](configuration.md). The official `rsctf challenge check` command is
authoritative when prose and the current binary disagree.

## Player handouts

Treat `dist/` as a release boundary, not a convenient copy of the author tree.

- Put only files named or implied by the player contract in it.
- Build generated archives reproducibly from reviewed inputs; do not commit transient
  build directories beside the final files.
- Open the exact final file or ZIP in an empty directory and inspect every member.
- Hash the final artifact and record that hash in the organizer solution and playtest
  evidence.
- Remove editor metadata, `.git`, dependency caches, debug databases, core dumps,
  credentials, test accounts, real flags, and unused assets.
- If source is public, include the exact player-facing source tree and instructions needed
  to build or inspect it. Do not silently rely on files that remain only under `src/`.

Repository Bindings attaches a single file directly. A one-file directory also becomes
that file; a directory with multiple files becomes a ZIP. Verify the actual imported
download because a local directory listing alone does not prove the player artifact.

## Service source and Docker contract

`src/` is a self-contained Docker build context. A reviewer should be able to run
`docker build` with that directory and understand every copied file.

- Use a minimal reviewed base image. Pin it by digest for a release when reproducible
  supply-chain identity matters.
- Run the service as a non-root user unless the exploit contract requires a narrowly
  justified privilege.
- Copy only runtime files. Do not use a broad parent-directory build context.
- Listen on `container.exposePort` and on the interface expected by the backend.
- Read dynamic or rotating flags through the documented runtime contract. A&D services
  read the current flag file at request time so rotation does not require a restart.
- Bound request size, concurrency, timeouts, memory use, temporary storage, and error
  output in proportion to hostile player input.
- Declare a Docker `HEALTHCHECK` for every long-running service. It must exercise the
  ordinary service protocol on loopback, finish quickly, avoid mutation, and never print
  or require a secret flag.
- Keep the container functional with the read-only, capability-free restrictions used by
  CI unless the manifest and deployment explicitly require a reviewed writable path or
  capability.

A healthy image proves that its process starts and answers the health probe. It does not
prove the challenge is solvable, that a flag rotates, that a checker classifies failures
correctly, or that Kubernetes readiness is configured.

## Checker, generator, reporter, and gateway boundaries

### Checker

Every A&D and KotH package includes the complete canonical `checker/` directory.
`lib.py` owns environment validation, verdicts, registration, shuffling, and exit-code
mapping. `run.py` owns the challenge protocol and assertions. An optional
`requirements.txt` contains only simple exact PyPI pins with available wheels.

Checks are bounded, read-only, order-independent, and limited to the supplied target IP
and port. An A&D suite verifies ordinary service health and the exact current flag. A KotH
suite verifies ordinary function without reading or modifying the marker or API control
source. See [Checker development](checkers.md).

### Generator

A local deterministic variant uses `generator/Dockerfile` and a self-contained generator
entry point. It reads only the documented input, uses no network, writes one valid JSON
result, and produces byte-identical output for identical input. Generated content and hints
may be player-facing; generated flags remain server-side. See
[Provenance](provenance.md).

### Managed reporter and gateway

Leaderboard evidence is owned inside the rsctf-managed target. rsctf injects exact context
and observation URLs plus a lifecycle-bound signing credential at runtime. The target
submits bounded native evidence and a unique-leader Crown assertion, never platform points.
Do not commit or forward that credential to a player client or avoidably broad child process.

Add `gateway/` only when the platform's exposed transport cannot directly carry the player
protocol. It may adapt TLS, HTTP, or UDP and track the current target, but it has no reporter
credential, scoring state, roster authority, or rsctf scoring access. See
[Managed KotH reporting](koth-reporting.md).

## Organizer solution boundary

The canonical local author path is:

```text
events/<event>/challenges/<mode>/<category>/<slug>/solution/
├── README.md            # required concise writeup and verification record
├── solve.py             # required simple Python reference solver
├── requirements.txt     # optional exact solver dependency pins
├── assets/              # optional screenshots or freezed figures actually referenced
└── fixtures/            # optional small redacted inputs needed only by the solver
```

These files are tracked. Anyone with repository read access can read them. Keep real pre-event
repositories restricted, or retain unpublished solutions in a separate access-controlled
store. This private repository deliberately tracks organizer solutions for its maintainers.
Tracking a solution does not make it player-facing: `provide`, Docker contexts, generators,
gateways, and playtest rooms must still exclude it.

Never put `challenge.yaml` or `.gzevent` inside `solution/`, because recursive discovery
would treat them as live manifests in a working tree. Never copy `solution/` into `dist/`,
`src/`, a generator context, gateway deployment bundle, screenshot, or playtest room.

Use the exact format and copyable template in [Solutions and reference solvers](solutions.md).

## Evidence locations

| Evidence | Location | Commit policy |
| --- | --- | --- |
| temporary blind-run room and raw captures | `.agents/skills/rsctf-challenge-playtest/playtest/` | ignored |
| organizer writeup screenshots | package `solution/assets/` | commit only when referenced, current, and redacted |
| durable human documentation images | `docs/assets/screenshots/` | commit only when current and useful |
| player-facing images or files | package `dist/` | committed as part of the exact handout |
| service UI assets | package `src/` | committed as runtime source |

Do not create a screenshot to decorate a text-only procedure. Capture one when a visual
state, layout, or multi-step UI interaction materially proves something that commands and
text cannot. Every retained image must come from the exact revision and identity it claims,
be readable, and be checked for secrets and stale UI.

## Authoring lifecycle

Use the same sequence for a one-file handout and a multi-service engine challenge; skip
only steps that genuinely do not apply.

1. **Contract:** choose type, category, delivery mode, flag flow, intended boundaries,
   difficulty target, and event constraints.
2. **Scaffold:** copy the nearest example, rename the slug, and delete unused components.
3. **Implement:** build the smallest working challenge and record the intended path in the
   organizer solution as it becomes real.
4. **Test components:** run language tests, build the exact handout or image, exercise
   malformed input and resource limits, and verify health behavior.
5. **Validate structure:** run `make validate` and `make matrix` with the rsctf binary that
   matches the target release.
6. **Prove the intended solve:** run the organizer solver against a clean target or extracted
   handout and update the solution's revision, hashes, commands, and evidence.
7. **Probe unintended paths:** test alternate identities, direct object access, default
   credentials, path traversal, debug routes, source leaks, race conditions, parser
   differences, and mode-specific control bypasses.
8. **Blind playtest:** give a fresh player only the declared contract and retain a report.
   Fix clue gaps and serious shortcuts, then rerun from a fresh room.
9. **Hidden import:** scan the exact commit, inspect all builds, keep the event hidden and
   challenges disabled, and exercise the normal player route.
10. **Release review:** complete the checklist for that same revision before enabling it.

## Definition of done

A challenge is ready only when all applicable claims have evidence for the same revision:

- the manifest and dynamic build matrix pass with the matching official rsctf binary;
- the exact handout or service image contains only its declared audience's files;
- component tests and a clean reference solve pass;
- long-running images reach Docker health and pass an independent player-visible protocol
  test;
- checkers, flag delivery, generators, managed reporters/gateways, BYOC, or engine lifecycle behavior have
  been exercised through their actual staging path when applicable;
- a fresh blind playtest supports the clue and difficulty assessment;
- high- and critical-severity unintended paths are fixed and retested; and
- the hidden staging import, permissions, schedule, and challenge count were reviewed.

Green validation, a working solver, Docker health, or one author's successful solve is only
one part of this evidence. Use the [release checklist](release-checklist.md) for the final
sign-off.
