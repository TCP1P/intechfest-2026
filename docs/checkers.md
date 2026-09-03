# Checker development guide

Repository Bindings automatically prepares a Python checker found at
`checker/run.py` or `checker/src/run.py` beside an A&D/KotH manifest. It copies
the whole source directory, so keep the reusable `lib.py` and any optional
`requirements.txt` beside `run.py`. Copy the complete checker directory when
starting from an example.

These checked-in KotH examples use the canonical `lib.py` and are intended to be copied only
for a compatible protocol:

- [Draft Control checker](../events/warmup/challenges/Koth/Web/draft-control/checker/)
  validates health, exact public protocol metadata, and a mutation-free path preview without
  reading or writing `/koth/king`.
- [Serpent Circuit checker](../events/warmup/challenges/Koth/PPC/serpent-circuit/checker/)
  validates health, rules, and the public board contract without authenticating a player or
  producing scoring evidence.
- [Rythme checker](../events/main-event/challenges/Koth/Reverse/rythme/checker/) validates
  health, exact release configuration, the live scheduler clock, and rejected invalid-token
  onboarding without creating a scoring session.
- [Minions in 32K checker](../events/main-event/challenges/Koth/PPC/minions-in-32k/checker/)
  validates health and an active pinned-certificate QUIC quality probe without reading the
  gateway or scoring source.
- [Tower of Babel checker](../events/main-event/challenges/Koth/Pwn/tower-of-babel/checker/)
  validates the read-only TCP greeting, wave metadata, and unauthenticated rejection paths
  without creating a relay session or touching managed evidence.

The [Parcel Panic checker](../events/warmup/challenges/AD/Web/parcel-panic/checker/) is the
self-hosted A&D example. It verifies the current injected flag through the legitimate vault
contract rather than requiring the intended traversal, so teams can patch the vulnerability
without breaking service-level checks.

Managed Leaderboard reporting is documented in [Managed KotH reporting](koth-reporting.md).
It runs beside the authoritative target state and remains separate from the read-only checker
contract.

## Runtime contract

rsctf runs the prepared checker as Python with no command-line arguments. The
host environment is cleared; these are the supported checker-contract values
(the sandbox also supplies its private `HOME` and `TMPDIR`):

| Variable | A&D | KotH | Meaning |
| --- | --- | --- | --- |
| `RSCTF_ACTION` | `check` | `check` | Reserved action selector |
| `RSCTF_TARGET_IP` | yes | yes | Resolved numeric target address |
| `RSCTF_TARGET_PORT` | yes | yes | Target port from `container.exposePort` |
| `RSCTF_ROUND` | yes | yes | Current round number |
| `RSCTF_TEAM_ID` | participation ID | `0` | Historical name; A&D receives the participation ID |
| `RSCTF_CHALLENGE_ID` | yes | yes | Imported challenge ID |
| `RSCTF_FLAG` | yes | absent | Expected rotating flag for this A&D service |

The process exit code is the entire result:

| Code | Verdict | Use it when |
| ---: | --- | --- |
| `0` | OK | The service is reachable and functionally correct |
| `1` | Mumble | It answered, but its protocol or content is wrong |
| `2` | Offline | Connection, reset, or request timeout prevented a response |
| `3` | InternalError | Checker configuration or checker code is broken |

Any other code is also `InternalError`. The shuffled-suite runners and
legacy decorators in the bundled `lib.py` load and validate this environment
and map checker outcomes to these codes:

- returning normally is OK;
- raising `Mumble` means the target answered incorrectly;
- raising `Offline` means the target could not complete the request;
- configuration errors and unexpected exceptions are InternalError.

This mapping matters because an ordinary uncaught Python exception exits with
code `1` and would otherwise incorrectly blame the team as Mumble.

## Template structure

Keep only platform concerns in the dependency-free `lib.py`: validated target
context, verdict exceptions, decorators, and exit-code mapping. It deliberately
does not define an application protocol. HTTP, raw TCP, binary framing, and
challenge-specific TCP handshakes belong in `run.py` because only the challenge
author knows what a healthy service exchange looks like.

For example, a future raw-TCP A&D service might use one newline-framed command per
connection:
`PING\n` must return `PONG\n`, and `GET_FLAG\n` must return the current flag plus
a newline. Its `requirements.txt` pins `pwntools==4.15.0`, while `run.py` owns
the raw TCP exchange:

```python
import os
from time import monotonic

os.environ["PWNLIB_NOTERM"] = "1"

from pwn import context as pwn_context, remote
from pwnlib.exception import PwnlibException

from lib import AdContext, Mumble, Offline, checker, run_ad_checker


REQUEST_TIMEOUT_SECONDS = 3
MAX_RESPONSE_BYTES = 4096
pwn_context.log_level = "critical"


def tcp_request(context: AdContext, command: str) -> str:
    if "\r" in command or "\n" in command:
        raise ValueError("checker commands must fit on one line")

    tube = None
    try:
        tube = remote(
            context.target_ip,
            context.target_port,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        tube.sendline(command.encode("ascii"))
        response = bytearray()
        deadline = monotonic() + REQUEST_TIMEOUT_SECONDS
        while b"\n" not in response and len(response) <= MAX_RESPONSE_BYTES:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            try:
                chunk = tube.recv(
                    numb=min(1024, MAX_RESPONSE_BYTES + 1 - len(response)),
                    timeout=remaining,
                )
            except EOFError as error:
                if response:
                    break
                raise Offline("the service closed without a response") from error
            if chunk == b"":
                break
            response.extend(chunk)
    except Offline:
        raise
    except (EOFError, TimeoutError, PwnlibException, OSError) as error:
        raise Offline("the service did not complete the request") from error
    finally:
        if tube is not None:
            tube.close()

    if response == b"":
        raise Offline("the service timed out or closed without a response")
    if len(response) > MAX_RESPONSE_BYTES:
        raise Mumble("the service response was too large")

    line, newline, trailing = response.partition(b"\n")
    if newline == b"" or trailing != b"":
        raise Mumble("the service did not return a complete response line")
    try:
        return line.removesuffix(b"\r").decode("utf-8")
    except UnicodeDecodeError as error:
        raise Mumble("the service response was not UTF-8") from error


@checker
def check_ping(context: AdContext) -> None:
    if tcp_request(context, "PING") != "PONG":
        raise Mumble("PING did not return PONG")


@checker
def check_flag(context: AdContext) -> None:
    if tcp_request(context, "GET_FLAG") != context.flag:
        raise Mumble("GET_FLAG did not return this round's flag")


if __name__ == "__main__":
    raise SystemExit(run_ad_checker())
```

For another TCP service, replace `tcp_request` with a bounded implementation of
that service's protocol. Raise `Offline` when the target cannot complete the
exchange and `Mumble` when it responds but violates the expected protocol or
content. Do not add a generic protocol to `lib.py`.

### Shuffled checker suites

`@checker` registers a focused function without wrapping it. At the bottom of
`run.py`, call `run_ad_checker()` or `run_koth_checker()` once. The runner first
validates the environment and creates the appropriate context, then
cryptographically shuffles a copy of the registry. **Every registered function
is attempted exactly once**; none is randomly skipped, and source-registration
order is not execution order. Each invocation shuffles independently, but a
fresh shuffle can still produce the same order as an earlier run.

One failed function does not prevent later functions from running. After the
whole shuffled suite is attempted, the runner deterministically combines its
results with this priority: InternalError, then Offline, then Mumble, then OK.
A context-validation failure happens before the suite and is InternalError. The
platform's outer hard timeout can still abort a checker that exceeds its total
deadline. Register at least one function and do not mix A&D and KotH functions
in one entry point.

Focused functions are encouraged. The A&D suite **collectively** must verify
service health, all required functionality, and the exact current
`context.flag`; for example, one function may check health and another may check
the flag. Each function must be read-only and independent: shuffled execution
means it cannot rely on another check having run first or leave state required
by a later check. All functions must return `None` normally.

An HTTP A&D checker follows the same split. It should disable redirects and proxy-environment
use, request identity encoding, and bound the streamed response. HTTP stays
challenge-specific; none of this behavior moves into `lib.py`.

KotH uses the matching runner but receives no flag. Its complete suite must
cover the intended health/functionality contract and must not touch the claim
source: `/koth/king` in Marker mode or authoritative gameplay/evidence state in
managed Leaderboard mode.
A minimal KotH checker can register focused health and public-function checks after its
bounded local protocol helper:

```python
from lib import KothContext, Mumble, checker, run_koth_checker


@checker
def check_health(context: KothContext) -> None:
    if http_get(context, "/health") != "ok":
        raise Mumble("the health endpoint did not return ok")


@checker
def check_banner(context: KothContext) -> None:
    expected_banner = "rsctf KotH demo: submit your token at /claim?token=..."
    if http_get(context, "/") != expected_banner:
        raise Mumble("the public hill banner was incorrect")


if __name__ == "__main__":
    raise SystemExit(run_koth_checker())
```

Varying request order, sequences, or client fingerprints can make a brittle
checker-detection rule less reliable. This is defense-in-depth against the
so-called Superman defense, not a complete prevention. Shuffled checks do
not hide or rotate the checker network path: a service can still observe a
stable source IP and special-case it. The check source is not secret;
cryptographic shuffling only makes execution order unpredictable, and every
registered request is still attempted. Do not claim shuffling alone defeats
source-IP allowlisting; organizers still need appropriate event rules, network
design, monitoring, and checks that exercise player-visible behavior.

### Legacy single-checker decorators

The original `@ad_checker` and `@koth_checker` APIs remain supported for a
single fixed checker. The decorator wraps one context-taking function as the
zero-argument entry point and performs the same context validation and verdict
mapping:

```python
from lib import AdContext, Mumble, ad_checker


@ad_checker
def check(context: AdContext) -> None:
    # This one function must perform the complete health and current-flag check.
    ...


if __name__ == "__main__":
    raise SystemExit(check())
```

For KotH, use `KothContext` with `@koth_checker`. Choose either the registered
suite runner or the legacy single-checker style in one `run.py`; do not combine
the entry-point patterns.

Copy the complete `checker/` directory from the closest example, then edit the
protocol exchange and challenge-specific assertions in `run.py`. Keep `lib.py`,
`run.py`, and any `requirements.txt` together; do not copy only the entry point.

## Optional PyPI dependencies

A checker may put `requirements.txt` beside `run.py`. Every requirement must be
a simple, exactly pinned PyPI package such as:

```text
pwntools==4.15.0
```

Repository Bindings rejects unpinned or ranged versions, URLs, local paths,
editable installs, and pip options such as alternate indexes. During checker
preparation, rsctf installs the requirements and their dependencies into the
checker's immutable virtual environment using wheels only. A package without a
compatible wheel is rejected; rsctf never falls back to a source distribution
or package build. Blank lines and `#` comments are allowed; the file is limited
to 16 KiB and 32 unique package names.

Dependency preparation requires the rsctf process performing the trusted
repository scan or admin approval to reach PyPI and its package file hosts. A
download or resolution failure fails checker preparation. This is an
administrator trust boundary: review the repository commit and every package
pin before scanning or approving it. Exact direct pins constrain accidental
top-level drift but do not make a third-party package trustworthy.

Use dependencies only when they make the checker clearer. A raw TCP checker might use an
exact `pwntools` pin and an HTTP checker might use an exact `httpx` pin. The warmup checkers
use the standard library and intentionally omit `requirements.txt`. `lib.py` remains
protocol-neutral and has no third-party imports.

## Sandbox rules

- Use only the standard library and packages installed from the reviewed,
  pinned `requirements.txt`. Runtime installation is unavailable.
- Set a short request timeout. The checker has one outer deadline, and an outer
  timeout becomes Offline.
- TCP network access is confined to exactly the supplied target IP and port. Do
  not follow redirects or call a database, DNS API, peer service, or second
  port.
- The checker source and venv are read-only. `HOME` and `TMPDIR` point to a small
  temporary writable directory.
- Do not shell out to `curl` or other binaries. The sandbox does not provide a
  dependable `PATH` and blocks general subprocess execution.
- stdout and stderr are discarded. Do not print flags; return the correct exit
  code instead.
- Resolve bundled data relative to `Path(__file__).resolve().parent`, because
  the working directory is not guaranteed to be the checker directory.

## Flag delivery

The platform delivers the flag before running the checker:

- Managed A&D writes it inside the service container at `RSCTF_FLAG_FILE`.
- Self-hosted A&D sends it through the BYOC relay, whose agent writes the shared
  `/shared/flag` file.
- The checker receives the same expected value as `RSCTF_FLAG`.

Service code must read its flag file at request time. A creation-time environment
value cannot rotate. The checker must not replace the flag itself, because that
would bypass the platform's delivery path.

KotH is different: it has no flag environment. In Marker mode, rsctf samples
`/koth/king` before and after the custom checker. In managed Leaderboard mode, it
uses the latest exact-context evidence submitted by the authoritative target.
The checker performs neither sampling nor submission; it should verify a normal
health/functionality exchange without reading or modifying either claim source.

Keep a prepared checker beside every enabled A&D and KotH challenge. The TCP
fallback can diagnose a missing checker, but official epoch scoring does not
start until every enabled engine challenge has a prepared checker.

## Local workflow

Each checker directory contains only the files copied by Repository Bindings: `lib.py`,
`run.py`, focused `test_run.py`, and an optional pinned `requirements.txt`. From the
repository root, validate manifest integration and all local checker verdict regressions:

```sh
make check RSCTF=/path/to/rsctf
```

For a focused functional check, start the exact service image and run its `checker/run.py`
with the platform environment. For a KotH package, change into its package
directory and use:

```sh
RSCTF_ACTION=check \
RSCTF_TARGET_IP=127.0.0.1 \
RSCTF_TARGET_PORT=8080 \
RSCTF_ROUND=1 \
RSCTF_TEAM_ID=0 \
RSCTF_CHALLENGE_ID=1 \
python3 checker/run.py
echo $?
```

Use the manifest's real exposed port (`8080` for every warmup service and Minions, or `1337`
for Rythme). KotH sets `RSCTF_TEAM_ID=0` and omits `RSCTF_FLAG`. An A&D checker receives a
participation ID and the current expected flag. When `requirements.txt` exists, install it
into a disposable venv with wheel-only exact pins before this run.

CI uses the matrix emitted by rsctf to build every bundled context and waits for each
service's Docker `HEALTHCHECK`. It does not exercise all checker verdict classes or
prove the flag-delivery path. Before release, test OK, Mumble, Offline, and
InternalError behavior through the intended backend during the hidden staging and
multi-team rehearsal described in the release checklist.
