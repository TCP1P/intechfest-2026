from pathlib import Path
from tempfile import TemporaryDirectory
import socket
from threading import Event, Lock, Thread
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import app


FLAG_ONE = "flag{" + "a" * 32 + "}"
FLAG_TWO = "flag{" + "b" * 32 + "}"


class RunningService:
    def __init__(self):
        self.temporary_directory = TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.parcel_root = root / "srv" / "parcels"
        self.parcel_root.mkdir(parents=True)
        (self.parcel_root / "manifest.txt").write_text(
            "Parcel Panic dispatch manifest\n"
            "depot_root=/srv/parcels\n"
            "example=manifest.txt\n",
            encoding="utf-8",
        )
        self.shared = root / "shared"
        self.shared.mkdir()
        self.flag_file = self.shared / "flag"
        self.server = app.create_server(
            app.AppConfig(self.parcel_root, self.flag_file),
            "127.0.0.1",
            0,
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary_directory.cleanup()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def request(self, path, headers=None):
        request = Request(self.base_url + path, headers=headers or {})
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()


class ServiceTests(unittest.TestCase):
    def test_health_does_not_require_a_delivered_flag(self):
        with RunningService() as service:
            self.assertEqual(service.request("/health"), (200, b"ok\n"))

    def test_normal_parcel_lookup_returns_the_manifest(self):
        with RunningService() as service:
            status, body = service.request("/parcel?name=manifest.txt")
            self.assertEqual(status, 200)
            self.assertIn(b"depot_root=/srv/parcels", body)

    def test_absolute_paths_are_rejected(self):
        with RunningService() as service:
            query = urlencode({"name": "/shared/flag"})
            self.assertEqual(
                service.request(f"/parcel?{query}"),
                (400, b"invalid parcel name\n"),
            )

    def test_decoded_parcel_name_is_bounded(self):
        with RunningService() as service:
            query = urlencode({"name": "a" * (app.MAX_NAME_BYTES + 1)})
            self.assertEqual(
                service.request(f"/parcel?{query}"),
                (400, b"invalid parcel name\n"),
            )

    def test_public_parcel_response_is_bounded(self):
        with RunningService() as service:
            (service.parcel_root / "oversized.txt").write_bytes(
                b"x" * (app.MAX_FILE_BYTES + 1)
            )
            self.assertEqual(
                service.request("/parcel?name=oversized.txt"),
                (413, b"parcel too large\n"),
            )

    def test_relative_traversal_reaches_the_byoc_flag(self):
        with RunningService() as service:
            service.flag_file.write_text(FLAG_ONE + "\n", encoding="utf-8")
            query = urlencode({"name": "../../shared/flag"})
            self.assertEqual(
                service.request(f"/parcel?{query}"),
                (200, (FLAG_ONE + "\n").encode()),
            )

    def test_vault_reads_each_rotated_flag_and_rejects_old_proofs(self):
        with RunningService() as service:
            service.flag_file.write_text(FLAG_ONE + "\n", encoding="utf-8")
            first_headers = {"X-Vault-Proof": app.vault_proof(FLAG_ONE)}
            self.assertEqual(
                service.request("/vault", first_headers),
                (200, (FLAG_ONE + "\n").encode()),
            )

            replacement = service.flag_file.with_suffix(".next")
            replacement.write_text(FLAG_TWO + "\n", encoding="utf-8")
            replacement.replace(service.flag_file)
            self.assertEqual(
                service.request("/vault", first_headers),
                (403, b"forbidden\n"),
            )
            second_headers = {"X-Vault-Proof": app.vault_proof(FLAG_TWO)}
            self.assertEqual(
                service.request("/vault", second_headers),
                (200, (FLAG_TWO + "\n").encode()),
            )


class ConcurrencyTests(unittest.TestCase):
    def test_server_caps_active_request_workers(self):
        lock = Lock()
        first_workers_started = Event()
        release_workers = Event()
        state = {"active": 0, "peak": 0}

        class BlockingHandler(app.BaseHTTPRequestHandler):
            def do_GET(self):
                with lock:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                    if state["active"] == 2:
                        first_workers_started.set()
                try:
                    release_workers.wait(timeout=3)
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                finally:
                    with lock:
                        state["active"] -= 1

            def log_message(self, *_args):
                pass

        class TwoWorkerServer(app.ParcelServer):
            max_workers = 2
            request_queue_size = 2

        server = TwoWorkerServer(("127.0.0.1", 0), BlockingHandler)
        server_thread = Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        responses = []

        def request():
            try:
                with socket.create_connection(
                    ("127.0.0.1", server.server_port),
                    timeout=2,
                ) as connection:
                    connection.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                    responses.append(connection.recv(32))
            except OSError:
                responses.append(b"")

        server_thread.start()
        first = [Thread(target=request) for _ in range(2)]
        overflow = [Thread(target=request) for _ in range(4)]
        try:
            for thread in first:
                thread.start()
            self.assertTrue(first_workers_started.wait(timeout=2))
            for thread in overflow:
                thread.start()
            for thread in overflow:
                thread.join(timeout=2)

            self.assertTrue(all(not thread.is_alive() for thread in overflow))
            self.assertEqual(state["peak"], 2)
            self.assertEqual(responses.count(b""), len(overflow))
        finally:
            release_workers.set()
            for thread in first + overflow:
                thread.join(timeout=3)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
