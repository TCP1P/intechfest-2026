import importlib.util
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from threading import Thread
import unittest
from unittest.mock import patch


CHECKER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CHECKER_DIR))
SPEC = importlib.util.spec_from_file_location("draft_control_checker", CHECKER_DIR / "run.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def info(**changes):
    value = dict(CHECKER.EXPECTED_INFO)
    value.update(changes)
    return value


class FixtureServer:
    def __init__(self, routes):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(("GET", self.path))
                status, content_type, body = routes.get(
                    self.path,
                    (404, "application/json", {"error": "not found"}),
                )
                if isinstance(body, dict):
                    encoded = json.dumps(body).encode("utf-8") + b"\n"
                else:
                    encoded = body
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self):
                requests.append(("POST", self.path))
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

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


def healthy_routes():
    return {
        "/health": (200, "text/plain; charset=utf-8", b"ok\n"),
        "/api/info": (200, "application/json; charset=utf-8", info()),
        "/api/preview?path=brief.txt": (
            200,
            "application/json; charset=utf-8",
            {"content": "any current draft\n", "path": "brief.txt"},
        ),
    }


def checker_environment(port):
    return {
        "RSCTF_ACTION": "check",
        "RSCTF_TEAM_ID": "0",
        "RSCTF_ROUND": "17",
        "RSCTF_CHALLENGE_ID": "1201",
        "RSCTF_TARGET_IP": "127.0.0.1",
        "RSCTF_TARGET_PORT": str(port),
    }


class CheckerContractTests(unittest.TestCase):
    def test_healthy_service_passes_using_get_requests_only(self):
        with FixtureServer(healthy_routes()) as fixture, patch.dict(
            os.environ,
            checker_environment(fixture.server.server_port),
            clear=True,
        ):
            self.assertEqual(CHECKER.main(), 0)

        self.assertEqual(
            sorted(fixture.requests),
            sorted(
                [
                    ("GET", "/health"),
                    ("GET", "/api/info"),
                    ("GET", "/api/preview?path=brief.txt"),
                ]
            ),
        )

    def test_changed_public_contract_is_mumble(self):
        routes = healthy_routes()
        routes["/api/info"] = (200, "application/json", info(draftRoot="/tmp/pages"))
        with FixtureServer(routes) as fixture, patch.dict(
            os.environ,
            checker_environment(fixture.server.server_port),
            clear=True,
        ):
            self.assertEqual(CHECKER.main(), 1)

    def test_invalid_preview_is_mumble(self):
        routes = healthy_routes()
        routes["/api/preview?path=brief.txt"] = (
            200,
            "application/json",
            {"content": "", "path": "brief.txt"},
        )
        with FixtureServer(routes) as fixture, patch.dict(
            os.environ,
            checker_environment(fixture.server.server_port),
            clear=True,
        ):
            self.assertEqual(CHECKER.main(), 1)

    def test_non_utf8_scalar_preview_is_mumble_not_internal_error(self):
        routes = healthy_routes()
        routes["/api/preview?path=brief.txt"] = (
            200,
            "application/json",
            {"content": "\ud800", "path": "brief.txt"},
        )
        with FixtureServer(routes) as fixture, patch.dict(
            os.environ,
            checker_environment(fixture.server.server_port),
            clear=True,
        ):
            self.assertEqual(CHECKER.main(), 1)

    def test_network_failure_is_offline(self):
        with patch.object(
            CHECKER,
            "http_get",
            side_effect=CHECKER.Offline("the service timed out"),
        ), patch.dict(os.environ, checker_environment(1), clear=True):
            self.assertEqual(CHECKER.main(), 2)

    def test_flag_or_invalid_team_context_is_internal_error(self):
        environment = checker_environment(1)
        environment["RSCTF_FLAG"] = "rsctf{should_not_exist}"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(CHECKER.main(), 3)

        environment = checker_environment(1)
        environment["RSCTF_TEAM_ID"] = "9"
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(CHECKER.main(), 3)


if __name__ == "__main__":
    unittest.main()
