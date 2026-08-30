"""Contract tests for Serpent Circuit's lifecycle-bound reporter."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import os
from threading import Thread
import unittest
from unittest.mock import patch

import reporter


TEAM_A = "a" * 64
TEAM_B = "b" * 64
OPAQUE = "c" * 64
SECRET = "koth_target_" + "s" * 43
MANAGED_ENV = {
    "RSCTF_KOTH_GAME_ID": "7",
    "RSCTF_KOTH_CHALLENGE_ID": "11",
    "RSCTF_KOTH_CONTEXT_URL": "https://rsctf.internal/context",
    "RSCTF_KOTH_OBSERVATION_URL": "https://rsctf.internal/observations",
    "RSCTF_KOTH_REPORTER_SECRET": SECRET,
}


def config(url: str = "http://127.0.0.1:9") -> reporter.Config:
    return reporter.Config(
        game_id=7,
        challenge_id=11,
        secret=SECRET,
        context_url=f"{url}/context",
        observation_url=f"{url}/observations",
        poll_seconds=1,
        timeout_seconds=2,
    )


def context(**changes) -> reporter.RoundContext:
    value = {
        "opaque": OPAQUE,
        "cycle_number": 3,
        "reset_attempt": 0,
        "round_number": 1,
        "starts_at": 0,
        "ends_at": 180_001,
        "cycle_ends_at": 300_000,
        "scoring_floor": 0,
        "generated_at": 180_000,
        "eligible_hashes": frozenset({TEAM_A, TEAM_B}),
        "objective_ids": reporter.OBJECTIVE_IDS,
        "objective_schema_hash": reporter._objective_schema_hash(reporter.OBJECTIVE_IDS),
    }
    value.update(changes)
    return reporter.RoundContext(**value)


def event(cursor: int, token_hash: str, earned: int, possible: int, wave: int = 0):
    return {
        "cursor": cursor,
        "cycleNumber": 3,
        "earned": earned,
        "occurredAt": wave + 10_000,
        "possible": possible,
        "resetAttempt": 0,
        "tokenHash": token_hash,
        "waveStart": wave,
    }


def feed(events):
    return {
        "events": events,
        "gap": False,
        "hasMore": False,
        "latestCursor": events[-1]["cursor"] if events else 0,
        "nextCursor": events[-1]["cursor"] if events else 0,
    }


class CaptureServer:
    def __init__(self, acknowledgement, status=200):
        self.requests = []
        self.status = status
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = self.rfile.read(length)
                owner.requests.append((dict(self.headers), body))
                encoded = json.dumps(acknowledgement).encode()
                self.send_response(owner.status)
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
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"


class ConfigurationTests(unittest.TestCase):
    def test_absent_environment_disables_reporting(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(reporter.load_config())

    def test_partial_environment_fails_closed(self):
        with patch.dict(os.environ, {"RSCTF_KOTH_GAME_ID": "7"}, clear=True):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                reporter.load_config()

    def test_managed_poll_defaults_to_one_second(self):
        with patch.dict(os.environ, MANAGED_ENV, clear=True):
            self.assertEqual(reporter.load_config().poll_seconds, 1)

    def test_empty_roster_context_can_publish_empty_finalized_waves(self):
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        raw_context = {
            "apiVersion": "v1",
            "context": OPAQUE,
            "cycleNumber": 3,
            "cycleEndsAt": 300_000,
            "resetAttempt": 0,
            "roundNumber": 1,
            "waveWindowStartsAt": 0,
            "waveWindowEndsAt": 180_001,
            "eligibleTokenHashes": [],
            "objectiveIds": [],
            "objectiveSchemaHash": None,
            "generatedAt": 180_000,
        }
        with (
            patch.object(client, "_get_json", return_value=raw_context),
            patch("reporter.time.time_ns", return_value=180_000_000_000),
        ):
            round_context = client.fetch_context()
        self.assertEqual(round_context.eligible_hashes, frozenset())
        body = json.loads(client._body(round_context, 182_001))
        self.assertEqual(body["objectiveIds"], ["route-efficiency"])
        self.assertTrue(body["waves"])
        self.assertTrue(all(wave["teams"] == [] for wave in body["waves"]))

    def test_replayed_context_expires_instead_of_refreshing_gameplay(self):
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        raw_context = {
            "apiVersion": "v1",
            "context": OPAQUE,
            "cycleNumber": 3,
            "cycleEndsAt": 300_000,
            "resetAttempt": 0,
            "roundNumber": 1,
            "waveWindowStartsAt": 0,
            "waveWindowEndsAt": 180_001,
            "eligibleTokenHashes": [TEAM_A],
            "objectiveIds": [],
            "objectiveSchemaHash": None,
            "generatedAt": 180_000,
        }
        with (
            patch.object(client, "_get_json", return_value=raw_context),
            patch("reporter.time.time_ns", return_value=180_000_000_000),
        ):
            first = client.fetch_context()
        client.context = first
        with (
            patch.object(client, "_get_json", return_value=raw_context),
            patch(
                "reporter.time.time_ns",
                return_value=(180_000 + reporter.CONTEXT_MAX_AGE_MS + 1) * 1_000_000,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                client.fetch_context()

    def test_roster_window_product_must_fit_one_rsctf_snapshot(self):
        hashes = [f"{index:064x}" for index in range(1_001)]
        raw_context = {
            "apiVersion": "v1",
            "context": OPAQUE,
            "cycleNumber": 3,
            "cycleEndsAt": 300_000,
            "resetAttempt": 0,
            "roundNumber": 1,
            "waveWindowStartsAt": 0,
            "waveWindowEndsAt": 180_001,
            "eligibleTokenHashes": hashes,
            "objectiveIds": [],
            "objectiveSchemaHash": None,
            "generatedAt": 180_000,
        }
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        with (
            patch.object(client, "_get_json", return_value=raw_context),
            patch("reporter.time.time_ns", return_value=180_000_000_000),
        ):
            with self.assertRaisesRegex(RuntimeError, "team-wave row bound"):
                client.fetch_context()

    def test_cycle_start_field_restores_exact_floor_after_late_target_start(self):
        raw_context = {
            "apiVersion": "v1",
            "context": OPAQUE,
            "cycleNumber": 3,
            "cycleStartsAt": 10_000,
            "cycleEndsAt": 300_000,
            "resetAttempt": 0,
            "roundNumber": 2,
            "waveWindowStartsAt": 50_000,
            "waveWindowEndsAt": 180_001,
            "eligibleTokenHashes": [TEAM_A],
            "objectiveIds": [],
            "objectiveSchemaHash": None,
            "generatedAt": 70_000,
        }
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        with (
            patch.object(client, "_get_json", return_value=raw_context),
            patch("reporter.time.time_ns", return_value=70_000_000_000),
        ):
            round_context = client.fetch_context()
        self.assertEqual(round_context.scoring_floor, 60_000)


class EvidenceTests(unittest.TestCase):
    def test_unique_positive_leader_gets_crown_and_rows_are_sorted(self):
        client = reporter.ManagedReporter(
            config(), lambda _cursor: feed([event(1, TEAM_B, 4, 10), event(2, TEAM_A, 9, 10)])
        )
        round_context = context()
        client._consume_feed(round_context)
        body = json.loads(client._body(round_context, 180_001 + 2_000))
        teams = body["waves"][0]["teams"]
        self.assertEqual([row["tokenHash"] for row in teams], [TEAM_A, TEAM_B])
        self.assertEqual([row["isCrown"] for row in teams], [True, False])
        self.assertEqual(teams[0]["objectives"], [{"earned": 9, "possible": 10}])

    def test_tie_and_zero_results_have_no_crown(self):
        for earned in (0, 5):
            client = reporter.ManagedReporter(
                config(),
                lambda _cursor, earned=earned: feed(
                    [event(1, TEAM_A, earned, 10), event(2, TEAM_B, earned, 10)]
                ),
            )
            round_context = context()
            client._consume_feed(round_context)
            body = json.loads(client._body(round_context, 182_001))
            self.assertFalse(any(row["isCrown"] for row in body["waves"][0]["teams"]))

    def test_ineligible_identity_is_filtered(self):
        outsider = "d" * 64
        client = reporter.ManagedReporter(
            config(), lambda _cursor: feed([event(1, outsider, 10, 10)])
        )
        round_context = context()
        client._consume_feed(round_context)
        body = json.loads(client._body(round_context, 182_001))
        self.assertEqual(body["waves"][0]["teams"], [])

    def test_duplicate_team_result_and_forged_ratio_fail_closed(self):
        client = reporter.ManagedReporter(
            config(), lambda _cursor: feed([event(1, TEAM_A, 5, 10), event(2, TEAM_A, 6, 10)])
        )
        with self.assertRaisesRegex(RuntimeError, "more than one"):
            client._consume_feed(context())

        client = reporter.ManagedReporter(
            config(), lambda _cursor: feed([event(1, TEAM_A, 11, 10)])
        )
        with self.assertRaisesRegex(RuntimeError, "invalid earned"):
            client._consume_feed(context())

    def test_evidence_cursors_must_be_contiguous(self):
        client = reporter.ManagedReporter(
            config(), lambda _cursor: feed([event(2, TEAM_A, 5, 10)])
        )
        with self.assertRaisesRegex(RuntimeError, "not contiguous"):
            client._consume_feed(context())

    def test_roster_rotation_prunes_rows_and_reassigns_exact_crown(self):
        client = reporter.ManagedReporter(
            config(), lambda _cursor: feed([event(1, TEAM_A, 9, 10), event(2, TEAM_B, 5, 10)])
        )
        original = context()
        client._consume_feed(original)
        first_body = json.loads(client._body(original, 182_001))
        self.assertTrue(first_body["waves"][0]["teams"][0]["isCrown"])

        rotated = context(opaque="d" * 64, eligible_hashes=frozenset({TEAM_B}))
        client._prune_ineligible(rotated)
        rotated_body = json.loads(client._body(rotated, 182_001))
        teams = rotated_body["waves"][0]["teams"]
        self.assertEqual([row["tokenHash"] for row in teams], [TEAM_B])
        self.assertEqual([row["isCrown"] for row in teams], [True])
        submitted = {
            row["tokenHash"]
            for wave in rotated_body["waves"]
            for row in wave["teams"]
        }
        self.assertEqual(submitted, {TEAM_B})

    def test_prior_lifecycle_evidence_is_ignored_after_reset(self):
        prior = event(1, TEAM_A, 10, 10)
        prior["resetAttempt"] = 0
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([prior]))
        reset = context(reset_attempt=1)
        client._consume_feed(reset)
        body = json.loads(client._body(reset, 182_001))
        self.assertEqual(body["waves"][0]["teams"], [])

    def test_round_one_skips_partial_wave_for_unaligned_context_start(self):
        round_context = context(starts_at=10_000)
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        body = json.loads(client._body(round_context, 182_001))
        self.assertEqual(
            [wave["waveId"] for wave in body["waves"]],
            ["serpent-60000", "serpent-120000"],
        )

    def test_round_one_starts_at_aligned_context_boundary(self):
        round_context = context(starts_at=60_000)
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        body = json.loads(client._body(round_context, 182_001))
        self.assertEqual(
            [wave["waveId"] for wave in body["waves"]],
            ["serpent-60000", "serpent-120000"],
        )

    def test_later_round_keeps_the_contiguous_crossing_wave(self):
        round_context = context(starts_at=10_000, round_number=2)
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        body = json.loads(client._body(round_context, 182_001))
        self.assertEqual(
            [wave["waveId"] for wave in body["waves"]],
            ["serpent-0", "serpent-60000", "serpent-120000"],
        )

    def test_later_round_never_reintroduces_the_event_start_partial_wave(self):
        round_context = context(
            starts_at=50_000,
            round_number=2,
            scoring_floor=60_000,
        )
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        body = json.loads(client._body(round_context, 182_001))
        self.assertEqual(
            [wave["waveId"] for wave in body["waves"]],
            ["serpent-60000", "serpent-120000"],
        )


class SubmissionTests(unittest.TestCase):
    def acknowledgement(self, **changes):
        value = {
            "accepted": True,
            "acceptedAt": 1_900_000,
            "cycleNumber": 3,
            "recognizedTeams": 1,
            "resetAttempt": 0,
            "roundNumber": 1,
            "submittedTeams": 1,
            "submittedWaves": 1,
        }
        value.update(changes)
        return value

    def test_exact_body_is_signed_and_full_acknowledgement_is_required(self):
        body = json.dumps(
            {
                "context": OPAQUE,
                "objectiveIds": list(reporter.OBJECTIVE_IDS),
                "waves": [
                    {
                        "endedAtUnixMs": 60_000,
                        "teams": [
                            {
                                "activity": {"earned": 1, "possible": 1},
                                "isCrown": True,
                                "objectives": [{"earned": 9, "possible": 10}],
                                "tokenHash": TEAM_A,
                            }
                        ],
                        "waveId": "serpent-0",
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        with CaptureServer(self.acknowledgement()) as server:
            client = reporter.ManagedReporter(config(server.url), lambda _cursor: feed([]))
            with patch("reporter.time.time_ns", return_value=1_800_000_000_000):
                client.submit(context(), body)
            headers, captured = server.requests[0]
        self.assertEqual(captured, body)
        headers = {name.lower(): value for name, value in headers.items()}
        timestamp = headers["x-rsctf-timestamp"]
        expected = hmac.new(
            SECRET.encode(), f"{timestamp}.7.11.".encode() + body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(headers["x-rsctf-signature"], f"sha256={expected}")

    def test_acknowledgement_without_accepted_at_is_rejected(self):
        acknowledgement = self.acknowledgement()
        acknowledgement.pop("acceptedAt")
        body = json.dumps(
            {"context": OPAQUE, "objectiveIds": list(reporter.OBJECTIVE_IDS), "waves": []},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        acknowledgement.update(
            {"recognizedTeams": 0, "submittedTeams": 0, "submittedWaves": 0}
        )
        with CaptureServer(acknowledgement) as server:
            client = reporter.ManagedReporter(config(server.url), lambda _cursor: feed([]))
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                client.submit(context(), body)

    def test_contract_json_on_http_202_is_not_an_acknowledgement(self):
        body = json.dumps(
            {"context": OPAQUE, "objectiveIds": list(reporter.OBJECTIVE_IDS), "waves": []},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        acknowledgement = self.acknowledgement(
            recognizedTeams=0,
            submittedTeams=0,
            submittedWaves=0,
        )
        with CaptureServer(acknowledgement, status=202) as server:
            client = reporter.ManagedReporter(config(server.url), lambda _cursor: feed([]))
            with self.assertRaises(reporter.ReporterHttpError) as rejected:
                client.submit(context(), body)
        self.assertEqual(rejected.exception.status, 202)

    def test_timestamps_are_strictly_monotonic(self):
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]))
        with patch("reporter.time.time_ns", return_value=1_000_000_000):
            self.assertEqual(client._next_timestamp(), "1000")
            self.assertEqual(client._next_timestamp(), "1001")

    def test_context_rejection_rolls_back_cursor_and_evidence(self):
        published = []
        client = reporter.ManagedReporter(
            config(),
            lambda _cursor: feed([event(1, TEAM_A, 9, 10)]),
            published.append,
        )
        with (
            patch.object(client, "fetch_context", return_value=context()),
            patch.object(
                client,
                "submit",
                side_effect=reporter.ReporterHttpError(409, "context rotated"),
            ),
            patch("reporter.time.time_ns", return_value=182_001_000_000),
        ):
            with self.assertRaises(reporter.ReporterHttpError):
                client.poll_once()
        self.assertEqual(client.cursor, 0)
        self.assertEqual(client.waves, {})
        self.assertIsNone(client.context)
        self.assertEqual(published, [None])

    def test_future_evidence_is_context_fenced_before_cursor_commit(self):
        round_context = context()
        future = event(1, TEAM_A, 9, 10, wave=180_000)
        published = []
        client = reporter.ManagedReporter(
            config(), lambda _cursor: feed([future]), published.append
        )
        client.context = round_context
        with patch("reporter.time.time_ns", return_value=182_001_000_000):
            unchanged_body = client._body(round_context, 182_001)
        client.last_submitted_digest = hashlib.sha256(unchanged_body).hexdigest()
        acknowledgement = self.acknowledgement(
            recognizedTeams=0,
            submittedTeams=0,
            submittedWaves=3,
        )
        with (
            patch.object(client, "fetch_context", return_value=round_context),
            patch.object(client, "submit", return_value=acknowledgement) as submit,
            patch("reporter.time.time_ns", return_value=182_001_000_000),
        ):
            self.assertTrue(client.poll_once())
        submit.assert_called_once()
        self.assertEqual(client.cursor, 1)
        self.assertEqual(published, [round_context])

    def test_lifecycle_reset_keeps_the_last_acked_process_cursor(self):
        original = context()
        reset = context(opaque="d" * 64, reset_attempt=1)
        published = []
        client = reporter.ManagedReporter(config(), lambda _cursor: feed([]), published.append)
        client.context = original
        client.cursor = 10_001
        client.waves = {0: {TEAM_A: reporter.TeamResult(9, 10)}}
        client.finalized_crowns = {0: TEAM_A}
        acknowledgement = self.acknowledgement(
            recognizedTeams=0,
            resetAttempt=1,
            submittedTeams=0,
            submittedWaves=3,
        )
        with (
            patch.object(client, "fetch_context", return_value=reset),
            patch.object(client, "_consume_feed", return_value=False),
            patch.object(client, "submit", return_value=acknowledgement),
            patch("reporter.time.time_ns", return_value=182_001_000_000),
        ):
            self.assertTrue(client.poll_once())
        self.assertEqual(client.cursor, 10_001)
        self.assertEqual(client.waves, {})
        self.assertEqual(client.finalized_crowns, {0: None, 60_000: None, 120_000: None})
        self.assertEqual(published, [None, reset])


if __name__ == "__main__":
    unittest.main()
