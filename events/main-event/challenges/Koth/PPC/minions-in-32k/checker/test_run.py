import importlib.util
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("minions_checker", Path(__file__).with_name("run.py"))
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def health(**changes):
    value = {
        "name": "Minions in 32K",
        "status": "idle",
        "protocol": "minions/0",
        "protocolVersion": 13,
        "renderWidth": 256,
        "renderHeight": 128,
        "players": 8,
        "npcs": 7,
        "certHash": "a" * 64,
        "build": "2026.09.04.1",
    }
    value.update(changes)
    return value


def quality(**changes):
    value = {
        "status": "ok",
        "probe": "pinned-quic-invalid-key",
        "mode": "idle",
        "protocolVersion": 13,
    }
    value.update(changes)
    return value


class FixtureServer:
    def __init__(self, routes):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes.get(self.path, (404, {"status": 404}))
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.server.server_port

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def checker_environment(port):
    return {
        "RSCTF_ACTION": "check",
        "RSCTF_TEAM_ID": "0",
        "RSCTF_ROUND": "17",
        "RSCTF_CHALLENGE_ID": "1097",
        "RSCTF_TARGET_IP": "127.0.0.1",
        "RSCTF_TARGET_PORT": str(port),
    }


class HealthContractTests(unittest.TestCase):
    def test_idle_protocol_v13_is_healthy(self):
        self.assertTrue(CHECKER.valid_health(health()))
        self.assertTrue(CHECKER.valid_health(health(players=0, npcs=0)))

    def test_running_protocol_v13_requires_match_identity_and_deadline(self):
        self.assertTrue(
            CHECKER.valid_health(
                health(status="running", matchId="m32-regression-match", expectedEndUnixMs=1_010_000),
                now_unix_ms=1_000_000,
            )
        )
        self.assertFalse(CHECKER.valid_health(health(status="running")))

    def test_running_deadline_and_match_identity_are_bounded(self):
        self.assertFalse(
            CHECKER.valid_health(
                health(status="running", matchId="m32-bad space", expectedEndUnixMs=1_010_000),
                now_unix_ms=1_000_000,
            )
        )
        self.assertFalse(
            CHECKER.valid_health(
                health(status="running", matchId="m32-stale", expectedEndUnixMs=900_000),
                now_unix_ms=1_000_000,
            )
        )

    def test_stale_protocol_revision_is_rejected(self):
        self.assertFalse(CHECKER.valid_health(health(protocolVersion=12)))
        self.assertFalse(
            CHECKER.valid_health(
                {key: value for key, value in health().items() if key != "protocolVersion"}
            )
        )

    def test_unknown_health_fields_fail_closed(self):
        self.assertFalse(CHECKER.valid_health(health(untrusted=True)))

    def test_runtime_identity_is_required_and_canonical(self):
        self.assertFalse(CHECKER.valid_health(health(certHash="A" * 64)))
        self.assertFalse(CHECKER.valid_health(health(certHash="a" * 63)))
        self.assertFalse(CHECKER.valid_health(health(build="dev")))
        self.assertFalse(
            CHECKER.valid_health(
                {key: value for key, value in health().items() if key != "build"}
            )
        )

    def test_full_npc_backfill_is_required(self):
        self.assertFalse(CHECKER.valid_health(health(npcs=6)))
        self.assertFalse(
            CHECKER.valid_health(
                health(
                    status="running",
                    players=0,
                    npcs=0,
                    matchId="m32-regression-match",
                    expectedEndUnixMs=1_010_000,
                ),
                now_unix_ms=1_000_000,
            )
        )

    def test_quality_contract_is_exact_and_bound_to_arena_mode(self):
        self.assertTrue(CHECKER.valid_quality(quality(), "idle"))
        self.assertFalse(CHECKER.valid_quality(quality(mode="running"), "idle"))
        self.assertFalse(CHECKER.valid_quality(quality(untrusted=True), "idle"))


class EndToEndCheckerTests(unittest.TestCase):
    def test_main_requires_the_active_quic_gameplay_probe(self):
        with FixtureServer({
            "/healthz": (200, health()),
            "/qualityz": (200, quality()),
        }) as port, patch.dict(os.environ, checker_environment(port), clear=True):
            self.assertEqual(CHECKER.main(), 0)

    def test_main_reports_mumble_when_http_lives_but_gameplay_probe_fails(self):
        with FixtureServer({
            "/healthz": (200, health()),
            "/qualityz": (503, {"title": "authoritative gameplay probe failed"}),
        }) as port, patch.dict(os.environ, checker_environment(port), clear=True):
            self.assertEqual(CHECKER.main(), 1)

    def test_main_reports_offline_when_the_target_cannot_answer(self):
        with patch.object(
            CHECKER,
            "get_json",
            side_effect=CHECKER.Offline("the arena did not complete the request"),
        ), patch.dict(os.environ, checker_environment(1), clear=True):
            self.assertEqual(CHECKER.main(), 2)

    def test_invalid_platform_context_is_internal_error(self):
        environment = checker_environment(1)
        environment["RSCTF_TEAM_ID"] = "4"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(CHECKER.main(), 3)

    def test_malformed_platform_context_is_internal_error(self):
        environment = checker_environment(1)
        environment["RSCTF_TARGET_PORT"] = "not-a-port"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(CHECKER.main(), 3)


if __name__ == "__main__":
    unittest.main()
