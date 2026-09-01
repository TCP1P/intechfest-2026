"""Authoritative HTTP game server for the Serpent Circuit Leaderboard hill."""

from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler
from typing import Any
import urllib.parse

from capability_auth import CapabilityAuthenticator
from capability_auth import CapabilityRejected
from capability_auth import CapabilityUnavailable
from http_transport import BoundedReader as _BoundedReader
from http_transport import BoundedThreadingHTTPServer
from reporter import load_config as load_reporter_config
from reporter import ReporterRuntime
from reporter import RoundContext
from reporter import start_managed_reporter


NAME = "Serpent Circuit"
PROTOCOL = "serpent-circuit/1"
PROTOCOL_VERSION = 1
PORT = 8080
BOARD_SIZE = 100
START_POSITION = 1
FINISH_POSITION = 100
WAVE_SECONDS = 60
WAVE_DURATION_MS = WAVE_SECONDS * 1_000
TURN_COUNT = 12
OBJECTIVE_ID = "route-efficiency"
MAX_REQUEST_BYTES = 4 * 1_024
MAX_REQUEST_STREAM_BYTES = 12 * 1_024
MAX_RESPONSE_EVENTS = 1_000
MAX_EVENTS = 10_000
MAX_WAVE_ADMISSIONS = 2_000
MAX_ACTIVE_TEAM_WAVE_ADMISSIONS = MAX_WAVE_ADMISSIONS * 2
MAX_SESSIONS = MAX_ACTIVE_TEAM_WAVE_ADMISSIONS * 2
MAX_HTTP_WORKERS = 64
MAX_REGULAR_HTTP_WORKERS = 56
MAX_REQUESTS_PER_CLIENT = MAX_REGULAR_HTTP_WORKERS
MAX_PENDING_HTTP_REQUESTS = 128
CONNECTION_TIMEOUT_SECONDS = 4
ABSOLUTE_REQUEST_SECONDS = 6
MAX_CONTEXT_STALENESS_SECONDS = 8
TOKEN_PATTERN = re.compile(r"^koth_[A-Za-z0-9_-]{8,128}$")
CANONICAL_HASH = re.compile(r"^[0-9a-f]{64}$")
STATIC_ROOT = Path(__file__).with_name("static")

# This board is original to Serpent Circuit. Every transition is applied once,
# immediately after a die move. It deliberately has no cycles.
TRANSITIONS = {
    3: 24,
    8: 30,
    17: 6,
    19: 46,
    28: 62,
    35: 15,
    42: 71,
    49: 27,
    58: 84,
    64: 40,
    73: 92,
    79: 51,
    88: 68,
    97: 76,
}


class GameError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class CapabilityConfig:
    platform_url: str
    game_id: int
    challenge_id: int


@dataclass
class Session:
    token_hash: str
    wave_start_ms: int
    wave_end_ms: int
    turns: tuple[tuple[int, int], ...]
    context_opaque: str | None
    cycle_number: int
    reset_attempt: int
    consumed: bool = False
    choices: tuple[int, ...] | None = None
    result: dict[str, object] | None = None


@dataclass(frozen=True)
class ManagedContext:
    opaque: str
    eligible_hashes: frozenset[str]
    cycle_ends_at: int
    final_scoring_cutoff: int
    cycle_number: int
    reset_attempt: int
    first_scorable_wave_start: int
    generated_at: int
    refreshed_at: float


@dataclass(frozen=True)
class CapabilityCandidate:
    token_hash: str
    context: ManagedContext | None
    config: CapabilityConfig | None


@dataclass(frozen=True)
class SessionAdmission:
    server_time_ms: int
    wave_start_ms: int
    wave_end_ms: int
    turns: tuple[tuple[int, int], ...]
    context_opaque: str | None
    cycle_number: int
    reset_attempt: int
    run_key: tuple[int, str, int, int]
    admission_key: tuple[int, int, int]


state_lock = threading.Lock()
sessions: OrderedDict[str, Session] = OrderedDict()
started_runs: OrderedDict[tuple[int, str, int, int], str] = OrderedDict()
wave_admissions: OrderedDict[tuple[int, int, int], int] = OrderedDict()
events: deque[dict[str, object]] = deque(maxlen=MAX_EVENTS)
next_cursor = 1
managed_context_lock = threading.Lock()
managed_context: ManagedContext | None = None
reporter_runtime = ReporterRuntime()
capability_authenticator = CapabilityAuthenticator()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def compact_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def wave_start(timestamp_ms: int) -> int:
    return timestamp_ms // WAVE_DURATION_MS * WAVE_DURATION_MS


@lru_cache(maxsize=4)
def turn_pairs(start_ms: int) -> tuple[tuple[int, int], ...]:
    """Return the same deterministic, original choice sequence for every team."""
    pairs: list[tuple[int, int]] = []
    for turn in range(1, TURN_COUNT + 1):
        digest = hashlib.sha256(
            f"serpent-circuit:v1:{start_ms}:{turn}".encode()
        ).digest()
        first = digest[0] % 6 + 1
        second = digest[1] % 6 + 1
        if second == first:
            second = second % 6 + 1
        pairs.append((first, second))
    return tuple(pairs)


def apply_roll(position: int, roll: int) -> tuple[int, int | None, int | None]:
    landing = position + roll
    if landing > FINISH_POSITION:
        landing = position
    destination = TRANSITIONS.get(landing)
    if destination is None:
        return landing, None, None
    return destination, landing, destination


@lru_cache(maxsize=4)
def optimal_position(turns: tuple[tuple[int, int], ...]) -> int:
    positions = {START_POSITION}
    for options in turns:
        positions = {
            apply_roll(position, roll)[0]
            for position in positions
            for roll in options
        }
    return max(positions)


def rules_document() -> dict[str, object]:
    transitions = [
        {
            "from": origin,
            "to": destination,
            "kind": "ladder" if destination > origin else "snake",
        }
        for origin, destination in sorted(TRANSITIONS.items())
    ]
    return {
        "boardSize": BOARD_SIZE,
        "finishPosition": FINISH_POSITION,
        "name": NAME,
        "objective": OBJECTIVE_ID,
        "protocolVersion": PROTOCOL_VERSION,
        "rules": [
            "Each 60-second wave gives every team the same twelve dice pairs.",
            "Choose one die from each pair; the server applies choices in turn order.",
            "A move past 100 stays on its current square before any transport is applied.",
            "Each team receives one one-use run per wave.",
            "Route efficiency is (finalPosition - startPosition) / (optimalPosition - startPosition).",
        ],
        "startPosition": START_POSITION,
        "transitions": transitions,
        "turnCount": TURN_COUNT,
        "waveSeconds": WAVE_SECONDS,
    }


def _positive_environment_integer(name: str) -> int:
    raw = os.environ.get(name, "")
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"invalid {name}") from error
    if value <= 0:
        raise RuntimeError(f"invalid {name}")
    return value


def capability_config() -> CapabilityConfig | None:
    names = (
        "RSCTF_KOTH_PLATFORM_URL",
        "RSCTF_KOTH_GAME_ID",
        "RSCTF_KOTH_CHALLENGE_ID",
        "RSCTF_KOTH_CONTEXT_URL",
        "RSCTF_KOTH_OBSERVATION_URL",
        "RSCTF_KOTH_REPORTER_SECRET",
    )
    present = [bool(os.environ.get(name)) for name in names]
    if not any(present):
        return None
    if not all(present):
        raise RuntimeError("incomplete managed KotH environment")

    # Gameplay and scoring are one lifecycle-bound contract. A valid capability
    # endpoint with a broken reporter must not admit runs that cannot be scored.
    try:
        reporter_config = load_reporter_config()
    except ValueError as error:
        raise RuntimeError("invalid managed reporter environment") from error
    if reporter_config is None:
        raise RuntimeError("incomplete managed reporter environment")

    raw_url = os.environ["RSCTF_KOTH_PLATFORM_URL"]
    stripped_url = raw_url.strip()
    parsed = urllib.parse.urlsplit(stripped_url)
    try:
        parsed.port
    except ValueError as error:
        raise RuntimeError("invalid RSCTF_KOTH_PLATFORM_URL") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or raw_url != stripped_url
    ):
        raise RuntimeError("invalid RSCTF_KOTH_PLATFORM_URL")
    platform_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return CapabilityConfig(
        platform_url=platform_url,
        game_id=_positive_environment_integer("RSCTF_KOTH_GAME_ID"),
        challenge_id=_positive_environment_integer("RSCTF_KOTH_CHALLENGE_ID"),
    )


def local_development_enabled() -> bool:
    return os.environ.get("SERPENT_LOCAL_DEV") == "1"


def runtime_mode() -> str:
    try:
        config = capability_config()
    except RuntimeError:
        return "misconfigured"
    if config is not None:
        return "managed"
    if local_development_enabled():
        return "local-development"
    return "unconfigured"


def scoring_ready() -> bool:
    mode = runtime_mode()
    if mode == "local-development":
        return True
    if mode != "managed":
        return False
    try:
        _current_managed_context()
    except GameError:
        return False
    return True


def health_document() -> tuple[int, dict[str, object]]:
    """Return a startup-tolerant health verdict with persistent failures visible."""
    mode = runtime_mode()
    reporter, reporter_healthy = reporter_runtime.health_snapshot()
    ready = scoring_ready()
    healthy = mode != "misconfigured"
    if mode == "managed":
        # A normal context rotation briefly revokes gameplay while the reporter
        # submits the replacement snapshot. That is readiness, not a liveness
        # failure. Persistent retries or a dead reporter still fail health.
        healthy = reporter_healthy
    return (
        200 if healthy else 503,
        {
            "mode": mode,
            "name": NAME,
            "protocol": PROTOCOL,
            "reporter": reporter,
            "scoringReady": ready,
            "status": "ok" if healthy else "error",
            "turnCount": TURN_COUNT,
            "waveSeconds": WAVE_SECONDS,
        },
    )


def publish_managed_context(context: RoundContext | None) -> None:
    """Atomically publish only a context that rsctf accepted for scoring."""
    global managed_context
    # Match gameplay's state -> context lock order so a rotation cannot become
    # visible halfway through accepting one result.
    with state_lock:
        with managed_context_lock:
            managed_context = (
                None
                if context is None
                else ManagedContext(
                    opaque=context.opaque,
                    eligible_hashes=context.eligible_hashes,
                    cycle_ends_at=context.cycle_ends_at,
                    final_scoring_cutoff=context.scoring_ends_at,
                    cycle_number=context.cycle_number,
                    reset_attempt=context.reset_attempt,
                    first_scorable_wave_start=context.scoring_floor,
                    generated_at=context.generated_at,
                    refreshed_at=time.monotonic(),
                )
            )


def _current_managed_context() -> ManagedContext:
    with managed_context_lock:
        context = managed_context
    if context is None:
        raise GameError(503, "the scoring roster is not ready")
    if time.monotonic() - context.refreshed_at > MAX_CONTEXT_STALENESS_SECONDS:
        raise GameError(503, "the scoring roster is temporarily stale")
    return context


def _capability_candidate(token: str) -> CapabilityCandidate | None:
    """Reject malformed/non-roster values before any platform request."""
    if not TOKEN_PATTERN.fullmatch(token):
        return None
    config = capability_config()
    if config is None:
        if local_development_enabled():
            return CapabilityCandidate(
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                context=None,
                config=None,
            )
        raise GameError(503, "managed gameplay is not configured")
    # Reject non-roster hashes before the authenticated platform call. This
    # prevents arbitrary token guesses from amplifying into control-plane work;
    # the bounded authenticator still confirms every admitted player identity.
    context = _current_managed_context()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if token_hash not in context.eligible_hashes:
        return None
    return CapabilityCandidate(
        token_hash=token_hash,
        context=context,
        config=config,
    )


def _confirm_capability(token: str, candidate: CapabilityCandidate) -> None:
    """Confirm a new managed session against RSCTF's live auth boundary."""
    if candidate.config is None:
        return
    if candidate.context is None:
        raise GameError(503, "managed gameplay is misconfigured")
    try:
        capability_authenticator.authenticate(
            platform_url=candidate.config.platform_url,
            game_id=candidate.config.game_id,
            challenge_id=candidate.config.challenge_id,
            token=token,
            token_hash=candidate.token_hash,
            context=candidate.context.opaque,
        )
    except CapabilityRejected as error:
        raise GameError(403, "invalid current KotH capability") from error
    except CapabilityUnavailable as error:
        raise GameError(503, "capability authentication is temporarily unavailable") from error


def _session_document(
    session_id: str,
    session: Session,
    server_time_ms: int,
) -> dict[str, object]:
    return {
        "serverTime": server_time_ms,
        "sessionId": session_id,
        "turns": [
            {"options": list(options), "turn": index}
            for index, options in enumerate(session.turns, 1)
        ],
        "waveEndsAt": session.wave_end_ms,
        "waveId": f"serpent-{session.wave_start_ms}",
    }


def _managed_identity_is_current(session: Session, timestamp_ms: int) -> None:
    if session.context_opaque is None:
        return
    context = _current_managed_context()
    if timestamp_ms >= context.cycle_ends_at:
        raise GameError(410, "the scoring cycle has ended")
    if (
        session.cycle_number != context.cycle_number
        or session.reset_attempt != context.reset_attempt
    ):
        raise GameError(410, "the scoring lifecycle changed")
    if session.token_hash not in context.eligible_hashes:
        raise GameError(403, "the KotH capability is no longer current")


def _prune_state(timestamp_ms: int) -> None:
    oldest_wave = wave_start(timestamp_ms) - WAVE_DURATION_MS
    for session_id in list(sessions):
        if len(sessions) <= MAX_SESSIONS and sessions[session_id].wave_start_ms >= oldest_wave:
            continue
        sessions.pop(session_id, None)
    for key in list(started_runs):
        if key[0] < oldest_wave:
            started_runs.pop(key, None)
    for key in list(wave_admissions):
        if key[0] < oldest_wave:
            wave_admissions.pop(key, None)


def _session_admission_locked(
    candidate: CapabilityCandidate,
    timestamp_ms: int,
) -> SessionAdmission:
    context = candidate.context
    if context is not None:
        current_context = _current_managed_context()
        if (
            current_context.opaque != context.opaque
            or current_context.cycle_number != context.cycle_number
            or current_context.reset_attempt != context.reset_attempt
            or candidate.token_hash not in current_context.eligible_hashes
        ):
            raise GameError(503, "the scoring roster changed; retry the request")
        context = current_context

    start_ms = wave_start(timestamp_ms)
    end_ms = start_ms + WAVE_DURATION_MS
    if context is not None and start_ms < context.first_scorable_wave_start:
        raise GameError(409, "wait for the first complete scoring wave")
    if context is not None and end_ms > context.final_scoring_cutoff:
        raise GameError(409, "the event ends before another complete scoring wave")

    cycle_number = context.cycle_number if context else 0
    reset_attempt = context.reset_attempt if context else 0
    _prune_state(timestamp_ms)
    return SessionAdmission(
        server_time_ms=timestamp_ms,
        wave_start_ms=start_ms,
        wave_end_ms=end_ms,
        turns=turn_pairs(start_ms),
        context_opaque=context.opaque if context else None,
        cycle_number=cycle_number,
        reset_attempt=reset_attempt,
        run_key=(start_ms, candidate.token_hash, cycle_number, reset_attempt),
        admission_key=(start_ms, cycle_number, reset_attempt),
    )


def _existing_session_locked(admission: SessionAdmission) -> dict[str, object] | None:
    session_id = started_runs.get(admission.run_key)
    if session_id is None:
        return None
    session = sessions.get(session_id)
    if session is None:
        raise GameError(503, "the prior session is temporarily unavailable")
    return _session_document(session_id, session, admission.server_time_ms)


def create_session(token: str, timestamp_ms: int | None = None) -> dict[str, object]:
    candidate = _capability_candidate(token)
    if candidate is None:
        raise GameError(403, "invalid current KotH capability")

    # A retry for an already-authenticated team-wave is safe to return after
    # revalidating the live context. A genuinely new session must cross RSCTF's
    # current authentication boundary, even if an earlier request succeeded.
    with state_lock:
        preflight_time = now_ms() if timestamp_ms is None else timestamp_ms
        preflight = _session_admission_locked(candidate, preflight_time)
        existing = _existing_session_locked(preflight)
        if existing is not None:
            return existing

    _confirm_capability(token, candidate)

    with state_lock:
        timestamp_ms = now_ms() if timestamp_ms is None else timestamp_ms
        admission = _session_admission_locked(candidate, timestamp_ms)
        existing = _existing_session_locked(admission)
        if existing is not None:
            return existing
        wave_rows = wave_admissions.get(admission.admission_key, 0)
        active_rows = sum(
            count
            for (wave, cycle_number, reset_attempt), count in wave_admissions.items()
            if wave >= admission.wave_start_ms - WAVE_DURATION_MS
            and cycle_number == admission.cycle_number
            and reset_attempt == admission.reset_attempt
        )
        if (
            active_rows >= MAX_ACTIVE_TEAM_WAVE_ADMISSIONS
            or wave_rows >= MAX_WAVE_ADMISSIONS
        ):
            raise GameError(503, "the arena team-wave admission budget is full")
        if len(sessions) >= MAX_SESSIONS:
            raise GameError(503, "the arena session budget is full")
        session_id = secrets.token_urlsafe(24)
        while session_id in sessions:
            session_id = secrets.token_urlsafe(24)
        sessions[session_id] = Session(
            token_hash=candidate.token_hash,
            wave_start_ms=admission.wave_start_ms,
            wave_end_ms=admission.wave_end_ms,
            turns=admission.turns,
            context_opaque=admission.context_opaque,
            cycle_number=admission.cycle_number,
            reset_attempt=admission.reset_attempt,
        )
        started_runs[admission.run_key] = session_id
        wave_admissions[admission.admission_key] = wave_rows + 1
        response = _session_document(session_id, sessions[session_id], timestamp_ms)

    return response


def _validated_choices(value: object) -> list[int]:
    if not isinstance(value, list) or len(value) != TURN_COUNT:
        raise GameError(400, f"selections must contain exactly {TURN_COUNT} turns")
    choices: list[int] = []
    for expected_turn, selection in enumerate(value, 1):
        if (
            not isinstance(selection, dict)
            or set(selection) != {"turn", "choice"}
            or isinstance(selection["turn"], bool)
            or selection["turn"] != expected_turn
            or isinstance(selection["choice"], bool)
            or not isinstance(selection["choice"], int)
            or not 0 <= selection["choice"] <= 1
        ):
            raise GameError(400, "selections must be ordered turns with choice 0 or 1")
        choices.append(selection["choice"])
    return choices


def play_session(
    session_id: object,
    selections: object,
    timestamp_ms: int | None = None,
) -> dict[str, object]:
    global next_cursor

    if not isinstance(session_id, str) or not 16 <= len(session_id) <= 128:
        raise GameError(400, "invalid sessionId")
    choices = _validated_choices(selections)
    explicit_timestamp = timestamp_ms

    with state_lock:
        timestamp_ms = now_ms() if explicit_timestamp is None else explicit_timestamp
        _prune_state(timestamp_ms)
        session = sessions.get(session_id)
        if session is None:
            raise GameError(404, "unknown session")
        choice_tuple = tuple(choices)
        if session.result is not None:
            if session.choices != choice_tuple:
                raise GameError(409, "session was already played with different choices")
            return deepcopy(session.result)
        if session.consumed:
            raise GameError(410, "the session wave has ended")
        _managed_identity_is_current(session, timestamp_ms)
        if timestamp_ms >= session.wave_end_ms:
            session.consumed = True
            raise GameError(410, "the session wave has ended")
        session.consumed = True

        position = START_POSITION
        landings: list[dict[str, object]] = []
        for turn, (options, choice) in enumerate(zip(session.turns, choices), 1):
            origin = position
            roll = options[choice]
            position, transport_from, transport_to = apply_roll(position, roll)
            landings.append(
                {
                    "from": origin,
                    "roll": roll,
                    "to": position,
                    "transportFrom": transport_from,
                    "transportTo": transport_to,
                    "turn": turn,
                }
            )

        optimum = optimal_position(session.turns)
        best_progress = max(0, optimum - START_POSITION)
        earned = min(max(0, position - START_POSITION), best_progress)
        possible = max(1, best_progress)
        events.append(
            {
                "cursor": next_cursor,
                "cycleNumber": session.cycle_number,
                "earned": earned,
                "occurredAt": timestamp_ms,
                "possible": possible,
                "resetAttempt": session.reset_attempt,
                "tokenHash": session.token_hash,
                "waveStart": session.wave_start_ms,
            }
        )
        next_cursor += 1

        result: dict[str, object] = {
            "accepted": True,
            "efficiency": {"earned": earned, "possible": possible},
            "finalPosition": position,
            "landings": landings,
            "optimalPosition": optimum,
            "waveId": f"serpent-{session.wave_start_ms}",
        }
        session.choices = choice_tuple
        session.result = result

    return deepcopy(result)


def read_evidence_page(after: int) -> dict[str, object]:
    """Return one bounded immutable page to the in-process managed reporter."""
    with state_lock:
        oldest = int(events[0]["cursor"]) if events else next_cursor
        selected = [dict(event) for event in events if int(event["cursor"]) > after][
            :MAX_RESPONSE_EVENTS
        ]
        latest = next_cursor - 1
    next_value = int(selected[-1]["cursor"]) if selected else after
    return {
        "events": selected,
        "gap": after + 1 < oldest,
        "hasMore": next_value < latest,
        "latestCursor": latest,
        "nextCursor": next_value,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "serpent-circuit"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.rfile = _BoundedReader(
            self.rfile,
            self.server.request_stream_bytes,
        )

    def handle(self) -> None:
        try:
            super().handle()
        except OSError:
            self.close_connection = True

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            self.close_connection = True
        except OSError:
            self.close_connection = True

    def _json(self, status: int, value: object) -> None:
        self._send(status, compact_json(value), "application/json")

    def _read_json(self, expected_keys: set[str]) -> dict[str, Any] | None:
        if self.headers.get("Transfer-Encoding") is not None:
            self._json(400, {"error": "transfer encoding is not accepted"})
            return None
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self._json(415, {"error": "use application/json"})
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._json(413, {"error": "invalid request size"})
            return None
        try:
            raw = self.rfile.read(length)
        except (TimeoutError, OSError):
            return None
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "invalid JSON"})
            return None
        if not isinstance(value, dict) or set(value) != expected_keys:
            self._json(400, {"error": "unexpected request fields"})
            return None
        return value

    def _static(self, filename: str, content_type: str) -> None:
        try:
            body = (STATIC_ROOT / filename).read_bytes()
        except OSError:
            self._json(503, {"error": "interface asset unavailable"})
            return
        self._send(200, body, content_type)

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler contract
        self.do_GET()

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler contract
        request = urllib.parse.urlsplit(self.path)
        if request.query:
            self._json(400, {"error": "query parameters are not accepted"})
            return
        if request.path == "/healthz":
            status, document = health_document()
            self._json(status, document)
            return
        if request.path == "/api/rules":
            self._json(200, rules_document())
            return
        if request.path == "/":
            self._static("index.html", "text/html; charset=utf-8")
            return
        if request.path == "/assets/styles.css":
            self._static("styles.css", "text/css; charset=utf-8")
            return
        if request.path == "/assets/app.js":
            self._static("app.js", "text/javascript; charset=utf-8")
            return
        if request.path == "/assets/clock.mjs":
            self._static("clock.mjs", "text/javascript; charset=utf-8")
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler contract
        request = urllib.parse.urlsplit(self.path)
        if request.query:
            self._json(400, {"error": "query parameters are not accepted"})
            return
        if request.path == "/api/session":
            value = self._read_json({"token"})
            if value is None:
                return
            token = value["token"]
            if not isinstance(token, str):
                self._json(400, {"error": "token must be a string"})
                return
            try:
                result = create_session(token)
            except GameError as error:
                self._json(error.status, {"error": error.message})
                return
            except RuntimeError:
                self._json(503, {"error": "managed gameplay is misconfigured"})
                return
            self._json(201, result)
            return
        if request.path == "/api/play":
            value = self._read_json({"sessionId", "selections"})
            if value is None:
                return
            try:
                result = play_session(value["sessionId"], value["selections"])
            except GameError as error:
                self._json(error.status, {"error": error.message})
                return
            self._json(200, result)
            return
        self._json(404, {"error": "not found"})

    def log_message(self, _format, *_args):
        # Request bodies may contain current capabilities, so access logs stay off.
        pass


class Server(BoundedThreadingHTTPServer):
    max_workers = MAX_HTTP_WORKERS
    max_regular_workers = MAX_REGULAR_HTTP_WORKERS
    max_requests_per_client = MAX_REQUESTS_PER_CLIENT
    max_pending_requests = MAX_PENDING_HTTP_REQUESTS
    # Leave room for protected health traffic to finish its TCP handshake
    # while the classifier evicts an older silent connection.
    request_queue_size = MAX_PENDING_HTTP_REQUESTS + MAX_HTTP_WORKERS
    request_stream_bytes = MAX_REQUEST_STREAM_BYTES
    read_timeout_seconds = CONNECTION_TIMEOUT_SECONDS
    absolute_request_seconds = ABSOLUTE_REQUEST_SECONDS
    protected_request_lines = frozenset(
        {
            b"GET /healthz HTTP/1.0",
            b"GET /healthz HTTP/1.1",
            b"HEAD /healthz HTTP/1.0",
            b"HEAD /healthz HTTP/1.1",
        }
    )


def main() -> None:
    try:
        port = int(os.environ.get("PORT", str(PORT)))
    except ValueError as error:
        raise SystemExit("PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be in 1..65535")
    start_managed_reporter(
        read_evidence_page,
        publish_managed_context,
        reporter_runtime,
    )
    Server(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
