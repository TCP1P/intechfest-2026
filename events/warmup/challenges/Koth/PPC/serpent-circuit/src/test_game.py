"""Regression tests for the authoritative Serpent Circuit game state."""

import hashlib
from contextlib import redirect_stderr
from http.client import HTTPConnection
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
from concurrent.futures import ThreadPoolExecutor
import socket
import time
import unittest
from unittest.mock import patch
from threading import Event, Thread

import app
import http_transport
import reporter


LOCAL_TOKEN = "koth_local_player_12345"
MANAGED_ENV = {
    "RSCTF_KOTH_GAME_ID": "7",
    "RSCTF_KOTH_CHALLENGE_ID": "11",
    "RSCTF_KOTH_PLATFORM_URL": "https://rsctf.internal",
    "RSCTF_KOTH_CONTEXT_URL": "https://rsctf.internal/context",
    "RSCTF_KOTH_OBSERVATION_URL": "https://rsctf.internal/observations",
    "RSCTF_KOTH_REPORTER_SECRET": "koth_target_" + "s" * 43,
}


class GameTests(unittest.TestCase):
    def setUp(self):
        with app.state_lock:
            app.sessions.clear()
            app.started_runs.clear()
            app.wave_admissions.clear()
            app.events.clear()
            app.next_cursor = 1
        app.publish_managed_context(None)
        app.reporter_runtime.set_stopped()
        app.capability_authenticator.reset()
        connection_patcher = patch.object(
            app.capability_authenticator,
            "_connect_upstream",
        )
        connection_patcher.start()
        self.addCleanup(connection_patcher.stop)

    def test_board_and_dice_sequence_are_deterministic_and_original(self):
        first = app.turn_pairs(1_800_000)
        self.assertEqual(first, app.turn_pairs(1_800_000))
        self.assertNotEqual(first, app.turn_pairs(1_860_000))
        self.assertEqual(len(first), app.TURN_COUNT)
        self.assertTrue(all(len(pair) == 2 and pair[0] != pair[1] for pair in first))
        self.assertTrue(all(1 <= die <= 6 for pair in first for die in pair))
        self.assertEqual(len(app.TRANSITIONS), 14)
        self.assertFalse(set(app.TRANSITIONS) & set(app.TRANSITIONS.values()))
        self.assertIn(
            "Route efficiency is (finalPosition - startPosition) / (optimalPosition - startPosition).",
            app.rules_document()["rules"],
        )

    def test_optimum_matches_exhaustive_ordered_routes(self):
        turns = app.turn_pairs(1_800_000)
        positions = {app.START_POSITION}
        for pair in turns:
            positions = {
                app.apply_roll(position, roll)[0]
                for position in positions
                for roll in pair
            }
        self.assertEqual(app.optimal_position(turns), max(positions))

    def test_gameplay_fails_closed_without_managed_or_explicit_local_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app.runtime_mode(), "unconfigured")
            with self.assertRaisesRegex(app.GameError, "not configured") as raised:
                app.create_session(LOCAL_TOKEN, 1_800_100)
        self.assertEqual(raised.exception.status, 503)

    def test_partial_environment_fails_closed_but_full_environment_is_managed(self):
        with patch.dict(os.environ, {"RSCTF_KOTH_GAME_ID": "7"}, clear=True):
            self.assertEqual(app.runtime_mode(), "misconfigured")
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                app.create_session(LOCAL_TOKEN, 1_800_100)
        with patch.dict(os.environ, MANAGED_ENV, clear=True):
            self.assertEqual(app.runtime_mode(), "managed")
            self.assertEqual(app.capability_config().challenge_id, 11)

    def test_invalid_full_reporter_environment_fails_gameplay_closed(self):
        invalid = dict(MANAGED_ENV)
        invalid["RSCTF_KOTH_REPORTER_SECRET"] = "not-a-target-secret"
        with patch.dict(os.environ, invalid, clear=True):
            self.assertEqual(app.runtime_mode(), "misconfigured")
            with self.assertRaisesRegex(RuntimeError, "invalid managed reporter"):
                app.create_session(LOCAL_TOKEN, 1_800_100)

    def test_health_tolerates_startup_but_surfaces_persistent_reporter_failure(self):
        stop = Event()
        reporter_thread = Thread(target=stop.wait, daemon=True)
        app.reporter_runtime.attach(reporter_thread)
        reporter_thread.start()
        try:
            with patch.dict(os.environ, MANAGED_ENV, clear=True):
                status, document = app.health_document()
                self.assertEqual(status, 200)
                self.assertEqual(document["status"], "ok")

                with patch("reporter.time.monotonic", return_value=100.0):
                    for _index in range(reporter.HEALTH_FAILURE_THRESHOLD):
                        app.reporter_runtime.mark_retrying()
                with patch("reporter.time.monotonic", return_value=116.0):
                    status, document = app.health_document()
                self.assertEqual(status, 503)
                self.assertEqual(document["status"], "error")
                self.assertEqual(
                    document["reporter"]["consecutiveFailures"],
                    reporter.HEALTH_FAILURE_THRESHOLD,
                )

                app.reporter_runtime.mark_waiting()
                status, document = app.health_document()
                self.assertEqual(status, 200)
                self.assertEqual(document["reporter"]["state"], "waiting")
        finally:
            stop.set()
            reporter_thread.join()

    def test_health_tolerates_the_normal_context_rotation_readiness_gap(self):
        stop = Event()
        reporter_thread = Thread(target=stop.wait, daemon=True)
        app.reporter_runtime.attach(reporter_thread)
        reporter_thread.start()
        try:
            app.reporter_runtime.mark_ready()
            with patch.dict(os.environ, MANAGED_ENV, clear=True):
                status, document = app.health_document()

            self.assertEqual(status, 200)
            self.assertTrue(document["reporter"]["alive"])
            self.assertEqual(document["reporter"]["state"], "ready")
            self.assertFalse(document["scoringReady"])
        finally:
            stop.set()
            reporter_thread.join()

    def test_local_session_creation_and_play_are_idempotent(self):
        timestamp = 1_800_100
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            session = app.create_session(LOCAL_TOKEN, timestamp)
            duplicate = app.create_session(LOCAL_TOKEN, timestamp + 1)
            self.assertEqual(duplicate["sessionId"], session["sessionId"])
            self.assertEqual(duplicate["turns"], session["turns"])
            self.assertEqual(session["serverTime"], timestamp)
            self.assertEqual(duplicate["serverTime"], timestamp + 1)
            self.assertLessEqual(
                session["waveEndsAt"] - session["serverTime"],
                app.WAVE_DURATION_MS,
            )

        choices = [
            {"turn": turn["turn"], "choice": 0}
            for turn in session["turns"]
        ]
        result = app.play_session(session["sessionId"], choices, timestamp + 2)
        self.assertTrue(result["accepted"])
        self.assertLessEqual(
            result["efficiency"]["earned"], result["efficiency"]["possible"]
        )
        self.assertEqual(len(result["landings"]), app.TURN_COUNT)
        replay = app.play_session(session["sessionId"], choices, timestamp + 3)
        self.assertEqual(replay, result)
        changed = [dict(selection) for selection in choices]
        changed[0]["choice"] = 1
        with self.assertRaises(app.GameError) as conflict:
            app.play_session(session["sessionId"], changed, timestamp + 4)
        self.assertEqual(conflict.exception.status, 409)

    def test_concurrent_session_admission_is_idempotent_per_team_wave(self):
        timestamp = 1_800_100

        def start():
            try:
                return app.create_session(LOCAL_TOKEN, timestamp)["sessionId"]
            except app.GameError as error:
                return error.status

        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: start(), range(2)))
        self.assertEqual(len(set(results)), 1)

    def test_current_team_wave_admission_budget_is_bounded(self):
        timestamp = 1_800_100
        start_ms = app.wave_start(timestamp)
        with app.state_lock:
            app.wave_admissions[(start_ms, 0, 0)] = app.MAX_WAVE_ADMISSIONS
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            with self.assertRaisesRegex(app.GameError, "admission budget") as full:
                app.create_session(LOCAL_TOKEN, timestamp)
        self.assertEqual(full.exception.status, 503)

    def test_managed_wave_accepts_the_full_2_000_team_roster(self):
        timestamp = 1_800_100
        tokens = [f"koth_player_{index:04d}_current" for index in range(2_000)]
        token_hashes = frozenset(
            hashlib.sha256(token.encode()).hexdigest() for token in tokens
        )
        context = app.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=0,
            ends_at=2_000_000,
            cycle_ends_at=3_000_000,
            scoring_ends_at=2_980_000,
            scoring_floor=0,
            generated_at=app.now_ms(),
            eligible_hashes=token_hashes,
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(
                app.capability_authenticator,
                "_authenticate_upstream",
                new=lambda **_kwargs: None,
            ),
        ):
            app.publish_managed_context(context)
            session_ids = {
                app.create_session(token, timestamp)["sessionId"]
                for token in tokens
            }
            with self.assertRaises(app.GameError) as outsider:
                app.create_session("koth_player_2000_current", timestamp)

        self.assertEqual(len(session_ids), 2_000)
        self.assertEqual(len(app.sessions), 2_000)
        self.assertEqual(outsider.exception.status, 403)

    def test_order_validation_does_not_spend_the_session(self):
        timestamp = 1_800_100
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            session = app.create_session(LOCAL_TOKEN, timestamp)
        wrong = [
            {"turn": turn["turn"], "choice": 0}
            for turn in session["turns"]
        ]
        wrong[3]["turn"] = 9
        with self.assertRaises(app.GameError) as malformed:
            app.play_session(session["sessionId"], wrong, timestamp + 1)
        self.assertEqual(malformed.exception.status, 400)
        correct = [
            {"turn": turn["turn"], "choice": 0}
            for turn in session["turns"]
        ]
        self.assertTrue(
            app.play_session(session["sessionId"], correct, timestamp + 2)["accepted"]
        )

    def test_unhashable_choice_is_a_bounded_player_error(self):
        selections = [
            {"turn": turn, "choice": 0}
            for turn in range(1, app.TURN_COUNT + 1)
        ]
        selections[0]["choice"] = []
        with self.assertRaises(app.GameError) as invalid:
            app._validated_choices(selections)
        self.assertEqual(invalid.exception.status, 400)

    def test_server_applies_transport_and_exact_finish_rule(self):
        self.assertEqual(app.apply_roll(1, 2), (24, 3, 24))
        self.assertEqual(app.apply_roll(94, 3), (76, 97, 76))
        self.assertEqual(app.apply_roll(99, 4), (99, None, None))

    def test_evidence_retains_only_the_canonical_hash(self):
        timestamp = 1_800_100
        expected_hash = hashlib.sha256(LOCAL_TOKEN.encode()).hexdigest()
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            session = app.create_session(LOCAL_TOKEN, timestamp)
        choices = [
            {"turn": turn["turn"], "choice": 0}
            for turn in session["turns"]
        ]
        app.play_session(session["sessionId"], choices, timestamp + 1)
        page = app.read_evidence_page(0)
        self.assertEqual(page["events"][0]["tokenHash"], expected_hash)
        self.assertNotIn(LOCAL_TOKEN, repr(app.sessions))
        self.assertNotIn(LOCAL_TOKEN, repr(page))
        self.assertEqual(page["events"][0]["cycleNumber"], 0)
        self.assertEqual(page["events"][0]["resetAttempt"], 0)

    def test_wave_expiry_consumes_the_session_without_evidence(self):
        timestamp = 1_800_100
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            session = app.create_session(LOCAL_TOKEN, timestamp)
        choices = [
            {"turn": turn["turn"], "choice": 0}
            for turn in session["turns"]
        ]
        with self.assertRaises(app.GameError) as expired:
            app.play_session(session["sessionId"], choices, session["waveEndsAt"])
        self.assertEqual(expired.exception.status, 410)
        self.assertEqual(app.read_evidence_page(0)["events"], [])

    def test_accepted_connections_receive_the_slow_client_deadline(self):
        server = app.Server(("127.0.0.1", 0), app.Handler)
        client = socket.create_connection(server.server_address, timeout=1)
        accepted, _address = server.get_request()
        try:
            self.assertEqual(accepted.gettimeout(), app.CONNECTION_TIMEOUT_SECONDS)
        finally:
            client.close()
            accepted.close()
            server.server_close()

    def test_managed_roster_authentication_is_platform_confirmed_and_lifecycle_fenced(self):
        timestamp = 1_800_100
        token_hash = hashlib.sha256(LOCAL_TOKEN.encode()).hexdigest()
        initial = app.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=0,
            ends_at=2_000_000,
            cycle_ends_at=3_000_000,
            scoring_ends_at=2_980_000,
            scoring_floor=0,
            generated_at=app.now_ms(),
            eligible_hashes=frozenset({token_hash}),
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(
                app.capability_authenticator,
                "_authenticate_upstream",
                return_value=None,
            ),
        ):
            app.publish_managed_context(initial)
            session = app.create_session(LOCAL_TOKEN, timestamp)
            self.assertEqual(session["waveId"], "serpent-1800000")

            rotated = app.RoundContext(
                **{**initial.__dict__, "opaque": "c" * 64, "reset_attempt": 1}
            )
            app.publish_managed_context(rotated)
            replacement = app.create_session(LOCAL_TOKEN, timestamp + 1)
            self.assertNotEqual(replacement["sessionId"], session["sessionId"])
            choices = [
                {"turn": turn["turn"], "choice": 0}
                for turn in session["turns"]
            ]
            with self.assertRaises(app.GameError) as stale:
                app.play_session(session["sessionId"], choices, timestamp + 1)
        self.assertEqual(stale.exception.status, 410)

    def test_managed_roster_rejects_unknown_and_stale_capabilities(self):
        token_hash = hashlib.sha256(LOCAL_TOKEN.encode()).hexdigest()
        context = app.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=0,
            ends_at=2_000_000,
            cycle_ends_at=3_000_000,
            scoring_ends_at=2_980_000,
            scoring_floor=0,
            generated_at=app.now_ms(),
            eligible_hashes=frozenset({token_hash}),
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with patch.dict(os.environ, MANAGED_ENV, clear=True):
            app.publish_managed_context(context)
            with self.assertRaises(app.GameError) as unknown:
                app.create_session("koth_unknown_player_12345", 1_800_100)
            self.assertEqual(unknown.exception.status, 403)
            with patch("app.time.monotonic", return_value=time.monotonic() + 60):
                with self.assertRaises(app.GameError) as stale:
                    app.create_session(LOCAL_TOKEN, 1_800_100)
            self.assertEqual(stale.exception.status, 503)

    def test_new_wave_reauthenticates_after_immediate_platform_revocation(self):
        token_hash = hashlib.sha256(LOCAL_TOKEN.encode()).hexdigest()
        context = app.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=0,
            ends_at=2_100_000,
            cycle_ends_at=3_000_000,
            scoring_ends_at=2_980_000,
            scoring_floor=0,
            generated_at=app.now_ms(),
            eligible_hashes=frozenset({token_hash}),
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(
                app.capability_authenticator,
                "_authenticate_upstream",
                side_effect=(
                    None,
                    app.CapabilityRejected("revoked by RSCTF"),
                ),
            ) as authenticate,
        ):
            app.publish_managed_context(context)
            first = app.create_session(LOCAL_TOKEN, 1_800_100)
            duplicate = app.create_session(LOCAL_TOKEN, 1_800_200)
            with self.assertRaises(app.GameError) as revoked:
                app.create_session(LOCAL_TOKEN, 1_860_100)

        self.assertEqual(duplicate["sessionId"], first["sessionId"])
        self.assertEqual(revoked.exception.status, 403)
        self.assertEqual(authenticate.call_count, 2)

    def test_cycle_cutoff_rejects_a_partial_final_wave(self):
        token_hash = hashlib.sha256(LOCAL_TOKEN.encode()).hexdigest()
        context = app.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=0,
            ends_at=1_800_001,
            cycle_ends_at=1_880_000,
            scoring_ends_at=1_859_999,
            scoring_floor=0,
            generated_at=app.now_ms(),
            eligible_hashes=frozenset({token_hash}),
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(
                app.capability_authenticator,
                "_authenticate_upstream",
                return_value=None,
            ),
        ):
            app.publish_managed_context(context)
            with self.assertRaises(app.GameError) as cutoff:
                app.create_session(LOCAL_TOKEN, 1_800_100)
        self.assertEqual(cutoff.exception.status, 409)

        permitted = app.RoundContext(
            **{**context.__dict__, "opaque": "c" * 64, "scoring_ends_at": 1_860_000}
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(
                app.capability_authenticator,
                "_authenticate_upstream",
                return_value=None,
            ),
        ):
            app.publish_managed_context(permitted)
            session = app.create_session(LOCAL_TOKEN, 1_800_100)
        self.assertEqual(session["waveEndsAt"], 1_860_000)

    def test_event_start_rejects_an_initial_partial_wave(self):
        token_hash = hashlib.sha256(LOCAL_TOKEN.encode()).hexdigest()
        context = app.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=1_830_000,
            ends_at=2_000_000,
            cycle_ends_at=3_000_000,
            scoring_ends_at=2_980_000,
            scoring_floor=1_860_000,
            generated_at=app.now_ms(),
            eligible_hashes=frozenset({token_hash}),
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(
                app.capability_authenticator,
                "_authenticate_upstream",
                return_value=None,
            ),
        ):
            app.publish_managed_context(context)
            with self.assertRaises(app.GameError) as initial:
                app.create_session(LOCAL_TOKEN, 1_830_100)
            session = app.create_session(LOCAL_TOKEN, 1_860_000)
        self.assertEqual(initial.exception.status, 409)
        self.assertEqual(session["waveId"], "serpent-1860000")

    def test_request_reader_bounds_headers_and_body_together(self):
        reader = app._BoundedReader(io.BytesIO(b"a" * 20), 12)
        self.assertEqual(reader.read(12), b"a" * 12)
        with self.assertRaises(OSError):
            reader.read(1)

    def test_health_uses_reserved_capacity_during_slow_player_requests(self):
        clients = []

        class TinyServer(app.Server):
            max_workers = 12
            max_regular_workers = 8
            max_requests_per_client = 8
            absolute_request_seconds = 2

        server = TinyServer(("127.0.0.1", 0), app.Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for _index in range(8):
                client = socket.create_connection(server.server_address, timeout=1)
                client.sendall(
                    b"POST /api/play HTTP/1.1\r\n"
                    b"Host: arena\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 100\r\n\r\n"
                    b"{"
                )
                clients.append(client)
            deadline = time.monotonic() + 1
            while len(server._admissions) < 8 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(server._admissions), 8)

            health = socket.create_connection(server.server_address, timeout=1)
            health.sendall(b"GET /healthz HTTP/1.1\r\nHost: arena")
            time.sleep(0.05)
            health.sendall(b"\r\nConnection: close\r\n\r\n")
            response = bytearray()
            while True:
                chunk = health.recv(4_096)
                if not chunk:
                    break
                response.extend(chunk)
            health.close()
            self.assertIn(b"HTTP/1.0 200 OK", response)
            self.assertIn(b'"status":"ok"', response)
        finally:
            for client in clients:
                client.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_health_survives_32_trickling_clients_without_tracebacks(self):
        clients = []
        errors = io.StringIO()

        class DeadlineServer(app.Server):
            read_timeout_seconds = 0.25
            absolute_request_seconds = 0.75
            classifier_poll_seconds = 0.005

        with redirect_stderr(errors):
            server = DeadlineServer(("127.0.0.1", 0), app.Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for _index in range(32):
                    client = socket.create_connection(server.server_address, timeout=2)
                    client.sendall(
                        b"POST /api/play HTTP/1.1\r\n"
                        b"Host: arena\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: 100\r\n\r\n"
                        b"{"
                    )
                    clients.append(client)
                deadline = time.monotonic() + 2
                while len(server._admissions) < 32 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(len(server._admissions), 32)

                for _round in range(2):
                    time.sleep(0.05)
                    for client in clients:
                        try:
                            client.sendall(b" ")
                        except OSError:
                            pass

                connection = HTTPConnection(*server.server_address, timeout=2)
                connection.request("GET", "/healthz", headers={"Connection": "close"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["status"], "ok")
                connection.close()
            finally:
                for client in clients:
                    try:
                        client.setsockopt(
                            socket.SOL_SOCKET,
                            socket.SO_LINGER,
                            struct.pack("ii", 1, 0),
                        )
                    except OSError:
                        pass
                    client.close()
                server.shutdown()
                server.server_close()
                thread.join()
        self.assertNotIn("Traceback", errors.getvalue())

    def test_deadline_thread_failure_releases_admission(self):
        server = app.Server(("127.0.0.1", 0), app.Handler)
        request, peer = socket.socketpair()
        client = "127.0.0.1"
        server._request_slots.acquire()
        server._regular_slots.acquire()
        with server._client_lock:
            server._client_requests[client] = 1
            server._admissions[id(request)] = (
                client,
                True,
                time.monotonic() + 1,
            )
        try:
            with patch.object(
                http_transport.threading.Timer,
                "start",
                side_effect=RuntimeError,
            ):
                with self.assertRaises(RuntimeError):
                    server.process_request_thread(request, (client, 12345))
            self.assertEqual(server._admissions, {})
            self.assertEqual(server._client_requests, {})
            self.assertEqual(server._request_slots._value, app.MAX_HTTP_WORKERS)
            self.assertEqual(server._regular_slots._value, app.MAX_REGULAR_HTTP_WORKERS)
        finally:
            request.close()
            peer.close()
            server.server_close()

    def test_aggregate_header_limit_closes_request_and_health_recovers(self):
        class TinyServer(app.Server):
            max_pending_requests = 16
            request_queue_size = 16
            absolute_request_seconds = 1

        server = TinyServer(("127.0.0.1", 0), app.Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = socket.create_connection(server.server_address, timeout=1)
        try:
            client.sendall(
                b"GET /api/rules HTTP/1.1\r\nHost: arena\r\nX-Fill: "
                + b"a" * app.MAX_REQUEST_STREAM_BYTES
                + b"\r\n\r\n"
            )
            client.settimeout(1)
            self.assertEqual(client.recv(1), b"")

            connection = HTTPConnection(*server.server_address, timeout=1)
            connection.request("GET", "/healthz", headers={"Connection": "close"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_silent_pending_connections_cannot_hide_health(self):
        class TinyServer(app.Server):
            max_pending_requests = 8
            request_queue_size = 32
            absolute_request_seconds = 1
            classifier_poll_seconds = 0.01

        class SlowHandler(app.Handler):
            def do_GET(self):  # noqa: N802 - HTTP handler contract
                time.sleep(0.1)
                super().do_GET()

        server = TinyServer(("127.0.0.1", 0), SlowHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        clients = []
        try:
            for _index in range(12):
                clients.append(socket.create_connection(server.server_address, timeout=1))
            deadline = time.monotonic() + 1
            while len(server._pending_requests) < 8 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(server._pending_requests), 8)

            connection = HTTPConnection(*server.server_address, timeout=1)
            connection.request("GET", "/healthz", headers={"Connection": "close"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
        finally:
            for client in clients:
                client.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_absolute_body_deadline_is_not_extended_by_trickle_bytes(self):
        class TinyServer(app.Server):
            max_workers = 4
            max_regular_workers = 2
            max_requests_per_client = 2
            max_pending_requests = 8
            request_queue_size = 8
            read_timeout_seconds = 1
            absolute_request_seconds = 0.25
            classifier_poll_seconds = 0.01

        server = TinyServer(("127.0.0.1", 0), app.Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = socket.create_connection(server.server_address, timeout=1)
        started = time.monotonic()
        try:
            client.sendall(
                b"POST /api/play HTTP/1.1\r\n"
                b"Host: arena\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 100\r\n\r\n{"
            )
            while time.monotonic() - started < 0.55:
                time.sleep(0.04)
                try:
                    client.sendall(b" ")
                except OSError:
                    break
            client.settimeout(1)
            self.assertEqual(client.recv(1), b"")
            self.assertLess(time.monotonic() - started, 1)

            connection = HTTPConnection(*server.server_address, timeout=1)
            connection.request("GET", "/healthz", headers={"Connection": "close"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_abrupt_disconnect_releases_all_worker_capacity(self):
        class TinyServer(app.Server):
            max_workers = 4
            max_regular_workers = 2
            max_requests_per_client = 2
            max_pending_requests = 8
            request_queue_size = 8
            absolute_request_seconds = 1
            classifier_poll_seconds = 0.01

        class SlowHandler(app.Handler):
            def do_GET(self):  # noqa: N802 - HTTP handler contract
                time.sleep(0.1)
                super().do_GET()

        server = TinyServer(("127.0.0.1", 0), SlowHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = socket.create_connection(server.server_address, timeout=1)
        try:
            client.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack("ii", 1, 0),
            )
            client.sendall(
                b"GET /api/rules HTTP/1.1\r\n"
                b"Host: arena\r\nConnection: close\r\n\r\n"
            )
            deadline = time.monotonic() + 1
            while not server._admissions and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(server._admissions)
            client.close()
            deadline = time.monotonic() + 1
            while (
                (server._admissions or server._client_requests)
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(server._admissions, {})
            self.assertEqual(server._client_requests, {})
            self.assertEqual(server._request_slots._value, TinyServer.max_workers)
            self.assertEqual(
                server._regular_slots._value,
                TinyServer.max_regular_workers,
            )
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_play_samples_authoritative_time_only_after_taking_state_lock(self):
        timestamp = 1_800_100
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            session = app.create_session(LOCAL_TOKEN, timestamp)
        choices = [
            {"turn": turn["turn"], "choice": 0}
            for turn in session["turns"]
        ]

        def locked_now():
            self.assertTrue(app.state_lock.locked())
            return session["waveEndsAt"]

        with patch.object(app, "now_ms", side_effect=locked_now):
            with self.assertRaises(app.GameError) as expired:
                app.play_session(session["sessionId"], choices)
        self.assertEqual(expired.exception.status, 410)
        self.assertEqual(app.read_evidence_page(0)["events"], [])

    def test_capability_bursts_coalesce_and_fake_tokens_stop_at_the_roster(self):
        token_hash = hashlib.sha256(LOCAL_TOKEN.encode()).hexdigest()
        context = app.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=0,
            ends_at=2_000_000,
            cycle_ends_at=3_000_000,
            scoring_ends_at=2_980_000,
            scoring_floor=0,
            generated_at=app.now_ms(),
            eligible_hashes=frozenset({token_hash}),
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(
                app.capability_authenticator,
                "_authenticate_upstream",
                side_effect=lambda **_kwargs: time.sleep(0.05),
            ) as authenticate,
        ):
            app.publish_managed_context(context)
            with ThreadPoolExecutor(max_workers=32) as executor:
                admitted = list(
                    executor.map(
                        lambda _index: app.create_session(LOCAL_TOKEN, 1_800_100),
                        range(32),
                    )
                )
            with ThreadPoolExecutor(max_workers=32) as executor:
                rejected = list(
                    executor.map(
                        lambda index: self._session_status(
                            f"koth_unknown_{index:08d}",
                            1_800_100,
                        ),
                        range(200),
                    )
                )
        self.assertEqual(len({item["sessionId"] for item in admitted}), 1)
        self.assertEqual(rejected, [403] * 200)
        self.assertEqual(len(app.sessions), 1)
        authenticate.assert_called_once()

    @staticmethod
    def _session_status(token: str, timestamp_ms: int) -> int:
        try:
            app.create_session(token, timestamp_ms)
        except app.GameError as error:
            return error.status
        return 201

    @unittest.skipUnless(shutil.which("node"), "Node is unavailable")
    def test_browser_server_clock_contract(self):
        subprocess.run(
            ["node", "--test", str(Path(__file__).with_name("test_clock.mjs"))],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
