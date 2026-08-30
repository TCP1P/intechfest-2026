---
name: rsctf-challenge-authoring
description: Create or modify rsctf Repository Bindings challenge packages. Use for package layout, manifests, handouts, container source, A&D/KotH checkers, deterministic generators, managed KotH reporters, or transport gateways; use the separate solution, playtest, and release-review skills for those phases.
---

# rsctf Challenge Authoring

Keep challenge repositories easy to copy, safe to import, and honest about what a
player receives. Preserve existing rsctf manifest and runtime contracts unless the
request explicitly changes them.

## Route the task

Read each selected document completely before editing:

- First-time authoring or choosing an example:
  [Getting started](../../../docs/getting-started.md)
- Repository/package layout, component ownership, or restructuring during implementation:
  [Authoring contract](../../../docs/authoring-contract.md) and
  [agent package contract](references/package-contract.md)
- `.gzevent`, `challenge.yaml`, attachments, containers, A&D/KotH fields:
  [Manifest reference](../../../docs/configuration.md)
- A&D/KotH checker or verdict behavior:
  [Checker development](../../../docs/checkers.md)
- Managed Leaderboard KotH reporting or a transport gateway:
  [Managed KotH reporting](../../../docs/koth-reporting.md)
- Deterministic variants or trusted solve receipts:
  [Provenance](../../../docs/provenance.md)
- Import mechanics needed while authoring:
  [Repository Bindings](../../../docs/importing.md)

For a cross-cutting request, read every applicable route. Do not read unrelated
references merely because they exist.

## Workflow

1. Inspect Git status, the closest example package, its manifest, relevant tests,
   current player delivery contract, and exact audience of every affected file. Preserve
   unrelated changes.
2. State the challenge type, player delivery mode, flag/control flow, and runtime owner.
   Separate public player material and team-owned BYOC controls from author source,
   secrets, solvers, and organizer operational tooling.
3. Copy the closest cohesive package and delete components the new challenge does not
   need. Choose `warmup` or `main-event`, then keep the package under
   `events/<event>/challenges/<mode>/<category>/<slug>/` and follow the canonical ownership
   table. Never copy a package across events merely to populate an empty event.
4. Put behavior in its owning layer: manifest metadata in `challenge.yaml`, service
   code in `src/`, player files in `dist/`, checker protocol in `checker/run.py`,
   deterministic generation in `generator/`, managed evidence reporting beside its
   authoritative state in `src/`, and an unavoidable transport-only adapter in `gateway/`.
5. Add or update focused regression coverage for contract changes. Keep inputs bounded,
   dependencies pinned, and secrets absent from source and evidence.
6. Run focused checks, `make validate`, and `make matrix`. Both commands must use the
   matching official `rsctf` binary; do not add a repository-local validator,
   discovery helper, or wrapper. Confirm CI imports the maintained
   `dimasma0305/rsctf` action, builds every emitted context, and waits for each
   service image's Docker `HEALTHCHECK`. When runtime or generator inputs change,
   require that dynamic container job and separately exercise player-visible
   protocol, checker verdicts, and flag behavior in hidden staging.
7. When the request includes downstream verification, continue with
   `$rsctf-challenge-solution`, `$rsctf-challenge-playtest`, and
   `$rsctf-challenge-release-review` as applicable. Do not substitute an authoring smoke
   test for those workflows.

## Hard boundaries

- Start imported examples hidden and disabled. Do not use public Git flags in a real
  event or imply that rewriting history makes a disclosed value secret again.
- Omit `minScoreRate`, `difficulty`, and `submissionLimit` when rsctf's current
  Jeopardy defaults are intended. Add them only for a deliberate scoring-policy
  override, and omit them from A&D and KotH manifests.
- Set `ad.allowEgress: true` for managed A&D and KotH packages so targets can reach the
  internet. Use `false` only for a deliberate reviewed isolation requirement.
- Never commit organizer tokens, reporter HMAC secrets, undisclosed internal endpoints,
  database dumps, container logs containing flags, live credentials, or unredacted secret
  evidence. Reviewed public player endpoints belong only in their owning release inputs.
- Give every package a tracked organizer `solution/README.md` and small `solution/solve.py`.
  Keep them outside every player import surface, build context, handout, and blind-playtest
  input. Never add a manifest inside `solution/` because local recursive validation still
  discovers nested manifest files.
- A&D checkers must verify health and the current flag without mutating service state.
  KotH checkers receive no flag and must not inspect or alter the control source.
- A managed Leaderboard target reads only rsctf-injected `RSCTF_KOTH_*` values, submits
  bounded native evidence rather than points, and filters those values from child-process
  environments. Add `gateway/` only for a concrete transport mismatch; it must remain
  stateless with respect to identity/scoring and receive no reporter credential.
- Checkers have one target IP/port, bounded time and output, a read-only source/venv,
  and only exact wheel-installable PyPI pins.
- Do not present the included `DynamicAttachment` manifest as playable while rsctf
  lacks per-participation handout/flag assignment.
- Keep human maintainer documentation in `docs/`. Files intentionally delivered to
  players remain in `dist/` even when they are text.
- Do not invent screenshots. Capture the current player-visible state, or use text
  evidence when that communicates the result better.
