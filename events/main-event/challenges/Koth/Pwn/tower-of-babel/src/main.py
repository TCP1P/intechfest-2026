#!/usr/bin/env python3
"""Shared Tower of Babel managed-Leaderboard KotH coordinator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from fractions import Fraction
from typing import BinaryIO, Callable

from reporter import start_managed_reporter


WAVE_DURATION_MS = 60_000
WAVE_FINALIZATION_LAG_MS = 2_000
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_LINE_BYTES = 70 * 1024
MAX_ATTEMPTS_PER_TEAM = 12
MAX_PATTERNS = 8
MAX_PATTERN_BYTES = 32
MAX_PATCH_ATTEMPTS = 3
RELAY_TIMEOUT_SECONDS = 5
MAX_ACTIVE_CLIENTS = 32
MAX_ACTIVE_RUNNERS = 8
TEAM_TOKEN_RE = re.compile(r"^koth_[A-Za-z0-9_-]{8,128}$")
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

ARCHITECTURES = {
    "Doctor": {
        "arch": "amd64",
        "syscalls": {"openat": 257, "read": 0, "write": 1, "close": 3, "execve": 59},
        "syscall_hex": "0f05",
    },
    "Kal'tsit": {
        "arch": "aarch64",
        "syscalls": {"openat": 56, "read": 63, "write": 64, "close": 57, "execve": 221},
        "syscall_hex": "010000d4",
    },
    "Theresa": {
        "arch": "riscv64",
        "syscalls": {"openat": 56, "read": 63, "write": 64, "close": 57, "execve": 221},
        "syscall_hex": "73000000",
    },
    "Civilight Eterna": {
        "arch": "mips64be",
        "syscalls": {"open": 5002, "read": 5000, "write": 5001, "close": 5003, "execve": 5057},
        "syscall_hex": "0000000c",
    },
}
ARCH_BY_ID = {value["arch"]: name for name, value in ARCHITECTURES.items()}
CLIENT_SLOTS = threading.BoundedSemaphore(MAX_ACTIVE_CLIENTS)
RUNNER_SLOTS = threading.BoundedSemaphore(MAX_ACTIVE_RUNNERS)


@dataclass(frozen=True)
class StageSpec:
    name: str
    arch: str
    secret_path: str
    runner_path: str
    next_runner: str


@dataclass(frozen=True)
class Attempt:
    depth: int
    payload_bytes: int
    complete: bool = False
    patch: tuple[tuple[str, str], ...] = ()

    @property
    def possible(self) -> int:
        return 5 * (MAX_PAYLOAD_BYTES + 1)

    @property
    def earned(self) -> int:
        if self.depth <= 0:
            return 0
        efficiency = max(0, MAX_PAYLOAD_BYTES - self.payload_bytes)
        return min(self.possible, self.depth * (MAX_PAYLOAD_BYTES + 1) + efficiency)

    @property
    def score(self) -> Fraction:
        return Fraction(self.earned, self.possible)


@dataclass
class Wave:
    start_ms: int
    wave_id: str
    stages: tuple[StageSpec, ...]
    active_patch: tuple[tuple[str, str], ...]
    attempts: dict[str, Attempt] = field(default_factory=dict)
    attempt_counts: dict[str, int] = field(default_factory=dict)
    finalized: bool = False
    crown_hash: str | None = None
    first_solver_team: str | None = None
    first_solver_payload: str | None = None
    first_solver_at_ms: int | None = None
    patch_owner: str | None = None
    patch_revision: int = 0
    ward_status: str = "empty"

    @property
    def end_ms(self) -> int:
        return self.start_ms + WAVE_DURATION_MS


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def aligned_wave_start(timestamp_ms: int) -> int:
    return timestamp_ms - (timestamp_ms % WAVE_DURATION_MS)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _patch_public(patterns: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"arch": arch, "hex": value} for arch, value in patterns]


def _pattern_contains_required_syscall(arch: str, pattern: bytes) -> bool:
    required = bytes.fromhex(ARCHITECTURES[ARCH_BY_ID[arch]]["syscall_hex"])
    return any(
        required[offset : offset + len(pattern)] == pattern
        for offset in range(len(required) - len(pattern) + 1)
    )


def validate_patch(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or set(value) != {"patterns"}:
        raise ValueError("patch must contain only patterns")
    raw_patterns = value["patterns"]
    if not isinstance(raw_patterns, list) or len(raw_patterns) > MAX_PATTERNS:
        raise ValueError("too many patch patterns")
    total = 0
    normalized: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_patterns:
        if not isinstance(raw, dict) or set(raw) != {"arch", "hex"}:
            raise ValueError("malformed patch pattern")
        arch = raw["arch"]
        encoded = raw["hex"]
        if not isinstance(arch, str) or arch not in ARCH_BY_ID:
            raise ValueError("unknown patch architecture")
        if not isinstance(encoded, str) or not re.fullmatch(r"[0-9a-fA-F]{4,16}", encoded):
            raise ValueError("patch bytes must be 2 to 8 bytes of hex")
        encoded = encoded.lower()
        pattern = bytes.fromhex(encoded)
        if _pattern_contains_required_syscall(arch, pattern):
            raise ValueError("a patch may not block a mandatory syscall opcode")
        item = (arch, encoded)
        if item in seen:
            raise ValueError("duplicate patch pattern")
        seen.add(item)
        normalized.append(item)
        total += len(pattern)
    if total > MAX_PATTERN_BYTES:
        raise ValueError("patch byte budget exceeded")
    return tuple(sorted(normalized))


def _new_wave(
    start_ms: int,
    active_patch: tuple[tuple[str, str], ...],
    patch_revision: int = 0,
    patch_owner: str | None = None,
) -> Wave:
    order = list(ARCHITECTURES)
    secrets.SystemRandom().shuffle(order)
    nonce = secrets.token_hex(5)
    stages: list[StageSpec] = []
    for index, name in enumerate(order):
        stage_dir = f"/tmp/babel/{nonce}"
        runner_path = f"{stage_dir}/runner-{index}"
        secret_path = f"{stage_dir}/secret-{index}"
        next_runner = f"{stage_dir}/runner-{index + 1}" if index + 1 < len(order) else ""
        stages.append(StageSpec(name, ARCHITECTURES[name]["arch"], secret_path, runner_path, next_runner))
    return Wave(
        start_ms,
        f"babel-{start_ms}",
        tuple(stages),
        active_patch,
        patch_owner=patch_owner,
        patch_revision=patch_revision,
        ward_status="active" if active_patch else "empty",
    )


class ArenaState:
    """Own all shared wave state; raw team capabilities never enter this object."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._waves: dict[int, Wave] = {}
        self._active_patch: tuple[tuple[str, str], ...] = ()
        self._patch_owner: str | None = None
        self._patch_revision = 0
        self._latest_start: int | None = None
        self._instance_id = secrets.token_hex(6)

    def _ensure_current_locked(self, timestamp_ms: int) -> Wave:
        current_start = aligned_wave_start(timestamp_ms)
        if self._latest_start is None:
            self._latest_start = current_start
            self._waves[current_start] = _new_wave(
                current_start, self._active_patch, self._patch_revision, self._patch_owner
            )
        elif current_start > self._latest_start:
            for start in range(self._latest_start + WAVE_DURATION_MS, current_start + 1, WAVE_DURATION_MS):
                self._finalize_locked(self._waves[self._latest_start], timestamp_ms)
                self._latest_start = start
                self._waves[start] = _new_wave(
                    start, self._active_patch, self._patch_revision, self._patch_owner
                )
        self._finalize_due_locked(timestamp_ms)
        return self._waves[current_start]

    def _finalize_due_locked(self, timestamp_ms: int) -> None:
        for wave in self._waves.values():
            if not wave.finalized and timestamp_ms >= wave.end_ms + WAVE_FINALIZATION_LAG_MS:
                self._finalize_locked(wave, timestamp_ms)

    def _finalize_locked(self, wave: Wave, timestamp_ms: int) -> None:
        if wave.finalized:
            return
        positive = [(team, attempt) for team, attempt in wave.attempts.items() if attempt.earned > 0]
        crown: str | None = None
        if positive:
            best = max(attempt.score for _, attempt in positive)
            leaders = [team for team, attempt in positive if attempt.score == best]
            if len(leaders) == 1:
                crown = leaders[0]
        wave.crown_hash = crown
        wave.finalized = True

    @staticmethod
    def _ward_document(wave: Wave) -> dict[str, object]:
        return {
            "status": wave.ward_status,
            "revision": wave.patch_revision,
            "effectiveWave": wave.wave_id if wave.active_patch else None,
            "owner": wave.patch_owner,
            "fallback": "previous-known-good" if wave.ward_status == "rejected" else None,
        }

    def current(self) -> Wave:
        with self._lock:
            return self._ensure_current_locked(now_ms())

    def info(self) -> dict[str, object]:
        with self._lock:
            wave = self._ensure_current_locked(now_ms())
            return {
                "protocol": "babel-koth-v2",
                "instanceId": self._instance_id,
                "waveId": wave.wave_id,
                "waveEndsAtUnixMs": wave.end_ms,
                "order": [stage.name for stage in wave.stages],
                "stages": [
                    {
                        "name": stage.name,
                        "arch": stage.arch,
                        "secretPath": stage.secret_path,
                        "runnerPath": stage.runner_path,
                        "nextRunner": stage.next_runner,
                    }
                    for stage in wave.stages
                ],
                "activeWard": _patch_public(wave.active_patch),
                "ward": self._ward_document(wave),
                "limits": {
                    "maxPayloadBytes": MAX_PAYLOAD_BYTES,
                    "maxAttemptsPerTeam": MAX_ATTEMPTS_PER_TEAM,
                    "maxPatchPatterns": MAX_PATTERNS,
                    "maxPatchBytes": MAX_PATTERN_BYTES,
                    "maxPatchAttempts": MAX_PATCH_ATTEMPTS,
                },
                "mandatorySyscallHex": {
                    value["arch"]: value["syscall_hex"] for value in ARCHITECTURES.values()
                },
            }

    def can_attempt(self, wave: Wave, team: str) -> bool:
        with self._lock:
            return wave.attempt_counts.get(team, 0) < MAX_ATTEMPTS_PER_TEAM and not wave.finalized

    @staticmethod
    def _better(candidate: Attempt, prior: Attempt | None) -> bool:
        if prior is None or candidate.score > prior.score:
            return True
        if prior is None or candidate.score < prior.score:
            return False
        if candidate.payload_bytes != prior.payload_bytes:
            return candidate.payload_bytes < prior.payload_bytes
        return (candidate.complete and not prior.complete) or (bool(candidate.patch) and not bool(prior.patch))

    def record(self, wave: Wave, team: str, attempt: Attempt) -> None:
        with self._lock:
            if wave.finalized:
                return
            wave.attempt_counts[team] = wave.attempt_counts.get(team, 0) + 1
            prior = wave.attempts.get(team)
            if self._better(attempt, prior):
                wave.attempts[team] = attempt

    def update_best(self, wave: Wave, team: str, attempt: Attempt) -> None:
        with self._lock:
            if wave.finalized:
                return
            prior = wave.attempts.get(team)
            if self._better(attempt, prior):
                wave.attempts[team] = attempt

    def register_first_solver(self, wave: Wave, team: str, payload: str, solved_at_ms: int) -> bool:
        with self._lock:
            if wave.finalized or wave.first_solver_team is not None:
                return False
            wave.first_solver_team = team
            wave.first_solver_payload = payload
            wave.first_solver_at_ms = solved_at_ms
            return True

    def can_patch(self, wave: Wave, team: str) -> bool:
        with self._lock:
            return not wave.finalized and wave.first_solver_team == team

    def is_first_solver(self, wave: Wave, team: str) -> bool:
        with self._lock:
            return wave.first_solver_team == team and not wave.finalized

    def reject_patch(self, wave: Wave) -> None:
        with self._lock:
            if not wave.finalized:
                wave.ward_status = "rejected"

    def activate_patch(
        self,
        wave: Wave,
        team: str,
        patch: tuple[tuple[str, str], ...],
    ) -> int | None:
        with self._lock:
            if wave.finalized or wave.first_solver_team != team:
                return None
            self._patch_revision += 1
            self._active_patch = patch
            self._patch_owner = team
            wave.active_patch = patch
            wave.patch_owner = team
            wave.patch_revision = self._patch_revision
            wave.ward_status = "active" if patch else "empty"
            return self._patch_revision

    def status(self, wave: Wave, team: str | None = None) -> dict[str, object]:
        with self._lock:
            self._ensure_current_locked(now_ms())
            result: dict[str, object] = {
                "waveId": wave.wave_id,
                "waveEndsAtUnixMs": wave.end_ms,
                "activeWard": _patch_public(wave.active_patch),
                "ward": self._ward_document(wave),
                "finalized": wave.finalized,
                "attemptedTeams": len(wave.attempts),
            }
            if team is not None and team in wave.attempts:
                attempt = wave.attempts[team]
                result["yourBest"] = {
                    "depth": attempt.depth,
                    "payloadBytes": attempt.payload_bytes,
                    "earned": attempt.earned,
                    "possible": attempt.possible,
                    "complete": attempt.complete,
                }
            return result

    def snapshot_for_reporter(self) -> list[dict[str, object]]:
        with self._lock:
            self._ensure_current_locked(now_ms())
            waves: list[dict[str, object]] = []
            for wave in sorted(self._waves.values(), key=lambda item: item.start_ms):
                if not wave.finalized:
                    continue
                teams = [
                    {
                        "tokenHash": team,
                        "earned": attempt.earned,
                        "possible": attempt.possible,
                    }
                    for team, attempt in sorted(wave.attempts.items())
                    if attempt.earned > 0
                ]
                waves.append({"waveStart": wave.start_ms, "endedAtUnixMs": wave.end_ms, "teams": teams})
            # Keep a bounded history; rsctf's context window is authoritative.
            for start in sorted(self._waves)[:-128]:
                del self._waves[start]
            return waves


class AuthenticationError(RuntimeError):
    pass


def _valid_team_name(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 256


def authenticate_capability(token: str) -> tuple[str, str]:
    if not TEAM_TOKEN_RE.fullmatch(token):
        raise AuthenticationError("invalid KotH capability")
    base = os.environ.get("RSCTF_KOTH_PLATFORM_URL", "").strip()
    if not base:
        if os.environ.get("BABEL_LOCAL_DEV") == "1":
            identity = token_hash(token)
            return identity, f"local-{identity[:8]}"
        raise AuthenticationError("KotH authentication is not available")
    try:
        game_id = int(os.environ["RSCTF_KOTH_GAME_ID"])
        challenge_id = int(os.environ["RSCTF_KOTH_CHALLENGE_ID"])
    except (KeyError, ValueError) as error:
        raise AuthenticationError("KotH authentication is not configured") from error
    body = json.dumps({"token": token, "gameId": game_id, "challengeId": challenge_id}, separators=(",", ":")).encode()
    request = urllib.request.Request(
        urllib.parse.urljoin(base.rstrip("/") + "/", "api/v1/koth/capability/authenticate"),
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            raw = response.read(64 * 1024 + 1)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        if isinstance(error, urllib.error.HTTPError) and error.code in {400, 401, 403}:
            raise AuthenticationError("invalid KotH capability") from error
        raise AuthenticationError("KotH authentication unavailable") from error
    if len(raw) > 64 * 1024:
        raise AuthenticationError("KotH authentication response too large")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AuthenticationError("KotH authentication response malformed") from error
    if (
        not isinstance(value, dict)
        or set(value) != {"teamId", "teamName"}
        or not isinstance(value["teamId"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["teamId"])
        or not _valid_team_name(value["teamName"])
    ):
        raise AuthenticationError("KotH authentication response malformed")
    return value["teamId"], value["teamName"].strip()


def _safe_child_environment() -> dict[str, str]:
    # Never pass RSCTF_KOTH_* reporter credentials or callback URLs to the VM runner.
    return {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/tmp"}


def _write_runner_config(session: "Session", patch: tuple[tuple[str, str], ...]) -> str:
    handle = tempfile.NamedTemporaryFile("w", prefix="babel-session-", suffix=".cfg", delete=False)
    try:
        for index, stage in enumerate(session.wave.stages):
            handle.write(
                "|".join((
                    "STAGE", str(index), stage.name, stage.arch, stage.secret_path,
                    session.stage_secrets[index], stage.runner_path, stage.next_runner,
                ))
                + "\n"
            )
        for arch, encoded in patch:
            handle.write(f"PATCH|{arch}|{encoded}\n")
        handle.flush()
        return handle.name
    finally:
        handle.close()


def execute_relay(
    session: "Session",
    encoded: str,
    patch_override: tuple[tuple[str, str], ...] | None = None,
) -> tuple[int, str, list[tuple[str, str]]]:
    payload = bytes.fromhex(encoded)
    if not RUNNER_SLOTS.acquire(timeout=1):
        return 0, "busy", []
    config_path: str | None = None
    try:
        patch = session.wave.active_patch if patch_override is None else patch_override
        config_path = _write_runner_config(session, patch)
        runner = os.environ.get("BABEL_RUNNER", "/app/babel-runner")
        process = subprocess.Popen(
            [runner, config_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_safe_child_environment(),
            text=True,
        )
        try:
            stdout, _ = process.communicate(encoded.lower() + "\n", timeout=RELAY_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
            return 0, "timeout", []
    except (OSError, ValueError):
        return 0, "runner-error", []
    finally:
        if config_path is not None:
            try:
                os.unlink(config_path)
            except OSError:
                pass
        RUNNER_SLOTS.release()
    stages: list[tuple[str, str]] = []
    result: tuple[int, int, str] | None = None
    for line in stdout.splitlines()[:32]:
        fields = line.split("|")
        if len(fields) == 3 and fields[0] == "BABEL_STAGE" and len(stages) < 4:
            stages.append((fields[1], fields[2]))
        elif len(fields) == 5 and fields[0] == "BABEL_RESULT":
            try:
                result = (int(fields[1]), int(fields[2]), fields[3])
            except ValueError:
                result = None
    if result is None or result[1] != len(payload) or not 0 <= result[0] <= 4:
        return 0, "runner-error", []
    status = result[2]
    if status == "complete" and result[0] != 4:
        status = "runner-error"
    if any(name != session.wave.stages[index].name for index, (name, _) in enumerate(stages)):
        return 0, "runner-error", []
    return result[0], status, stages


@dataclass
class Session:
    team_hash: str
    team_name: str
    wave: Wave
    stage_secrets: tuple[str, ...]
    attempts: int = 0
    last_attempt: Attempt | None = None
    last_relay_complete: bool = False
    last_payload: str | None = None
    first_solver: bool = False
    patch_attempts: int = 0


HELP_TEXT = (
    "HELLO <koth-capability> | INFO | RELAY <hex> | AUTH <secret-sequence> | "
    "PATCH {\"patterns\":[{\"arch\":\"amd64\",\"hex\":\"4889\"}]} | STATUS | QUIT"
)


def _json_line(prefix: str, value: object) -> bytes:
    return (prefix + json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def protocol_session(reader: BinaryIO, writer: BinaryIO, state: ArenaState) -> None:
    def send(value: str | bytes) -> None:
        data = value.encode() if isinstance(value, str) else value
        writer.write(data if data.endswith(b"\n") else data + b"\n")
        writer.flush()

    send("BABEL/2 Tower of Babel — managed Leaderboard KotH")
    send("Send PING, INFO, or HELLO <current KotH capability>.")
    session: Session | None = None
    while True:
        raw = reader.readline(MAX_LINE_BYTES + 1)
        if not raw:
            return
        if len(raw) > MAX_LINE_BYTES:
            send("ERR line-too-large")
            return
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            send("ERR input-not-utf8")
            continue
        if not line:
            continue
        command, _, argument = line.partition(" ")
        command = command.upper()
        if command == "PING" and not argument:
            send("PONG")
        elif command == "HELP" and not argument:
            send(HELP_TEXT)
        elif command == "INFO" and not argument:
            send(_json_line("INFO ", state.info()))
        elif command == "HELLO" and argument and session is None:
            try:
                team_hash, team_name = authenticate_capability(argument)
            except AuthenticationError as error:
                send(f"ERR auth: {error}")
                continue
            wave = state.current()
            session = Session(
                team_hash,
                team_name,
                wave,
                tuple(secrets.token_hex(8) for _ in wave.stages),
            )
            send(f"WELCOME {team_hash}")
            send(_json_line("INFO ", state.info()))
        elif command == "RELAY" and argument and session is not None:
            if session.wave.start_ms != state.current().start_ms:
                send("ERR wave-expired")
                continue
            if session.attempts >= MAX_ATTEMPTS_PER_TEAM or not state.can_attempt(session.wave, session.team_hash):
                send("ERR attempt-limit")
                continue
            if len(argument) > MAX_PAYLOAD_BYTES * 2 or len(argument) % 2 or not HEX_RE.fullmatch(argument):
                send("ERR payload-hex")
                continue
            if len(argument) == 0:
                send("ERR payload-empty")
                continue
            session.attempts += 1
            depth, status, stage_lines = execute_relay(session, argument)
            attempt = Attempt(depth, len(argument) // 2)
            session.last_attempt = attempt
            session.last_payload = argument.lower()
            # Count every bounded execution against the per-team budget; zero-depth
            # probes are not emitted as scoring rows but cannot be used for DoS.
            session.last_relay_complete = depth == 4 and status == "complete"
            if not session.last_relay_complete:
                state.record(session.wave, session.team_hash, attempt)
            for name, secret in stage_lines:
                send(f"STAGE {name} {secret}")
            send(_json_line("RELAY_RESULT ", {
                "depth": depth,
                "payloadBytes": len(argument) // 2,
                "status": status,
                "attemptsRemaining": MAX_ATTEMPTS_PER_TEAM - session.attempts,
            }))
            if depth == 4 and status == "complete":
                send("AUTH_REQUIRED")
        elif command == "AUTH" and argument and session is not None:
            if (
                session.last_attempt is None
                or session.last_attempt.depth != 4
                or not session.last_relay_complete
                or not re.fullmatch(r"[A-Za-z0-9_-]+", argument)
            ):
                send("ERR no-complete-relay")
                continue
            expected = "".join(session.stage_secrets)
            if not secrets.compare_digest(argument, expected):
                send("AUTH_FAIL")
                continue
            session.last_attempt = Attempt(
                session.last_attempt.depth,
                session.last_attempt.payload_bytes,
                complete=True,
                patch=session.last_attempt.patch,
            )
            state.record(session.wave, session.team_hash, session.last_attempt)
            session.first_solver = state.register_first_solver(
                session.wave, session.team_hash, session.last_payload or "", now_ms()
            ) or state.is_first_solver(session.wave, session.team_hash)
            session.last_relay_complete = False
            send("RELAY_ACCEPTED")
            if session.first_solver:
                send("PATCH_WINDOW first verified solver may submit a canary-tested PATCH")
            else:
                send("PATCH_LOCKED first verified solver already owns the patch window")
        elif command == "PATCH" and argument and session is not None:
            if session.last_attempt is None or not session.last_attempt.complete:
                send("ERR complete-relay-required")
                continue
            if not state.can_patch(session.wave, session.team_hash):
                send("ERR patch-locked")
                continue
            if session.patch_attempts >= MAX_PATCH_ATTEMPTS:
                send("ERR patch-attempt-limit")
                continue
            try:
                patch = validate_patch(json.loads(argument))
            except (ValueError, json.JSONDecodeError):
                send("ERR invalid-patch")
                continue
            session.patch_attempts += 1
            canary_payload = session.wave.first_solver_payload
            if not canary_payload:
                send("ERR canary-unavailable")
                continue
            canary_depth, canary_status, _ = execute_relay(
                session, canary_payload, patch_override=patch
            )
            if canary_depth != 4 or canary_status != "complete":
                state.reject_patch(session.wave)
                send(_json_line("PATCH_REJECTED ", {
                    "status": "rejected",
                    "fallback": "previous-known-good",
                    "canaryDepth": canary_depth,
                    "canaryStatus": canary_status,
                    "activePatterns": _patch_public(session.wave.active_patch),
                }))
                continue
            revision = state.activate_patch(session.wave, session.team_hash, patch)
            if revision is None:
                send("ERR wave-expired")
                continue
            session.last_attempt = Attempt(
                session.last_attempt.depth,
                session.last_attempt.payload_bytes,
                complete=True,
                patch=patch,
            )
            state.update_best(session.wave, session.team_hash, session.last_attempt)
            send(_json_line("PATCH_ACCEPTED ", {
                "patterns": _patch_public(patch),
                "applies": "current-wave-and-later",
                "revision": revision,
                "canaryDepth": canary_depth,
            }))
        elif command == "STATUS" and not argument:
            send(_json_line("STATUS ", state.status(session.wave, session.team_hash if session else None)))
        elif command == "QUIT" and not argument:
            send("BYE")
            return
        else:
            send("ERR command; use HELP")


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(state: ArenaState, host: str, port: int) -> None:
    # The reporter is owned by this shared coordinator, never by a per-client
    # worker. It receives only immutable native snapshots and its child runner
    # environment is filtered separately below.
    start_managed_reporter(state.snapshot_for_reporter)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            if not CLIENT_SLOTS.acquire(blocking=False):
                try:
                    self.wfile.write(b"ERR busy\n")
                    self.wfile.flush()
                except OSError:
                    pass
                return
            try:
                protocol_session(self.rfile, self.wfile, state)
            except (BrokenPipeError, ConnectionResetError, OSError):
                # A bounded health/client probe may close immediately after its
                # assertion; this is not a service fault and must not log a trace.
                return
            finally:
                CLIENT_SLOTS.release()

    server = _ThreadingTCPServer((host, port), Handler)
    server.request_queue_size = 64
    with server:
        # serve_forever is intentionally the only long-running loop in the target.
        server.serve_forever()


def health_probe() -> int:
    port = int(os.environ.get("PORT_LISTENER", "1094"))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            greeting = bytearray()
            while greeting.count(b"\n") < 2 and len(greeting) < 512:
                chunk = connection.recv(128)
                if not chunk:
                    return 1
                greeting.extend(chunk)
            connection.sendall(b"PING\n")
            response = bytearray()
            while b"\n" not in response and len(response) < 64:
                chunk = connection.recv(64)
                if not chunk:
                    break
                response.extend(chunk)
    except (OSError, ValueError):
        return 1
    return 0 if response.splitlines()[:1] == [b"PONG"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        return health_probe()
    state = ArenaState()
    if args.serve:
        try:
            serve(state, "0.0.0.0", int(os.environ.get("PORT_LISTENER", "1094")))
        except (OSError, ValueError):
            return 1
        return 0
    protocol_session(sys.stdin.buffer, sys.stdout.buffer, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
