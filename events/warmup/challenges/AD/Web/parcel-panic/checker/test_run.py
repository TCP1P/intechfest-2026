import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from threading import Thread
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "parcel_panic_checker",
    Path(__file__).with_name("run.py"),
)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

FLAG = "flag{" + "c" * 32 + "}"
MANIFEST = (
    "Parcel Panic dispatch manifest\n"
    "depot_root=/srv/parcels\n"
    "example=manifest.txt\n"
)


class FixtureServer:
    def __init__(self, *, current_flag=FLAG, health="ok\n"):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    return self.reply(200, health)
                if self.path == "/parcel?name=manifest.txt":
                    return self.reply(200, MANIFEST)
                if self.path == "/vault":
                    proof = self.headers.get("X-Vault-Proof", "")
                    if proof != CHECKER.vault_proof(current_flag):
                        return self.reply(403, "forbidden\n")
                    return self.reply(200, current_flag + "\n")
                return self.reply(404, "not found\n")

            def reply(self, status, body):
                encoded = body.encode()
                self.send_response(status)
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
        "RSCTF_TEAM_ID": "41",
        "RSCTF_ROUND": "7",
        "RSCTF_CHALLENGE_ID": "2026",
        "RSCTF_TARGET_IP": "127.0.0.1",
        "RSCTF_TARGET_PORT": str(port),
        "RSCTF_FLAG": FLAG,
    }


class CheckerTests(unittest.TestCase):
    def test_complete_service_contract_passes_without_using_traversal(self):
        with FixtureServer() as port, patch.dict(
            os.environ,
            checker_environment(port),
            clear=True,
        ):
            self.assertEqual(CHECKER.main(), 0)

    def test_wrong_flag_is_mumble(self):
        with FixtureServer(current_flag="flag{" + "d" * 32 + "}") as port, patch.dict(
            os.environ,
            checker_environment(port),
            clear=True,
        ):
            self.assertEqual(CHECKER.main(), 1)

    def test_broken_health_is_mumble(self):
        with FixtureServer(health="starting\n") as port, patch.dict(
            os.environ,
            checker_environment(port),
            clear=True,
        ):
            self.assertEqual(CHECKER.main(), 1)

    def test_timeout_is_offline(self):
        with patch.object(
            CHECKER,
            "http_get",
            side_effect=CHECKER.Offline("service timeout"),
        ), patch.dict(os.environ, checker_environment(1), clear=True):
            self.assertEqual(CHECKER.main(), 2)

    def test_invalid_runtime_environment_is_internal_error(self):
        environment = checker_environment(1)
        environment["RSCTF_ACTION"] = "putflag"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(CHECKER.main(), 3)


if __name__ == "__main__":
    unittest.main()
