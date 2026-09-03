# Managed KotH reporting

Leaderboard King of the Hill challenges report bounded native evidence from the
platform-managed target. The challenge never calculates or submits rsctf
points. rsctf authenticates the target, binds evidence to the active lifecycle
and round, resolves capability hashes to eligible teams, normalizes objectives,
validates the Crown, and calculates the scoreboard result.

Four packages use this model:

- [Serpent Circuit](../events/warmup/challenges/Koth/PPC/serpent-circuit/) owns its original
  multiplayer Snake & Ladders state and reports bounded `board-progress` evidence from each
  completed 60-second tick of an active four-team match directly from the same process. Its
  server-controlled FIFO queue admits one seat per eligible capability, assigns opponents,
  runs one five-minute match at a time, and starts the next full group automatically. One
  team contributes at most one furthest-position row per tick; waiting and finished matches
  do not carry progress into later ticks. The player-facing HTTP transport needs no gateway.

- [Rythme](../events/main-event/challenges/Koth/Reverse/rythme/) runs its reporter
  inside the Go coordinator. Its transport-only
  [gateway](../events/main-event/challenges/Koth/Reverse/rythme/gateway/) keeps
  the released client's stable HTTPS origin pointed at the current target.
- [Minions in 32K](../events/main-event/challenges/Koth/PPC/minions-in-32k/)
  runs match validation and reporting inside the arena supervisor. Its
  [gateway](../events/main-event/challenges/Koth/PPC/minions-in-32k/gateway/)
  is a stateless TCP/UDP protocol adapter and has no scoring credential.

- [Tower of Babel](../events/main-event/challenges/Koth/Pwn/tower-of-babel/) owns a direct
  TCP coordinator and a player-safe terminal-lobby handout, then reports one `babel-mastery` ratio per finalized 60-second wave from
  its in-memory verified relay state. Every authenticated team present in a wave gets one
  bounded native row, including a zero-progress row when it has not completed a relay. The
  first authenticated full solver owns a bounded three-attempt-per-wave patch window that
  survives reconnects; the coordinator replay-canary-tests candidates and exposes
  rejection/fallback state before changing the active ward. The ward is gameplay state, not
  a second scoring source, and no transport gateway is required.

`observation` remains part of the rsctf endpoint and environment-variable names, but it
does not describe a repository component. Do not add an `observer/` directory, observer
container, or sidecar. The in-process reporter reads an immutable view of the state already
owned by the authoritative game service. The separately invoked challenge checker stays
bounded and read-only; it never reports evidence, carries a reporter credential, advances a
wave, or changes the claim source.

## Platform contract

The rsctf deployment sets `RSCTF_KOTH_REPORTER_BASE_URL` to a private control
origin that a managed KotH target can reach. Enabling Leaderboard scoring for a
hill causes rsctf to create a reporter credential before the crash-recoverable
replacement container is launched. The same reset retry reuses that credential;
a new reset rotates it.

This private managed callback path does not itself require general Internet access. This
repository nevertheless sets `ad.allowEgress: true` on managed KotH packages so gameplay
can reach the Internet by default. Use `false` only when isolation is part of the reviewed
challenge contract. The deployment must use a backend that can enforce the requested policy;
rsctf's current Docker backend rejects competitive `true` workloads.

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

### Context versions and exact shapes

Every new managed reporter requests the context with this exact header:

```http
GET <RSCTF_KOTH_CONTEXT_URL>
Accept: application/json
Cache-Control: no-store
X-RSCTF-API-Version: v2
```

The header value is exactly `v2`. A current platform returns an exact v2 object; an absent
header returns the exact v1 object with neither v2 field, and any other explicit version is
rejected. An older platform may ignore the v2 header and return the exact legacy v1 object,
which the reporter may handle through the conservative fallback below. Do not treat the two
versions as additive, accept a hybrid object, or silently ignore unknown fields.

Both versions contain exactly these fields:

```text
apiVersion, context, cycleNumber, cycleEndsAt, resetAttempt, roundNumber,
waveWindowStartsAt, waveWindowEndsAt, eligibleTokenHashes, objectiveIds,
objectiveSchemaHash, generatedAt
```

V2 contains exactly those fields plus `cycleStartsAt` and `scoringEndsAt`, and its
`apiVersion` is `v2`. V1 has neither added field and its `apiVersion` is `v1`. Important
values have these meanings:

- `context` and every member of `eligibleTokenHashes` are lowercase 64-character hashes.
  Hashes are unique, and the roster has at most 2,000 members.
- `cycleNumber` and `roundNumber` are positive integers; `resetAttempt` is a nonnegative
  integer. A change to the cycle/reset pair is a lifecycle fence, not a continuation of the
  prior reporter state.
- `cycleStartsAt`, `cycleEndsAt`, `scoringEndsAt`, both wave-window bounds, and
  `generatedAt` are Unix milliseconds. Reject booleans, floating-point values, future or
  regressing `generatedAt` values, reversed bounds, and values outside the reporter's
  integer limits. `generatedAt` is a durable generation fence, not a response timestamp:
  an unchanged context can legitimately retain it for a full scoring round.
- `cycleStartsAt` is the stable, inclusive start of the first official Leaderboard scoring
  round for this lifecycle. It does not move on a target restart or later checker round.
- `cycleEndsAt` is the event deadline retained under its compatibility name.
- `scoringEndsAt` is the stable, inclusive latest end time rsctf can score after reserving
  its settlement lag. A challenge cadence may end earlier, never later.
- `waveWindowStartsAt` is inclusive and `waveWindowEndsAt` is exclusive. They select which
  already-finalized wave end times belong in this submission; they do not redefine the
  game's permanent cadence grid.
- Before the first accepted schema, `objectiveIds` is `[]` and
  `objectiveSchemaHash` is `null`. Afterwards, the IDs must exactly match the reporter's
  stable order and the hash must match that ordered schema. Compute the schema hash as
  SHA-256 over the big-endian unsigned 64-bit ID count followed, for each UTF-8 ID in order,
  by its big-endian unsigned 64-bit byte length and exact bytes; encode the result as lowercase
  hexadecimal.

For an exact legacy v1 response, derive a conservative scoring cutoff as
`max(0, cycleEndsAt - 20_000)`. Establish one aligned floor for the observed
`(cycleNumber, resetAttempt)` lifecycle: use the first-round window start when available;
when attaching later, start no earlier than the first context's `generatedAt`. Keep that
floor for subsequent rounds. This fallback deliberately forgoes history it cannot prove;
it must not invent old waves or report past the derived cutoff. Prefer v2 because only v2
can recover the authoritative lifecycle-wide grid after a late start or restart.

### Scoreable wave grid and boundary rules

Define the native wave cadence once per lifecycle and never rebase it to `generatedAt`, a
checker round, a reporter restart, or the process clock. For an epoch-aligned cadence `C`,
the first complete wave starts at:

```text
firstStart = ceil(cycleStartsAt / C) * C
```

A native wave beginning at `s` occupies `[s, s + C)` and reports
`endedAtUnixMs = s + C`. It is scoreable only when it is complete, its start is on that
permanent grid, and its end is no later than the inclusive `scoringEndsAt`. Do not open a
new scoreable session that cannot finish by that cutoff.

For each context, emit only finalized waves whose `endedAtUnixMs` is in
`[waveWindowStartsAt, waveWindowEndsAt)`. Equality with an ordinary
`waveWindowEndsAt` belongs to the next context, preventing double settlement. The terminal
context is the one exception in encoding, not in interval semantics: rsctf may publish
`waveWindowEndsAt = scoringEndsAt + 1` so a wave ending exactly at the inclusive
`scoringEndsAt` remains inside the half-open window. Never emit an end time later than
`scoringEndsAt`, and never apply the extra millisecond yourself.

RSCTF runs the challenge checker before it activates a prepared cycle. During
that readiness phase the managed environment is present, but the context route
is intentionally inactive. A Leaderboard checker must therefore distinguish
preactivation from misconfiguration: accept a healthy managed target whose
read-only capability probe returns the service's exact roster-not-ready `503`,
and accept the exact invalid-capability `403` after context becomes active.
Never require `scoringReady: true` as a precondition for activation, and never
accept an arbitrary `503` body.

The reporter secret is valid only for the exact game, challenge, cycle, reset
attempt, target row, container identity, event window, and managed Leaderboard mode that
created it. rsctf rejects it after a target reset or ownership change. A target
compromise can forge that target's native evidence for its remaining lifecycle;
it cannot choose points, reuse the credential for another hill, or survive a
reset. This is the intended trust boundary: the authoritative game server is
already the source of native gameplay truth.

## Reporter loop

An in-process reporter should:

1. Poll the exact context URL at a bounded interval such as one second, sending
   `X-RSCTF-API-Version: v2` and accepting only an exact v2 or exact legacy v1 shape.
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

### Native evidence document

The signed request body has one exact top-level shape:

```json
{
  "context": "<64-lowercase-hex>",
  "objectiveIds": ["board-progress"],
  "waves": [
    {
      "waveId": "arena-1785123400000",
      "endedAtUnixMs": 1785123460000,
      "teams": [
        {
          "tokenHash": "<sha256-current-capability>",
          "activity": {"earned": 1, "possible": 1},
          "objectives": [{"earned": 100, "possible": 100}],
          "isCrown": true
        }
      ]
    }
  ]
}
```

Unknown fields are invalid at every level. `objectiveIds` contains 1–16 unique stable IDs;
each ID is 1–64 bytes, begins with `a-z`, and otherwise contains only lowercase letters,
digits, `-`, `_`, or `.`. Every team's `objectives` array has exactly the same length and
order. Each `activity` or objective value is a native integer ratio satisfying
`0 <= earned <= possible <= 1_000_000_000_000` with `possible > 0`. Submit completion,
distance, efficiency, survival, or another challenge-native measurement; never calculate or
submit rsctf points.

`activity` is the completion gate, not another score: only a row with
`activity.earned == activity.possible` can score or hold the Crown. Rsctf gives every
objective equal weight even when their native denominators differ. For each objective it
computes this integer normalization independently:

```text
normalized = (earned * 1_000_000 + floor(possible / 2)) // possible
```

The row's objective rate is the mean of those normalized values. Among completed rows with a
positive objective rate, the sole greatest rate must be the one `isCrown: true` row. A tie
for greatest rate, no completed positive row, or an empty wave must have no Crown. Mirror this
normalization when choosing the Crown; comparing raw totals or challenge-specific point
scales can select a different team and will be rejected.

Use a unique stable 1–128 byte `waveId` beginning with an ASCII letter or digit and otherwise
containing only letters, digits, `.`, `_`, `:`, or `-`. Keep token hashes unique within a
wave. `endedAtUnixMs` is a positive Unix-millisecond value inside the current published
window and no later than rsctf's current time or `scoringEndsAt`. Emit an empty
`teams` array for a finalized wave with no results and an empty `waves` array when the
current settlement window contains no finalized wave.

The complete body is at most 512 KiB, contains at most 64 waves and at most 2,000
team-wave rows, and uses deterministic ordering and serialization. Later snapshots in one
context preserve the accepted finalized prefix exactly and append new waves; they do not
revise old evidence. Filter locally to `eligibleTokenHashes`, then require the success
acknowledgement's submitted counts to match the document and its recognized-team count to
match the unique submitted identities. `submittedTeams` and `recognizedTeams` count distinct
token hashes across the whole snapshot, not team-wave rows.

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

Do not add a private scoring HTTP feed solely for a second scoring process.
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

Name it `gateway/` and document why it cannot be removed. Test both protocol
directions. If the target protocol is already compatible with
rsctf exposure, do not create a gateway.

## Local and integration testing

Unit tests should cover:

- raw success-body decoding and bounded/trailing-body rejection;
- the exact v2 request header and response shape, exact legacy v1 fallback, and rejection of
  hybrid or unknown-field context objects;
- a late target start that retains the v2 cadence floor, ordinary half-open boundary
  equality, and terminal `scoringEndsAt` equality through the published `+1 ms` bound;
- exact HMAC construction and monotonic timestamps;
- stale/inactive `409` handling;
- deterministic roster/evidence ordering;
- duplicate or malformed capability hashes;
- wrong objective schema, out-of-window waves, and oversized snapshots;
- forged rank, impossible counters, duplicate teams, and forged Crown, including
  multi-objective rows with different native denominators;
- exact ties and zero evidence;
- target child-environment secret filtering;
- gateway pass-through without reporter state, when applicable.

For a platform integration test, use a hidden, disabled staging event bound to the exact
candidate revision and a backend that can enforce the manifest's egress policy. Create two
accepted teams, prepare and enable the hill before scoring begins, then exercise the real
player capability exchange for both teams. Keep the event hidden from normal users throughout
the rehearsal.

Run at least one complete scoreable wave in which both teams produce different positive native
ratios. Require an exact v2 context, verify its permanent grid and window boundaries, confirm
the unique leader is the only Crown, require the exact acknowledgement counts, and wait for
the rsctf checker to settle the scoreboard. Also exercise a tied wave or zero-result wave and
verify it has no Crown. A useful boundary rehearsal includes one ordinary wave-end equality
that moves to the next half-open window and, when the cadence permits it, a terminal wave
ending exactly at `scoringEndsAt`.

Capture the current reporter credential and context, reset the target, and wait for the new
managed target. With a fresh timestamp and otherwise valid signed request, prove the old
credential receives `401`; prove the new credential and context can report. Exercise an
inactive `409` gap and target restart/adoption without rebasing the v2 wave grid or replaying
prior-lifecycle evidence.

Run the package checker against the same target before and after gameplay. Verify it receives
no reporter secret, does not submit observations, and leaves the target's native sessions,
wave cursor, evidence feed, and claim source unchanged. The checker verifies health and
player-visible gameplay quality only. Preserve the two-team lifecycle evidence, exact commit,
container digest, context/acknowledgement fields, reset `401`, checker state comparison, and
scoreboard result for release review without retaining any secret value.
