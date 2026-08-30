"""Bounded threaded HTTP transport shared by Serpent Circuit's handlers."""

from __future__ import annotations

from collections import OrderedDict
import select
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from typing import BinaryIO


class BoundedReader:
    """Apply one aggregate byte limit to a request line, headers, and body."""

    def __init__(self, raw: BinaryIO, limit: int):
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


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Classify complete bounded headers before consuming a worker slot."""

    allow_reuse_address = True
    daemon_threads = True
    max_workers = 64
    max_regular_workers = 56
    max_requests_per_client = 56
    max_pending_requests = 128
    request_queue_size = 128
    request_stream_bytes = 12 * 1_024
    read_timeout_seconds = 4.0
    absolute_request_seconds = 6.0
    classifier_poll_seconds = 0.025
    protected_request_lines: frozenset[bytes] = frozenset()

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
            name="serpent-header-classifier",
            daemon=True,
        )
        self._classifier_thread.start()

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(self.read_timeout_seconds)
        return request, client_address

    def process_request(self, request, client_address) -> None:
        if self._classifier_stop.is_set():
            self.shutdown_request(request)
            return
        deadline = time.monotonic() + self.absolute_request_seconds
        evicted = None
        with self._pending_lock:
            if len(self._pending_requests) >= self.max_pending_requests:
                _, evicted = self._pending_requests.popitem(last=False)
            self._pending_requests[id(request)] = (request, client_address, deadline)
        if evicted is not None:
            self.shutdown_request(evicted[0])
        self._classifier_wakeup.set()

    def _classify_pending_requests(self) -> None:
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
            wait_seconds = self.classifier_poll_seconds
            if nearest_deadline is not None:
                wait_seconds = min(
                    wait_seconds,
                    max(0.0, nearest_deadline - time.monotonic()),
                )
            self._classifier_wakeup.wait(wait_seconds if pending else 0.5)

    def _classify_request(self, request: socket.socket) -> str:
        try:
            readable, _writable, _exceptional = select.select([request], [], [], 0)
            if not readable:
                return "pending"
            peeked = request.recv(
                self.request_stream_bytes + 1,
                socket.MSG_PEEK | socket.MSG_DONTWAIT,
            )
        except BlockingIOError:
            return "pending"
        except (OSError, ValueError):
            return "reject"
        if not peeked or len(peeked) > self.request_stream_bytes:
            return "reject"
        if b"\r\n\r\n" not in peeked:
            return "pending"
        request_line = peeked.split(b"\r\n", 1)[0]
        return "protected" if request_line in self.protected_request_lines else "regular"

    def _take_pending_request(self, request_id: int, request: socket.socket):
        with self._pending_lock:
            entry = self._pending_requests.get(request_id)
            if entry is None or entry[0] is not request:
                return None
            del self._pending_requests[request_id]
            return entry

    def _promote_request(self, request, client_address, deadline: float, *, protected: bool) -> None:
        if self._classifier_stop.is_set() or time.monotonic() >= deadline:
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

    def process_request_thread(self, request, client_address) -> None:
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

    def server_close(self) -> None:
        self._classifier_stop.set()
        self._classifier_wakeup.set()
        if hasattr(self, "_classifier_thread"):
            self._classifier_thread.join()
        with self._pending_lock:
            pending = list(self._pending_requests.values())
            self._pending_requests.clear()
        for request, _client_address, _deadline in pending:
            self.shutdown_request(request)
        super().server_close()

    def handle_error(self, _request, _client_address) -> None:
        # Hostile disconnects must not generate an unbounded traceback stream.
        pass
