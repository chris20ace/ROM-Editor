"""HTTP build routes with disposable snapshots and a fixture-only compiler."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from rom_builds import ACTIVE, GBA_LOGO
from server import Handler, Project, ThreadingHTTPServer


class RomBuildHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rom-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / "source/pokeemerald"
        source.mkdir(parents=True)
        (source / "rom.sha1").write_text("HTTP test source only\n", encoding="utf-8")
        (source / "saved.txt").write_text("Saved map and artwork fixture\n", encoding="utf-8")
        self.project = Project(self.root)
        self.original = self.snapshot()
        self.builds = self.project.builds
        compiler = self.root / "fixture-agbcc"
        compiler.mkdir()
        (compiler / "README.txt").write_text("Fixture, never a real compiler", encoding="utf-8")
        (compiler / "include").mkdir()
        for relative in ("bin/agbcc", "bin/old_agbcc", "bin/agbcc_arm", "lib/libc.a", "lib/libgcc.a"):
            path = compiler / (relative + (".exe" if os.name == "nt" and relative.startswith("bin/") else ""))
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"Fixture compiler entry, never executed")
        config = {"build_root": str(self.root / "build-output"), "agbcc": str(compiler), "bin_dirs": []}
        configured = patch.object(self.builds, "_config", return_value=config)
        configured.start()
        self.addCleanup(configured.stop)
        # Valid header bytes exercise ROM validation and attachment handling;
        # these fixture bytes are never presented as a playable user build.
        rom = bytearray(1024)
        rom[:4] = (0xEA00002E).to_bytes(4, "little")
        rom[4:0xA0] = GBA_LOGO
        rom[0xA0:0xAC] = b"HTTP FIXTURE"
        rom[0xAC:0xB0] = b"TEST"
        rom[0xB2] = 0x96
        rom[0xBD] = (-sum(rom[0xA0:0xBD]) - 0x19) & 0xFF
        self.rom = bytes(rom)
        script = self.root / "fixture_compiler.py"
        script.write_text(
            "from pathlib import Path\nimport sys,time\n"
            "print('Compiling the disposable fixture', flush=True)\n"
            "time.sleep(float(sys.argv[1]))\n"
            "if sys.argv[2] == 'fail':\n    print('ERROR: fixture missing script label', flush=True)\n    sys.exit(3)\n"
            f"Path('pokeemerald.gba').write_bytes(bytes.fromhex('{self.rom.hex()}'))\n"
            "print('Fixture compilation complete', flush=True)\n", encoding="utf-8")
        self.delay, self.compiler_fails = 0, False
        command = patch.object(self.builds, "_command", side_effect=lambda _: [sys.executable, str(script), str(self.delay), "fail" if self.compiler_fails else "pass"])
        command.start()
        self.addCleanup(command.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.project = self.project
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        status, _, raw = self.request("GET", "/api/session", token=False)
        self.assertEqual(status, 200)
        self.token = json.loads(raw)["token"]

    def stop(self):
        if self.builds._active:
            self.builds.cancel(self.builds._active)
        if self.builds._thread:
            self.builds._thread.join(10)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def snapshot(self):
        return {p.relative_to(self.project.source).as_posix(): p.read_bytes()
                for p in self.project.source.rglob("*") if p.is_file()}

    def request(self, method, path, body=None, token=True, origin=None):
        headers = {"Origin": origin or f"http://127.0.0.1:{self.server.server_port}"}
        if token:
            headers["X-Workbench-Token"] = self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def json(self, method, path, body=None, **kwargs):
        status, _, raw = self.request(method, path, body, **kwargs)
        return status, json.loads(raw)

    def start(self, name="HTTP adventure"):
        status, job = self.json("POST", "/api/build/start", {"name": name})
        self.assertEqual(status, 200, job)
        self.assertIn(job["status"], ACTIVE | {"succeeded"})
        self.assertFalse(any(key.startswith("_") for key in job))
        return job["id"]

    def finished(self, identifier):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, job = self.json("GET", "/api/build/jobs?" + urlencode({"id": identifier}))
            self.assertEqual(status, 200, job)
            if job["status"] not in ACTIVE:
                if self.builds._thread:
                    self.builds._thread.join(5)
                return job
            time.sleep(0.025)
        self.fail("Fixture compiler did not finish")

    def test_build_page_assets_and_initial_status_are_available(self):
        for path, mime, marker in (("/build", "text/html", b"Build your ROM"),
                                   ("/build.js", "javascript", b"/api/build/start"),
                                   ("/build.css", "text/css", b".build-grid")):
            with self.subTest(path=path):
                status, headers, raw = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(mime, headers["Content-Type"])
                self.assertIn(marker, raw)
        status, catalog = self.json("GET", "/api/build")
        self.assertEqual(status, 200)
        self.assertTrue(catalog["toolchain"]["ready"])
        self.assertIsNone(catalog["active_job"])
        self.assertEqual(catalog["history"], [])

    def test_start_rejects_missing_token_cross_origin_and_invalid_payload(self):
        for kwargs in ({"token": False}, {"origin": "https://other.example"}):
            with self.subTest(kwargs=kwargs):
                status, error = self.json("POST", "/api/build/start", {"name": "Untrusted"}, **kwargs)
                self.assertEqual(status, 403, error)
        status, error = self.json("POST", "/api/build/start", [])
        self.assertEqual(status, 400, error)
        self.assertEqual(self.builds.status()["history"], [])
        self.assertEqual(self.snapshot(), self.original)

    def test_success_round_trip_and_attachment_match_verified_rom(self):
        identifier = self.start('My "download" adventure')
        job = self.finished(identifier)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertIn("Fixture compilation complete", job["log"])
        self.assertEqual(job["rom_sha256"], hashlib.sha256(self.rom).hexdigest())
        self.assertEqual(len(job["source_sha256"]), 64)
        status, headers, content = self.request("GET", job["download_url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="My-download-adventure.gba"')
        self.assertEqual(int(headers["Content-Length"]), len(self.rom))
        self.assertEqual(content, self.rom)
        history = self.json("GET", "/api/build")[1]["history"]
        self.assertEqual(history[0]["id"], identifier)
        self.assertEqual(history[0]["download_url"], job["download_url"])
        self.assertFalse(any(key.startswith("_") for key in history[0]))
        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual(self.json("GET", "/api/transactions")[1]["transactions"], [])

    def test_failed_build_preserves_error_log_and_never_downloads(self):
        self.compiler_fails = True
        identifier = self.start()
        job = self.finished(identifier)
        self.assertEqual(job["status"], "failed", job)
        self.assertIn("fixture missing script label", job["log"])
        self.assertIn("code 3", job["error"])
        self.assertIsNone(job["download_url"])
        status, error = self.json("GET", "/api/build/download?" + urlencode({"id": identifier}))
        self.assertEqual(status, 400, error)
        self.assertIn("successfully completed", error["error"])
        self.assertEqual(self.snapshot(), self.original)

    def test_active_build_cannot_download_or_be_cancelled_without_token(self):
        self.delay = 3
        identifier = self.start()
        status, error = self.json("GET", "/api/build/download?" + urlencode({"id": identifier}))
        self.assertEqual(status, 400, error)
        status, error = self.json("POST", "/api/build/cancel", {"id": identifier}, token=False)
        self.assertEqual(status, 403, error)
        status, job = self.json("POST", "/api/build/cancel", {"id": identifier})
        self.assertEqual(status, 200, job)
        self.assertEqual(self.finished(identifier)["status"], "cancelled")
        self.assertEqual(self.snapshot(), self.original)

    def test_unknown_and_path_like_ids_are_rejected(self):
        for route in ("/api/build/jobs", "/api/build/download"):
            for identifier in ("", "missing", "../../saved.txt", "C:\\outside.gba"):
                with self.subTest(route=route, identifier=identifier):
                    status, error = self.json("GET", route + "?" + urlencode({"id": identifier}))
                    self.assertEqual(status, 400, error)
        self.assertEqual(self.snapshot(), self.original)

    def test_changed_success_artifact_is_not_served(self):
        identifier = self.start()
        job = self.finished(identifier)
        self.assertEqual(job["status"], "succeeded", job)
        artifact = self.builds.download(identifier)["path"]
        tampered = bytearray(artifact.read_bytes())
        tampered[-1] ^= 1
        artifact.write_bytes(tampered)
        status, error = self.json("GET", job["download_url"])
        self.assertEqual(status, 400, error)
        self.assertIn("changed after", error["error"])
        self.assertEqual(self.snapshot(), self.original)

    def test_retry_requires_auth_and_uses_original_snapshot_after_live_edits(self):
        self.compiler_fails = True
        identifier = self.start()
        failed = self.finished(identifier)
        self.assertEqual(failed["status"], "failed")
        for kwargs in ({"token": False}, {"origin": "https://other.example"}):
            status, error = self.json("POST", "/api/build/retry", {"id": identifier}, **kwargs)
            self.assertEqual(status, 403, error)
        (self.project.source / "saved.txt").write_text("New saved edits after the first snapshot\n", encoding="utf-8")
        newer_source = self.snapshot()
        self.compiler_fails = False
        status, retried = self.json("POST", "/api/build/retry", {"id": identifier})
        self.assertEqual(status, 200, retried)
        self.assertEqual(retried["id"], identifier)
        self.assertEqual(retried["source_sha256"], failed["source_sha256"])
        succeeded = self.finished(identifier)
        self.assertEqual(succeeded["status"], "succeeded", succeeded)
        self.assertEqual(succeeded["attempt_count"], 2)
        self.assertTrue(succeeded["attempts"])
        self.assertRegex(succeeded["log"], r"original saved.*snapshot")
        self.assertEqual(self.request("GET", succeeded["download_url"])[2], self.rom)
        artifact = self.builds.download(identifier)["path"]
        self.assertEqual((artifact.parent / "source/saved.txt").read_bytes(), self.original["saved.txt"])
        self.assertEqual(self.snapshot(), newer_source)

    def test_cancelled_saved_copy_can_retry_to_success(self):
        self.delay = 3
        identifier = self.start()
        status, result = self.json("POST", "/api/build/cancel", {"id": identifier})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.finished(identifier)["status"], "cancelled")
        self.delay = 0
        status, retried = self.json("POST", "/api/build/retry", {"id": identifier})
        self.assertEqual(status, 200, retried)
        self.assertEqual(self.finished(identifier)["status"], "succeeded")
        self.assertEqual(self.snapshot(), self.original)

    def test_retry_rejects_success_unknown_ids_and_changed_saved_inputs(self):
        identifier = self.start()
        self.assertEqual(self.finished(identifier)["status"], "succeeded")
        for body in ({"id": identifier}, {"id": "../../outside"}, {}, []):
            with self.subTest(body=body):
                status, error = self.json("POST", "/api/build/retry", body)
                self.assertEqual(status, 400, error)
        self.compiler_fails = True
        failed_id = self.start()
        self.assertEqual(self.finished(failed_id)["status"], "failed")
        saved = self.builds._jobs[failed_id]
        (Path(saved["_home"]) / "source/saved.txt").write_text("Tampered snapshot inputs\n", encoding="utf-8")
        status, error = self.json("POST", "/api/build/retry", {"id": failed_id})
        self.assertEqual(status, 400, error)
        self.assertEqual(self.builds.get(failed_id)["status"], "failed")
        self.assertEqual(self.snapshot(), self.original)


if __name__ == "__main__":
    unittest.main()
