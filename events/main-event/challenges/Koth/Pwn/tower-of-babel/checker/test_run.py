import io
import json
import unittest
from unittest.mock import patch

import run
from lib import KothContext, Mumble


CONTEXT = KothContext("127.0.0.1", 1094, 1, 1)
GREETING = b"BABEL/2 Tower of Babel \xe2\x80\x94 managed Leaderboard KotH\nSend PING, INFO, or HELLO <current KotH capability>.\n"


class FakeConnection:
    def __init__(self, response: bytes):
        self.response = response

    def settimeout(self, value):
        return None

    def makefile(self, *args, **kwargs):
        return io.BytesIO(GREETING + self.response)

    def sendall(self, value):
        return None

    def close(self):
        return None


def info_response():
    order = ["Doctor", "Kal'tsit", "Theresa", "Civilight Eterna"]
    nonce = "a1b2c3d4e5"
    stages = []
    for index, name in enumerate(order):
        stages.append({
            "name": name,
            "arch": {"Doctor": "amd64", "Kal'tsit": "aarch64", "Theresa": "riscv64", "Civilight Eterna": "mips64be"}[name],
            "secretPath": f"/tmp/babel/{nonce}/secret-{index}",
            "runnerPath": f"/tmp/babel/{nonce}/runner-{index}",
            "nextRunner": f"/tmp/babel/{nonce}/runner-{index + 1}" if index < 3 else "",
        })
    value = {
        "protocol": "babel-koth-v2",
        "instanceId": "0123456789ab",
        "waveId": "babel-60000",
        "waveEndsAtUnixMs": 120000,
        "order": order,
        "stages": stages,
        "activeWard": [],
        "ward": {"status": "empty", "revision": 0, "effectiveWave": None, "owner": None, "fallback": None},
        "limits": {"maxPayloadBytes": 32768, "maxAttemptsPerTeam": 12, "maxTeamsPerWave": 2000, "maxPatchPatterns": 8, "maxPatchBytes": 32, "maxPatchAttempts": 3},
        "mandatorySyscallHex": run.SYSCALL_HEX,
    }
    return b"INFO " + json.dumps(value, separators=(",", ":")).encode() + b"\n"


class CheckerTests(unittest.TestCase):
    def test_ping_is_read_only(self):
        with patch.object(run.socket, "create_connection", return_value=FakeConnection(b"PONG\n")):
            run.check_ping(CONTEXT)

    def test_info_contract(self):
        with patch.object(run.socket, "create_connection", return_value=FakeConnection(info_response())):
            run.check_info(CONTEXT)

    def test_bad_info_is_mumble(self):
        with patch.object(run.socket, "create_connection", return_value=FakeConnection(b"INFO {}\n")):
            with self.assertRaises(Mumble):
                run.check_info(CONTEXT)

    def test_invalid_capability_is_rejected(self):
        with patch.object(run.socket, "create_connection", return_value=FakeConnection(b"ERR auth: invalid KotH capability\n")):
            run.check_invalid_capability(CONTEXT)

    def test_read_only_commands_are_rejected_without_a_session(self):
        responses = [
            FakeConnection(b"ERR command; use HELP\n"),
            FakeConnection(b"ERR command; use HELP\n"),
            FakeConnection(b"ERR auth-required\n"),
        ]
        with patch.object(run.socket, "create_connection", side_effect=responses):
            run.check_read_only_errors(CONTEXT)


if __name__ == "__main__":
    unittest.main()
