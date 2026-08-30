#!/usr/bin/env python3
"""Small intentionally vulnerable parcel lookup service."""

from dataclasses import dataclass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import socket
from socketserver import ThreadingMixIn
from threading import BoundedSemaphore, Lock
from urllib.parse import parse_qs, urlsplit


DEFAULT_PARCEL_ROOT = Path("/srv/parcels")
DEFAULT_FLAG_FILE = Path("/run/rsctf/flag")
MAX_CONCURRENT_REQUESTS = 64
MAX_REQUESTS_PER_CLIENT = 16
MAX_FILE_BYTES = 4096
MAX_FLAG_BYTES = 256
MAX_NAME_BYTES = 128
MAX_REQUEST_TARGET_BYTES = 2048
READ_TIMEOUT_SECONDS = 4
VAULT_PROOF_PREFIX = b"parcel-panic:v1\0"


class FileTooLarge(Exception):
    """A requested file exceeded the public response limit."""


@dataclass(frozen=True)
class AppConfig:
    parcel_root: Path
    flag_file: Path


def read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise FileTooLarge
    return data


def read_current_flag(path: Path) -> str:
    data = read_bounded(path, MAX_FLAG_BYTES)
    try:
        flag = data.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ValueError("flag file is not UTF-8") from error
    if not flag:
        raise ValueError("flag file is empty")
    return flag


def vault_proof(flag: str) -> str:
    return hashlib.sha256(VAULT_PROOF_PREFIX + flag.encode("utf-8")).hexdigest()


class ParcelHandler(BaseHTTPRequestHandler):
    config = AppConfig(DEFAULT_PARCEL_ROOT, DEFAULT_FLAG_FILE)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if len(self.path.encode("utf-8", errors="ignore")) > MAX_REQUEST_TARGET_BYTES:
            self.send_text(414, "request target too long\n")
            return

        request = urlsplit(self.path)
        if request.path == "/health":
            self.handle_health()
        elif request.path == "/parcel":
            self.handle_parcel(request.query)
        elif request.path == "/vault":
            self.handle_vault()
        elif request.path == "/":
            self.send_text(
                200,
                "Parcel Panic depot\n"
                "GET /parcel?name=manifest.txt to retrieve a dispatch record.\n",
            )
        else:
            self.send_text(404, "not found\n")

    def handle_health(self) -> None:
        try:
            manifest = self.config.parcel_root / "manifest.txt"
            if not manifest.is_file() or read_bounded(manifest, MAX_FILE_BYTES) == b"":
                raise OSError
        except (FileTooLarge, OSError):
            self.send_text(503, "depot unavailable\n")
            return
        self.send_text(200, "ok\n")

    def handle_parcel(self, query: str) -> None:
        try:
            fields = parse_qs(
                query,
                keep_blank_values=True,
                max_num_fields=4,
                strict_parsing=True,
            )
        except ValueError:
            self.send_text(400, "invalid query\n")
            return

        names = fields.get("name", [])
        if set(fields) != {"name"} or len(names) != 1:
            self.send_text(400, "provide one parcel name\n")
            return

        name = names[0]
        if (
            not name
            or len(name.encode("utf-8")) > MAX_NAME_BYTES
            or Path(name).is_absolute()
            or "\x00" in name
        ):
            self.send_text(400, "invalid parcel name\n")
            return

        # Intentional challenge boundary: relative traversal is not contained
        # beneath parcel_root. Absolute paths are still rejected.
        parcel_path = self.config.parcel_root / name
        try:
            body = read_bounded(parcel_path, MAX_FILE_BYTES)
        except FileTooLarge:
            self.send_text(413, "parcel too large\n")
            return
        except (IsADirectoryError, OSError):
            self.send_text(404, "parcel not found\n")
            return
        self.send_bytes(200, body)

    def handle_vault(self) -> None:
        try:
            flag = read_current_flag(self.config.flag_file)
        except (FileTooLarge, OSError, ValueError):
            self.send_text(503, "flag not delivered\n")
            return

        supplied = self.headers.get("X-Vault-Proof", "")
        expected = vault_proof(flag)
        if len(supplied) != len(expected) or not hmac.compare_digest(supplied, expected):
            self.send_text(403, "forbidden\n")
            return
        self.send_text(200, f"{flag}\n")

    def send_text(self, status: int, body: str) -> None:
        self.send_bytes(status, body.encode("utf-8"))

    def send_bytes(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class ParcelServer(ThreadingMixIn, HTTPServer):
    """HTTP server with bounded workers and per-connection read deadlines."""

    allow_reuse_address = True
    daemon_threads = True
    max_workers = MAX_CONCURRENT_REQUESTS
    max_requests_per_client = MAX_REQUESTS_PER_CLIENT
    request_queue_size = 16

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.worker_slots = BoundedSemaphore(self.max_workers)
        self.client_lock = Lock()
        self.client_workers: dict[str, int] = {}
        super().__init__(*args, **kwargs)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        connection, address = super().get_request()
        connection.settimeout(READ_TIMEOUT_SECONDS)
        return connection, address

    def process_request(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        if not self.worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        client = str(client_address[0])
        with self.client_lock:
            active = self.client_workers.get(client, 0)
            if active >= self.max_requests_per_client:
                self.worker_slots.release()
                self.shutdown_request(request)
                return
            self.client_workers[client] = active + 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.release_client(client)
            self.worker_slots.release()
            raise

    def process_request_thread(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.release_client(str(client_address[0]))
            self.worker_slots.release()

    def release_client(self, client: str) -> None:
        with self.client_lock:
            remaining = self.client_workers.get(client, 1) - 1
            if remaining <= 0:
                self.client_workers.pop(client, None)
            else:
                self.client_workers[client] = remaining

    def handle_error(
        self,
        _request: socket.socket,
        _client_address: tuple[str, int],
    ) -> None:
        # Hostile disconnects must not turn into unbounded traceback output.
        pass


def make_handler(config: AppConfig) -> type[ParcelHandler]:
    class ConfiguredParcelHandler(ParcelHandler):
        pass

    ConfiguredParcelHandler.config = config
    return ConfiguredParcelHandler


def create_server(config: AppConfig, host: str, port: int) -> ParcelServer:
    return ParcelServer((host, port), make_handler(config))


def config_from_environment() -> AppConfig:
    parcel_root = Path(os.environ.get("PARCEL_ROOT", str(DEFAULT_PARCEL_ROOT)))
    flag_file = Path(os.environ.get("RSCTF_FLAG_FILE", str(DEFAULT_FLAG_FILE)))
    if not parcel_root.is_absolute() or not flag_file.is_absolute():
        raise ValueError("PARCEL_ROOT and RSCTF_FLAG_FILE must be absolute")
    return AppConfig(parcel_root=parcel_root, flag_file=flag_file)


def main() -> int:
    port = int(os.environ.get("PORT", "8080"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    server = create_server(config_from_environment(), "0.0.0.0", port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
