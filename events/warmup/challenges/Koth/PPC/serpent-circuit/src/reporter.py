"""Lifecycle-bound managed Leaderboard reporter for Serpent Circuit."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import hmac
import json
import math
import os
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request


MAX_RESPONSE_BYTES = 2 * 1_024 * 1_024
MAX_SUBMISSION_BYTES = 512 * 1_024
MAX_TEAMS = 2_000
MAX_WAVES = 64
MAX_PAGE_EVENTS = 1_000
WAVE_DURATION_MS = 60_000
WAVE_FINALIZATION_LAG_MS = 2_000
LEGACY_SETTLEMENT_LAG_MS = 20_000
CONTEXT_MAX_AGE_MS = 8_000
CONTEXT_MAX_FUTURE_MS = 5_000
USER_AGENT = "serpent-circuit-managed-reporter/1"
OBJECTIVE_IDS = ("route-efficiency",)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


_DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
)


class ReporterHttpError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class ReporterRuntime:
    """Expose bounded reporter liveness without retaining error or secret text."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = "stopped"
        self._failures = 0
        self._thread: threading.Thread | None = None

    def set_stopped(self, *, failed: bool = False) -> None:
        with self._lock:
            self._state = "stopped"
            self._failures = 1 if failed else 0
            self._thread = None

    def set_disabled(self) -> None:
        with self._lock:
            self._state = "disabled"
            self._failures = 0
            self._thread = None

    def attach(self, thread: threading.Thread) -> None:
        with self._lock:
            self._state = "starting"
            self._failures = 0
            self._thread = thread

    def mark_ready(self) -> None:
        with self._lock:
            self._state = "ready"
            self._failures = 0

    def mark_waiting(self) -> None:
        with self._lock:
            self._state = "waiting"
            self._failures = 0

    def mark_retrying(self) -> None:
        with self._lock:
            self._state = "retrying"
            self._failures = min(self._failures + 1, 2**31 - 1)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            state = self._state
            failures = self._failures
            thread = self._thread
        alive = thread.is_alive() if thread is not None else False
        if thread is not None and thread.ident is not None and not alive:
            state = "stopped"
        return {
            "alive": alive,
            "consecutiveFailures": failures,
            "state": state,
        }


@dataclass(frozen=True)
class Config:
    game_id: int
    challenge_id: int
    secret: str
    context_url: str
    observation_url: str
    poll_seconds: float
    timeout_seconds: float


@dataclass(frozen=True)
class TeamResult:
    earned: int
    possible: int


@dataclass(frozen=True)
class RoundContext:
    opaque: str
    cycle_number: int
    reset_attempt: int
    round_number: int
    starts_at: int
    ends_at: int
    cycle_ends_at: int
    scoring_ends_at: int
    scoring_floor: int
    generated_at: int
    eligible_hashes: frozenset[str]
    objective_ids: tuple[str, ...]
    objective_schema_hash: str | None


def _window_wave_starts(context: RoundContext, cutoff_ms: int) -> list[int]:
    aligned_boundary = (
        (context.starts_at + WAVE_DURATION_MS - 1) // WAVE_DURATION_MS
    ) * WAVE_DURATION_MS
    end_ms = (
        aligned_boundary + WAVE_DURATION_MS
        if context.round_number <= 1
        else aligned_boundary
    )
    starts: list[int] = []
    while (
        end_ms < context.ends_at
        and end_ms <= context.scoring_ends_at
        and end_ms <= cutoff_ms
    ):
        start_ms = end_ms - WAVE_DURATION_MS
        if start_ms >= context.scoring_floor:
            starts.append(start_ms)
        end_ms += WAVE_DURATION_MS
    if len(starts) > MAX_WAVES:
        raise RuntimeError("settlement window contains more than 64 finalized arena waves")
    return starts


def _objective_schema_hash(objective_ids: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(len(objective_ids).to_bytes(8, "big"))
    for objective_id in objective_ids:
        encoded = objective_id.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ValueError(f"missing {name}")
    return value


def _positive_integer(name: str) -> int:
    try:
        value = int(_required_environment(name))
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bounded_number(name: str, default: str, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}]")
    return value


def _normalize_url(name: str, value: str) -> str:
    stripped = value.strip()
    parsed = urllib.parse.urlsplit(stripped)
    try:
        parsed.port
    except ValueError as error:
        raise ValueError(f"{name} contains an invalid port") from error
    if (
        stripped != value
        or parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(character) < 33 for character in stripped)
    ):
        raise ValueError(
            f"{name} must be an absolute HTTP(S) URL without credentials or a query"
        )
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def load_config() -> Config | None:
    names = (
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
        missing = ", ".join(name for name in names if not os.environ.get(name))
        raise ValueError(f"incomplete managed reporter environment; missing {missing}")

    secret = _required_environment("RSCTF_KOTH_REPORTER_SECRET")
    if (
        secret != secret.strip()
        or not secret.startswith("koth_target_")
        or not 32 <= len(secret) <= 256
        or any(ord(character) < 33 for character in secret)
    ):
        raise ValueError("RSCTF_KOTH_REPORTER_SECRET is not a valid target credential")
    return Config(
        game_id=_positive_integer("RSCTF_KOTH_GAME_ID"),
        challenge_id=_positive_integer("RSCTF_KOTH_CHALLENGE_ID"),
        secret=secret,
        context_url=_normalize_url(
            "RSCTF_KOTH_CONTEXT_URL",
            _required_environment("RSCTF_KOTH_CONTEXT_URL"),
        ),
        observation_url=_normalize_url(
            "RSCTF_KOTH_OBSERVATION_URL",
            _required_environment("RSCTF_KOTH_OBSERVATION_URL"),
        ),
        poll_seconds=_bounded_number("RSCTF_KOTH_POLL_SECONDS", "1", 1, 300),
        timeout_seconds=_bounded_number("RSCTF_KOTH_TIMEOUT_SECONDS", "5", 1, 60),
    )


def _request_json(request: urllib.request.Request, timeout: float) -> dict[str, Any]:
    try:
        with _DIRECT_OPENER.open(request, timeout=timeout) as response:
            status = response.status
            content_type = response.headers.get_content_type()
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raw_detail = error.read(2_048).decode("utf-8", errors="replace").strip()
        detail = raw_detail.encode("unicode_escape").decode("ascii")[:512]
        raise ReporterHttpError(error.code, detail or "request rejected") from error
    if status != 200:
        raise ReporterHttpError(status, "expected HTTP 200")
    if content_type != "application/json":
        raise RuntimeError("remote endpoint returned an invalid media type")
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("remote JSON response exceeded the 2 MiB limit")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("remote endpoint returned invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("remote endpoint returned a non-object JSON value")
    return value


def _canonical_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _integer(value: object, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RuntimeError(f"RSCTF or arena returned invalid {name}")
    return value


class ManagedReporter:
    def __init__(
        self,
        config: Config,
        read_evidence_page: Callable[[int], dict[str, Any]],
        publish_context: Callable[[RoundContext | None], None] | None = None,
    ):
        self.config = config
        self.read_evidence_page = read_evidence_page
        self.publish_context = publish_context or (lambda _context: None)
        self.context: RoundContext | None = None
        self.cursor = 0
        self.waves: dict[int, dict[str, TeamResult]] = {}
        self.finalized_crowns: dict[int, str | None] = {}
        self.last_submitted_digest: str | None = None
        self.last_timestamp_ms = 0
        self.deferred_wave_start: int | None = None

    def _get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "User-Agent": USER_AGENT,
                "X-RSCTF-API-Version": "v2",
            },
        )
        return _request_json(request, self.config.timeout_seconds)

    def fetch_context(self) -> RoundContext:
        value = self._get_json(self.config.context_url)
        expected = {
            "apiVersion",
            "context",
            "cycleNumber",
            "cycleEndsAt",
            "resetAttempt",
            "roundNumber",
            "waveWindowStartsAt",
            "waveWindowEndsAt",
            "eligibleTokenHashes",
            "objectiveIds",
            "objectiveSchemaHash",
            "generatedAt",
        }
        fields = set(value)
        api_version = value.get("apiVersion")
        v2_fields = expected | {"cycleStartsAt", "scoringEndsAt"}
        if not (
            (api_version == "v1" and fields == expected)
            or (api_version == "v2" and fields == v2_fields)
        ):
            raise RuntimeError("RSCTF returned an unexpected KotH arena context")
        opaque = value["context"]
        hashes = value["eligibleTokenHashes"]
        objective_ids = value["objectiveIds"]
        objective_schema_hash = value["objectiveSchemaHash"]
        if (
            not _canonical_hash(opaque)
            or not isinstance(hashes, list)
            or len(hashes) > MAX_TEAMS
            or any(not _canonical_hash(item) for item in hashes)
            or len(set(hashes)) != len(hashes)
        ):
            raise RuntimeError("RSCTF returned invalid eligible capability hashes")
        if objective_ids == []:
            if objective_schema_hash is not None:
                raise RuntimeError("RSCTF returned a hash without an objective schema")
        elif (
            objective_ids != list(OBJECTIVE_IDS)
            or not _canonical_hash(objective_schema_hash)
            or objective_schema_hash != _objective_schema_hash(OBJECTIVE_IDS)
        ):
            raise RuntimeError("RSCTF returned a different Leaderboard objective schema")
        starts_at = _integer(
            value["waveWindowStartsAt"], 0, 2**63 - 2, "waveWindowStartsAt"
        )
        ends_at = _integer(
            value["waveWindowEndsAt"], starts_at + 1, 2**63 - 1, "waveWindowEndsAt"
        )
        cycle_ends_at = _integer(
            value["cycleEndsAt"], ends_at, 2**63 - 1, "cycleEndsAt"
        )
        cycle_starts_at = (
            _integer(value["cycleStartsAt"], 0, starts_at, "cycleStartsAt")
            if api_version == "v2"
            else None
        )
        scoring_ends_at = (
            _integer(value["scoringEndsAt"], 0, cycle_ends_at, "scoringEndsAt")
            if api_version == "v2"
            else max(0, cycle_ends_at - LEGACY_SETTLEMENT_LAG_MS)
        )
        if api_version == "v2" and (
            scoring_ends_at <= cycle_starts_at
            or ends_at > scoring_ends_at + 1
        ):
            raise RuntimeError("RSCTF returned inconsistent KotH scoring bounds")
        generated_at = _integer(value["generatedAt"], 1, 2**63 - 1, "generatedAt")
        current_time_ms = time.time_ns() // 1_000_000
        if (
            generated_at < current_time_ms - CONTEXT_MAX_AGE_MS
            or generated_at > current_time_ms + CONTEXT_MAX_FUTURE_MS
        ):
            raise RuntimeError("RSCTF returned a stale KotH arena context")
        same_lifecycle = self.context is not None and (
            self.context.cycle_number
            == _integer(value["cycleNumber"], 1, 2**31 - 1, "cycleNumber")
            and self.context.reset_attempt
            == _integer(value["resetAttempt"], 0, 2**31 - 1, "resetAttempt")
        )
        if same_lifecycle:
            scoring_floor = self.context.scoring_floor
        else:
            if api_version == "v2":
                floor_source = cycle_starts_at
            else:
                floor_source = starts_at if value["roundNumber"] == 1 else generated_at
            scoring_floor = (
                (floor_source + WAVE_DURATION_MS - 1)
                // WAVE_DURATION_MS
                * WAVE_DURATION_MS
            )
        context = RoundContext(
            opaque=opaque,
            cycle_number=_integer(value["cycleNumber"], 1, 2**31 - 1, "cycleNumber"),
            reset_attempt=_integer(value["resetAttempt"], 0, 2**31 - 1, "resetAttempt"),
            round_number=_integer(value["roundNumber"], 1, 2**31 - 1, "roundNumber"),
            starts_at=starts_at,
            ends_at=ends_at,
            cycle_ends_at=cycle_ends_at,
            scoring_ends_at=scoring_ends_at,
            scoring_floor=scoring_floor,
            generated_at=generated_at,
            eligible_hashes=frozenset(hashes),
            objective_ids=tuple(objective_ids),
            objective_schema_hash=objective_schema_hash,
        )
        _window_wave_starts(context, context.ends_at)
        return context

    def _consume_feed(
        self,
        context: RoundContext,
        current_time_ms: int | None = None,
    ) -> bool:
        """Stage a complete feed transaction, stopping before the first open wave."""
        current_time_ms = (
            time.time_ns() // 1_000_000
            if current_time_ms is None
            else current_time_ms
        )
        staged_cursor = self.cursor
        staged_waves = {
            start_ms: dict(rows)
            for start_ms, rows in self.waves.items()
        }
        staged_rows = sum(len(rows) for rows in staged_waves.values())
        changed = False
        current_wave: int | None = None
        previous_wave: int | None = None
        checkpoint_cursor = staged_cursor
        checkpoint_changed = changed
        checkpoint_rows = staged_rows
        checkpoint_wave_rows: dict[str, TeamResult] | None = None
        while True:
            value = self.read_evidence_page(staged_cursor)
            if set(value) != {"events", "gap", "hasMore", "latestCursor", "nextCursor"}:
                raise RuntimeError("arena returned an unexpected evidence page")
            if value["gap"] is not False:
                raise RuntimeError("arena evidence feed is incomplete; refusing partial scoring")
            page = value["events"]
            if not isinstance(page, list) or len(page) > MAX_PAGE_EVENTS:
                raise RuntimeError("arena returned an invalid evidence page")

            previous = staged_cursor
            for item in page:
                if not isinstance(item, dict):
                    raise RuntimeError("arena returned a malformed evidence event")
                event_cursor = _integer(
                    item.get("cursor"), 1, 2**63 - 1, "event cursor"
                )
                if event_cursor != previous + 1:
                    raise RuntimeError("arena evidence cursor was not contiguous")
                previous = event_cursor
            next_value = _integer(
                value["nextCursor"], staged_cursor, 2**63 - 1, "nextCursor"
            )
            latest = _integer(
                value["latestCursor"], next_value, 2**63 - 1, "latestCursor"
            )
            if page and next_value != previous:
                raise RuntimeError("arena evidence page cursor is inconsistent")
            if not page and next_value != staged_cursor:
                raise RuntimeError("empty arena evidence page advanced its cursor")
            if value["hasMore"] is False:
                if next_value != latest:
                    raise RuntimeError("arena evidence page hid newer events")
            elif value["hasMore"] is not True or not page or next_value >= latest:
                raise RuntimeError("arena evidence pagination did not make progress")

            for item in page:
                item_wave = _integer(
                    item.get("waveStart"),
                    0,
                    2**63 - WAVE_DURATION_MS,
                    "wave start",
                )
                if previous_wave is not None and item_wave < previous_wave:
                    raise RuntimeError("arena evidence waves were not monotonic")
                previous_wave = item_wave
                if item_wave != current_wave:
                    current_wave = item_wave
                    checkpoint_cursor = staged_cursor
                    checkpoint_changed = changed
                    checkpoint_rows = staged_rows
                    checkpoint_wave_rows = (
                        dict(staged_waves[item_wave])
                        if item_wave in staged_waves
                        else None
                    )
                disposition = self._stage_event(
                    item,
                    context,
                    staged_waves,
                    current_time_ms,
                    staged_rows,
                )
                if disposition in {"defer", "overflow"}:
                    if disposition == "overflow":
                        prior_wave_rows = len(checkpoint_wave_rows or {})
                        if checkpoint_rows - prior_wave_rows == 0:
                            raise RuntimeError(
                                "one arena wave exceeded the 2,000 team row bound"
                            )
                    if checkpoint_wave_rows is None:
                        staged_waves.pop(item_wave, None)
                    else:
                        staged_waves[item_wave] = checkpoint_wave_rows
                    self.cursor = checkpoint_cursor
                    self.waves = staged_waves
                    self.deferred_wave_start = item_wave
                    return checkpoint_changed
                if disposition == "added":
                    staged_rows += 1
                staged_cursor = int(item["cursor"])
                changed = True
            if value["hasMore"] is False:
                self.cursor = staged_cursor
                self.waves = staged_waves
                self.deferred_wave_start = None
                return changed

    def _stage_event(
        self,
        event: object,
        context: RoundContext,
        staged_waves: dict[int, dict[str, TeamResult]],
        current_time_ms: int,
        staged_rows: int,
    ) -> str:
        if not isinstance(event, dict) or set(event) != {
            "cursor",
            "cycleNumber",
            "earned",
            "occurredAt",
            "possible",
            "resetAttempt",
            "tokenHash",
            "waveStart",
        }:
            raise RuntimeError("arena returned a malformed evidence event")
        _integer(event["cursor"], 1, 2**63 - 1, "event cursor")
        wave = _integer(event["waveStart"], 0, 2**63 - WAVE_DURATION_MS, "wave start")
        occurred_at = _integer(event["occurredAt"], wave, wave + WAVE_DURATION_MS - 1, "event time")
        if wave % WAVE_DURATION_MS != 0:
            raise RuntimeError("arena evidence used an unaligned wave")
        token_hash = event["tokenHash"]
        if not _canonical_hash(token_hash):
            raise RuntimeError("arena returned malformed evidence identity")
        possible = _integer(event["possible"], 1, 1_000_000_000_000, "possible")
        earned = _integer(event["earned"], 0, possible, "earned")
        cycle_number = _integer(event["cycleNumber"], 0, 2**31 - 1, "cycle number")
        reset_attempt = _integer(event["resetAttempt"], 0, 2**31 - 1, "reset attempt")
        if occurred_at >= wave + WAVE_DURATION_MS:
            raise RuntimeError("arena event fell outside its wave")
        wave_end = wave + WAVE_DURATION_MS
        event_lifecycle = (cycle_number, reset_attempt)
        context_lifecycle = (context.cycle_number, context.reset_attempt)
        if event_lifecycle < context_lifecycle:
            return "consume"
        if event_lifecycle > context_lifecycle:
            return "defer"
        if wave < context.scoring_floor or wave_end > context.scoring_ends_at:
            raise RuntimeError("arena emitted evidence outside the authoritative scoring cycle")
        cutoff = current_time_ms - WAVE_FINALIZATION_LAG_MS
        if wave_end >= context.ends_at or wave_end > cutoff:
            return "defer"
        if wave not in _window_wave_starts(context, cutoff):
            if wave_end <= context.starts_at:
                return "consume"
            raise RuntimeError("arena emitted evidence outside the authoritative scoring window")
        if token_hash not in context.eligible_hashes:
            return "consume"
        if wave in self.finalized_crowns:
            raise RuntimeError("arena appended evidence to an already finalized wave")
        rows = staged_waves.setdefault(wave, {})
        if token_hash in rows:
            raise RuntimeError("arena reported more than one team result in a wave")
        if staged_rows >= MAX_TEAMS:
            return "overflow"
        rows[token_hash] = TeamResult(earned=earned, possible=possible)
        return "added"

    @staticmethod
    def _performance(result: TeamResult) -> Fraction:
        return Fraction(result.earned, result.possible)

    def _finalize_wave(self, start_ms: int) -> None:
        rows = self.waves.get(start_ms, {})
        if not rows:
            crown = None
        else:
            best = max(self._performance(result) for result in rows.values())
            tied = [
                token_hash
                for token_hash, result in rows.items()
                if self._performance(result) == best
            ]
            crown = tied[0] if best > 0 and len(tied) == 1 else None
        self.finalized_crowns[start_ms] = crown

    def _prune_ineligible(self, context: RoundContext) -> None:
        """Drop revoked rows and recompute each retained finalized Crown."""
        self.waves = {
            start_ms: {
                token_hash: result
                for token_hash, result in rows.items()
                if token_hash in context.eligible_hashes
            }
            for start_ms, rows in self.waves.items()
        }
        for start_ms in self.finalized_crowns:
            self._finalize_wave(start_ms)

    def _body(self, context: RoundContext, current_time_ms: int) -> bytes:
        cutoff = current_time_ms - WAVE_FINALIZATION_LAG_MS
        finalized_starts = _window_wave_starts(context, cutoff)
        if self.deferred_wave_start is not None:
            finalized_starts = [
                start_ms
                for start_ms in finalized_starts
                if start_ms < self.deferred_wave_start
            ]
        for start_ms in finalized_starts:
            if start_ms not in self.finalized_crowns:
                self._finalize_wave(start_ms)

        waves = []
        team_wave_rows = 0
        for start_ms in finalized_starts:
            crown = self.finalized_crowns[start_ms]
            teams = [
                {
                    "activity": {"earned": 1, "possible": 1},
                    "isCrown": token_hash == crown,
                    "objectives": [
                        {"earned": result.earned, "possible": result.possible}
                    ],
                    "tokenHash": token_hash,
                }
                for token_hash, result in sorted(self.waves.get(start_ms, {}).items())
                if token_hash in context.eligible_hashes
            ]
            team_wave_rows += len(teams)
            waves.append(
                {
                    "endedAtUnixMs": start_ms + WAVE_DURATION_MS,
                    "teams": teams,
                    "waveId": f"serpent-{start_ms}",
                }
            )
        if team_wave_rows > MAX_TEAMS:
            raise RuntimeError("snapshot exceeds RSCTF's 2,000 team-wave row bound")
        body = json.dumps(
            {
                "context": context.opaque,
                "objectiveIds": list(OBJECTIVE_IDS),
                "waves": waves,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        if len(body) > MAX_SUBMISSION_BYTES:
            raise RuntimeError("normalized Leaderboard snapshot exceeds RSCTF's 512 KiB limit")
        return body

    def _next_timestamp(self) -> str:
        current = time.time_ns() // 1_000_000
        self.last_timestamp_ms = max(current, self.last_timestamp_ms + 1)
        return str(self.last_timestamp_ms)

    def submit(self, context: RoundContext, body: bytes) -> dict[str, Any]:
        timestamp = self._next_timestamp()
        message = f"{timestamp}.{self.config.game_id}.{self.config.challenge_id}.".encode() + body
        signature = hmac.new(self.config.secret.encode(), message, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            self.config.observation_url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "X-RSCTF-Timestamp": timestamp,
                "X-RSCTF-Signature": f"sha256={signature}",
            },
        )
        value = _request_json(request, self.config.timeout_seconds)
        expected = {
            "accepted",
            "acceptedAt",
            "cycleNumber",
            "recognizedTeams",
            "resetAttempt",
            "roundNumber",
            "submittedTeams",
            "submittedWaves",
        }
        if set(value) != expected or value["accepted"] is not True:
            raise RuntimeError("RSCTF returned an unexpected arena acknowledgement")
        decoded = json.loads(body)
        expected_waves = len(decoded["waves"])
        expected_teams = len(
            {
                team["tokenHash"]
                for wave in decoded["waves"]
                for team in wave["teams"]
            }
        )
        _integer(value["acceptedAt"], 1, 2**63 - 1, "acceptedAt")
        if (
            value["cycleNumber"] != context.cycle_number
            or value["resetAttempt"] != context.reset_attempt
            or value["roundNumber"] != context.round_number
            or value["submittedWaves"] != expected_waves
            or value["submittedTeams"] != expected_teams
            or value["recognizedTeams"] != expected_teams
            or any(
                isinstance(value[name], bool) or not isinstance(value[name], int)
                for name in (
                    "cycleNumber",
                    "resetAttempt",
                    "roundNumber",
                    "submittedWaves",
                    "submittedTeams",
                    "recognizedTeams",
                )
            )
        ):
            raise RuntimeError("RSCTF did not acknowledge the complete arena snapshot")
        return value

    def poll_once(self) -> bool:
        # Cursor advancement is committed only after rsctf acknowledges the
        # exact context. A rotation or lost response can therefore be retried
        # from the prior page without losing evidence.
        saved = (
            self.context,
            self.cursor,
            {start: dict(rows) for start, rows in self.waves.items()},
            dict(self.finalized_crowns),
            self.last_submitted_digest,
            self.deferred_wave_start,
        )
        try:
            context = self.fetch_context()
            context_changed = self.context is None or context.opaque != self.context.opaque
            if context_changed:
                self.publish_context(None)
                runtime_changed = self.context is not None and (
                    context.cycle_number != self.context.cycle_number
                    or context.reset_attempt != self.context.reset_attempt
                )
                if runtime_changed:
                    self.waves = {}
                    self.finalized_crowns = {}
                self.context = context
                self.waves = {
                    start: rows
                    for start, rows in self.waves.items()
                    if start + WAVE_DURATION_MS >= context.starts_at
                }
                self.finalized_crowns = {
                    start: crown
                    for start, crown in self.finalized_crowns.items()
                    if start + WAVE_DURATION_MS >= context.starts_at
                }
                self.last_submitted_digest = None
            else:
                self.context = context
            self._prune_ineligible(context)
            poll_time_ms = time.time_ns() // 1_000_000
            feed_changed = self._consume_feed(context, poll_time_ms)
            body = self._body(context, poll_time_ms)
            digest = hashlib.sha256(body).hexdigest()
            if digest == self.last_submitted_digest and not feed_changed:
                self.publish_context(context)
                return False
            accepted = self.submit(context, body)
            self.last_submitted_digest = digest
            self.publish_context(context)
            print(
                "accepted Serpent Circuit snapshot:"
                f" round={accepted['roundNumber']}"
                f" waves={accepted['submittedWaves']}"
                f" teams={accepted['recognizedTeams']}"
                f" cycle={accepted['cycleNumber']}"
                f" reset={accepted['resetAttempt']}",
                flush=True,
            )
            return True
        except Exception:
            (
                self.context,
                self.cursor,
                self.waves,
                self.finalized_crowns,
                self.last_submitted_digest,
                self.deferred_wave_start,
            ) = saved
            raise


def _retry_delay(failures: int, poll_seconds: float) -> float:
    return min(30.0, max(1.0, poll_seconds) * (2 ** min(failures - 1, 5)))


def start_managed_reporter(
    read_evidence_page: Callable[[int], dict[str, Any]],
    publish_context: Callable[[RoundContext | None], None] | None = None,
    runtime: ReporterRuntime | None = None,
) -> threading.Thread | None:
    """Start only when rsctf injected the complete managed-target contract."""
    runtime = runtime or ReporterRuntime()
    try:
        config = load_config()
    except ValueError as error:
        runtime.set_stopped(failed=True)
        print(f"managed reporter configuration error: {error}", flush=True)
        return None
    if config is None:
        runtime.set_disabled()
        print("managed reporter disabled: rsctf environment is absent", flush=True)
        return None

    client = ManagedReporter(config, read_evidence_page, publish_context)

    def run() -> None:
        failures = 0
        try:
            while True:
                try:
                    client.poll_once()
                    failures = 0
                    runtime.mark_ready()
                    time.sleep(config.poll_seconds)
                except ReporterHttpError as error:
                    client.publish_context(None)
                    if error.status == 409:
                        failures = 0
                        runtime.mark_waiting()
                        time.sleep(config.poll_seconds)
                        continue
                    failures += 1
                    runtime.mark_retrying()
                    print(f"managed reporter error: {error}", flush=True)
                    time.sleep(_retry_delay(failures, config.poll_seconds))
                except (urllib.error.URLError, OSError, RuntimeError) as error:
                    client.publish_context(None)
                    failures += 1
                    runtime.mark_retrying()
                    print(f"managed reporter error: {error}", flush=True)
                    time.sleep(_retry_delay(failures, config.poll_seconds))
        finally:
            try:
                client.publish_context(None)
            finally:
                runtime.set_stopped(failed=True)

    thread = threading.Thread(target=run, name="serpent-circuit-reporter", daemon=True)
    runtime.attach(thread)
    try:
        thread.start()
    except RuntimeError:
        runtime.set_stopped(failed=True)
        raise
    return thread
