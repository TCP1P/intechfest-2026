"""Regression tests for bounded RSCTF player-capability authentication."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock, Thread
import time
import unittest
from unittest.mock import patch

import capability_auth


TOKEN = "koth_current_player_12345"
TOKEN_HASH = hashlib.sha256(TOKEN.encode()).hexdigest()
CONTEXT = "c" * 64


class AuthServer:
    def __init__(
        self,
        *,
        status: int = 200,
        delay: float = 0.0,
        drip_interval: float = 0.0,
        oversized_header: bool = False,
    ):
        self.requests = []
        self.status = status
        self.delay = delay
        self.drip_interval = drip_interval
        self.oversized_header = oversized_header
        self.lock = Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = self.rfile.read(length)
                with owner.lock:
                    owner.requests.append((self.path, dict(self.headers), body))
                if owner.delay:
                    time.sleep(owner.delay)
                token = json.loads(body)["token"]
                response = {
                    "teamId": hashlib.sha256(token.encode()).hexdigest(),
                    "teamName": "Official Team",
                }
                encoded = json.dumps(response).encode()
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                if owner.oversized_header:
                    self.send_header("X-Padding", "x" * 512)
                self.end_headers()
                try:
                    if owner.drip_interval:
                        for byte in encoded:
                            self.wfile.write(bytes((byte,)))
                            self.wfile.flush()
                            time.sleep(owner.drip_interval)
                    else:
                        self.wfile.write(encoded)
                except OSError:
                    pass

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"


def authenticate(client: capability_auth.CapabilityAuthenticator, url: str, token=TOKEN):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    client.authenticate(
        platform_url=url,
        game_id=7,
        challenge_id=11,
        token=token,
        token_hash=token_hash,
        context=CONTEXT,
    )


class CapabilityAuthenticationTests(unittest.TestCase):
    def test_exact_exchange_keeps_raw_capability_out_of_retained_state(self):
        client = capability_auth.CapabilityAuthenticator()
        with AuthServer() as server:
            authenticate(client, server.url)

        self.assertEqual(len(server.requests), 1)
        path, headers, body = server.requests[0]
        self.assertEqual(path, "/api/v1/koth/capability/authenticate")
        self.assertEqual(
            json.loads(body),
            {"challengeId": 11, "gameId": 7, "token": TOKEN},
        )
        lowered = {name.lower(): value for name, value in headers.items()}
        self.assertEqual(lowered["accept"], "application/json")
        self.assertEqual(lowered["content-type"], "application/json")
        self.assertNotIn(TOKEN, repr(client.__dict__))

    def test_identical_concurrent_burst_makes_one_platform_request(self):
        client = capability_auth.CapabilityAuthenticator()
        with AuthServer(delay=0.1) as server:
            with ThreadPoolExecutor(max_workers=32) as executor:
                futures = [
                    executor.submit(authenticate, client, server.url)
                    for _index in range(32)
                ]
                for future in futures:
                    future.result()
        self.assertEqual(len(server.requests), 1)

    def test_platform_rejection_is_briefly_coalesced(self):
        client = capability_auth.CapabilityAuthenticator()
        with AuthServer(status=401) as server:
            for _index in range(2):
                with self.assertRaises(capability_auth.CapabilityRejected):
                    authenticate(client, server.url)
        self.assertEqual(len(server.requests), 1)

    def test_only_the_endpoint_authentication_status_is_a_player_rejection(self):
        client = capability_auth.CapabilityAuthenticator()
        for status, error_type in (
            (400, capability_auth.CapabilityUnavailable),
            (401, capability_auth.CapabilityRejected),
            (403, capability_auth.CapabilityUnavailable),
        ):
            with self.subTest(status=status), AuthServer(status=status) as server:
                with self.assertRaises(error_type):
                    authenticate(client, server.url, f"{TOKEN}_{status}")

    def test_response_headers_are_bounded_before_http_client_buffers_them(self):
        client = capability_auth.CapabilityAuthenticator()
        with (
            patch.object(capability_auth, "AUTH_RESPONSE_HEADER_BYTES", 256),
            AuthServer(oversized_header=True) as server,
        ):
            with self.assertRaises(capability_auth.CapabilityUnavailable):
                authenticate(client, server.url)

    def test_trickling_platform_body_cannot_extend_the_absolute_deadline(self):
        client = capability_auth.CapabilityAuthenticator()
        started = time.monotonic()
        with (
            patch.object(capability_auth, "AUTH_REQUEST_SECONDS", 0.2),
            AuthServer(drip_interval=0.05) as server,
        ):
            with self.assertRaises(capability_auth.CapabilityUnavailable):
                authenticate(client, server.url)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_stalled_dns_obeys_the_deadline_and_keeps_its_slot_until_exit(self):
        client = capability_auth.CapabilityAuthenticator()
        entered = Lock()
        entered.acquire()
        release = Lock()
        release.acquire()
        original = capability_auth.socket.getaddrinfo

        def stalled_dns(*args, **kwargs):
            if entered.locked():
                entered.release()
            release.acquire()
            release.release()
            return original(*args, **kwargs)

        with AuthServer() as server, patch.object(
            capability_auth,
            "AUTH_REQUEST_SECONDS",
            0.2,
        ), patch.object(capability_auth.socket, "getaddrinfo", side_effect=stalled_dns):
            started = time.monotonic()
            with self.assertRaises(capability_auth.CapabilityUnavailable):
                authenticate(client, server.url)
            elapsed = time.monotonic() - started
            self.assertFalse(entered.locked())
            self.assertLess(elapsed, 1.0)
            self.assertEqual(
                client._slots._value,
                capability_auth.AUTH_MAX_IN_FLIGHT - 1,
            )
            release.release()
            deadline = time.monotonic() + 1.0
            while (
                client._slots._value != capability_auth.AUTH_MAX_IN_FLIGHT
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(client._slots._value, capability_auth.AUTH_MAX_IN_FLIGHT)
            self.assertEqual(server.requests, [])

    def test_same_flight_followers_have_one_global_bound(self):
        client = capability_auth.CapabilityAuthenticator()
        upstream_started = Lock()
        upstream_started.acquire()
        release = Lock()
        release.acquire()

        def upstream(**_kwargs):
            if upstream_started.locked():
                upstream_started.release()
            release.acquire()
            release.release()

        def outcome():
            try:
                authenticate(client, "https://rsctf.internal")
            except capability_auth.CapabilityUnavailable:
                return "unavailable"
            return "accepted"

        with (
            patch.object(capability_auth, "AUTH_MAX_WAITERS", 3),
            patch.object(client, "_connect_upstream"),
            patch.object(client, "_authenticate_upstream", side_effect=upstream),
            ThreadPoolExecutor(max_workers=7) as executor,
        ):
            futures = [executor.submit(outcome)]
            deadline = time.monotonic() + 1.0
            while upstream_started.locked() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(upstream_started.locked())
            futures.extend(executor.submit(outcome) for _index in range(6))
            deadline = time.monotonic() + 1.0
            while (
                client._waiting_followers < 3
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(client._waiting_followers, 3)
            release.release()
            results = [future.result() for future in futures]

        self.assertEqual(results.count("accepted"), 4)
        self.assertEqual(results.count("unavailable"), 3)
        self.assertEqual(client._waiting_followers, 0)

    def test_unexpected_transport_defects_are_mapped_without_secret_text(self):
        client = capability_auth.CapabilityAuthenticator()
        with (
            patch.object(client, "_connect_upstream"),
            patch.object(
                client,
                "_authenticate_upstream",
                side_effect=RuntimeError(f"parser defect involving {TOKEN}"),
            ),
        ):
            with self.assertRaises(capability_auth.CapabilityUnavailable) as raised:
                authenticate(client, "https://rsctf.internal")
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_distinct_platform_calls_never_exceed_the_fixed_concurrency_bound(self):
        client = capability_auth.CapabilityAuthenticator()
        active = 0
        peak = 0
        lock = Lock()

        def upstream(**_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.02)
            finally:
                with lock:
                    active -= 1

        tokens = [f"koth_player_{index:08d}" for index in range(64)]
        with (
            patch.object(client, "_connect_upstream"),
            patch.object(client, "_authenticate_upstream", side_effect=upstream),
            ThreadPoolExecutor(max_workers=64) as executor,
        ):
            futures = [
                executor.submit(authenticate, client, "https://rsctf.internal", token)
                for token in tokens
            ]
            for future in futures:
                future.result()

        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, capability_auth.AUTH_MAX_IN_FLIGHT)
        self.assertLessEqual(
            len(client._rejections),
            capability_auth.AUTH_REJECTION_CACHE_ENTRIES,
        )


if __name__ == "__main__":
    unittest.main()
