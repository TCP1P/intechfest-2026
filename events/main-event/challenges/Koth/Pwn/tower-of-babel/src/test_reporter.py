import hashlib
import json
import unittest
from unittest.mock import patch

import reporter


class ReporterTests(unittest.TestCase):
    def context(self, **overrides):
        value = dict(
            opaque="a" * 64,
            cycle_number=1,
            reset_attempt=0,
            round_number=1,
            window_start=60000,
            window_end=180001,
            cycle_end=180000,
            scoring_end=180000,
            scoring_floor=0,
            generated_at=1000,
            eligible_hashes=frozenset({"a" * 64, "b" * 64}),
            objective_ids=("babel-mastery",),
            objective_schema_hash=reporter._schema_hash(("babel-mastery",)),
        )
        value.update(overrides)
        return reporter.Context(**value)

    def test_body_selects_unique_native_leader(self):
        context = self.context()
        body = reporter._snapshot_body(
            context,
            [{
                "waveStart": 60000,
                "endedAtUnixMs": 120000,
                "teams": [
                    {"tokenHash": "b" * 64, "earned": 4, "possible": 10},
                    {"tokenHash": "a" * 64, "earned": 8, "possible": 10},
                ],
            }],
            130000,
        )
        value = json.loads(body)
        teams = value["waves"][0]["teams"]
        self.assertEqual(value["objectiveIds"], ["babel-mastery"])
        self.assertTrue(next(team for team in teams if team["tokenHash"] == "a" * 64)["isCrown"])
        self.assertFalse(next(team for team in teams if team["tokenHash"] == "b" * 64)["isCrown"])

    def test_tie_has_no_crown_and_window_is_half_open(self):
        context = self.context()
        body = reporter._snapshot_body(
            context,
            [
                {"waveStart": 60000, "endedAtUnixMs": 120000, "teams": [{"tokenHash": "a" * 64, "earned": 1, "possible": 1}, {"tokenHash": "b" * 64, "earned": 1, "possible": 1}]},
                {"waveStart": 120001, "endedAtUnixMs": 180001, "teams": []},
            ],
            200000,
        )
        value = json.loads(body)
        self.assertEqual(len(value["waves"]), 1)
        self.assertFalse(any(team["isCrown"] for team in value["waves"][0]["teams"]))

    def test_submit_uses_exact_hmac_message_and_ack_counts(self):
        config = reporter.Config(7, 9, "koth_target_test", "https://rctf.invalid/context", "https://rctf.invalid/observe")
        context = self.context()
        body = reporter._snapshot_body(context, [], 200000)
        expected = {
            "accepted": True,
            "acceptedAt": 200000,
            "cycleNumber": 1,
            "resetAttempt": 0,
            "roundNumber": 1,
            "submittedWaves": 0,
            "submittedTeams": 0,
            "recognizedTeams": 0,
        }
        captured = {}

        def fake_request(request, timeout):
            captured["timestamp"] = request.get_header("X-rsctf-timestamp")
            captured["signature"] = request.get_header("X-rsctf-signature")
            return expected

        with patch.object(reporter, "_json_request", side_effect=fake_request), patch.object(reporter.time, "time_ns", return_value=200000000000):
            timestamp, _ = reporter._submit(config, context, body, 0)
        message = f"{timestamp}.7.9.".encode() + body
        import hmac
        expected_signature = hmac.new(config.secret.encode(), message, hashlib.sha256).hexdigest()
        self.assertEqual(captured["signature"], f"sha256={expected_signature}")

    def test_context_request_uses_v2_and_v1_fallback_has_settlement_lag(self):
        config = reporter.Config(7, 9, "koth_target_test", "https://rctf.invalid/context", "https://rctf.invalid/observe")
        captured = {}
        legacy = {
            "apiVersion": "v1", "context": "a" * 64, "cycleNumber": 1,
            "cycleEndsAt": 180000, "resetAttempt": 0, "roundNumber": 2,
            "waveWindowStartsAt": 60000, "waveWindowEndsAt": 120000,
            "eligibleTokenHashes": [], "objectiveIds": [], "objectiveSchemaHash": None,
            "generatedAt": 100000,
        }

        def fake_request(request, timeout):
            captured["version"] = request.get_header("X-rsctf-api-version")
            captured["cache"] = request.get_header("Cache-control")
            return legacy

        with patch.object(reporter, "_json_request", side_effect=fake_request), patch.object(reporter.time, "time_ns", return_value=100000000000):
            context = reporter._fetch_context(config, None)
        self.assertEqual(captured["version"], "v2")
        self.assertEqual(captured["cache"], "no-store")
        self.assertEqual(context.scoring_end, 160000)


if __name__ == "__main__":
    unittest.main()
