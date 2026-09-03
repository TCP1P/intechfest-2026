"""Regression tests for Serpent Circuit's authoritative fair matchmaking."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event, Thread
import unittest
from unittest.mock import patch

import app
import reporter


LOCAL_TOKENS = [f"koth_local_player_{index:05d}" for index in range(1, 17)]
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
            app.pending_team_waves.clear()
            app.events.clear()
            app.next_cursor = 1
        app.publish_managed_context(None)
        with app.managed_context_lock:
            app.managed_lifecycle = None
        app.reporter_runtime.set_stopped()
        app.capability_authenticator.reset()
        connection_patcher = patch.object(app.capability_authenticator, "_connect_upstream")
        connection_patcher.start()
        self.addCleanup(connection_patcher.stop)

    def enqueue(self, count, *, first_token=0, timestamp=1_800_000):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            return [
                app.matchmake(
                    LOCAL_TOKENS[first_token + index],
                    f"Player {first_token + index + 1}",
                    timestamp + index,
                )
                for index in range(count)
            ]

    def active_match(self, *, timestamp=1_800_000):
        seats = self.enqueue(4, timestamp=timestamp)
        code = app.seat_index[seats[0]["playerToken"]][0]
        return seats, code

    def test_rules_publish_four_seat_npc_fill_contract(self):
        rules = app.rules_document()
        self.assertEqual(rules["protocolVersion"], 6)
        self.assertEqual(rules["minPlayers"], 4)
        self.assertEqual(rules["maxPlayers"], 4)
        self.assertEqual(rules["matchmakingSeconds"], 15)
        self.assertEqual(rules["matchSeconds"], 300)
        self.assertEqual(rules["turnSeconds"], 30)
        self.assertEqual(rules["objective"], "board-progress")
        self.assertEqual(len(rules["transitions"]), 14)
        text = " ".join(rules["rules"])
        self.assertIn("server-controlled FIFO queue", text)
        self.assertIn("exactly four seats", text)
        self.assertIn("server-controlled NPCs", text)
        self.assertIn("highest square", text)
        self.assertNotIn("private room", text.lower())

    def test_server_applies_ladders_snakes_and_exact_finish(self):
        self.assertEqual(app.apply_roll(0, 3), (24, 3, 24))
        self.assertEqual(app.apply_roll(16, 1), (6, 17, 6))
        self.assertEqual(app.apply_roll(99, 2), (99, None, None))
        self.assertEqual(app.apply_roll(99, 1), (100, None, None))

    def test_gameplay_fails_closed_without_managed_or_local_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(app.GameError, "not configured") as raised:
                app.matchmake(LOCAL_TOKENS[0], "Alice", 1_800_100)
        self.assertEqual(raised.exception.status, 503)

    def test_partial_managed_environment_fails_closed(self):
        with patch.dict(os.environ, {"RSCTF_KOTH_GAME_ID": "7"}, clear=True):
            self.assertEqual(app.runtime_mode(), "misconfigured")
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                app.matchmake(LOCAL_TOKENS[0], "Alice", 1_800_100)

    def test_match_starts_immediately_on_fourth_distinct_team(self):
        seats = self.enqueue(3)
        self.assertTrue(all(seat["room"]["status"] == "waiting" for seat in seats))
        fourth = self.enqueue(1, first_token=3, timestamp=1_800_003)[0]
        self.assertEqual(fourth["room"]["status"], "playing")
        self.assertEqual(len(fourth["room"]["players"]), 4)
        self.assertEqual(fourth["room"]["matchDeadline"], 2_100_003)
        self.assertEqual(fourth["room"]["turnDeadline"], 1_830_003)
        self.assertNotIn("code", fourth["room"])
        encoded = json.dumps(fourth)
        self.assertFalse(any(token in encoded for token in LOCAL_TOKENS[:4]))

    def test_match_countdown_fills_empty_seats_with_npcs(self):
        seat = self.enqueue(1)[0]
        self.assertEqual(seat["room"]["status"], "waiting")
        self.assertEqual(seat["room"]["matchStartsAt"], 1_815_000)

        state = app.room_state(seat["playerToken"], 1_815_000)

        self.assertEqual(state["status"], "playing")
        self.assertIsNone(state["matchStartsAt"])
        self.assertEqual(len(state["players"]), 4)
        self.assertEqual(sum(player["isNpc"] for player in state["players"]), 3)
        self.assertFalse(next(player for player in state["players"] if player["id"] == state["you"]["id"])["isNpc"])
        self.assertEqual(len(app.seat_index), 1)

    def test_npc_turn_uses_short_automatic_deadline(self):
        seat = self.enqueue(1)[0]
        state = app.room_state(seat["playerToken"], 1_815_000)
        with patch.object(app.secrets, "randbelow", return_value=0):
            state = app.roll_room(seat["playerToken"], "action_human_01", 1_815_001)
            self.assertTrue(next(player for player in state["players"] if player["id"] == state["currentPlayerId"])["isNpc"])
            self.assertEqual(state["turnDeadline"], 1_817_001)
            state = app.room_state(seat["playerToken"], 1_817_001)
        self.assertTrue(state["lastMove"]["automatic"])
        self.assertTrue(next(player for player in state["players"] if player["id"] == state["lastMove"]["playerId"])["isNpc"])

    def test_matchmaking_retry_is_idempotent_and_cannot_duplicate_a_team(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            first = app.matchmake(LOCAL_TOKENS[0], "Alice", 1_800_000)
            retry = app.matchmake(LOCAL_TOKENS[0], "Different name", 1_800_001)
        self.assertEqual(first["playerToken"], retry["playerToken"])
        self.assertEqual(len(app.rooms), 1)
        self.assertEqual(len(next(iter(app.rooms.values())).players), 1)

    def test_concurrent_matchmaking_returns_one_active_seat(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            with ThreadPoolExecutor(max_workers=16) as executor:
                results = list(
                    executor.map(
                        lambda _: app.matchmake(LOCAL_TOKENS[0], "Alice", 1_800_000),
                        range(16),
                    )
                )
        self.assertEqual(len({result["playerToken"] for result in results}), 1)
        self.assertEqual(len(app.seat_index), 1)

    def test_later_group_waits_then_starts_automatically(self):
        first = self.enqueue(4)
        second = self.enqueue(4, first_token=4, timestamp=1_800_010)
        self.assertEqual(second[-1]["room"]["status"], "waiting")
        self.assertEqual(second[-1]["room"]["queueAhead"], 1)

        first_code = app.seat_index[first[0]["playerToken"]][0]
        second_code = app.seat_index[second[0]["playerToken"]][0]
        with app.state_lock:
            first_room = app.rooms[first_code]
            observed_at = first_room.match_deadline_ms + 5_000
            for player in app.rooms[second_code].players:
                player.last_seen_ms = observed_at - 1
        state = app.room_state(second[0]["playerToken"], observed_at)

        self.assertEqual(app.rooms[first_code].status, "finished")
        self.assertEqual(state["status"], "playing")
        self.assertEqual(state["queueAhead"], 0)
        self.assertEqual(
            state["matchDeadline"], observed_at + app.MATCH_SECONDS * 1_000
        )

    def test_abandoned_waiting_seat_expires_without_blocking_group(self):
        seats = self.enqueue(3)
        with app.state_lock:
            room_code = app.seat_index[seats[0]["playerToken"]][0]
            app.rooms[room_code].players[1].last_seen_ms = 1_700_000
        replacement = self.enqueue(1, first_token=3, timestamp=1_800_001)[0]
        self.assertEqual(replacement["room"]["status"], "waiting")
        self.assertEqual(len(replacement["room"]["players"]), 3)
        self.assertNotIn(seats[1]["playerToken"], app.seat_index)

    def test_timeout_uses_unique_highest_position(self):
        seats, code = self.active_match()
        with app.state_lock:
            room = app.rooms[code]
            for player, position in zip(room.players, [62, 81, 40, 79], strict=True):
                player.position = position
            deadline = room.match_deadline_ms
        result = app.room_state(seats[0]["playerToken"], deadline)
        winner = next(player for player in result["players"] if player["position"] == 81)
        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["winnerId"], winner["id"])
        self.assertIsNone(result["matchDeadline"])

    def test_timeout_top_tie_has_no_winner(self):
        seats, code = self.active_match()
        with app.state_lock:
            room = app.rooms[code]
            for player, position in zip(room.players, [80, 80, 50, 10], strict=True):
                player.position = position
            deadline = room.match_deadline_ms
        result = app.room_state(seats[0]["playerToken"], deadline)
        self.assertEqual(result["status"], "finished")
        self.assertIsNone(result["winnerId"])

    def test_reaching_square_100_finishes_before_deadline(self):
        seats, code = self.active_match()
        with app.state_lock:
            app.rooms[code].players[0].position = 99
        with patch.object(app.secrets, "randbelow", return_value=0):
            result = app.roll_room(seats[0]["playerToken"], "action_roll_001", 1_800_010)
        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["winnerId"], result["you"]["id"])

    def test_roll_is_idempotent_and_turn_bound(self):
        seats, _code = self.active_match()
        with self.assertRaisesRegex(app.GameError, "wait for your turn") as wrong:
            app.roll_room(seats[1]["playerToken"], "action_wrong_01", 1_800_010)
        self.assertEqual(wrong.exception.status, 409)
        with patch.object(app.secrets, "randbelow", return_value=1) as die:
            result = app.roll_room(seats[0]["playerToken"], "action_roll_001", 1_800_011)
            replay = app.roll_room(seats[0]["playerToken"], "action_roll_001", 1_800_012)
        self.assertEqual(replay, result)
        self.assertEqual(result["lastMove"]["roll"], 2)
        self.assertEqual(die.call_count, 1)

    def test_expired_turn_rolls_automatically(self):
        seats, _code = self.active_match()
        with patch.object(app.secrets, "randbelow", return_value=0):
            result = app.room_state(seats[1]["playerToken"], 1_830_003)
        self.assertTrue(result["lastMove"]["automatic"])
        self.assertEqual(result["lastMove"]["roll"], 1)
        self.assertEqual(result["currentPlayerId"], result["you"]["id"])

    def test_completed_tick_records_four_furthest_positions(self):
        _seats, code = self.active_match()
        with app.state_lock:
            room = app.rooms[code]
            for player, position in zip(room.players, [20, 40, 60, 80], strict=True):
                player.position = position
            room.turn_deadline_ms = 2_000_000
            app._record_room_progress_locked(room, 1_800_000)
        feed = app.read_evidence_page(0, current_time_ms=1_860_000)
        self.assertEqual(len(feed["events"]), 4)
        self.assertEqual(sorted(event["earned"] for event in feed["events"]), [20, 40, 60, 80])
        self.assertTrue(all(event["possible"] == 100 for event in feed["events"]))

    def test_npc_match_records_only_authenticated_human_progress(self):
        seat = self.enqueue(1)[0]
        app.room_state(seat["playerToken"], 1_815_000)
        with app.state_lock:
            code = app.seat_index[seat["playerToken"]][0]
            room = app.rooms[code]
            for player, position in zip(room.players, [40, 60, 80, 90], strict=True):
                player.position = position
            room.turn_deadline_ms = 2_000_000
            app._record_room_progress_locked(room, 1_800_000)
        feed = app.read_evidence_page(0, current_time_ms=1_860_000)
        self.assertEqual(len(feed["events"]), 1)
        self.assertEqual(feed["events"][0]["earned"], 40)

    def test_delayed_checkpoint_never_records_after_match_deadline(self):
        _seats, code = self.active_match()
        with app.state_lock:
            deadline = app.rooms[code].match_deadline_ms
        feed = app.read_evidence_page(0, current_time_ms=deadline + 180_000)
        self.assertTrue(feed["events"])
        self.assertLessEqual(
            max(event["waveStart"] for event in feed["events"]),
            app.wave_start(deadline),
        )

    def test_managed_identity_authenticates_and_lifecycle_is_fenced(self):
        hashes = frozenset(hashlib.sha256(token.encode()).hexdigest() for token in LOCAL_TOKENS[:4])
        context = self.managed_context(hashes)
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(app.capability_authenticator, "_authenticate_upstream") as authenticate,
        ):
            app.publish_managed_context(context)
            seat = app.matchmake(LOCAL_TOKENS[0], "Alice", 1_800_000)
            authenticate.assert_called_once()
            app.publish_managed_context(
                reporter.RoundContext(**{**context.__dict__, "opaque": "c" * 64, "reset_attempt": 1})
            )
            with self.assertRaisesRegex(app.GameError, "invalid match player token") as raised:
                app.room_state(seat["playerToken"], 1_800_001)
        self.assertEqual(raised.exception.status, 403)

    def test_unknown_managed_capability_stops_before_authentication(self):
        known_hash = hashlib.sha256(LOCAL_TOKENS[0].encode()).hexdigest()
        with (
            patch.dict(os.environ, MANAGED_ENV, clear=True),
            patch.object(app.capability_authenticator, "_authenticate_upstream") as authenticate,
        ):
            app.publish_managed_context(self.managed_context(frozenset({known_hash})))
            with self.assertRaises(app.GameError) as raised:
                app.matchmake(LOCAL_TOKENS[1], "Bob", 1_800_000)
        self.assertEqual(raised.exception.status, 403)
        authenticate.assert_not_called()

    @staticmethod
    def managed_context(hashes):
        return reporter.RoundContext(
            opaque="a" * 64,
            cycle_number=3,
            reset_attempt=0,
            round_number=1,
            starts_at=1_800_000,
            ends_at=1_860_001,
            cycle_ends_at=2_400_000,
            scoring_ends_at=2_340_000,
            scoring_floor=1_800_000,
            generated_at=app.now_ms(),
            eligible_hashes=hashes,
            objective_ids=(app.OBJECTIVE_ID,),
            objective_schema_hash="b" * 64,
        )

    def test_health_surfaces_protocol_and_reporter_failure(self):
        stop = Event()
        thread = Thread(target=stop.wait, daemon=True)
        app.reporter_runtime.attach(thread)
        thread.start()
        try:
            with patch.dict(os.environ, MANAGED_ENV, clear=True):
                status, document = app.health_document()
                self.assertEqual(status, 200)
                self.assertEqual(document["protocol"], "serpent-circuit/6")
                with patch("reporter.time.monotonic", return_value=100.0):
                    for _ in range(reporter.HEALTH_FAILURE_THRESHOLD):
                        app.reporter_runtime.mark_retrying()
                with patch("reporter.time.monotonic", return_value=116.0):
                    status, document = app.health_document()
                self.assertEqual(status, 503)
                self.assertEqual(document["status"], "error")
        finally:
            stop.set()
            thread.join()

    def test_http_contract_rejects_old_private_room_routes(self):
        with patch.dict(os.environ, {"SERPENT_LOCAL_DEV": "1"}, clear=True):
            server = app.Server(("127.0.0.1", 0), app.Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, rules = self.http_json(server, "GET", "/api/rules")
                self.assertEqual(status, 200)
                self.assertEqual(rules["minPlayers"], 4)
                seats = []
                for index in range(4):
                    status, seat = self.http_json(
                        server,
                        "POST",
                        "/api/matchmaking",
                        {"name": f"Player {index + 1}", "token": LOCAL_TOKENS[index]},
                    )
                    self.assertEqual(status, 201)
                    seats.append(seat)
                self.assertEqual(seats[-1]["room"]["status"], "playing")
                status, state = self.http_json(
                    server,
                    "POST",
                    "/api/matchmaking/state",
                    {"playerToken": seats[0]["playerToken"]},
                )
                self.assertEqual(status, 200)
                self.assertEqual(state["status"], "playing")
                for path in ("/api/rooms", "/api/rooms/join", "/api/rooms/start", "/api/rooms/npc"):
                    status, _ = self.http_json(server, "POST", path, {"unused": True})
                    self.assertEqual(status, 404)
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
