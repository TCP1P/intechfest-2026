"""Lifecycle-bound native Leaderboard reporter for Tower of Babel."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


OBJECTIVE_IDS = ("babel-mastery",)
WAVE_DURATION_MS = 60_000
WAVE_FINALIZATION_LAG_MS = 2_000
LEGACY_SETTLEMENT_LAG_MS = 20_000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SUBMISSION_BYTES = 512 * 1024
MAX_TEAMS = 2_000
MAX_WAVES = 64
MAX_FUTURE_MS = 5_000
USER_AGENT = "tower-of-babel-managed-reporter/1"


class ReporterHTTPError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class ReporterInactive(ReporterHTTPError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


@dataclass(frozen=True)
class Config:
    game_id: int
    challenge_id: int
    secret: str
    context_url: str
    observation_url: str
    poll_seconds: float = 1.0
    timeout_seconds: float = 4.0


@dataclass(frozen=True)
class Context:
    opaque: str
    cycle_number: int
    reset_attempt: int
    round_number: int
    window_start: int
    window_end: int
    cycle_end: int
    scoring_end: int
    scoring_floor: int
    generated_at: int
    eligible_hashes: frozenset[str]
    objective_ids: tuple[str, ...]
    objective_schema_hash: str | None


class ReporterRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state = "stopped"
        self.failures = 0
        self._thread: threading.Thread | None = None
        self._state_since = time.monotonic()

    def attach(self, thread: threading.Thread) -> None:
        with self._lock:
            self._thread = thread
            self.state = "starting"
            self.failures = 0
            self._state_since = time.monotonic()

    def set(self, state: str, failed: bool = False) -> None:
        with self._lock:
            self.state = state
            self.failures = self.failures + 1 if failed else 0
            self._state_since = time.monotonic()

    def healthy(self) -> bool:
        with self._lock:
            thread = self._thread
            state = self.state
            failures = self.failures
            since = self._state_since
        if state == "disabled":
            return True
        if thread is not None and not thread.is_alive():
            return False
        if state == "starting":
            return time.monotonic() - since < 20
        if state == "retrying":
            return failures < 3 and time.monotonic() - since < 20
        return state in {"waiting", "ready"}

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {"state": self.state, "consecutiveFailures": self.failures}


def _integer(value: object, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RuntimeError(f"invalid {name}")
    return value


def _canonical_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _schema_hash(objective_ids: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(len(objective_ids).to_bytes(8, "big"))
    for objective_id in objective_ids:
        encoded = objective_id.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _url(name: str, value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError(f"invalid reporter {name}")
    return value


def load_config() -> Config | None:
    names = {
        "game_id": os.environ.get("RSCTF_KOTH_GAME_ID", ""),
        "challenge_id": os.environ.get("RSCTF_KOTH_CHALLENGE_ID", ""),
        "secret": os.environ.get("RSCTF_KOTH_REPORTER_SECRET", ""),
        "context_url": os.environ.get("RSCTF_KOTH_CONTEXT_URL", ""),
        "observation_url": os.environ.get("RSCTF_KOTH_OBSERVATION_URL", ""),
    }
    if not any(names.values()):
        return None
    if not all(names.values()):
        raise RuntimeError("incomplete managed reporter environment")
    try:
        game_id = int(names["game_id"])
        challenge_id = int(names["challenge_id"])
    except ValueError as error:
        raise RuntimeError("invalid managed reporter scope") from error
    if game_id <= 0 or challenge_id <= 0:
        raise RuntimeError("invalid managed reporter scope")
    secret = names["secret"]
    if not secret.startswith("koth_target_") or len(secret) > 256:
        raise RuntimeError("invalid managed reporter credential")
    return Config(
        game_id=game_id,
        challenge_id=challenge_id,
        secret=secret,
        context_url=_url("context URL", names["context_url"]),
        observation_url=_url("observation URL", names["observation_url"]),
    )


def _json_request(request: urllib.request.Request, timeout: float) -> dict[str, Any]:
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        body = error.read(MAX_RESPONSE_BYTES + 1)
        detail = ""
        if len(body) <= MAX_RESPONSE_BYTES:
            try:
                value = json.loads(body)
                if isinstance(value, dict) and isinstance(value.get("title"), str):
                    detail = value["title"]
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        raise ReporterHTTPError(error.code, detail) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ReporterHTTPError(0, "control plane unavailable") from error
    if status < 200 or status >= 300:
        raise ReporterHTTPError(status, "unexpected control response")
    if len(body) > MAX_RESPONSE_BYTES:
        raise ReporterHTTPError(status, "control response too large")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReporterHTTPError(status, "control response malformed") from error
    if not isinstance(value, dict):
        raise ReporterHTTPError(status, "control response is not an object")
    return value


def _fetch_context(config: Config, prior: Context | None) -> Context:
    request = urllib.request.Request(
        config.context_url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "X-RSCTF-API-Version": "v2",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        value = _json_request(request, config.timeout_seconds)
    except ReporterHTTPError as error:
        if error.status == 409 and error.detail in {
            "Leaderboard KotH context is not active",
            "Leaderboard KotH context changed: fetch context and retry",
        }:
            raise ReporterInactive(error.status, error.detail) from error
        raise
    base_fields = {
        "apiVersion", "context", "cycleNumber", "cycleEndsAt", "resetAttempt", "roundNumber",
        "waveWindowStartsAt", "waveWindowEndsAt", "eligibleTokenHashes", "objectiveIds",
        "objectiveSchemaHash", "generatedAt",
    }
    version = value.get("apiVersion")
    expected_fields = base_fields if version == "v1" else base_fields | {"cycleStartsAt", "scoringEndsAt"}
    if set(value) != expected_fields or version not in {"v1", "v2"}:
        raise RuntimeError("unexpected managed KotH context shape")
    hashes = value["eligibleTokenHashes"]
    if (
        not _canonical_hash(value["context"])
        or not isinstance(hashes, list)
        or len(hashes) > MAX_TEAMS
        or any(not _canonical_hash(item) for item in hashes)
        or len(set(hashes)) != len(hashes)
    ):
        raise RuntimeError("invalid managed KotH roster")
    objective_ids = value["objectiveIds"]
    if objective_ids == []:
        if value["objectiveSchemaHash"] is not None:
            raise RuntimeError("objective schema hash without IDs")
    elif (
        objective_ids != list(OBJECTIVE_IDS)
        or not _canonical_hash(value["objectiveSchemaHash"])
        or value["objectiveSchemaHash"] != _schema_hash(OBJECTIVE_IDS)
    ):
        raise RuntimeError("unexpected managed objective schema")
    window_start = _integer(value["waveWindowStartsAt"], 0, 2**63 - 2, "wave window start")
    window_end = _integer(value["waveWindowEndsAt"], window_start + 1, 2**63 - 1, "wave window end")
    cycle_end = _integer(value["cycleEndsAt"], window_end, 2**63 - 1, "cycle end")
    cycle_number = _integer(value["cycleNumber"], 1, 2**31 - 1, "cycle number")
    reset_attempt = _integer(value["resetAttempt"], 0, 2**31 - 1, "reset attempt")
    round_number = _integer(value["roundNumber"], 1, 2**31 - 1, "round number")
    generated_at = _integer(value["generatedAt"], 1, 2**63 - 1, "context generation")
    if generated_at > time.time_ns() // 1_000_000 + MAX_FUTURE_MS:
        raise RuntimeError("future managed KotH context")
    if version == "v2":
        cycle_start = _integer(value["cycleStartsAt"], 0, window_start, "cycle start")
        scoring_end = _integer(value["scoringEndsAt"], cycle_start + 1, cycle_end, "scoring end")
    else:
        scoring_end = max(0, cycle_end - LEGACY_SETTLEMENT_LAG_MS)
    if prior is not None and (cycle_number, reset_attempt) == (prior.cycle_number, prior.reset_attempt):
        if generated_at < prior.generated_at:
            raise RuntimeError("regressed managed KotH context")
        if value["context"] != prior.opaque and generated_at <= prior.generated_at:
            raise RuntimeError("unfenced managed KotH context")
        floor = prior.scoring_floor
    elif version == "v2":
        floor = ((cycle_start + WAVE_DURATION_MS - 1) // WAVE_DURATION_MS) * WAVE_DURATION_MS
    else:
        floor = ((window_start if round_number == 1 else generated_at) + WAVE_DURATION_MS - 1) // WAVE_DURATION_MS * WAVE_DURATION_MS
    if version == "v2":
        if scoring_end <= cycle_start or window_end > scoring_end + 1:
            raise RuntimeError("inconsistent managed scoring bounds")
    if window_end > scoring_end + 1:
        raise RuntimeError("window exceeds managed scoring end")
    return Context(
        opaque=value["context"], cycle_number=cycle_number, reset_attempt=reset_attempt,
        round_number=round_number, window_start=window_start, window_end=window_end,
        cycle_end=cycle_end, scoring_end=scoring_end, scoring_floor=floor,
        generated_at=generated_at, eligible_hashes=frozenset(hashes),
        objective_ids=tuple(objective_ids), objective_schema_hash=value["objectiveSchemaHash"],
    )


def _snapshot_body(context: Context, snapshots: list[dict[str, object]], current_ms: int) -> bytes:
    waves: list[dict[str, object]] = []
    seen_starts: set[int] = set()
    previous_start: int | None = None
    for snapshot in snapshots:
        ended = snapshot.get("endedAtUnixMs")
        start = snapshot.get("waveStart")
        if (
            isinstance(start, bool) or not isinstance(start, int)
            or isinstance(ended, bool) or not isinstance(ended, int)
            or ended != start + WAVE_DURATION_MS
            or ended < context.scoring_floor
            or not context.window_start <= ended < context.window_end
            or ended > context.scoring_end
            or ended > current_ms - WAVE_FINALIZATION_LAG_MS
            or start % WAVE_DURATION_MS != 0
        ):
            continue
        if start in seen_starts or (previous_start is not None and start < previous_start):
            raise RuntimeError("native waves were duplicate or out of order")
        seen_starts.add(start)
        previous_start = start
        raw_teams = snapshot.get("teams")
        if not isinstance(raw_teams, list) or len(raw_teams) > MAX_TEAMS:
            raise RuntimeError("invalid native wave snapshot")
        teams: list[dict[str, object]] = []
        scores: list[tuple[str, Fraction]] = []
        seen_teams: set[str] = set()
        for raw in raw_teams:
            if not isinstance(raw, dict) or set(raw) != {"tokenHash", "earned", "possible"}:
                raise RuntimeError("invalid native team snapshot")
            identity = raw["tokenHash"]
            possible = _integer(raw["possible"], 1, 1_000_000_000_000, "possible")
            earned = _integer(raw["earned"], 0, possible, "earned")
            if not _canonical_hash(identity) or identity not in context.eligible_hashes:
                continue
            if identity in seen_teams:
                raise RuntimeError("native wave contained a duplicate team")
            seen_teams.add(identity)
            scores.append((identity, Fraction(earned, possible)))
            teams.append({
                "activity": {"earned": 1, "possible": 1},
                "isCrown": False,
                "objectives": [{"earned": earned, "possible": possible}],
                "tokenHash": identity,
            })
        best = max((score for _, score in scores), default=Fraction(0, 1))
        leaders = [identity for identity, score in scores if score == best and score > 0]
        crown = leaders[0] if len(leaders) == 1 else None
        for team in teams:
            team["isCrown"] = team["tokenHash"] == crown
        waves.append({"endedAtUnixMs": ended, "teams": sorted(teams, key=lambda x: x["tokenHash"]), "waveId": f"babel-{start}"})
    waves.sort(key=lambda item: item["endedAtUnixMs"])
    if len(waves) > MAX_WAVES:
        raise RuntimeError("native snapshot exceeds 64 waves")
    body = json.dumps({"context": context.opaque, "objectiveIds": list(OBJECTIVE_IDS), "waves": waves}, separators=(",", ":"), sort_keys=True).encode()
    if len(body) > MAX_SUBMISSION_BYTES:
        raise RuntimeError("native snapshot exceeds 512 KiB")
    return body


def _submit(config: Config, context: Context, body: bytes, last_timestamp: int) -> tuple[int, dict[str, Any]]:
    timestamp = max(time.time_ns() // 1_000_000, last_timestamp + 1)
    message = f"{timestamp}.{config.game_id}.{config.challenge_id}.".encode() + body
    signature = hmac.new(config.secret.encode(), message, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        config.observation_url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT,
            "X-RSCTF-Timestamp": str(timestamp), "X-RSCTF-Signature": f"sha256={signature}",
        },
    )
    try:
        value = _json_request(request, config.timeout_seconds)
    except ReporterHTTPError as error:
        if error.status == 409 and error.detail in {
            "Leaderboard KotH context is not active",
            "Leaderboard KotH context changed: fetch context and retry",
        }:
            raise ReporterInactive(error.status, error.detail) from error
        raise
    expected = {"accepted", "acceptedAt", "cycleNumber", "recognizedTeams", "resetAttempt", "roundNumber", "submittedTeams", "submittedWaves"}
    if set(value) != expected or value.get("accepted") is not True:
        raise RuntimeError("unexpected Leaderboard acknowledgement")
    decoded = json.loads(body)
    submitted_waves = len(decoded["waves"])
    submitted_teams = len({team["tokenHash"] for wave in decoded["waves"] for team in wave["teams"]})
    for key, minimum in (("acceptedAt", 1), ("cycleNumber", 1), ("resetAttempt", 0), ("roundNumber", 1), ("submittedWaves", 0), ("submittedTeams", 0), ("recognizedTeams", 0)):
        _integer(value[key], minimum, 2**63 - 1, key)
    if (
        value["cycleNumber"] != context.cycle_number
        or value["resetAttempt"] != context.reset_attempt
        or value["roundNumber"] != context.round_number
        or value["submittedWaves"] != submitted_waves
        or value["submittedTeams"] != submitted_teams
        or value["recognizedTeams"] != submitted_teams
    ):
        raise RuntimeError("incomplete Leaderboard acknowledgement")
    return timestamp, value


class ManagedReporter:
    def __init__(self, config: Config, snapshots: Callable[[], list[dict[str, object]]], runtime: ReporterRuntime):
        self.config = config
        self.snapshots = snapshots
        self.runtime = runtime
        self.context: Context | None = None
        self.last_digest: tuple[str, str] | None = None
        self.last_timestamp = 0

    def poll_once(self) -> None:
        context = _fetch_context(self.config, self.context)
        if self.context is None or (context.cycle_number, context.reset_attempt) != (
            self.context.cycle_number, self.context.reset_attempt
        ) or context.opaque != self.context.opaque:
            self.last_digest = None
        self.context = context
        body = _snapshot_body(context, self.snapshots(), time.time_ns() // 1_000_000)
        digest = hashlib.sha256(body).hexdigest()
        marker = (context.opaque, digest)
        if marker == self.last_digest:
            self.runtime.set("ready")
            return
        self.last_timestamp, _ = _submit(self.config, context, body, self.last_timestamp)
        self.last_digest = marker
        self.runtime.set("ready")

    def run(self) -> None:
        while True:
            try:
                self.runtime.set("starting")
                self.poll_once()
            except ReporterInactive:
                self.runtime.set("waiting")
            except Exception:
                self.runtime.set("retrying", failed=True)
            time.sleep(self.config.poll_seconds)


def start_managed_reporter(snapshots: Callable[[], list[dict[str, object]]]) -> ReporterRuntime:
    runtime = ReporterRuntime()
    try:
        config = load_config()
    except RuntimeError:
        runtime.set("retrying", failed=True)
        return runtime
    if config is None:
        runtime.set("disabled")
        return runtime
    reporter = ManagedReporter(config, snapshots, runtime)
    thread = threading.Thread(target=reporter.run, name="babel-managed-reporter", daemon=True)
    runtime.attach(thread)
    thread.start()
    return runtime
