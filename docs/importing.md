# Importing with Repository Bindings

Import only a reviewed commit. Repository Bindings is an administrator trust
boundary: a scan reads manifests, archives source, builds local Docker contexts and
generators, and prepares checker dependencies.

Before the first hidden import, run rsctf's offline preflight and inspect the dynamic
container matrix:

```sh
make check RSCTF=/path/to/rsctf
```

Use `make check` without the assignment when the matching official binary is already on
`PATH`. Its CLI stages catch importer-schema and static package errors without a database.
They do not execute builds, install checker wheels, verify backend networking, or prove a
challenge is solvable, so they cannot replace the hidden staging scan and player smoke test
below.

Also inspect the committed tree, not only the working directory. Each package intentionally
tracks `solution/README.md` and `solution/solve.py`. Repository readers can see them, but they
must not enter `provide`, a Docker context, generated player content, a gateway bundle, or a
blind-playtest room. Keep real pre-event repository access restricted and keep live secrets
out of Git entirely.

The GitHub manifest job imports `dimasma0305/rsctf` and invokes the CLI from the
corresponding official rsctf image. The action resolves the pulled image to an
immutable digest before execution; an optional image override must be an exact
digest from the action repository's GHCR package. It also exposes rsctf's discovered
container matrix for the repository's build job. No repository-local validation or
discovery script participates in either decision.

## First hidden import

1. Reproduce the organizer writeup and reference solver on the intended revision, mark them
   frozen only after that run, then push and record the exact commit.
2. Sign in as an rsctf administrator and open **Admin → Repository Bindings**
   (`/admin/repo-bindings`).
3. Add the Git URL and branch. A public repository needs no token. For a private
   repository, use a fine-grained token with read-only contents access unless the
   workflow explicitly requires push-back.
4. Run **Scan now** and inspect the complete result. This repository should report two
   events: a warmup with three challenge manifests and a main event with four.
5. Open both imported games. Confirm each is hidden and every imported challenge remains
   disabled.
6. Review every discovered service-image build, each deterministic generator
   build/replay result, all prepared checker suites, dependency pins, and any
   reported notice.
7. Set the real schedule and event policy in Admin. The sample `.gzevent` deliberately
   omits `start` and `end`; the importer supplies temporary defaults on first import.

Do not enable challenges from the scan result alone. Finish the
[release checklist](release-checklist.md) and exercise the imported player route with
a normal account first.

## What source builds do

Every current container manifest omits `containerImage`. A trusted scan archives the whole
package, selects the adjacent `src/` Docker context, and builds an internal image. A future
deterministic variant would use the separately documented `generator/Dockerfile` contract.

Local and GitHub checks discover those same direct `src/Dockerfile` and
`generator/Dockerfile` paths automatically. Adding a package does not require editing
a CI matrix. The GitHub job builds every emitted context and requires each service
image to declare and pass its Docker `HEALTHCHECK`; one-shot generators are build-only.
That health gate is an availability check, so exercise player-visible protocol and
checker behavior separately in hidden staging.

This source-build pattern requires the Docker builder and runtime owner to share the
same daemon. Kubernetes and deployments with independent node-local daemons need
reviewed images in a registry, pinned by immutable digest. Pin or mirror the base
image as well when the build host must avoid Docker Hub.

Before enabling a locally built service, inspect its build log and immutable image
identity, then test its port, resource limits, writable paths, flag delivery, and
restart behavior through the actual backend.

## Rescans reconcile by manifest path

Event settings from `.gzevent` seed the game only on first import, so later operator edits
such as the real schedule persist. Within a binding, the manifest path is the durable
challenge identity. A later scan updates a same-path challenge in place while preserving its
ID, solve and engine history, review state, enabled state, and protected runtime state.

A manifest that disappears is guarded and disabled as a tombstone rather than deleted.
Removal of a live or evidenced challenge can be rejected until an operator explicitly
disables it. Renaming a path therefore creates a new challenge identity and sends the old
path through that guarded removal flow; changing challenge type at one path is rejected.

Rehearse removals, renames, rebuilt services, checkers, variants, managed reporters, and
gateway reconciliation in hidden staging before scanning a running event. Git remains the
source of authored challenge configuration, while preserved Admin and runtime state must
also be reviewed.
