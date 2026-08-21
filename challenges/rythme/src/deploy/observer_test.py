import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error


SECRET_FILE = tempfile.NamedTemporaryFile(mode="w", delete=False)
SECRET_FILE.write("koth_api_" + "a" * 48)
SECRET_FILE.close()
os.environ.update(
    {
        "RSCTF_GAME_ID": "7",
        "RSCTF_CHALLENGE_ID": "9",
        "RYTHME_ARENA_URL": "http://arena.invalid",
        "RSCTF_OBSERVER_SECRET_FILE": SECRET_FILE.name,
    }
)
observer = importlib.import_module("observer")


class ObserverScoringContractTest(unittest.TestCase):
    token_a = "a" * 64
    token_b = "b" * 64

    @classmethod
    def tearDownClass(cls):
        Path(SECRET_FILE.name).unlink(missing_ok=True)

    def context(self):
        return {
            "context": "c" * 64,
            "eligibleTokenHashes": [self.token_a, self.token_b],
            "objectiveIds": [],
            "waveWindowStartsAt": 1_000,
            "waveWindowEndsAt": 2_000,
        }

    def wave(self, wave_id, ended_at, first_score, second_score, crown):
        return {
            "wave_id": wave_id,
            "ended_at_unix_ms": ended_at,
            "crown_owner_id": crown,
            "results": [
                {
                    "team_id": self.token_a,
                    "team_name": "A",
                    "score": first_score,
                    "is_crown": crown == self.token_a,
                },
                {
                    "team_id": self.token_b,
                    "team_name": "B",
                    "score": second_score,
                    "is_crown": crown == self.token_b,
                },
            ],
        }

    def test_multiple_finalized_waves_keep_native_scores_and_one_crown_each(self):
        body = json.loads(
            observer.build_body(
                self.context(),
                {
                    "waves": [
                        self.wave("heat-2", 1_800, 20, 150, self.token_b),
                        self.wave("heat-1", 1_200, 150, 20, self.token_a),
                    ]
                },
            )
        )
        self.assertEqual([wave["waveId"] for wave in body["waves"]], ["heat-1", "heat-2"])
        for wave in body["waves"]:
            self.assertEqual(sum(team["isCrown"] for team in wave["teams"]), 1)
            self.assertEqual({team["objectives"][0]["possible"] for team in wave["teams"]}, {150})
            self.assertTrue(all(team["activity"] == {"earned": 1, "possible": 1} for team in wave["teams"]))

    def test_exact_tie_requires_no_crown(self):
        body = json.loads(
            observer.build_body(
                self.context(),
                {"waves": [self.wave("heat-tie", 1_400, 150, 150, "")]},
            )
        )
        crowned = [team["tokenHash"] for team in body["waves"][0]["teams"] if team["isCrown"]]
        self.assertEqual(crowned, [])

        invalid = self.wave("heat-tied-crown", 1_500, 150, 150, self.token_a)
        with self.assertRaisesRegex(RuntimeError, "tied wave"):
            observer.build_body(self.context(), {"waves": [invalid]})

    def test_forged_or_unrecognized_crown_is_rejected(self):
        invalid = self.wave("heat-bad", 1_400, 20, 150, self.token_a)
        with self.assertRaisesRegex(RuntimeError, "unique recognized Crown"):
            observer.build_body(self.context(), {"waves": [invalid]})

        unknown = "d" * 64
        invalid = self.wave("heat-unknown", 1_400, 20, 0, unknown)
        invalid["results"].append(
            {
                "team_id": unknown,
                "team_name": "Unknown",
                "score": 150,
                "is_crown": True,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "unique recognized Crown"):
            observer.build_body(self.context(), {"waves": [invalid]})

    def test_empty_window_is_an_explicit_no_wave_snapshot(self):
        body = json.loads(observer.build_body(self.context(), {"waves": []}))
        self.assertEqual(body["waves"], [])

    def test_finalized_wave_without_performers_is_a_valid_empty_wave(self):
        body = json.loads(
            observer.build_body(
                self.context(),
                {
                    "waves": [
                        {
                            "wave_id": "heat-empty",
                            "ended_at_unix_ms": 1_400,
                            "crown_owner_id": "",
                            "results": [],
                        }
                    ]
                },
            )
        )
        self.assertEqual(body["waves"][0]["teams"], [])

    def test_zero_score_wave_rejects_even_an_unrecognized_crown_owner(self):
        invalid = self.wave("heat-zero", 1_400, 0, 0, "f" * 64)
        with self.assertRaisesRegex(RuntimeError, "zero-score wave"):
            observer.build_body(self.context(), {"waves": [invalid]})

    def test_health_heartbeat_is_touchable(self):
        original = observer.HEARTBEAT_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                heartbeat = Path(directory) / "heartbeat"
                observer.HEARTBEAT_FILE = heartbeat
                observer.mark_healthy()
                self.assertTrue(heartbeat.is_file())
        finally:
            observer.HEARTBEAT_FILE = original

    def test_only_round_gap_conflicts_are_expected(self):
        conflict = urllib.error.HTTPError("http://rsctf", 409, "Conflict", {}, None)
        failure = urllib.error.HTTPError("http://rsctf", 500, "Failure", {}, None)
        self.assertTrue(observer.is_round_gap(conflict))
        self.assertFalse(observer.is_round_gap(failure))


if __name__ == "__main__":
    unittest.main()
