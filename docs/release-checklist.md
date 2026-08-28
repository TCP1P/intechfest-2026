# Release checklist

Use this before requesting a Repository Bindings scan or enabling a challenge. Read
[Importing with Repository Bindings](importing.md) for the scan/rescan lifecycle. A
checked box means the behavior was observed on the exact revision being proposed.
AI readiness requests automatically route to `$rsctf-challenge-release-review`, which
applies these gates without authorizing an import or enablement by itself.

## Package

- [ ] The package is under
      `events/<event>/challenges/<mode>/<category>/<slug>/`, the event assignment is
      deliberate, and the manifest category matches the directory.
- [ ] Every package component has an owner in the
      [challenge authoring contract](authoring-contract.md); copied placeholders, duplicate
      files, scratch artifacts, and unused directories are removed.
- [ ] Player copy states the objective without leaking the intended exploit.
- [ ] `dist/` contains exactly the files players receive, with no solver, writeup,
      source, history, real flag, or organizer notes unless source is intentionally
      part of the challenge.
- [ ] Container and generator build contexts contain no credentials, private keys,
      reporter/legacy-observer secrets, admin tokens, or production-only configuration.
- [ ] Every dependency is necessary, reviewed, and pinned appropriately.
- [ ] All committed flags are treated as disclosed and replaced for the real event.
- [ ] Every package commits a concise `solution/README.md` and simple `solution/solve.py`;
      neither appears in the handout, image context, generated content, gateway bundle, or
      playtest room.
- [ ] The organizer solution follows the [standard format](solutions.md), records the exact
      candidate commit and applicable artifact/image hashes, and has a durable
      access-controlled home before a real event.

## Functional verification

- [ ] `make validate` passes with the official `rsctf` binary matching the target
      release.
- [ ] `make matrix` succeeds and lists every expected build context.
- [ ] GitHub Actions passes the imported `dimasma0305/rsctf` step, pinned to the
      target rsctf release (or to an action commit plus matching exact image digest).
- [ ] Every direct `src/Dockerfile` and `generator/Dockerfile` appears in the
      dynamically generated matrix; no package is maintained in a hand-written CI list.
- [ ] Every long-running service image declares a Docker `HEALTHCHECK`, reaches
      `healthy` in the dynamic CI job, and then passes an independent
      player-visible protocol check in hidden staging.
- [ ] The service was exercised through the same protocol and exposed port players
      will use, including failure and timeout behavior.
- [ ] A second trusted maintainer reproduced the documented organizer solution or reference
      solver from a clean target/handout for the exact candidate revision.
- [ ] A&D checks retrieve the current rotating flag through player-visible behavior.
- [ ] KotH checks are read-only and do not read or alter the ownership/control source.
- [ ] Resource limits, writable paths, restarts, and flag rotation were tested on the
      intended container backend.

## Player verification

- [ ] A fresh playtester received only the declared service/handout/hybrid/BYOC contract.
- [ ] The run was isolated from author notes, source not issued to players, solver,
      repository history, old reports, privileged logs, organizer-side container
      administration, and container access outside the declared BYOC contract.
- [ ] Required non-obvious transitions have visible breadcrumbs.
- [ ] High- and critical-severity unintended paths are fixed and retested.
- [ ] Difficulty is based on the blind report, not only the author's solve time.
- [ ] Any retained screenshot is current, player-equivalent, readable, and redacted.

## Hidden staging import

- [ ] `.gzevent` remains hidden and the imported challenges remain disabled.
- [ ] The scan reports exactly the warmup and main event, with the expected challenge count
      under each event root.
- [ ] Every local service/generator build completed and its log was reviewed.
- [ ] A normal non-admin player account can access only the intended pre-start and
      player-visible surfaces.
- [ ] A rescan was rehearsed away from a live event; challenge ID/admin-edit churn is
      understood.
- [ ] A&D or KotH changes passed a full multi-team lifecycle rehearsal, including
      scheduling, VPN/BYOC paths, flag delivery, checking, reset, and scoring.
- [ ] Managed KotH reporting was exercised through an actual rsctf lifecycle: the target
      submitted bounded evidence, a reset rotated the credential, the stale target was
      rejected, and no child process inherited `RSCTF_KOTH_*` values.
- [ ] A transport `gateway/`, when present, was tested in both directions and has no reporter
      secret, roster/scoring state, or rsctf scoring access.

Do not enable a future `DynamicAttachment` package until the target rsctf release supports
and rehearses its per-participation download and flag assignment.
