"""Authoritative multiplayer HTTP server for the Serpent Circuit hill."""

from __future__ import annotations

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass, field
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
PROTOCOL = "serpent-circuit/5"
PROTOCOL_VERSION = 5
PORT = 8080
BOARD_SIZE = 100
START_POSITION = 0
FINISH_POSITION = 100
MIN_PLAYERS = 4
MAX_PLAYERS = 4
TURN_SECONDS = 30
MATCH_SECONDS = 5 * 60
MAX_MATCH_TURNS = 1_000
SCORING_WAVE_SECONDS = 60
WAVE_DURATION_MS = SCORING_WAVE_SECONDS * 1_000
OBJECTIVE_ID = "board-progress"
ROOM_CODE_LENGTH = 6
ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PLAYER_COLORS = ("coral", "blue", "green", "purple")
MAX_NAME_BYTES = 48
MAX_REQUEST_BYTES = 4 * 1_024
MAX_REQUEST_STREAM_BYTES = 12 * 1_024
MAX_RESPONSE_EVENTS = 1_000
MAX_EVENTS = 10_000
MAX_ROOMS = 500
MAX_SEATS = 2_000
MAX_ACTIONS_PER_ROOM = 256
LOBBY_TTL_MS = 30 * 60 * 1_000
QUEUE_SEAT_TTL_MS = 60 * 1_000
PLAYING_TTL_MS = 2 * 60 * 60 * 1_000
FINISHED_TTL_MS = 10 * 60 * 1_000
MAX_HTTP_WORKERS = 64
MAX_REGULAR_HTTP_WORKERS = 56
MAX_REQUESTS_PER_CLIENT = MAX_REGULAR_HTTP_WORKERS
MAX_PENDING_HTTP_REQUESTS = 128
CONNECTION_TIMEOUT_SECONDS = 4
ABSOLUTE_REQUEST_SECONDS = 6
MAX_CONTEXT_STALENESS_SECONDS = 8
TOKEN_PATTERN = re.compile(r"^koth_[A-Za-z0-9_-]{8,128}$")
ROOM_CODE_PATTERN = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
ACTION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
CANONICAL_HASH = re.compile(r"^[0-9a-f]{64}$")
STATIC_ROOT = Path(__file__).with_name("static")

# This board is original to Serpent Circuit. A transition is applied once,
# immediately after a legal die move, and the graph deliberately has no cycles.
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


@dataclass
class Player:
    player_id: str
    player_token: str | None
    token_hash: str | None
    name: str
    color: str
    position: int = START_POSITION
    last_seen_ms: int = 0


@dataclass
class Room:
    code: str
    context_opaque: str | None
    first_scorable_wave_start: int | None
    final_scoring_cutoff: int | None
    cycle_number: int
    reset_attempt: int
    created_at_ms: int
    last_activity_ms: int
    players: list[Player] = field(default_factory=list)
    status: str = "waiting"
    current_player_index: int = 0
    turn_deadline_ms: int | None = None
    match_deadline_ms: int | None = None
    turn_number: int = 0
    winner_id: str | None = None
    finished_at_ms: int | None = None
    score_status: str = "pending"
    last_move: dict[str, object] | None = None
    scoring_checkpoint_wave: int | None = None
    actions: OrderedDict[str, dict[str, object]] = field(default_factory=OrderedDict)


state_lock = threading.Lock()
rooms: OrderedDict[str, Room] = OrderedDict()
seat_index: dict[str, tuple[str, str]] = {}
active_team_seats: dict[tuple[int, int, str], str] = {}
pending_team_waves: dict[tuple[int, int, int], dict[str, int]] = {}
events: deque[dict[str, object]] = deque(maxlen=MAX_EVENTS)
next_cursor = 1
managed_context_lock = threading.Lock()
managed_context: ManagedContext | None = None
managed_lifecycle: tuple[int, int] | None = None
reporter_runtime = ReporterRuntime()
capability_authenticator = CapabilityAuthenticator()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def compact_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def wave_start(timestamp_ms: int) -> int:
    return timestamp_ms // WAVE_DURATION_MS * WAVE_DURATION_MS


def apply_roll(position: int, roll: int) -> tuple[int, int | None, int | None]:
    landing = position + roll
    if landing > FINISH_POSITION:
        landing = position
    destination = TRANSITIONS.get(landing)
    if destination is None:
        return landing, None, None
    return destination, landing, destination


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
        "maxPlayers": MAX_PLAYERS,
        "minPlayers": MIN_PLAYERS,
        "name": NAME,
        "objective": OBJECTIVE_ID,
        "protocolVersion": PROTOCOL_VERSION,
        "rules": [
            "Submit one current KotH capability to enter the server-controlled FIFO queue.",
            "Every match has exactly four distinct authenticated teams and starts automatically when it reaches the front of the queue.",
            "Players take turns rolling one server-generated six-sided die.",
            "Ladders climb and snakes slide immediately after a legal move.",
            "A roll must land exactly on 100; an overshoot leaves the piece in place.",
            "Rolling a six grants another turn, and an expired turn is rolled automatically.",
            "During a human-only match, each completed 60-second scoring tick records every team's furthest server-confirmed square in that tick.",
            "The first player to reach 100 wins; otherwise the sole player on the highest square wins when the five-minute match deadline expires.",
        ],
        "matchSeconds": MATCH_SECONDS,
        "scoringWaveSeconds": SCORING_WAVE_SECONDS,
        "startPosition": START_POSITION,
        "transitions": transitions,
        "turnSeconds": TURN_SECONDS,
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
    mode = runtime_mode()
    reporter, reporter_healthy = reporter_runtime.health_snapshot()
    ready = scoring_ready()
    healthy = mode != "misconfigured"
    if mode == "managed":
        healthy = reporter_healthy
    with state_lock:
        room_count = len(rooms)
        player_count = len(seat_index)
    return (
        200 if healthy else 503,
        {
            "mode": mode,
            "matchSeconds": MATCH_SECONDS,
            "name": NAME,
            "players": player_count,
            "protocol": PROTOCOL,
            "reporter": reporter,
            "rooms": room_count,
            "scoringReady": ready,
            "status": "ok" if healthy else "error",
            "turnSeconds": TURN_SECONDS,
        },
    )


def publish_managed_context(context: RoundContext | None) -> None:
    """Atomically publish only a context that rsctf accepted for scoring."""
    global managed_context, managed_lifecycle
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
            if context is not None:
                next_lifecycle = (context.cycle_number, context.reset_attempt)
                if managed_lifecycle is not None and managed_lifecycle != next_lifecycle:
                    for key in list(pending_team_waves):
                        if key[1:] != next_lifecycle:
                            pending_team_waves.pop(key, None)
                    for room_code, room in list(rooms.items()):
                        if (room.cycle_number, room.reset_attempt) != next_lifecycle:
                            _drop_room_locked(room_code)
                managed_lifecycle = next_lifecycle


def _current_managed_context() -> ManagedContext:
    with managed_context_lock:
        context = managed_context
    if context is None:
        raise GameError(503, "the scoring roster is not ready")
    if time.monotonic() - context.refreshed_at > MAX_CONTEXT_STALENESS_SECONDS:
        raise GameError(503, "the scoring roster is temporarily stale")
    return context


def _capability_candidate(token: str) -> CapabilityCandidate | None:
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
    context = _current_managed_context()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if token_hash not in context.eligible_hashes:
        return None
    return CapabilityCandidate(token_hash=token_hash, context=context, config=config)


def _confirm_capability(token: str, candidate: CapabilityCandidate) -> None:
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


def _validate_name(value: object) -> str:
    if not isinstance(value, str):
        raise GameError(400, "name must be a string")
    name = value.strip()
    if not name or len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise GameError(400, "name must contain 1 to 48 UTF-8 bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise GameError(400, "name contains unsupported control characters")
    return name


def _validate_player_token(value: object) -> str:
    if not isinstance(value, str) or not 16 <= len(value) <= 128:
        raise GameError(400, "invalid playerToken")
    return value


def _validate_action_id(value: object) -> str:
    if not isinstance(value, str) or not ACTION_ID_PATTERN.fullmatch(value):
        raise GameError(400, "invalid actionId")
    return value


def _candidate_lifecycle(candidate: CapabilityCandidate) -> tuple[int, int]:
    context = candidate.context
    return (
        context.cycle_number if context else 0,
        context.reset_attempt if context else 0,
    )


def _team_key(room: Room, token_hash: str) -> tuple[int, int, str]:
    return (room.cycle_number, room.reset_attempt, token_hash)


def _ensure_candidate_current_locked(candidate: CapabilityCandidate) -> ManagedContext | None:
    context = candidate.context
    if context is None:
        return None
    current = _current_managed_context()
    if (
        current.opaque != context.opaque
        or current.cycle_number != context.cycle_number
        or current.reset_attempt != context.reset_attempt
        or candidate.token_hash not in current.eligible_hashes
    ):
        raise GameError(503, "the scoring roster changed; retry the request")
    return current


def _managed_room_is_current(room: Room, timestamp_ms: int) -> None:
    if room.context_opaque is None:
        return
    context = _current_managed_context()
    if timestamp_ms >= context.cycle_ends_at:
        raise GameError(410, "the scoring cycle has ended")
    if (
        room.cycle_number != context.cycle_number
        or room.reset_attempt != context.reset_attempt
    ):
        raise GameError(410, "the scoring lifecycle changed")
    if any(player.token_hash not in context.eligible_hashes for player in room.players):
        raise GameError(403, "a room capability is no longer current")


def _room_expired(room: Room, timestamp_ms: int) -> bool:
    if room.status == "finished":
        return timestamp_ms - room.last_activity_ms >= FINISHED_TTL_MS
    if room.status == "playing":
        return timestamp_ms - room.last_activity_ms >= PLAYING_TTL_MS
    return timestamp_ms - room.last_activity_ms >= LOBBY_TTL_MS


def _drop_room_locked(room_code: str) -> None:
    room = rooms.pop(room_code, None)
    if room is None:
        return
    for player in room.players:
        if player.player_token is None or player.token_hash is None:
            raise RuntimeError("human player credentials are missing")
        seat_index.pop(player.player_token, None)
        key = _team_key(room, player.token_hash)
        if active_team_seats.get(key) == player.player_token:
            active_team_seats.pop(key, None)


def _room_wave_is_scoreable(room: Room, start_ms: int) -> bool:
    if room.context_opaque is None:
        return True
    with managed_context_lock:
        current_lifecycle = managed_lifecycle
    return (
        current_lifecycle == (room.cycle_number, room.reset_attempt)
        and room.first_scorable_wave_start is not None
        and room.final_scoring_cutoff is not None
        and start_ms >= room.first_scorable_wave_start
        and start_ms + WAVE_DURATION_MS <= room.final_scoring_cutoff
    )


def _record_room_progress_locked(room: Room, start_ms: int) -> bool:
    """Retain one bounded best position per human team for one native tick."""
    if (
        room.status != "playing"
        or len(room.players) < MIN_PLAYERS
        or not _room_wave_is_scoreable(room, start_ms)
    ):
        return False
    rows = pending_team_waves.setdefault(
        (start_ms, room.cycle_number, room.reset_attempt),
        {},
    )
    for player in room.players:
        if player.token_hash is None:
            raise RuntimeError("human player capability hash is missing")
        rows[player.token_hash] = max(rows.get(player.token_hash, 0), player.position)
    return True


def _checkpoint_room_progress_locked(room: Room, timestamp_ms: int) -> None:
    """Carry an unchanged active position across elapsed tick boundaries."""
    if room.status != "playing":
        return
    checkpoint = room.scoring_checkpoint_wave
    if checkpoint is None:
        checkpoint = wave_start(timestamp_ms)
        room.scoring_checkpoint_wave = checkpoint
        _record_room_progress_locked(room, checkpoint)
        return

    active_until = min(timestamp_ms + 1, room.last_activity_ms + PLAYING_TTL_MS)
    if room.match_deadline_ms is not None:
        active_until = min(active_until, room.match_deadline_ms + 1)
    if active_until <= checkpoint:
        return
    last_active_wave = wave_start(active_until - 1)
    while checkpoint < last_active_wave:
        checkpoint += WAVE_DURATION_MS
        _record_room_progress_locked(room, checkpoint)
    room.scoring_checkpoint_wave = max(room.scoring_checkpoint_wave, checkpoint)


def _finalize_team_waves_locked(timestamp_ms: int) -> None:
    """Append immutable, wave-ordered evidence only after a tick has ended."""
    global next_cursor
    ready = sorted(
        key
        for key in pending_team_waves
        if key[0] + WAVE_DURATION_MS <= timestamp_ms
    )
    for start_ms, cycle_number, reset_attempt in ready:
        rows = pending_team_waves.pop((start_ms, cycle_number, reset_attempt))
        for token_hash, earned in sorted(rows.items()):
            events.append(
                {
                    "cursor": next_cursor,
                    "cycleNumber": cycle_number,
                    "earned": earned,
                    "occurredAt": start_ms + WAVE_DURATION_MS - 1,
                    "possible": FINISH_POSITION,
                    "resetAttempt": reset_attempt,
                    "tokenHash": token_hash,
                    "waveStart": start_ms,
                }
            )
            next_cursor += 1


def _checkpoint_scoring_locked(timestamp_ms: int) -> None:
    for room in list(rooms.values()):
        _checkpoint_room_progress_locked(room, timestamp_ms)
        _finish_expired_match_locked(room, timestamp_ms)
        if room.status == "playing" and not _room_expired(room, timestamp_ms):
            _advance_expired_turn_locked(room, timestamp_ms)
    _finalize_team_waves_locked(timestamp_ms)


def _remove_waiting_player_locked(room: Room, player: Player) -> None:
    if player.player_token is None or player.token_hash is None:
        raise RuntimeError("human player credentials are missing")
    seat_index.pop(player.player_token, None)
    key = _team_key(room, player.token_hash)
    if active_team_seats.get(key) == player.player_token:
        active_team_seats.pop(key, None)
    room.players.remove(player)
    for index, remaining in enumerate(room.players):
        remaining.color = PLAYER_COLORS[index]


def _prune_waiting_players_locked(timestamp_ms: int) -> None:
    for room_code, room in list(rooms.items()):
        if room.status != "waiting":
            continue
        for player in list(room.players):
            if timestamp_ms - player.last_seen_ms >= QUEUE_SEAT_TTL_MS:
                _remove_waiting_player_locked(room, player)
        if not room.players:
            _drop_room_locked(room_code)


def _prune_state_locked(timestamp_ms: int) -> None:
    _prune_waiting_players_locked(timestamp_ms)
    _checkpoint_scoring_locked(timestamp_ms)
    _start_next_match_locked(timestamp_ms)
    for room_code, room in list(rooms.items()):
        if _room_expired(room, timestamp_ms):
            _drop_room_locked(room_code)
    while len(rooms) > MAX_ROOMS:
        _drop_room_locked(next(iter(rooms)))


def _new_room_code_locked() -> str:
    for _attempt in range(128):
        code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
        if code not in rooms:
            return code
    raise GameError(503, "room codes are temporarily exhausted")


def _new_secret(existing: object) -> str:
    for _attempt in range(128):
        value = secrets.token_urlsafe(24)
        if value not in existing:
            return value
    raise GameError(503, "player identifiers are temporarily exhausted")


def _find_player(room: Room, player_id: str) -> Player:
    for player in room.players:
        if player.player_id == player_id:
            return player
    raise GameError(404, "unknown player")


def _authorized_room_locked(player_token: str) -> tuple[Room, Player]:
    indexed = seat_index.get(player_token)
    if indexed is None:
        raise GameError(403, "invalid match player token")
    room = rooms.get(indexed[0])
    if room is None:
        raise GameError(404, "match not found")
    return room, _find_player(room, indexed[1])


def _queue_ahead_locked(room: Room) -> int:
    if room.status != "waiting":
        return 0
    ahead = 0
    for candidate in rooms.values():
        if candidate is room:
            break
        if candidate.status == "playing" or (
            candidate.status == "waiting" and len(candidate.players) == MAX_PLAYERS
        ):
            ahead += 1
    return ahead


def _room_document(room: Room, player: Player, timestamp_ms: int) -> dict[str, object]:
    current_id = (
        room.players[room.current_player_index].player_id
        if room.status == "playing" and room.players
        else None
    )
    return {
        "currentPlayerId": current_id,
        "lastMove": deepcopy(room.last_move),
        "matchDeadline": room.match_deadline_ms,
        "maxPlayers": MAX_PLAYERS,
        "minPlayers": MIN_PLAYERS,
        "players": [
            {
                "color": item.color,
                "id": item.player_id,
                "name": item.name,
                "position": item.position,
            }
            for item in room.players
        ],
        "queueAhead": _queue_ahead_locked(room),
        "scoreStatus": room.score_status,
        "serverTime": timestamp_ms,
        "status": room.status,
        "turnDeadline": room.turn_deadline_ms,
        "turnNumber": room.turn_number,
        "winnerId": room.winner_id,
        "you": {"id": player.player_id},
    }


def _seat_response(room: Room, player: Player, timestamp_ms: int) -> dict[str, object]:
    if player.player_token is None:
        raise RuntimeError("a human player token is missing")
    return {
        "playerToken": player.player_token,
        "room": _room_document(room, player, timestamp_ms),
    }


def _existing_active_seat_locked(
    candidate: CapabilityCandidate,
    timestamp_ms: int,
) -> dict[str, object] | None:
    cycle, reset = _candidate_lifecycle(candidate)
    key = (cycle, reset, candidate.token_hash)
    player_token = active_team_seats.get(key)
    if player_token is None:
        return None
    indexed = seat_index.get(player_token)
    if indexed is None:
        active_team_seats.pop(key, None)
        return None
    room = rooms.get(indexed[0])
    if room is None or room.status == "finished":
        active_team_seats.pop(key, None)
        return None
    player = _find_player(room, indexed[1])
    player.last_seen_ms = timestamp_ms
    return _seat_response(room, player, timestamp_ms)


def matchmake(token: str, name: object, timestamp_ms: int | None = None) -> dict[str, object]:
    name = _validate_name(name)
    candidate = _capability_candidate(token)
    if candidate is None:
        raise GameError(403, "invalid current KotH capability")
    _confirm_capability(token, candidate)

    with state_lock:
        timestamp_ms = now_ms() if timestamp_ms is None else timestamp_ms
        _prune_state_locked(timestamp_ms)
        context = _ensure_candidate_current_locked(candidate)
        existing = _existing_active_seat_locked(candidate, timestamp_ms)
        if existing is not None:
            return existing
        if len(rooms) >= MAX_ROOMS or len(seat_index) >= MAX_SEATS:
            raise GameError(503, "the matchmaking capacity is full")

        lifecycle = _candidate_lifecycle(candidate)
        room = next(
            (
                queued
                for queued in rooms.values()
                if queued.status == "waiting"
                and len(queued.players) < MAX_PLAYERS
                and (queued.cycle_number, queued.reset_attempt) == lifecycle
            ),
            None,
        )
        if room is None:
            code = _new_room_code_locked()
            room = Room(
                code=code,
                context_opaque=context.opaque if context else None,
                first_scorable_wave_start=(
                    context.first_scorable_wave_start if context else None
                ),
                final_scoring_cutoff=context.final_scoring_cutoff if context else None,
                cycle_number=context.cycle_number if context else 0,
                reset_attempt=context.reset_attempt if context else 0,
                created_at_ms=timestamp_ms,
                last_activity_ms=timestamp_ms,
            )
            rooms[code] = room
        player_ids = {player.player_id for room in rooms.values() for player in room.players}
        player_id = _new_secret(player_ids)
        player_token = _new_secret(seat_index)
        player = Player(
            player_id=player_id,
            player_token=player_token,
            token_hash=candidate.token_hash,
            name=name,
            color=PLAYER_COLORS[len(room.players)],
            last_seen_ms=timestamp_ms,
        )
        room.players.append(player)
        room.last_activity_ms = timestamp_ms
        seat_index[player_token] = (room.code, player_id)
        active_team_seats[_team_key(room, candidate.token_hash)] = player_token
        _start_next_match_locked(timestamp_ms)
        return _seat_response(room, player, timestamp_ms)


def _cache_action(room: Room, action_id: str, response: dict[str, object]) -> None:
    room.actions[action_id] = deepcopy(response)
    room.actions.move_to_end(action_id)
    while len(room.actions) > MAX_ACTIONS_PER_ROOM:
        room.actions.popitem(last=False)


def _finish_match_locked(
    room: Room,
    timestamp_ms: int,
    winner_id: str | None,
    *,
    start_next_at_ms: int | None = None,
) -> None:
    _record_room_progress_locked(room, wave_start(timestamp_ms))
    room.status = "finished"
    room.winner_id = winner_id
    room.finished_at_ms = timestamp_ms
    room.turn_deadline_ms = None
    room.match_deadline_ms = None
    for player in room.players:
        if player.player_token is None or player.token_hash is None:
            raise RuntimeError("human player credentials are missing")
        key = _team_key(room, player.token_hash)
        if active_team_seats.get(key) == player.player_token:
            active_team_seats.pop(key, None)
    if _room_wave_is_scoreable(room, wave_start(timestamp_ms)):
        room.score_status = "recorded"
    else:
        room.score_status = "outside-scoring-window"
    _start_next_match_locked(
        timestamp_ms if start_next_at_ms is None else start_next_at_ms
    )


def _start_next_match_locked(timestamp_ms: int) -> None:
    if any(room.status == "playing" for room in rooms.values()):
        return
    room = next(
        (
            queued
            for queued in rooms.values()
            if queued.status == "waiting" and len(queued.players) == MAX_PLAYERS
        ),
        None,
    )
    if room is None:
        return
    room.status = "playing"
    room.current_player_index = 0
    room.match_deadline_ms = timestamp_ms + MATCH_SECONDS * 1_000
    room.turn_deadline_ms = min(
        timestamp_ms + TURN_SECONDS * 1_000,
        room.match_deadline_ms,
    )
    room.last_activity_ms = timestamp_ms
    room.scoring_checkpoint_wave = wave_start(timestamp_ms)
    _record_room_progress_locked(room, room.scoring_checkpoint_wave)


def _finish_expired_match_locked(room: Room, timestamp_ms: int) -> None:
    if (
        room.status != "playing"
        or room.match_deadline_ms is None
        or timestamp_ms < room.match_deadline_ms
    ):
        return
    highest = max(player.position for player in room.players)
    leaders = [player for player in room.players if player.position == highest]
    winner_id = leaders[0].player_id if len(leaders) == 1 else None
    _finish_match_locked(
        room,
        room.match_deadline_ms,
        winner_id,
        start_next_at_ms=timestamp_ms,
    )


def _roll_locked(room: Room, timestamp_ms: int, *, automatic: bool) -> None:
    if room.status != "playing":
        raise GameError(409, "the match is not active")
    player = room.players[room.current_player_index]
    origin = player.position
    roll = secrets.randbelow(6) + 1
    position, transport_from, transport_to = apply_roll(origin, roll)
    player.position = position
    room.turn_number += 1
    _record_room_progress_locked(room, wave_start(timestamp_ms))

    won = position == FINISH_POSITION
    extra_turn = roll == 6 and not won
    if not won and room.turn_number < MAX_MATCH_TURNS:
        if not extra_turn:
            room.current_player_index = (room.current_player_index + 1) % len(room.players)
        if room.match_deadline_ms is None:
            raise RuntimeError("an active match deadline is missing")
        room.turn_deadline_ms = min(
            timestamp_ms + TURN_SECONDS * 1_000,
            room.match_deadline_ms,
        )

    room.last_move = {
        "automatic": automatic,
        "extraTurn": extra_turn,
        "from": origin,
        "playerId": player.player_id,
        "roll": roll,
        "to": position,
        "transportFrom": transport_from,
        "transportTo": transport_to,
    }
    if not automatic:
        room.last_activity_ms = timestamp_ms
    if won:
        _finish_match_locked(room, timestamp_ms, player.player_id)
    elif room.turn_number >= MAX_MATCH_TURNS:
        _finish_match_locked(room, timestamp_ms, None)


def _advance_expired_turn_locked(room: Room, timestamp_ms: int) -> None:
    if (
        room.status == "playing"
        and room.turn_deadline_ms is not None
        and timestamp_ms >= room.turn_deadline_ms
    ):
        _roll_locked(room, timestamp_ms, automatic=True)


def room_state(player_token: object, timestamp_ms: int | None = None) -> dict[str, object]:
    token = _validate_player_token(player_token)
    with state_lock:
        timestamp_ms = now_ms() if timestamp_ms is None else timestamp_ms
        _prune_state_locked(timestamp_ms)
        room, player = _authorized_room_locked(token)
        _managed_room_is_current(room, timestamp_ms)
        player.last_seen_ms = timestamp_ms
        room.last_activity_ms = timestamp_ms
        return _room_document(room, player, timestamp_ms)


def roll_room(
    player_token: object,
    action_id: object,
    timestamp_ms: int | None = None,
) -> dict[str, object]:
    token = _validate_player_token(player_token)
    action = _validate_action_id(action_id)
    with state_lock:
        timestamp_ms = now_ms() if timestamp_ms is None else timestamp_ms
        _prune_state_locked(timestamp_ms)
        room, player = _authorized_room_locked(token)
        _managed_room_is_current(room, timestamp_ms)
        player.last_seen_ms = timestamp_ms
        cached = room.actions.get(action)
        if cached is not None:
            return deepcopy(cached)
        _advance_expired_turn_locked(room, timestamp_ms)
        if room.status != "playing":
            raise GameError(409, "the match is not active")
        current = room.players[room.current_player_index]
        if current.player_id != player.player_id:
            raise GameError(409, "wait for your turn")

        _roll_locked(room, timestamp_ms, automatic=False)
        response = _room_document(room, player, timestamp_ms)
        _cache_action(room, action, response)
        return response


def read_evidence_page(
    after: int,
    current_time_ms: int | None = None,
) -> dict[str, object]:
    """Return one bounded immutable page to the in-process managed reporter."""
    with state_lock:
        _checkpoint_scoring_locked(
            now_ms() if current_time_ms is None else current_time_ms
        )
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
        self.rfile = _BoundedReader(self.rfile, self.server.request_stream_bytes)

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

    def _handle_action(self, expected_keys: set[str], action, success_status: int = 200) -> None:
        value = self._read_json(expected_keys)
        if value is None:
            return
        try:
            result = action(value)
        except GameError as error:
            self._json(error.status, {"error": error.message})
            return
        except RuntimeError:
            self._json(503, {"error": "managed gameplay is misconfigured"})
            return
        self._json(success_status, result)

    @staticmethod
    def _token(value: object) -> str:
        if not isinstance(value, str):
            raise GameError(400, "token must be a string")
        return value

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler contract
        request = urllib.parse.urlsplit(self.path)
        if request.query:
            self._json(400, {"error": "query parameters are not accepted"})
            return
        if request.path == "/api/matchmaking":
            self._handle_action(
                {"name", "token"},
                lambda value: matchmake(self._token(value["token"]), value["name"]),
                201,
            )
            return
        if request.path == "/api/matchmaking/state":
            self._handle_action(
                {"playerToken"},
                lambda value: room_state(value["playerToken"]),
            )
            return
        if request.path == "/api/matchmaking/roll":
            self._handle_action(
                {"actionId", "playerToken"},
                lambda value: roll_room(value["playerToken"], value["actionId"]),
            )
            return
        self._json(404, {"error": "not found"})

    def log_message(self, _format, *_args):
        # Request bodies may contain current capabilities or room session tokens.
        pass


class Server(BoundedThreadingHTTPServer):
    max_workers = MAX_HTTP_WORKERS
    max_regular_workers = MAX_REGULAR_HTTP_WORKERS
    max_requests_per_client = MAX_REQUESTS_PER_CLIENT
    max_pending_requests = MAX_PENDING_HTTP_REQUESTS
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
    start_managed_reporter(read_evidence_page, publish_managed_context, reporter_runtime)
    Server(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
