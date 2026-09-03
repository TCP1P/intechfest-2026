import io
import json
import os
import unittest
from unittest.mock import patch

import main


class PatchTests(unittest.TestCase):
    def test_empty_patch_and_non_syscall_pattern_are_canonical(self):
        self.assertEqual(main.validate_patch({"patterns": []}), ())
        self.assertEqual(
            main.validate_patch({"patterns": [{"arch": "amd64", "hex": "4889"}]}),
            (("amd64", "4889"),),
        )

    def test_mandatory_syscall_opcode_cannot_be_warded(self):
        with self.assertRaises(ValueError):
            main.validate_patch({"patterns": [{"arch": "amd64", "hex": "0f05"}]})
        with self.assertRaises(ValueError):
            main.validate_patch({"patterns": [{"arch": "aarch64", "hex": "00d4"}]})


class ScoringTests(unittest.TestCase):
    def test_first_verified_solver_can_activate_a_known_good_patch(self):
        state = main.ArenaState()
        wave = main._new_wave(0, ())
        state._waves[0] = wave
        state._latest_start = 0
        self.assertTrue(state.register_first_solver(wave, "a" * 64, "aa", 100))
        self.assertTrue(state.can_patch(wave, "a" * 64))
        self.assertFalse(state.can_patch(wave, "b" * 64))
        revision = state.activate_patch(wave, "a" * 64, (("amd64", "4889"),))
        self.assertEqual(revision, 1)
        state.record(wave, "a" * 64, main.Attempt(4, 100, True, (("amd64", "4889"),)))
        state.record(wave, "b" * 64, main.Attempt(3, 100, True))
        state._finalize_locked(wave, 100000)
        self.assertEqual(wave.crown_hash, "a" * 64)
        self.assertEqual(state._active_patch, (("amd64", "4889"),))

    def test_rejected_patch_keeps_previous_ward_visible_as_fallback(self):
        state = main.ArenaState()
        wave = main._new_wave(0, (("amd64", "4889"),), 4, "c" * 64)
        state._waves[0] = wave
        state._latest_start = 0
        state.reject_patch(wave)
        self.assertEqual(wave.active_patch, (("amd64", "4889"),))
        self.assertEqual(wave.ward_status, "rejected")
        self.assertEqual(state._ward_document(wave)["fallback"], "previous-known-good")

    def test_accepted_ward_carries_into_the_next_wave(self):
        state = main.ArenaState()
        first = main._new_wave(0, ())
        state._waves[0] = first
        state._latest_start = 0
        self.assertTrue(state.register_first_solver(first, "a" * 64, "aa", 100))
        self.assertEqual(state.activate_patch(first, "a" * 64, (("amd64", "aabb"),)), 1)
        next_wave = state._ensure_current_locked(main.WAVE_DURATION_MS)
        self.assertEqual(next_wave.active_patch, (("amd64", "aabb"),))
        self.assertEqual(next_wave.patch_revision, 1)
        self.assertEqual(next_wave.ward_status, "active")

    def test_exact_tie_has_no_crown_and_keeps_patch(self):
        state = main.ArenaState()
        wave = main._new_wave(0, (("amd64", "4889"),))
        state._active_patch = wave.active_patch
        state._waves[0] = wave
        state._latest_start = 0
        state.record(wave, "a" * 64, main.Attempt(4, 100, True))
        state.record(wave, "b" * 64, main.Attempt(4, 100, True))
        state._finalize_locked(wave, 100000)
        self.assertIsNone(wave.crown_hash)
        self.assertEqual(state._active_patch, (("amd64", "4889"),))


class BoundaryTests(unittest.TestCase):
    def test_reporter_values_are_filtered_from_runner_environment(self):
        os.environ["RSCTF_KOTH_REPORTER_SECRET"] = "do-not-forward"
        environment = main._safe_child_environment()
        self.assertFalse(any(key.startswith("RSCTF_KOTH_") for key in environment))
        self.assertNotIn("RSCTF_KOTH_REPORTER_SECRET", environment)

    def test_protocol_rejects_full_relay_without_authentication(self):
        state = main.ArenaState()
        fake = main.Session("a" * 64, "test", main._new_wave(0, ()), ("a", "b", "c", "d"))
        input_stream = io.BytesIO(b"HELLO koth_localtest123\nRELAY 00\nAUTH abcd\nQUIT\n")
        output_stream = io.BytesIO()
        with patch.object(main, "authenticate_capability", return_value=(fake.team_hash, fake.team_name)), patch.object(
            main, "execute_relay", return_value=(4, "partial", [])
        ):
            main.protocol_session(input_stream, output_stream, state)
        self.assertIn(b'"depth":4', output_stream.getvalue())
        self.assertIn(b"ERR no-complete-relay", output_stream.getvalue())

    def test_first_solver_bad_patch_is_rejected_with_visible_fallback(self):
        state = main.ArenaState()
        input_stream = io.BytesIO(
            b"HELLO koth_localtest123\n"
            b"RELAY 00\n"
            b"AUTH 1111111111111111222222222222222233333333333333334444444444444444\n"
            b"PATCH {\"patterns\":[{\"arch\":\"amd64\",\"hex\":\"aabb\"}]}\n"
            b"INFO\nQUIT\n"
        )
        output_stream = io.BytesIO()
        with patch.object(main, "authenticate_capability", return_value=("a" * 64, "test")), patch.object(
            main.secrets,
            "token_hex",
            side_effect=["aaaaaaaaaa", "1111111111111111", "2222222222222222", "3333333333333333", "4444444444444444"],
        ), patch.object(
            main,
            "execute_relay",
            side_effect=[(4, "complete", []), (0, "warded", [])],
        ):
            main.protocol_session(input_stream, output_stream, state)
        output = output_stream.getvalue()
        self.assertIn(b"PATCH_REJECTED", output)
        self.assertIn(b'"fallback":"previous-known-good"', output)
        self.assertIn(b'"status":"rejected"', output)


if __name__ == "__main__":
    unittest.main()
