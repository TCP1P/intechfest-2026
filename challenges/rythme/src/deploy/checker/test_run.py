import importlib.util
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("rythme_checker", Path(__file__).with_name("run.py"))
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def config(**changes):
    value = {
        "arena_mods": "implemented lazer modifiers selected after the shared reveal",
        "client_build": "r6-regression",
        "local_auth": False,
        "pool_schema": "rythme-pool-v2",
        "pool_song_count": 21,
        "pool_source": "organizer-curated-real-osu-beatmaps",
        "pool_verified_at": "2026-07-31T00:00:00Z",
        "protocol": "rythme-secure-channel-v3",
        "rctf_auth": True,
        "rulesets": ["osu", "taiko", "fruits", "mania"],
        "server_build": "2026.08.21.1",
        "transport": "authenticated arena channel",
    }
    value.update(changes)
    return value


def state(now_ms=None, **changes):
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    value = {
        "server_now_unix_ms": now_ms,
        "pool_schema": "rythme-pool-v2",
        "crown_threshold": 2_100_000,
        "raw_score_floor": 700_000,
        "heat": {
            "id": "heat-000005",
            "index": 5,
            "phase": "enrollment",
            "commitment": "a" * 64,
            "enrollment_ends_unix_ms": now_ms + 2_000,
            "start_at_unix_ms": now_ms + 32_000,
            "end_at_unix_ms": now_ms + 137_000,
            "submission_ends_unix_ms": now_ms + 149_000,
            "next_heat_at_unix_ms": now_ms + 169_000,
            "performers": [],
        },
        "series": {
            "index": 2,
            "current_leg": 1,
            "total_legs": 4,
            "standings": [],
        },
        "ruleset_bag_rule": "one native mode per four-wave rotation",
        "series_rule": "every finalized heat scores independently",
    }
    value.update(changes)
    return value


def selection(series_index=2, series_leg=1):
    return {
        "song_id": "regression-map",
        "song_title": "Regression",
        "song_artist": "RSCTF",
        "ruleset": "osu",
        "chart_digest": "b" * 64,
        "control_profile": "osu-standard",
        "bpm": 180,
        "duration_ms": 90_000,
        "lead_in_ms": 1_000,
        "note_count": 500,
        "beatmapset_id": 1,
        "beatmap_id": 2,
        "difficulty": "Quality",
        "star_rating": 5.2,
        "status": "ranked",
        "mapper": "RSCTF",
        "source_url": "https://example.invalid/source",
        "official_url": "https://example.invalid/official",
        "archive_sha256": "c" * 64,
        "nonce": "quality-nonce",
        "series_index": series_index,
        "series_leg": series_leg,
    }


class FixtureServer:
    def __init__(self, broken_quality=False, broken_onboarding=False):
        base = int(time.time() * 1000)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/healthz":
                    return self.json({"status": "ok"})
                if self.path == "/api/v1/config":
                    return self.json(config())
                if self.path == "/api/v1/state":
                    current = int(time.time() * 1000)
                    payload = state(current)
                    payload["heat"]["enrollment_ends_unix_ms"] = base + 10_000
                    payload["heat"]["start_at_unix_ms"] = base + 40_000
                    payload["heat"]["end_at_unix_ms"] = base + 145_000
                    payload["heat"]["submission_ends_unix_ms"] = base + 157_000
                    payload["heat"]["next_heat_at_unix_ms"] = base + 177_000
                    if broken_quality:
                        payload["server_now_unix_ms"] = base
                    return self.json(payload)
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                if self.path != "/api/v2/session/rctf":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                if request != {
                    "team_token": "invalid-local-quality-probe",
                    "client_public_key": "",
                    "client_nonce": "",
                }:
                    return self.json({"error": "Bad Request", "detail": "invalid probe", "status": 400}, 400)
                if broken_onboarding:
                    return self.json({"status": "unexpected session"}, 201)
                return self.json({
                    "error": "Unauthorized",
                    "detail": "RSCTF KotH token was rejected",
                    "status": 401,
                }, 401)

            def json(self, value, status=200):
                encoded = json.dumps(value).encode()
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
        "RSCTF_CHALLENGE_ID": "1096",
        "RSCTF_TARGET_IP": "127.0.0.1",
        "RSCTF_TARGET_PORT": str(port),
    }


class ContractTests(unittest.TestCase):
    def test_config_requires_secure_rctf_only_protocol(self):
        self.assertTrue(CHECKER.valid_config(config()))
        self.assertFalse(CHECKER.valid_config(config(local_auth=True)))
        self.assertFalse(CHECKER.valid_config(config(server_build="dev")))
        self.assertFalse(CHECKER.valid_config(config(rulesets=["osu", "mania"])))
        self.assertFalse(CHECKER.valid_config(config(untrusted=True)))

    def test_state_checks_clock_schedule_series_and_dense_identity(self):
        now = 1_000_000
        value = state(now)
        self.assertTrue(CHECKER.valid_state(value, local_now_ms=now))
        broken = state(now)
        broken["series"]["current_leg"] = 4
        self.assertFalse(CHECKER.valid_state(broken, local_now_ms=now))
        broken = state(now)
        broken["server_now_unix_ms"] -= 60_000
        self.assertFalse(CHECKER.valid_state(broken, local_now_ms=now))

    def test_revealed_selection_must_have_complete_pinned_metadata(self):
        now = 1_000_000
        value = state(now)
        value["heat"].update({
            "phase": "reveal",
            "enrollment_ends_unix_ms": now - 1_000,
            "start_at_unix_ms": now + 30_000,
            "end_at_unix_ms": now + 135_000,
            "submission_ends_unix_ms": now + 147_000,
            "next_heat_at_unix_ms": now + 167_000,
            "selection": selection(),
        })
        self.assertTrue(CHECKER.valid_state(value, local_now_ms=now))
        value["heat"]["selection"]["archive_sha256"] = "bad"
        self.assertFalse(CHECKER.valid_state(value, local_now_ms=now))

    def test_duplicate_team_rows_are_rejected(self):
        row = {
            "team_id": "team-a", "team_name": "A", "completed_legs": 0,
            "eligible_legs": 0, "complete": False,
        }
        self.assertFalse(CHECKER.valid_standings([row, dict(row)]))


class EndToEndCheckerTests(unittest.TestCase):
    def test_live_dynamic_contract_passes(self):
        with FixtureServer() as port, patch.dict(os.environ, checker_environment(port), clear=True):
            self.assertEqual(CHECKER.main(), 0)

    def test_static_state_clock_is_mumble_not_offline(self):
        with FixtureServer(broken_quality=True) as port, patch.dict(os.environ, checker_environment(port), clear=True):
            self.assertEqual(CHECKER.main(), CHECKER.MUMBLE)

    def test_broken_onboarding_contract_is_mumble(self):
        with FixtureServer(broken_onboarding=True) as port, patch.dict(
            os.environ, checker_environment(port), clear=True
        ):
            self.assertEqual(CHECKER.main(), CHECKER.MUMBLE)

    def test_wrong_execution_context_is_internal_error(self):
        environment = checker_environment(1)
        environment["RSCTF_ACTION"] = "putflag"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(CHECKER.main(), CHECKER.INTERNAL_ERROR)

    def test_malformed_execution_context_is_internal_error(self):
        environment = checker_environment(1)
        environment["RSCTF_TARGET_PORT"] = "not-a-port"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(CHECKER.main(), CHECKER.INTERNAL_ERROR)


if __name__ == "__main__":
    unittest.main()
