"""Focused contract tests for the Serpent Circuit checker."""

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


CHECKER_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(CHECKER_DIRECTORY))

import run  # noqa: E402
from lib import KothContext, Mumble  # noqa: E402


VALID_TRANSITIONS = [dict(item) for item in run.EXPECTED_TRANSITIONS]
VALID_HEALTH = {
    "name": "Serpent Circuit",
    "status": "ok",
    "mode": "managed",
    "players": 0,
    "protocol": "serpent-circuit/2",
    "reporter": {
        "alive": True,
        "consecutiveFailures": 0,
        "state": "ready",
    },
    "rooms": 0,
    "scoringReady": True,
    "turnSeconds": 30,
}
VALID_RULES = {
    "name": "Serpent Circuit",
    "protocolVersion": 2,
    "boardSize": 100,
    "startPosition": 0,
    "finishPosition": 100,
    "minPlayers": 2,
    "maxPlayers": 4,
    "turnSeconds": 30,
    "scoringWaveSeconds": 60,
    "objective": "board-progress",
    "transitions": VALID_TRANSITIONS,
    "rules": list(run.EXPECTED_RULES),
}


class FakeResponse:
    def __init__(
        self,
        document: object,
        *,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
    ) -> None:
        self.status = status
        self._body = json.dumps(document).encode("utf-8")
        self._content_type = content_type

    def read(self, _amount: int) -> bytes:
        return self._body

    def getheader(self, name: str) -> str | None:
        if name.lower() == "content-type":
            return self._content_type
        return None


class FakeConnection:
    routes: dict[str, FakeResponse] = {}

    def __init__(self, host: str, port: int, *, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.path = ""

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        headers: dict[str, str],
    ) -> None:
        if method == "GET":
            if body is not None or headers != {"Connection": "close"}:
                raise AssertionError("checker issued an unexpected GET")
        elif method == "POST":
            expected_body = json.dumps(
                {"name": "checker", "token": run.INVALID_PROBE_TOKEN},
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            if (
                path != "/api/rooms"
                or body != expected_body
                or headers
                != {
                    "Accept": "application/json",
                    "Connection": "close",
                    "Content-Type": "application/json",
                }
            ):
                raise AssertionError("checker issued an unexpected capability probe")
        else:
            raise AssertionError("checker issued an unsupported method")
        self.path = path

    def getresponse(self) -> FakeResponse:
        return self.routes[self.path]

    def close(self) -> None:
        pass


class OfflineConnection(FakeConnection):
    def request(self, method: str, path: str, body=None, *, headers: dict[str, str]) -> None:
        raise OSError("connection refused")


class BrokenConnection(FakeConnection):
    def request(self, method: str, path: str, body=None, *, headers: dict[str, str]) -> None:
        raise RuntimeError("checker defect")


class SerpentCheckerTests(unittest.TestCase):
    context = KothContext(
        target_ip="127.0.0.1",
        target_port=8080,
        round_number=1,
        challenge_id=1,
    )

    def setUp(self) -> None:
        FakeConnection.routes = {
            "/healthz": FakeResponse(VALID_HEALTH),
            "/api/rules": FakeResponse(VALID_RULES),
            "/api/rooms": FakeResponse(
                {"error": "invalid current KotH capability"}, status=403
            ),
        }
        self.environment = {
            "RSCTF_ACTION": "check",
            "RSCTF_TARGET_IP": "127.0.0.1",
            "RSCTF_TARGET_PORT": "8080",
            "RSCTF_ROUND": "1",
            "RSCTF_CHALLENGE_ID": "1",
            "RSCTF_TEAM_ID": "0",
        }

    def run_verdict(self, connection: type[FakeConnection]) -> int:
        with (
            patch.dict(os.environ, self.environment, clear=True),
            patch.object(run, "HTTPConnection", connection),
        ):
            return run.run_koth_checker()

    def test_valid_contract_returns_ok(self) -> None:
        self.assertEqual(self.run_verdict(FakeConnection), 0)

    def test_wrong_contract_returns_mumble(self) -> None:
        invalid = dict(VALID_HEALTH)
        invalid["protocol"] = "serpent-circuit/1"
        FakeConnection.routes["/healthz"] = FakeResponse(invalid)
        self.assertEqual(self.run_verdict(FakeConnection), 1)

    def test_preactivation_lifecycle_is_ready_for_activation(self) -> None:
        preactivation = dict(VALID_HEALTH)
        preactivation["reporter"] = {
            "alive": True,
            "consecutiveFailures": 0,
            "state": "waiting",
        }
        preactivation["scoringReady"] = False
        FakeConnection.routes["/healthz"] = FakeResponse(preactivation)
        FakeConnection.routes["/api/rooms"] = FakeResponse(
            {"error": "the scoring roster is not ready"}, status=503
        )
        self.assertEqual(self.run_verdict(FakeConnection), 0)

    def test_stopped_or_inconsistent_reporter_returns_mumble(self) -> None:
        for diagnostics in (
            {"alive": False, "consecutiveFailures": 0, "state": "stopped"},
            {"alive": True, "consecutiveFailures": 0, "state": "retrying"},
            {"alive": True, "consecutiveFailures": 1, "state": "ready"},
            {"alive": True, "consecutiveFailures": 3, "state": "retrying"},
        ):
            with self.subTest(diagnostics=diagnostics):
                invalid = dict(VALID_HEALTH)
                invalid["reporter"] = diagnostics
                with self.assertRaises(Mumble):
                    run._validate_health(invalid)

    def test_retrying_reporter_is_visible_without_failing_http_health(self) -> None:
        retrying = dict(VALID_HEALTH)
        retrying["reporter"] = {
            "alive": True,
            "consecutiveFailures": 2,
            "state": "retrying",
        }
        run._validate_health(retrying)

    def test_non_boolean_scoring_readiness_returns_mumble(self) -> None:
        invalid = dict(VALID_HEALTH)
        invalid["scoringReady"] = 0
        FakeConnection.routes["/healthz"] = FakeResponse(invalid)
        self.assertEqual(self.run_verdict(FakeConnection), 1)

    def test_managed_invalid_capability_rejection_is_healthy(self) -> None:
        with patch.object(run, "HTTPConnection", FakeConnection):
            run._check_capability_rejection(self.context)

    def test_unconfigured_capability_probe_returns_mumble(self) -> None:
        preactivation = dict(VALID_HEALTH)
        preactivation["scoringReady"] = False
        FakeConnection.routes["/healthz"] = FakeResponse(preactivation)
        FakeConnection.routes["/api/rooms"] = FakeResponse(
            {"error": "managed gameplay is not configured"}, status=503
        )
        self.assertEqual(self.run_verdict(FakeConnection), 1)

    def test_malformed_preactivation_response_returns_mumble(self) -> None:
        preactivation = dict(VALID_HEALTH)
        preactivation["scoringReady"] = False
        FakeConnection.routes["/healthz"] = FakeResponse(preactivation)
        FakeConnection.routes["/api/rooms"] = FakeResponse(
            {"error": "the scoring roster is not ready", "retry": True}, status=503
        )
        self.assertEqual(self.run_verdict(FakeConnection), 1)

    def test_unknown_null_capability_response_returns_mumble(self) -> None:
        FakeConnection.routes["/api/rooms"] = FakeResponse(None, status=500)
        self.assertEqual(self.run_verdict(FakeConnection), 1)

    def test_unreachable_service_returns_offline(self) -> None:
        self.assertEqual(self.run_verdict(OfflineConnection), 2)

    def test_checker_defect_returns_internal_error(self) -> None:
        self.assertEqual(self.run_verdict(BrokenConnection), 3)

    def test_rules_contract_fixture_is_accepted(self) -> None:
        run._validate_rules(VALID_RULES)

    def test_rules_contract_rejects_duplicate_transition_origins(self) -> None:
        invalid = dict(VALID_RULES)
        invalid["transitions"] = [dict(item) for item in VALID_TRANSITIONS]
        invalid["transitions"][1]["from"] = VALID_TRANSITIONS[0]["from"]
        with self.assertRaises(Mumble):
            run._validate_rules(invalid)


if __name__ == "__main__":
    unittest.main()
