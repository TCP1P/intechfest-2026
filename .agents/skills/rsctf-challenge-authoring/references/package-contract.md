# Agent package contract

Use this reference when creating, moving, or reviewing challenge package files. The
authoritative human contract is
[`docs/authoring-contract.md`](../../../../docs/authoring-contract.md); read it completely
before changing layout.

## Classify before editing

Resolve these facts from the request, nearest example, and current manifest:

1. Mode: `AD`, `Jeopardy`, or `Koth`.
2. Challenge type: attachment, container, Attack & Defense, or King of the Hill.
3. Player delivery: `service`, `handout`, `hybrid`, or `byoc`.
4. Flag/control flow: static flag, injected dynamic flag, rotating flag file,
   deterministic variant, KotH marker, or managed Leaderboard evidence.
5. Runtime owner: rsctf-managed container, team-owned BYOC host, optional transport
   gateway, or no service.
6. Exact public inputs and exact author-only inputs.

Do not infer that repository source is player-visible. Do not infer a handout from the
presence of `dist/`; verify `provide` and the intended delivery contract.

## Build the smallest valid package

The canonical root is
`events/<event>/challenges/<mode>/<category>/<slug>/`, where `<event>` is `warmup` or
`main-event`. Use lowercase kebab-case
for the slug, an exact supported category, and `challenge.yaml` as the filename.

| Concern | Owning path | Add when |
| --- | --- | --- |
| platform contract | `challenge.yaml` | always |
| player download | `dist/` | players receive one or more files |
| attackable service | `src/` | rsctf builds a service locally |
| A&D/KotH health | `checker/` | challenge uses the A&D engine |
| deterministic variant | `generator/` | local `PerParticipation` generation is used |
| managed reporter | `src/` | the KotH target owns native evidence and rsctf scoring is enabled |
| transport adapter | `gateway/` | the player protocol cannot use the platform's exposed transport directly |
| organizer solve | `solution/` | every package has concise `README.md` and `solve.py` |

Delete every copied component not justified by this table. Do not create package-local
author README files, a generic `assets/`, a root script directory, or duplicate source.
Player instructions belong in manifest copy or `dist/`; repository guidance belongs in
`docs/`.

## Ownership tests

For every added or moved file, ask:

- Who reads it: player, service, rsctf checker/generator, gateway operator, author, or
  repository contributor?
- Is that audience already represented by a canonical directory?
- Can the file be removed without changing the declared contract?
- Could the file leak through `provide`, a Docker `COPY`, a generated archive, logs, or a
  screenshot?
- Is another copy already the source of truth?

Move the file to its owner or remove it when these answers are unclear. Do not create a new
directory merely to avoid deciding ownership.

## Component rules

### Manifest

- Copy the closest supported example and remove inapplicable keys.
- Keep name unique within the event and category consistent with the path.
- Omit default Jeopardy scoring keys unless the request explicitly changes policy.
- Never add guessed keys. `rsctf challenge check` rejects unknown fields.
- Keep live secrets out even if the remote is currently private.

### Handout

- Treat `dist/` as byte-for-byte public.
- Inspect the final imported file or ZIP, not only its source directory.
- Hash it for the organizer solution and playtest contract.
- Exclude solvers, solutions, repository metadata, hidden source, organizer notes, and
  live flags.

### Service

- Use `src/` as a narrow self-contained Docker context.
- Run non-root unless the exploit contract requires a reviewed exception.
- Keep hostile input and resource use bounded.
- Follow the correct static, injected, or rotating flag contract.
- Add a fast, non-mutating Docker `HEALTHCHECK` against the ordinary loopback protocol;
  never expose a flag through it.

### Checker

- Copy the whole nearest checker directory.
- Keep shared sandbox/verdict behavior in `lib.py` and protocol assertions in `run.py`.
- Make each registered check bounded, read-only, and order-independent.
- A&D verifies ordinary behavior plus the current flag. KotH never touches its control
  source.

### Generator

- Make identical input produce byte-identical output.
- Use only the documented input and one bounded JSON output.
- Disable network and mutable external inputs by design.
- Keep player content/hints separate from server-side generated flags.

### Managed reporter and gateway

- Keep the reporter beside the authoritative scoring state in `src/`; do not create a
  second private scoring feed merely for another process.
- Read the exact injected URLs, scope IDs, and `koth_target_…` secret. Stay healthy without
  them for pre-cycle provisioning, but fail closed on event identity/scoring operations.
- Filter eligible capability hashes, validate evidence/Crown invariants, sign exact raw JSON,
  verify the full raw acknowledgement, and submit evidence rather than invented points.
- Never pass `RSCTF_KOTH_*` values into a player-facing child process or persist them.
- Add `gateway/` only for an unavoidable transport adapter. It may proxy/relay and track the
  active target, but receives no reporter secret and owns no roster, evidence, or scoring
  state.

### Solution

- Keep tracked `README.md` and `solve.py` challenge-specific, concise, and reproducible.
- Treat them as disclosed to every repository reader. Restrict real pre-event repository
  access or preserve an unpublished copy in a separate controlled store.
- Use the solution skill for writeup, solver, screenshot, freezed, and freeze-state rules.
- Never read it during a blind solve or copy it into a playtest room.

## Change procedure

1. Inspect Git status and preserve unrelated edits.
2. Read the nearest example and every routed human document.
3. State the player contract and component set.
4. Make the smallest cohesive package change.
5. Check audience boundaries and inspect archive/Docker copy paths.
6. Run component-specific tests, then `make validate` and `make matrix` with the matching
   official rsctf binary.
7. Update the organizer solution verification record when behavior changed.
8. Require a fresh isolated playtest and hidden staging evidence for readiness claims.

Documentation-only layout changes do not require Docker builds, but all links, examples,
ignore rules, and official CLI commands still require verification.
