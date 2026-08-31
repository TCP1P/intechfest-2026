"""Bounded, coalesced RSCTF player-capability authentication."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import http.client
import json
import socket
import threading
import time
from typing import Any
import urllib.parse


AUTH_REJECTION_CACHE_ENTRIES = 2_000
AUTH_REJECTION_SECONDS = 1.0
AUTH_MAX_IN_FLIGHT = 24
AUTH_MAX_PENDING = 64
AUTH_MAX_WAITERS = 64
AUTH_QUEUE_SECONDS = 1.5
AUTH_REQUEST_SECONDS = 3.0
AUTH_RESPONSE_BYTES = 8 * 1_024
AUTH_RESPONSE_HEADER_BYTES = 16 * 1_024
USER_AGENT = "serpent-circuit-managed-target/1"


class CapabilityRejected(RuntimeError):
    """The platform rejected a syntactically valid capability."""


class CapabilityUnavailable(RuntimeError):
    """The bounded platform authentication path could not complete."""


class _BoundedHeaderReader:
    """Bound status and header bytes before http.client buffers them."""

    def __init__(self, raw, limit: int):
        self.raw = raw
        self.remaining = limit
        self.bounded = True

    def readline(self, size: int = -1) -> bytes:
        if not self.bounded:
            return self.raw.readline(size)
        available = self.remaining + 1
        requested = available if size < 0 else min(size, available)
        data = self.raw.readline(requested)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise http.client.HTTPException(
                "capability response headers exceeded their size limit"
            )
        return data

    def __getattr__(self, name: str):
        return getattr(self.raw, name)


class _BoundedHeaderResponse(http.client.HTTPResponse):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fp = _BoundedHeaderReader(self.fp, AUTH_RESPONSE_HEADER_BYTES)

    def begin(self) -> None:
        super().begin()
        self.fp.bounded = False


@dataclass
class _Flight:
    ready: threading.Event
    outcome: str | None = None


class CapabilityAuthenticator:
    """Authenticate without retaining raw tokens or allowing unbounded fan-out."""

    def __init__(self):
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(AUTH_MAX_IN_FLIGHT)
        self._rejections: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._flights: dict[tuple[str, str], _Flight] = {}
        self._waiting_followers = 0

    def reset(self) -> None:
        """Clear process-local authentication state for deterministic tests."""
        with self._lock:
            self._rejections.clear()
            self._flights.clear()
            self._waiting_followers = 0

    def authenticate(
        self,
        *,
        platform_url: str,
        game_id: int,
        challenge_id: int,
        token: str,
        token_hash: str,
        context: str,
    ) -> None:
        key = (context, token_hash)
        now = time.monotonic()
        with self._lock:
            expires_at = self._rejections.get(key)
            if expires_at is not None:
                if expires_at > now:
                    self._rejections.move_to_end(key)
                    raise CapabilityRejected("invalid current KotH capability")
                self._rejections.pop(key, None)

            flight = self._flights.get(key)
            if flight is None:
                if len(self._flights) >= AUTH_MAX_PENDING:
                    raise CapabilityUnavailable("capability authentication queue is full")
                flight = _Flight(threading.Event())
                self._flights[key] = flight
                leader = True
            else:
                if self._waiting_followers >= AUTH_MAX_WAITERS:
                    raise CapabilityUnavailable(
                        "capability authentication waiters are full"
                    )
                self._waiting_followers += 1
                leader = False

        if not leader:
            try:
                if not flight.ready.wait(
                    AUTH_QUEUE_SECONDS + AUTH_REQUEST_SECONDS + 0.5
                ):
                    raise CapabilityUnavailable("capability authentication timed out")
            finally:
                with self._lock:
                    self._waiting_followers -= 1
            self._raise_for_outcome(flight.outcome or "unavailable")
            return

        try:
            outcome = self._bounded_upstream_outcome(
                platform_url=platform_url,
                game_id=game_id,
                challenge_id=challenge_id,
                token=token,
                expected_team_id=token_hash,
            )
        except BaseException:
            # This boundary never lets transport/parser defects escape through
            # request handling with the raw token still present in frame locals.
            outcome = "unavailable"
        finally:
            with self._lock:
                if outcome == "rejected":
                    self._rejections[key] = (
                        time.monotonic() + AUTH_REJECTION_SECONDS
                    )
                    self._rejections.move_to_end(key)
                    while len(self._rejections) > AUTH_REJECTION_CACHE_ENTRIES:
                        self._rejections.popitem(last=False)
                flight.outcome = outcome
                flight.ready.set()
                self._flights.pop(key, None)

        self._raise_for_outcome(outcome)

    def _bounded_upstream_outcome(
        self,
        *,
        platform_url: str,
        game_id: int,
        challenge_id: int,
        token: str,
        expected_team_id: str,
    ) -> str:
        """Resolve/connect without the token, then bound the sensitive exchange."""
        if not self._slots.acquire(timeout=AUTH_QUEUE_SECONDS):
            return "unavailable"

        deadline_at = time.monotonic() + AUTH_REQUEST_SECONDS
        finished = threading.Event()
        ownership_lock = threading.Lock()
        state: dict[str, object] = {
            "cancelled": False,
            "connection": None,
        }

        def connect_without_capability() -> None:
            connection: http.client.HTTPConnection | None = None
            try:
                connection = self._connect_upstream(platform_url)
            except BaseException:
                pass

            caller_owns_connection = False
            with ownership_lock:
                if connection is not None and not state["cancelled"]:
                    state["connection"] = connection
                    caller_owns_connection = True
            if not caller_owns_connection:
                if connection is not None:
                    connection.close()
                self._slots.release()
            finished.set()

        worker = threading.Thread(
            target=connect_without_capability,
            name="serpent-capability-connect",
            daemon=True,
        )
        try:
            worker.start()
        except BaseException:
            self._slots.release()
            return "unavailable"

        remaining = max(0.0, deadline_at - time.monotonic())
        if not finished.wait(remaining):
            with ownership_lock:
                state["cancelled"] = True
                connection = state["connection"]
                state["connection"] = None
            if connection is not None:
                connection.close()
                self._slots.release()
            return "unavailable"

        with ownership_lock:
            connection = state["connection"]
            state["connection"] = None
        if connection is None:
            return "unavailable"
        if time.monotonic() >= deadline_at:
            connection.close()
            self._slots.release()
            return "unavailable"

        try:
            self._authenticate_upstream(
                connection=connection,
                deadline_at=deadline_at,
                game_id=game_id,
                challenge_id=challenge_id,
                token=token,
                expected_team_id=expected_team_id,
            )
            return "accepted"
        except CapabilityRejected:
            return "rejected"
        except BaseException:
            return "unavailable"
        finally:
            connection.close()
            self._slots.release()

    @staticmethod
    def _raise_for_outcome(outcome: str) -> None:
        if outcome == "accepted":
            return
        if outcome == "rejected":
            raise CapabilityRejected("invalid current KotH capability")
        raise CapabilityUnavailable("capability authentication is unavailable")

    @staticmethod
    def _connect_upstream(platform_url: str) -> http.client.HTTPConnection:
        """Complete the untrusted DNS, TCP, and TLS stages without a token."""
        parsed = urllib.parse.urlsplit(platform_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise CapabilityUnavailable("capability service URL is invalid")
        try:
            port = parsed.port
        except ValueError as error:
            raise CapabilityUnavailable("capability service URL is invalid") from error

        connection_type = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            parsed.hostname,
            port,
            timeout=AUTH_REQUEST_SECONDS,
        )
        connection.response_class = _BoundedHeaderResponse
        try:
            connection.connect()
        except BaseException:
            connection.close()
            raise
        return connection

    @staticmethod
    def _authenticate_upstream(
        *,
        connection: http.client.HTTPConnection,
        deadline_at: float,
        game_id: int,
        challenge_id: int,
        token: str,
        expected_team_id: str,
    ) -> None:
        body = json.dumps(
            {
                "challengeId": challenge_id,
                "gameId": game_id,
                "token": token,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        remaining = deadline_at - time.monotonic()
        active_socket = connection.sock
        if remaining <= 0 or active_socket is None:
            raise CapabilityUnavailable("capability service deadline expired")
        active_socket.settimeout(remaining)
        deadline_reached = threading.Event()

        def abort_at_deadline() -> None:
            deadline_reached.set()
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        deadline = threading.Timer(remaining, abort_at_deadline)
        deadline.daemon = True
        deadline.start()
        try:
            connection.request(
                "POST",
                "/api/v1/koth/capability/authenticate",
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            response = connection.getresponse()
            status = response.status
            content_type = response.headers.get_content_type()
            raw = response.read(AUTH_RESPONSE_BYTES + 1)
            if deadline_reached.is_set() or time.monotonic() >= deadline_at:
                raise CapabilityUnavailable("capability service deadline expired")
        except (TimeoutError, http.client.HTTPException, OSError) as error:
            raise CapabilityUnavailable("capability service did not answer") from error
        finally:
            deadline.cancel()

        if status == 401:
            raise CapabilityRejected("invalid current KotH capability")
        if status != 200 or content_type != "application/json":
            raise CapabilityUnavailable("capability service returned an invalid response")
        if len(raw) > AUTH_RESPONSE_BYTES:
            raise CapabilityUnavailable("capability response exceeded its size limit")
        try:
            value: Any = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CapabilityUnavailable("capability service returned invalid JSON") from error
        if not isinstance(value, dict) or set(value) != {"teamId", "teamName"}:
            raise CapabilityUnavailable("capability service returned an invalid identity")
        team_name = value["teamName"]
        if (
            value["teamId"] != expected_team_id
            or not isinstance(team_name, str)
            or not team_name.strip()
            or len(team_name) > 256
            or any(ord(character) < 32 for character in team_name)
        ):
            raise CapabilityUnavailable("capability service returned an invalid identity")
