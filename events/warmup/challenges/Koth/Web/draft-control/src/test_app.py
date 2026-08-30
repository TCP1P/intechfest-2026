import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from threading import Thread
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import app


class RunningService:
    def __init__(self, pages_dir: Path):
        self.pages_dir = pages_dir

    def __enter__(self):
        self.pages_patch = patch.object(app, "PAGES_DIR", self.pages_dir)
        self.pages_patch.start()
        self.server = app.create_server("127.0.0.1", 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.pages_patch.stop()

    def get(self, path: str):
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=2) as response:
            return response.status, response.read()

    def post_json(self, path: str, value: object):
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(value).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)


class DraftControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.pages = self.root / "srv" / "pages"
        self.pages.mkdir(parents=True)
        (self.pages / "brief.txt").write_text("public brief\n", encoding="utf-8")
        (self.pages / "status.txt").write_text("ready\n", encoding="utf-8")
        self.koth = self.root / "koth"
        self.koth.mkdir()
        self.marker = self.koth / "king"
        self.marker.write_text("unclaimed", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_health_info_and_preview_do_not_mutate_state(self):
        before = {
            path: path.read_bytes()
            for path in (self.pages / "brief.txt", self.pages / "status.txt", self.marker)
        }
        with RunningService(self.pages) as service:
            status, body = service.get("/health")
            self.assertEqual((status, body), (200, b"ok\n"))

            status, body = service.get("/api/info")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["draftRoot"], "/srv/pages")

            status, body = service.get("/api/preview?path=brief.txt")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"content": "public brief\n", "path": "brief.txt"})

        after = {path: path.read_bytes() for path in before}
        self.assertEqual(after, before)

    def test_health_fails_when_the_marker_contract_is_missing(self):
        self.marker.unlink()
        with RunningService(self.pages) as service:
            try:
                service.get("/health")
            except urllib.error.HTTPError as error:
                self.assertEqual(error.code, 503)
                self.assertEqual(error.read(), b"unavailable\n")
            else:
                self.fail("health unexpectedly ignored the missing marker")

    def test_relative_existing_file_overwrite_reaches_koth_marker(self):
        with RunningService(self.pages) as service:
            status, value = service.post_json(
                "/api/drafts",
                {"path": "../../koth/king", "content": "koth_local_team"},
            )
        self.assertEqual(
            value,
            {"bytes": 15, "path": "../../koth/king", "status": "saved"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "koth_local_team")
        self.assertEqual(list(self.koth.glob(".draft-*")), [])

    def test_missing_target_is_not_created(self):
        with RunningService(self.pages) as service:
            status, value = service.post_json(
                "/api/drafts",
                {"path": "new.txt", "content": "not allowed"},
            )
        self.assertEqual(status, 404)
        self.assertEqual(value, {"error": "draft does not exist"})
        self.assertFalse((self.pages / "new.txt").exists())

    def test_final_symlink_is_rejected_without_touching_its_target(self):
        link = self.pages / "marker-link"
        link.symlink_to(self.marker)
        with RunningService(self.pages) as service:
            status, value = service.post_json(
                "/api/drafts",
                {"path": "marker-link", "content": "koth_symlink_attempt"},
            )
        self.assertEqual(status, 404)
        self.assertEqual(value, {"error": "draft does not exist"})
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "unclaimed")

    def test_concurrent_marker_writes_stay_complete_and_health_remains_ok(self):
        tokens = ("koth_alpha_" + "a" * 96, "koth_bravo_" + "b" * 96)
        with RunningService(self.pages) as service, ThreadPoolExecutor(max_workers=12) as pool:
            writes = [
                pool.submit(
                    service.post_json,
                    "/api/drafts",
                    {"path": "../../koth/king", "content": tokens[index % 2]},
                )
                for index in range(24)
            ]
            health_reads = [pool.submit(service.get, "/health") for _ in range(12)]

            for future in writes:
                status, value = future.result(timeout=3)
                self.assertEqual(status, 200)
                self.assertEqual(value["status"], "saved")
            for future in health_reads:
                self.assertEqual(future.result(timeout=3), (200, b"ok\n"))

        self.assertIn(self.marker.read_text(encoding="utf-8"), tokens)
        self.assertEqual(list(self.koth.glob(".draft-*")), [])

    def test_preview_rejects_traversal_without_reading_marker(self):
        with RunningService(self.pages) as service:
            try:
                service.get("/api/preview?path=../../koth/king")
            except urllib.error.HTTPError as error:
                self.assertEqual(error.code, 404)
                self.assertEqual(json.load(error), {"error": "preview is not available"})
            else:
                self.fail("preview traversal unexpectedly succeeded")
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "unclaimed")

    def test_absolute_paths_and_multiline_content_are_rejected(self):
        with RunningService(self.pages) as service:
            status, _ = service.post_json(
                "/api/drafts",
                {"path": "/koth/king", "content": "koth_local_team"},
            )
            self.assertEqual(status, 400)
            status, _ = service.post_json(
                "/api/drafts",
                {"path": "../../koth/king", "content": "token\nsecond-line"},
            )
            self.assertEqual(status, 400)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "unclaimed")

    def test_direct_claim_route_does_not_exist(self):
        with RunningService(self.pages) as service:
            request = urllib.request.Request(f"{service.base_url}/claim")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=2)
        self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
