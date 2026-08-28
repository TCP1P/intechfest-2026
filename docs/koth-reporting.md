# Managed KotH reporting

Leaderboard King of the Hill challenges report bounded native evidence from the
platform-managed target. The challenge never calculates or submits rsctf
points. rsctf authenticates the target, binds evidence to the active lifecycle
and round, resolves capability hashes to eligible teams, normalizes objectives,
validates the Crown, and calculates the scoreboard result.

The two main-event examples use this model:

- [Rythme](../events/main-event/challenges/Koth/Reverse/rythme/) runs its reporter
  inside the Go coordinator. Its transport-only
  [gateway](../events/main-event/challenges/Koth/Reverse/rythme/gateway/) keeps
  the released client's stable HTTPS origin pointed at the current target.
- [Minions in 32K](../events/main-event/challenges/Koth/PPC/minions-in-32k/)
  runs match validation and reporting inside the arena supervisor. Its
  [gateway](../events/main-event/challenges/Koth/PPC/minions-in-32k/gateway/)
  is a stateless TCP/UDP protocol adapter and has no scoring credential.

## Platform contract

The rsctf deployment sets `RSCTF_KOTH_REPORTER_BASE_URL` to a private control
origin that a managed KotH target can reach. Enabling Leaderboard scoring for a
hill causes rsctf to create a reporter credential before the crash-recoverable
replacement container is launched. The same reset retry reuses that credential;
a new reset rotates it.

rsctf injects these values into only that target:

| Environment variable | Meaning |
| --- | --- |
| `RSCTF_KOTH_GAME_ID` | numeric game scope |
| `RSCTF_KOTH_CHALLENGE_ID` | numeric challenge scope |
| `RSCTF_KOTH_PLATFORM_URL` | private rsctf origin for capability authentication |
| `RSCTF_KOTH_CONTEXT_URL` | exact active-context endpoint |
| `RSCTF_KOTH_OBSERVATION_URL` | exact evidence-submission endpoint |
| `RSCTF_KOTH_REPORTER_SECRET` | lifecycle-bound `koth_target_…` HMAC credential |

Do not define these names in `challenge.yaml`, a Dockerfile, Compose, a handout,
or GitHub Actions. Challenge code reads them when present and should stay
healthy without them so rsctf can provision the pre-cycle target. Gameplay that
requires event identity must fail closed until the managed values are present.

The reporter secret is valid only for the exact game, challenge, cycle, reset
attempt, target row, container identity, event window, and API scoring mode that
created it. rsctf rejects it after a target reset or ownership change. A target
compromise can forge that target's native evidence for its remaining lifecycle;
it cannot choose points, reuse the credential for another hill, or survive a
reset. This is the intended trust boundary: the authoritative game server is
already the source of native gameplay truth.

## Reporter loop

An in-process reporter should:

1. Poll the exact context URL at a bounded interval such as one second.
2. Treat HTTP `409` as a normal inactive/reset gap.
3. Validate all context fields, objective IDs, capability hashes, time windows,
   roster size, and response size before using them.
4. Read an immutable snapshot from the challenge's authoritative scoring state.
5. Filter rows to `eligibleTokenHashes`; never submit a raw capability.
6. Emit deterministic JSON with stable ordering, bounded waves, bounded team
   rows, nonnegative evidence ratios, unique wave IDs, and server-confirmed end
   times inside the supplied window.
7. Assert a Crown only for one unique eligible positive leader. Exact ties and
   zero-result waves have no Crown.
8. Sign the exact body with HMAC-SHA256 over:

   ```text
   <Unix milliseconds>.<game ID>.<challenge ID>.<exact JSON body bytes>
   ```

9. Send `X-RSCTF-Timestamp` and
   `X-RSCTF-Signature: sha256=<lowercase hex HMAC>` to the exact observation
   URL.
10. Accept success only when the raw JSON acknowledgement matches the context's
    cycle, reset, and round plus the submitted wave/team counts.
11. Deduplicate an unchanged `(context, body digest)` while retrying failures.

Successful rsctf responses are raw JSON objects, not `{data: ...}` envelopes.
Every HTTP body must be bounded before decoding. Use a client timeout and never
let reporter retries block the gameplay scheduler or health endpoint.

## Evidence ownership

Keep evidence production next to the code that owns the truth:

- a coordinator can expose a locked read-only scoring snapshot to its own
  reporter goroutine;
- a supervisor can accept authenticated child-process reports over loopback,
  validate ordering and invariants, retain replay evidence, then report;
- a player-facing process must not receive the platform reporter secret when a
  narrower supervisor can own it.

If the target starts a child process, build an explicit child environment and
remove every `RSCTF_KOTH_*` value. Pass only a domain-separated local credential
needed for the child-to-supervisor report route. Tests must prove the platform
secret and callback URLs are absent from the child environment.

Do not add a private scoring HTTP feed solely for a second observer process.
Prefer an in-memory snapshot or loopback-only route. Do not persist the managed
reporter secret to disk.

## When a gateway is justified

Add `gateway/` only when the platform's exposed transport cannot directly serve
the player protocol, as with Rythme's stable HTTPS client origin or Minions'
public HTTPS metadata plus UDP gameplay.
A gateway may adapt transport, terminate TLS, rate-limit sessions, and track the
current target address. It must not:

- receive the managed reporter secret;
- decide eligibility, normalize results, assert scores, or retain scoring state;
- accept a raw capability beyond the narrow pass-through needed by the target;
- share a secret or state volume with the target;
- call rsctf scoring endpoints.

Name it `gateway/`, not `observer/`. Document why it cannot be removed and test
both protocol directions. If the target protocol is already compatible with
rsctf exposure, do not create a gateway.

## Local and integration testing

Unit tests should cover:

- raw success-body decoding and bounded/trailing-body rejection;
- exact HMAC construction and monotonic timestamps;
- stale/inactive `409` handling;
- deterministic roster/evidence ordering;
- duplicate or malformed capability hashes;
- wrong objective schema, out-of-window waves, and oversized snapshots;
- forged rank, impossible counters, duplicate teams, and forged Crown;
- exact ties and zero evidence;
- target child-environment secret filtering;
- gateway pass-through without reporter state, when applicable.

For a platform integration test, enable Leaderboard scoring, wait for the
managed replacement target, authenticate at least two event capabilities, run a
complete wave, and verify the acknowledgement and scoreboard. Then reset the
hill and prove the old target credential receives `401` while the new target
can report. Exercise target restart/adoption and an inactive round gap.

Run the package's checker against the same target, but keep it read-only: a
checker verifies health and gameplay quality; it does not submit evidence.

## Legacy external compatibility

rsctf retains its external `koth_api_…` credential and the same context and
observation endpoints for deployments that cannot run reporting inside a
managed target. That credential is optional compatibility, not the default
challenge-package architecture. Store it outside player workloads and follow
the same evidence validation rules. New packages in this repository must use
managed target reporting unless the author records a concrete platform
constraint that makes it impossible.
