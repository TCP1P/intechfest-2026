#!/usr/bin/env python3
"""Bounded draft editor with one intentional relative-path overwrite."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
PAGES_DIR = Path(os.environ.get("DRAFT_PAGES_DIR", "/srv/pages"))
PUBLIC_PAGES = ("brief.txt", "status.txt")
MAX_REQUEST_BYTES = 4096
MAX_PATH_BYTES = 128
MAX_CONTENT_BYTES = 256
REQUEST_TIMEOUT_SECONDS = 4
MAX_ACTIVE_REQUESTS = 64
MAX_REQUESTS_PER_CLIENT = 16
WRITE_LOCK = threading.Lock()


class ApiError(Exception):
    """A bounded client-visible API error."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


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
    request_queue_size = MAX_ACTIVE_REQUESTS

    def __init__(self, server_address, handler_class):
        self._request_slots = threading.BoundedSemaphore(MAX_ACTIVE_REQUESTS)
        self._client_lock = threading.Lock()
        self._client_requests: dict[str, int] = {}
        super().__init__(server_address, handler_class)

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            request.close()
            return
        client = str(client_address[0])
        with self._client_lock:
            active = self._client_requests.get(client, 0)
            if active >= MAX_REQUESTS_PER_CLIENT:
                self._request_slots.release()
                request.close()
                return
            self._client_requests[client] = active + 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_client(client)
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_client(str(client_address[0]))
            self._request_slots.release()

    def _release_client(self, client: str) -> None:
        with self._client_lock:
            remaining = self._client_requests.get(client, 1) - 1
            if remaining <= 0:
                self._client_requests.pop(client, None)
            else:
                self._client_requests[client] = remaining


class Handler(BaseHTTPRequestHandler):
    """Player-visible HTTP API."""

    server_version = "DraftControl/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

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
