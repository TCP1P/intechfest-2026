#!/usr/bin/env python3
"""Bounded draft editor with one intentional relative-path overwrite."""

from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import socket
import stat
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
PAGES_DIR = Path(os.environ.get("DRAFT_PAGES_DIR", "/srv/pages"))
PUBLIC_PAGES = ("brief.txt", "status.txt")
MAX_REQUEST_BYTES = 4096
MAX_PATH_BYTES = 128
MAX_CONTENT_BYTES = 256
MAX_REQUEST_STREAM_BYTES = 12 * 1024
MAX_PENDING_REQUESTS = 128
REQUEST_TIMEOUT_SECONDS = 4
MAX_ACTIVE_REQUESTS = 64
MAX_REGULAR_REQUESTS = 56
MAX_REQUESTS_PER_CLIENT = 16
LISTEN_BACKLOG = MAX_PENDING_REQUESTS + MAX_ACTIVE_REQUESTS
ABSOLUTE_REQUEST_SECONDS = 6
CLASSIFIER_POLL_SECONDS = 0.025
WRITE_LOCK = threading.Lock()

PROTECTED_REQUEST_LINES = {
    b"GET /health HTTP/1.0",
    b"GET /health HTTP/1.1",
    b"GET /api/info HTTP/1.0",
    b"GET /api/info HTTP/1.1",
    b"GET /api/preview?path=brief.txt HTTP/1.0",
    b"GET /api/preview?path=brief.txt HTTP/1.1",
}


class ApiError(Exception):
    """A bounded client-visible API error."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class _BoundedReader:
    """Bound request-line, header, and body buffering for one connection."""

    def __init__(self, raw, limit: int):
        self.raw = raw
        self.remaining = limit

    def _size(self, requested: int) -> int:
        available = self.remaining + 1
        return available if requested < 0 else min(requested, available)

    def _account(self, data: bytes) -> bytes:
        self.remaining -= len(data)
        if self.remaining < 0:
            raise OSError("request stream limit exceeded")
        return data

    def read(self, size: int = -1) -> bytes:
        return self._account(self.raw.read(self._size(size)))

    def readline(self, size: int = -1) -> bytes:
        return self._account(self.raw.readline(self._size(size)))

    def __getattr__(self, name: str):
        return getattr(self.raw, name)


def validate_relative_path(value: object) -> str:
    """Accept a short relative POSIX path, including the intentional `..` flaw."""
    if not isinstance(value, str):
        raise ApiError(400, "path must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ApiError(400, "path must be valid UTF-8") from error
    if not encoded or len(encoded) > MAX_PATH_BYTES:
        raise ApiError(400, "path length is invalid")
    if "\\" in value or any(ord(character) < 32 for character in value):
        raise ApiError(400, "path contains invalid characters")
    if PurePosixPath(value).is_absolute():
        raise ApiError(400, "path must be relative")
    return value


def validate_content(value: object) -> str:
    """Accept exactly one bounded UTF-8 line."""
    if not isinstance(value, str):
        raise ApiError(400, "content must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ApiError(400, "content must be valid UTF-8") from error
    if not encoded or len(encoded) > MAX_CONTENT_BYTES or "\n" in value or "\r" in value:
        raise ApiError(400, "content must be one non-empty line of at most 256 bytes")
    return value


def replace_existing_draft(pages_dir: Path, relative_path: str, content: str) -> None:
    """Atomically replace an existing file relative to the draft root.

    The missing containment check is the challenge's intentional vulnerability.
    """
    target = pages_dir / relative_path
    encoded = content.encode("utf-8")

    with WRITE_LOCK:
        try:
            # The final path must already be a regular file. Reject a symlink
            # instead of following it into another writable boundary.
            target_stat = target.lstat()
        except (FileNotFoundError, NotADirectoryError) as error:
            raise ApiError(404, "draft does not exist") from error
        except OSError as error:
            raise ApiError(409, "draft cannot be inspected") from error
        if not stat.S_ISREG(target_stat.st_mode):
            raise ApiError(404, "draft does not exist")

        temporary_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                dir=target.parent,
                prefix=".draft-",
            )
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
        except OSError as error:
            raise ApiError(409, "draft could not be saved") from error
        finally:
            if temporary_path is not None:
                try:
                    Path(temporary_path).unlink()
                except FileNotFoundError:
                    pass


def read_public_page(pages_dir: Path, page_name: str) -> str:
    """Read one fixed public page without accepting a traversal path."""
    if page_name not in PUBLIC_PAGES:
        raise ApiError(404, "preview is not available")
    try:
        data = (pages_dir / page_name).read_bytes()
    except OSError as error:
        raise ApiError(409, "preview is unavailable") from error
    if len(data) > MAX_CONTENT_BYTES:
        raise ApiError(409, "preview is too large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ApiError(409, "preview is not UTF-8") from error


def service_is_ready(pages_dir: Path) -> bool:
    """Check the public data and intended marker target without mutating either."""
    try:
        for page_name in PUBLIC_PAGES:
            if not (pages_dir / page_name).is_file():
                return False
            read_public_page(pages_dir, page_name)
        marker = pages_dir / "../../koth/king"
        marker_stat = marker.lstat()
        return stat.S_ISREG(marker_stat.st_mode) and os.access(marker, os.W_OK)
    except (ApiError, OSError):
        return False


class BoundedHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with a small fixed concurrency ceiling."""

    daemon_threads = True
    # Leave room for protected requests to finish their TCP handshake while
    # the classifier evicts an older silent connection from its bounded set.
    request_queue_size = LISTEN_BACKLOG
    max_workers = MAX_ACTIVE_REQUESTS
    max_regular_workers = MAX_REGULAR_REQUESTS
    max_requests_per_client = MAX_REQUESTS_PER_CLIENT
    max_pending_requests = MAX_PENDING_REQUESTS
    absolute_request_seconds = ABSOLUTE_REQUEST_SECONDS

    def __init__(self, server_address, handler_class):
        self._request_slots = threading.BoundedSemaphore(self.max_workers)
        self._regular_slots = threading.BoundedSemaphore(self.max_regular_workers)
        self._client_lock = threading.Lock()
        self._client_requests: dict[str, int] = {}
        self._admissions: dict[int, tuple[str, bool, float]] = {}
        self._pending_lock = threading.Lock()
        self._pending_requests: OrderedDict[
            int,
            tuple[socket.socket, tuple[str, int], float],
        ] = OrderedDict()
        self._classifier_stop = threading.Event()
        self._classifier_wakeup = threading.Event()
        super().__init__(server_address, handler_class)
        self._classifier_thread = threading.Thread(
            target=self._classify_pending_requests,
            name="draft-header-classifier",
            daemon=True,
        )
        self._classifier_thread.start()

    def get_request(self):
        request, client_address = super().get_request()
        request.setblocking(False)
        return request, client_address

    def process_request(self, request, client_address):
        deadline = time.monotonic() + self.absolute_request_seconds
        evicted = None
        rejected = False
        with self._pending_lock:
            if self._classifier_stop.is_set():
                rejected = True
            else:
                if len(self._pending_requests) >= self.max_pending_requests:
                    _, evicted = self._pending_requests.popitem(last=False)
                self._pending_requests[id(request)] = (request, client_address, deadline)
        if rejected:
            self.shutdown_request(request)
            return
        if evicted is not None:
            self.shutdown_request(evicted[0])
        self._classifier_wakeup.set()

    def _classify_pending_requests(self):
        while not self._classifier_stop.is_set():
            self._classifier_wakeup.clear()
            with self._pending_lock:
                pending = list(self._pending_requests.items())

            now = time.monotonic()
            for request_id, entry in pending:
                request, client_address, deadline = entry
                state = "reject" if now >= deadline else self._classify_request(request)
                if state == "pending":
                    continue
                claimed = self._take_pending_request(request_id, request)
                if claimed is None:
                    continue
                if state in {"protected", "regular"} and not self._classifier_stop.is_set():
                    self._promote_request(
                        request,
                        client_address,
                        deadline,
                        protected=state == "protected",
                    )
                else:
                    self.shutdown_request(request)

            with self._pending_lock:
                nearest_deadline = min(
                    (entry[2] for entry in self._pending_requests.values()),
                    default=None,
                )
            wait_seconds = CLASSIFIER_POLL_SECONDS
            if nearest_deadline is not None:
                wait_seconds = min(
                    wait_seconds,
                    max(0.0, nearest_deadline - time.monotonic()),
                )
            self._classifier_wakeup.wait(wait_seconds if pending else 0.5)

    @staticmethod
    def _classify_request(request: socket.socket) -> str:
        try:
            peeked = request.recv(
                MAX_REQUEST_STREAM_BYTES + 1,
                socket.MSG_PEEK | socket.MSG_DONTWAIT,
            )
        except BlockingIOError:
            return "pending"
        except OSError:
            return "reject"
        if not peeked or len(peeked) > MAX_REQUEST_STREAM_BYTES:
            return "reject"
        if b"\r\n\r\n" not in peeked:
            return "pending"
        request_line = peeked.split(b"\r\n", 1)[0]
        return "protected" if request_line in PROTECTED_REQUEST_LINES else "regular"

    def _take_pending_request(self, request_id: int, request: socket.socket):
        with self._pending_lock:
            entry = self._pending_requests.get(request_id)
            if entry is None or entry[0] is not request:
                return None
            del self._pending_requests[request_id]
            return entry

    def _promote_request(self, request, client_address, deadline, *, protected):
        if time.monotonic() >= deadline:
            self.shutdown_request(request)
            return
        try:
            request.settimeout(REQUEST_TIMEOUT_SECONDS)
        except OSError:
            self.shutdown_request(request)
            return
        client = str(client_address[0])
        if not protected:
            with self._client_lock:
                active = self._client_requests.get(client, 0)
                if active >= self.max_requests_per_client:
                    self.shutdown_request(request)
                    return
                self._client_requests[client] = active + 1
        regular_acquired = protected or self._regular_slots.acquire(blocking=False)
        total_acquired = regular_acquired and self._request_slots.acquire(blocking=False)
        if not total_acquired:
            if regular_acquired and not protected:
                self._regular_slots.release()
            if not protected:
                self._release_client(client)
            self.shutdown_request(request)
            return
        with self._client_lock:
            self._admissions[id(request)] = (client, not protected, deadline)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_admission(request)
            raise

    def process_request_thread(self, request, client_address):
        with self._client_lock:
            admission = self._admissions.get(id(request))
        deadline_at = admission[2] if admission is not None else time.monotonic()
        deadline = threading.Timer(
            max(0.0, deadline_at - time.monotonic()),
            self._close_expired_request,
            args=(request,),
        )
        deadline.daemon = True
        try:
            deadline.start()
            super().process_request_thread(request, client_address)
        finally:
            deadline.cancel()
            self._release_admission(request)

    @staticmethod
    def _close_expired_request(request: socket.socket) -> None:
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _release_client(self, client: str) -> None:
        with self._client_lock:
            remaining = self._client_requests.get(client, 1) - 1
            if remaining <= 0:
                self._client_requests.pop(client, None)
            else:
                self._client_requests[client] = remaining

    def _release_admission(self, request: socket.socket) -> None:
        with self._client_lock:
            admission = self._admissions.pop(id(request), None)
        if admission is None:
            return
        client, used_regular_slot, _deadline = admission
        if used_regular_slot:
            self._release_client(client)
            self._regular_slots.release()
        self._request_slots.release()

    def server_close(self):
        self._classifier_stop.set()
        self._classifier_wakeup.set()
        self._classifier_thread.join()
        with self._pending_lock:
            pending = list(self._pending_requests.values())
            self._pending_requests.clear()
        for request, _client_address, _deadline in pending:
            self.shutdown_request(request)
        super().server_close()

    def handle_error(self, _request, _client_address):
        # Hostile disconnects must not create unbounded traceback output.
        pass


class Handler(BaseHTTPRequestHandler):
    """Player-visible HTTP API."""

    server_version = "DraftControl/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)
        self.rfile = _BoundedReader(self.rfile, MAX_REQUEST_STREAM_BYTES)

    def handle(self):
        try:
            super().handle()
        except OSError:
            self.close_connection = True

    def send_body(self, status: int, content_type: str, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
        self.send_body(status, "application/json; charset=utf-8", body)

    def send_error_json(self, error: ApiError) -> None:
        self.send_json(error.status, {"error": error.message})

    def read_json_object(self) -> dict:
        if self.headers.get("Transfer-Encoding") is not None:
            raise ApiError(400, "transfer encoding is not supported")
        length_value = self.headers.get("Content-Length")
        if length_value is None:
            raise ApiError(411, "content length is required")
        try:
            length = int(length_value)
        except ValueError as error:
            raise ApiError(400, "content length is invalid") from error
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ApiError(413, "request body is too large")
        try:
            body = self.rfile.read(length)
        except (TimeoutError, OSError) as error:
            raise ApiError(408, "request body timed out") from error
        if len(body) != length:
            raise ApiError(400, "request body is incomplete")
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(400, "request body must be JSON") from error
        if not isinstance(value, dict):
            raise ApiError(400, "request body must be an object")
        return value

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        request = urlsplit(self.path)
        try:
            if request.path == "/health":
                if service_is_ready(PAGES_DIR):
                    self.send_body(200, "text/plain; charset=utf-8", b"ok\n")
                else:
                    self.send_body(503, "text/plain; charset=utf-8", b"unavailable\n")
                return
            if request.path == "/":
                self.send_body(
                    200,
                    "text/plain; charset=utf-8",
                    b"Draft Control warmup\nInspect GET /api/info.\n",
                )
                return
            if request.path == "/api/info":
                self.send_json(
                    200,
                    {
                        "draftRoot": "/srv/pages",
                        "name": "Draft Control",
                        "pages": list(PUBLIC_PAGES),
                        "preview": "GET /api/preview?path=brief.txt",
                        "status": "ready",
                        "update": "POST /api/drafts",
                        "writePolicy": "relative path; existing regular files only",
                    },
                )
                return
            if request.path == "/api/preview":
                values = parse_qs(request.query, keep_blank_values=True).get("path", [])
                if len(values) != 1:
                    raise ApiError(400, "one preview path is required")
                page_name = values[0]
                content = read_public_page(PAGES_DIR, page_name)
                self.send_json(200, {"content": content, "path": page_name})
                return
            raise ApiError(404, "not found")
        except ApiError as error:
            self.send_error_json(error)

    def do_POST(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        request = urlsplit(self.path)
        try:
            if request.path != "/api/drafts":
                raise ApiError(404, "not found")
            value = self.read_json_object()
            if set(value) != {"content", "path"}:
                raise ApiError(400, "request requires only path and content")
            relative_path = validate_relative_path(value["path"])
            content = validate_content(value["content"])
            replace_existing_draft(PAGES_DIR, relative_path, content)
            self.send_json(
                200,
                {
                    "bytes": len(content.encode("utf-8")),
                    "path": relative_path,
                    "status": "saved",
                },
            )
        except ApiError as error:
            self.send_error_json(error)

    def log_message(self, _format, *_args):
        pass


def create_server(host: str = HOST, port: int = PORT) -> BoundedHTTPServer:
    """Create the service without starting it, for tests and main."""
    return BoundedHTTPServer((host, port), Handler)


def main() -> int:
    with create_server() as server:
        server.serve_forever(poll_interval=0.25)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
