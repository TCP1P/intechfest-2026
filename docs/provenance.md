# Challenge provenance automation

INTECHFEST 2026 does not currently contain a deterministic-variant challenge. This page is
the contract to follow if one is added later: rsctf can own deterministic per-participation
content, hints, and flags, while solve receipts require an organizer-operated verifier. Do
not add a placeholder generator merely to exercise the schema.

## What Repository Bindings imports

The challenge manifest owns these fields:

```yaml
variantMode: PerParticipation
solveReceiptMode: Disabled
```

Because the package contains `generator/Dockerfile`, a trusted Repository
Bindings scan automatically archives that source, builds it, and exercises its
contract twice with identical input. Only deterministic valid output completes
the build. rsctf stores the daemon-local immutable image ID and build log in the
database; neither is authored or written back into `challenge.yaml`.

Changing any file under `generator/` queues a new build. An unchanged rescan
reuses the existing immutable identity. A missing Dockerfile, failed build,
invalid output, nondeterminism, or an event that has already started leaves the
new generator unavailable instead of falling back to old or mutable code.

The generator source must read `RSCTF_VARIANT_INPUT` and emit one JSON manifest. rsctf runs
it twice with the same input in a network-disabled, read-only, resource-limited container.
Only byte-identical output is frozen.
The player receives generated `content` and `hints`; the generated `flag` stays
server-side for grading.

If the generator includes the optional `artifactSha256`, it hashes the UTF-8
compact JSON form of `manifest` with object keys sorted lexicographically and
array order preserved. rsctf independently normalizes and checks that value.

Validate the package and inspect its discovered build context locally:

```sh
make validate
make matrix
```

CI builds the emitted generator context. The hidden Repository Bindings scan is the
authoritative runtime check: rsctf runs the deterministic contract twice and rejects
non-identical or invalid output.

## Deployment topology and registry fallback

Automatic source builds use the trusted Docker daemon. They work in the
all-in-one deployment and in split-role Docker deployments only when every
builder and generator request shares that daemon and
`RSCTF_SHARED_DOCKER_DAEMON=true` acknowledges the topology.

Kubernetes and independent node-local Docker deployments must publish the
reviewed generator to a registry instead. In that case, explicitly add both
fields to the manifest:

```yaml
variantGeneratorImage: "REGISTRY/ORGANIZATION/GENERATOR@sha256:DIGEST"
variantGeneratorDigest: "sha256:DIGEST"
```

Build and publish it with, for example:

```sh
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --provenance=mode=max --sbom=true --push \
  -t REGISTRY/ORGANIZATION/GENERATOR:REVISION \
  events/<event>/challenges/Jeopardy/<category>/<slug>/generator
```

The two explicit fields must be changed together; mutable tags are rejected.
The trusted generator host must contain the exact referenced image before
variant generation. Repository rescans may change either source-built or
registry-pinned provenance only before the event starts.

The trusted Docker daemon used by rsctf's control role must already contain the
exact image because the generator checks the local immutable identity and does
not pull during a generation request:

```sh
docker pull 'REGISTRY/ORGANIZATION/GENERATOR@sha256:DIGEST'
```

## Generate and freeze variants

Configure the rsctf deployment with a stable secret before importing an enabled
variant policy:

```text
RSCTF_EVENT_VPN_CREDENTIAL_KEY=<at-least-32-non-whitespace-characters>
```

The same key derives deterministic seeds across restarts. Rotating it after
variants have been frozen does not rewrite their immutable records, but it
changes any future generation and therefore requires an explicit event plan.

Accept the event's teams before generating variants. From a trusted administrator
client, using an administrator JWT kept in the automation secret store, call:

1. `POST /api/edit/games/42/variants/generate`
2. `GET /api/edit/games/42/variants`

Generation is idempotent: it creates only missing frozen variants. Run it again
for teams accepted later, still before the start time. Verify that the returned
inventory equals the accepted/suspended participation count times the enabled
`PerParticipation` challenge count. After the event starts, generation and policy
changes are rejected. This repository does not ship an admin-token client; use your
organization's reviewed automation and fail closed on non-2xx or malformed responses.

## Optional trusted solve receipts

Receipts are not an exploit uploader or an organizer solver. Enable them only
after deploying a verifier that independently checks a meaningful player action.
Before the event starts, change the challenge manifest to:

```yaml
solveReceiptMode: Required
receiptVerifierIdentity: "example-verifier-v1"
```

Configure the control deployment and the verifier's secret store with the same
independent machine credential:

```text
RSCTF_SOLVE_RECEIPT_ISSUER_TOKEN=<at-least-32-non-whitespace-characters>
```

After the verifier has authenticated the player and confirmed the solve, its trusted
backend sends this JSON body to
`POST /api/internal/event-security/solve-receipts`:

```json
{
  "gameId": 42,
  "challengeId": 17,
  "participationId": 93,
  "userId": null,
  "variantId": "018f3c6a-d79b-7cc0-8f68-8fdbad0f57bb",
  "answer": "rsctf{sum_10485}",
  "issuerIdentity": "example-verifier-v1"
}
```

For a challenge without generated variants, send `"variantId": null`. For a
generated challenge, use the canonical variant ID exposed in that participant's
challenge details; rsctf rejects any mismatch. Authenticate with
`Authorization: Bearer <RSCTF_SOLVE_RECEIPT_ISSUER_TOKEN>` over the protected control
network. Return the endpoint's short-lived proof to the player, who submits it beside
the exact answer. rsctf binds the proof to the game, challenge, participation,
optional user, canonical variant, answer hash, issuer, and expiry, then consumes it
in the grading transaction.

Do not expose this endpoint or its credential directly to players, and do not treat
receipt issuance as verification. The verifier must derive participation identity
from trusted authentication rather than trusting player-supplied IDs, and arbitrary
player code must run only in a separate hardened judge. This repository intentionally
does not ship a receipt client or verifier implementation.
