"""Regression tests for the authoritative Serpent Circuit multiplayer state."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

import app
import reporter


LOCAL_TOKENS = [f"koth_local_player_{index:05d}" for index in range(1, 6)]
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
            app.rooms.clear()
            app.seat_index.clear()
            app.active_team_seats.clear()
            app.scored_team_waves.clear()
            app.events.clear()
            app.next_cursor = 1
        app.publish_managed_context(None)
        app.reporter_runtime.set_stopped()
        app.capability_authenticator.reset()
        connection_patcher = patch.object(app.capability_authenticator, "_connect_upstream")
        connection_patcher.start()
        self.addCleanup(connection_patcher.stop)

    def create_two_player_room(self, timestamp=1_800_100):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            host = app.create_room(LOCAL_TOKENS[0], "Alice", timestamp)
            guest = app.join_room(
                LOCAL_TOKENS[1], "Bob", host["room"]["code"], timestamp + 1
            )
        return host, guest

    def test_rules_publish_the_multiplayer_contract_and_original_board(self):
        rules = app.rules_document()
        self.assertEqual(rules["protocolVersion"], 2)
        self.assertEqual(rules["startPosition"], 0)
        self.assertEqual(rules["finishPosition"], 100)
        self.assertEqual(rules["minPlayers"], 2)
        self.assertEqual(rules["maxPlayers"], 4)
        self.assertEqual(rules["turnSeconds"], 30)
        self.assertEqual(rules["objective"], "board-progress")
        self.assertEqual(len(rules["transitions"]), 14)
        self.assertFalse(set(app.TRANSITIONS) & set(app.TRANSITIONS.values()))
        self.assertIn("The first player to reach 100 wins the match.", rules["rules"])

    def test_server_applies_ladders_snakes_and_exact_finish(self):
        self.assertEqual(app.apply_roll(0, 3), (24, 3, 24))
        self.assertEqual(app.apply_roll(16, 1), (6, 17, 6))
        self.assertEqual(app.apply_roll(99, 2), (99, None, None))
        self.assertEqual(app.apply_roll(99, 1), (100, None, None))

    def test_gameplay_fails_closed_without_managed_or_local_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app.runtime_mode(), "unconfigured")
            with self.assertRaisesRegex(app.GameError, "not configured") as raised:
                app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_100)
        self.assertEqual(raised.exception.status, 503)

    def test_partial_and_invalid_managed_environments_fail_closed(self):
        with patch.dict(os.environ, {"RSCTF_KOTH_GAME_ID": "7"}, clear=True):
            self.assertEqual(app.runtime_mode(), "misconfigured")
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_100)

        invalid = dict(MANAGED_ENV)
        invalid["RSCTF_KOTH_REPORTER_SECRET"] = "not-a-target-secret"
        with patch.dict(os.environ, invalid, clear=True):
            self.assertEqual(app.runtime_mode(), "misconfigured")
            with self.assertRaisesRegex(RuntimeError, "invalid managed reporter"):
                app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_100)

    def test_create_and_join_return_private_reconnect_tokens(self):
        host, guest = self.create_two_player_room()
        room = guest["room"]
        self.assertRegex(room["code"], app.ROOM_CODE_PATTERN)
        self.assertEqual(room["status"], "lobby")
        self.assertEqual([player["name"] for player in room["players"]], ["Alice", "Bob"])
        self.assertTrue(room["players"][0]["isHost"])
        self.assertNotEqual(host["playerToken"], guest["playerToken"])
        encoded = json.dumps(guest)
        self.assertNotIn(LOCAL_TOKENS[0], encoded)
        self.assertNotIn(LOCAL_TOKENS[1], encoded)

    def test_create_retry_reconnects_to_the_active_room(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            first = app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_100)
            retry = app.create_room(LOCAL_TOKENS[0], "Different", 1_800_101)
        self.assertEqual(first["playerToken"], retry["playerToken"])
        self.assertEqual(first["room"]["code"], retry["room"]["code"])
        self.assertEqual(len(app.rooms), 1)

    def test_one_team_cannot_occupy_two_active_rooms(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            first = app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_100)
            second = app.create_room(LOCAL_TOKENS[1], "Bob", 1_800_101)
            with self.assertRaisesRegex(app.GameError, "already active") as raised:
                app.join_room(
                    LOCAL_TOKENS[0], "Alice", second["room"]["code"], 1_800_102
                )
        self.assertEqual(raised.exception.status, 409)
        self.assertNotEqual(first["room"]["code"], second["room"]["code"])

    def test_room_rejects_invalid_names_codes_and_a_fifth_player(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            with self.assertRaises(app.GameError):
                app.create_room(LOCAL_TOKENS[0], "\n", 1_800_100)
            host = app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_101)
            with self.assertRaises(app.GameError):
                app.join_room(LOCAL_TOKENS[1], "Bob", "OOOOOO", 1_800_102)
            for index in range(1, 4):
                app.join_room(
                    LOCAL_TOKENS[index],
                    f"Player {index + 1}",
                    host["room"]["code"],
                    1_800_102 + index,
                )
            with self.assertRaisesRegex(app.GameError, "full") as raised:
                app.join_room(
                    LOCAL_TOKENS[4], "Fifth", host["room"]["code"], 1_800_110
                )
        self.assertEqual(raised.exception.status, 409)

    def test_only_host_can_start_and_two_players_are_required(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            host = app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_100)
            code = host["room"]["code"]
            with self.assertRaisesRegex(app.GameError, "at least two") as too_few:
                app.start_room(code, host["playerToken"], "action_start_1", 1_800_101)
            guest = app.join_room(LOCAL_TOKENS[1], "Bob", code, 1_800_102)
            with self.assertRaisesRegex(app.GameError, "only the room host") as not_host:
                app.start_room(code, guest["playerToken"], "action_start_2", 1_800_103)
            started = app.start_room(code, host["playerToken"], "action_start_3", 1_800_104)
        self.assertEqual(too_few.exception.status, 409)
        self.assertEqual(not_host.exception.status, 403)
        self.assertEqual(started["status"], "playing")
        self.assertEqual(started["currentPlayerId"], started["you"]["id"])
        self.assertEqual(started["turnDeadline"], 1_830_104)

    def test_roll_changes_turn_and_is_idempotent(self):
        host, guest = self.create_two_player_room()
        code = host["room"]["code"]
        started = app.start_room(code, host["playerToken"], "action_start_1", 1_800_200)
        with patch.object(app.secrets, "randbelow", return_value=1) as random_die:
            result = app.roll_room(code, host["playerToken"], "action_roll_001", 1_800_201)
            replay = app.roll_room(code, host["playerToken"], "action_roll_001", 1_800_202)
        self.assertEqual(replay, result)
        self.assertEqual(result["lastMove"]["roll"], 2)
        self.assertEqual(result["lastMove"]["to"], 2)
        self.assertEqual(result["currentPlayerId"], guest["room"]["you"]["id"])
        self.assertEqual(random_die.call_count, 1)
        self.assertEqual(started["turnNumber"], 0)

    def test_rolling_six_grants_another_turn(self):
        host, _guest = self.create_two_player_room()
        code = host["room"]["code"]
        app.start_room(code, host["playerToken"], "action_start_1", 1_800_200)
        with patch.object(app.secrets, "randbelow", return_value=5):
            result = app.roll_room(code, host["playerToken"], "action_roll_001", 1_800_201)
        self.assertEqual(result["currentPlayerId"], result["you"]["id"])
        self.assertTrue(result["lastMove"]["extraTurn"])
        self.assertEqual(result["lastMove"]["roll"], 6)

    def test_wrong_player_cannot_roll(self):
        host, guest = self.create_two_player_room()
        code = host["room"]["code"]
        app.start_room(code, host["playerToken"], "action_start_1", 1_800_200)
        with self.assertRaisesRegex(app.GameError, "wait for your turn") as raised:
            app.roll_room(code, guest["playerToken"], "action_roll_001", 1_800_201)
        self.assertEqual(raised.exception.status, 409)

    def test_expired_turn_is_rolled_automatically(self):
        host, guest = self.create_two_player_room()
        code = host["room"]["code"]
        app.start_room(code, host["playerToken"], "action_start_1", 1_800_200)
        with patch.object(app.secrets, "randbelow", return_value=0):
            state = app.room_state(code, guest["playerToken"], 1_830_200)
        self.assertTrue(state["lastMove"]["automatic"])
        self.assertEqual(state["lastMove"]["roll"], 1)
        self.assertEqual(state["currentPlayerId"], state["you"]["id"])

    def test_winner_finishes_match_and_records_every_player_position(self):
        host, guest = self.create_two_player_room(timestamp=1_800_000)
        code = host["room"]["code"]
        app.start_room(code, host["playerToken"], "action_start_1", 1_800_010)
        with app.state_lock:
            room = app.rooms[code]
            room.players[0].position = 99
            room.players[1].position = 71
        with patch.object(app.secrets, "randbelow", return_value=0):
            result = app.roll_room(code, host["playerToken"], "action_roll_001", 1_800_020)

        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["winnerId"], result["you"]["id"])
        self.assertEqual(result["scoreStatus"], "recorded")
        feed = app.read_evidence_page(0)
        self.assertEqual(len(feed["events"]), 2)
        self.assertEqual(sorted(event["earned"] for event in feed["events"]), [71, 100])
        self.assertTrue(all(event["possible"] == 100 for event in feed["events"]))
        self.assertTrue(all(app.CANONICAL_HASH.fullmatch(event["tokenHash"]) for event in feed["events"]))
        encoded = json.dumps(feed)
        self.assertNotIn(LOCAL_TOKENS[0], encoded)
        self.assertNotIn(LOCAL_TOKENS[1], encoded)

    def test_exact_finish_overshoot_keeps_match_active(self):
        host, _guest = self.create_two_player_room()
        code = host["room"]["code"]
        app.start_room(code, host["playerToken"], "action_start_1", 1_800_200)
        with app.state_lock:
            app.rooms[code].players[0].position = 99
        with patch.object(app.secrets, "randbelow", return_value=1):
            result = app.roll_room(code, host["playerToken"], "action_roll_001", 1_800_201)
        self.assertEqual(result["status"], "playing")
        self.assertEqual(result["lastMove"]["from"], 99)
        self.assertEqual(result["lastMove"]["to"], 99)

    def test_finished_team_can_enter_a_new_room(self):
        host, _guest = self.create_two_player_room(timestamp=1_800_000)
        code = host["room"]["code"]
        app.start_room(code, host["playerToken"], "action_start_1", 1_800_010)
        with app.state_lock:
            app.rooms[code].players[0].position = 99
        with patch.object(app.secrets, "randbelow", return_value=0):
            app.roll_room(code, host["playerToken"], "action_roll_001", 1_800_020)
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            replacement = app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_030)
        self.assertNotEqual(replacement["room"]["code"], code)

    def test_match_turn_cap_finishes_as_a_draw(self):
        host, _guest = self.create_two_player_room()
        code = host["room"]["code"]
        app.start_room(code, host["playerToken"], "action_start_1", 1_800_200)
        with app.state_lock:
            app.rooms[code].turn_number = app.MAX_MATCH_TURNS - 1
        with patch.object(app.secrets, "randbelow", return_value=0):
            result = app.roll_room(code, host["playerToken"], "action_roll_001", 1_800_201)
        self.assertEqual(result["status"], "finished")
        self.assertIsNone(result["winnerId"])

    def test_scoring_retains_only_one_result_per_team_wave(self):
        host, guest = self.create_two_player_room(timestamp=1_800_000)
        code = host["room"]["code"]
        app.start_room(code, host["playerToken"], "action_start_1", 1_800_010)
        with app.state_lock:
            room = app.rooms[code]
            room.players[0].position = 99
            room.players[1].position = 50
        with patch.object(app.secrets, "randbelow", return_value=0):
            app.roll_room(code, host["playerToken"], "action_roll_001", 1_800_020)

        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            second = app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_030)
            second_guest = app.join_room(
                LOCAL_TOKENS[1], "Bob", second["room"]["code"], 1_800_031
            )
        app.start_room(
            second["room"]["code"], second["playerToken"], "action_start_2", 1_800_032
        )
        with app.state_lock:
            room = app.rooms[second["room"]["code"]]
            room.players[0].position = 99
        with patch.object(app.secrets, "randbelow", return_value=0):
            result = app.roll_room(
                second["room"]["code"], second["playerToken"], "action_roll_002", 1_800_040
            )
        self.assertEqual(result["scoreStatus"], "already-recorded-this-wave")
        self.assertEqual(len(app.read_evidence_page(0)["events"]), 2)
        self.assertTrue(second_guest["playerToken"])

    def test_managed_identity_is_authenticated_and_lifecycle_fenced(self):
        token_hash = hashlib.sha256(LOCAL_TOKENS[0].encode()).hexdigest()
        context = reporter.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=1_800_000,
            ends_at=1_860_001,
            cycle_ends_at=2_000_000,
            scoring_ends_at=1_980_000,
            scoring_floor=1_800_000,
            generated_at=app.now_ms(),
            eligible_hashes=frozenset({token_hash}),
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(app.capability_authenticator, "_authenticate_upstream") as authenticate,
        ):
            app.publish_managed_context(context)
            room = app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_100)
            authenticate.assert_called_once()
            next_round = reporter.RoundContext(
                **{
                    **context.__dict__,
                    "opaque": "b" * 64,
                    "round_number": 2,
                    "generated_at": context.generated_at + 1,
                }
            )
            app.publish_managed_context(next_round)
            current = app.room_state(
                room["room"]["code"], room["playerToken"], 1_800_101
            )
            self.assertEqual(current["status"], "lobby")
            changed = reporter.RoundContext(
                **{**next_round.__dict__, "opaque": "c" * 64, "reset_attempt": 1}
            )
            app.publish_managed_context(changed)
            with self.assertRaisesRegex(app.GameError, "lifecycle changed") as raised:
                app.room_state(room["room"]["code"], room["playerToken"], 1_800_102)
        self.assertEqual(raised.exception.status, 410)

    def test_unknown_managed_capability_stops_before_platform_authentication(self):
        known_hash = hashlib.sha256(LOCAL_TOKENS[0].encode()).hexdigest()
        context = reporter.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=1_800_000,
            ends_at=1_860_001,
            cycle_ends_at=2_000_000,
            scoring_ends_at=1_980_000,
            scoring_floor=1_800_000,
            generated_at=app.now_ms(),
            eligible_hashes=frozenset({known_hash}),
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(app.capability_authenticator, "_authenticate_upstream") as authenticate,
        ):
            app.publish_managed_context(context)
            with self.assertRaises(app.GameError) as raised:
                app.create_room(LOCAL_TOKENS[1], "Bob", 1_800_100)
        self.assertEqual(raised.exception.status, 403)
        authenticate.assert_not_called()

    def test_concurrent_create_requests_return_one_active_seat(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            with ThreadPoolExecutor(max_workers=16) as executor:
                results = list(
                    executor.map(
                        lambda _index: app.create_room(
                            LOCAL_TOKENS[0], "Alice", 1_800_100
                        ),
                        range(16),
                    )
                )
        self.assertEqual(len({result["playerToken"] for result in results}), 1)
        self.assertEqual(len(app.rooms), 1)

    def test_room_expiry_releases_seat_indexes(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            room = app.create_room(LOCAL_TOKENS[0], "Alice", 1_800_100)
            app.create_room(
                LOCAL_TOKENS[1], "Bob", 1_800_100 + app.LOBBY_TTL_MS + 1
            )
        self.assertNotIn(room["room"]["code"], app.rooms)
        self.assertNotIn(room["playerToken"], app.seat_index)

    def test_health_tolerates_startup_and_surfaces_persistent_reporter_failure(self):
        stop = Event()
        thread = Thread(target=stop.wait, daemon=True)
        app.reporter_runtime.attach(thread)
        thread.start()
        try:
            with patch.dict(os.environ, MANAGED_ENV, clear=True):
                status, document = app.health_document()
                self.assertEqual(status, 200)
                self.assertEqual(document["protocol"], "serpent-circuit/2")
                self.assertEqual(document["turnSeconds"], 30)

                with patch("reporter.time.monotonic", return_value=100.0):
                    for _index in range(reporter.HEALTH_FAILURE_THRESHOLD):
                        app.reporter_runtime.mark_retrying()
                with patch("reporter.time.monotonic", return_value=116.0):
                    status, document = app.health_document()
                self.assertEqual(status, 503)
                self.assertEqual(document["status"], "error")
        finally:
            stop.set()
            thread.join()

    def test_http_multiplayer_flow_and_request_shapes(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            server = app.Server(("127.0.0.1", 0), app.Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, rules = self.http_json(server, "GET", "/api/rules")
                self.assertEqual(status, 200)
                self.assertEqual(rules["minPlayers"], 2)

                status, host = self.http_json(
                    server,
                    "POST",
                    "/api/rooms",
                    {"name": "Alice", "token": LOCAL_TOKENS[0]},
                )
                self.assertEqual(status, 201)
                code = host["room"]["code"]
                status, guest = self.http_json(
                    server,
                    "POST",
                    "/api/rooms/join",
                    {"name": "Bob", "roomCode": code, "token": LOCAL_TOKENS[1]},
                )
                self.assertEqual(status, 201)
                self.assertEqual(len(guest["room"]["players"]), 2)

                status, malformed = self.http_json(
                    server,
                    "POST",
                    "/api/rooms/state",
                    {"playerToken": host["playerToken"], "roomCode": code, "extra": True},
                )
                self.assertEqual(status, 400)
                self.assertEqual(malformed, {"error": "unexpected request fields"})
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    @staticmethod
    def http_json(server, method, path, value=None):
        body = None if value is None else json.dumps(value).encode()
        headers = {"Accept": "application/json", "Connection": "close"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = HTTPConnection(*server.server_address, timeout=2)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        document = json.loads(response.read())
        status = response.status
        connection.close()
        return status, document

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
