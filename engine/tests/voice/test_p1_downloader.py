"""Phase 1 tests: the voice-model download manager. From the backend folder:

    python -m unittest tests.test_p1_downloader -v

The network is a FAKE server (no sockets): it serves in-memory bytes, honours or ignores Range, drops
connections, corrupts data on demand. Nothing is downloaded and no real model is touched.
"""
import hashlib
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path


from engine.voice.stt_engines import downloader as D  # noqa: E402
from engine.voice.stt_engines.downloader import ModelDownloader, ModelFile, ModelSpec  # noqa: E402


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


BIG = bytes((i * 7 + 3) % 256 for i in range(50_000))          # "the encoder"
SMALL = b"hello vocab\n" * 10
CFG = json.dumps({"model_type": "x"}).encode()


def make_spec(url_prefix="https://example.test/", big=BIG, big_sha=None, scheme_override=None) -> ModelSpec:
    return ModelSpec(
        id="test-model", title="Test", revision="rev1",
        files=(
            ModelFile("config.json", url_prefix + "config.json", None, None, len(CFG)),
            ModelFile("vocab.txt", url_prefix + "vocab.txt", None, None, len(SMALL)),
            ModelFile("enc.onnx", (scheme_override or url_prefix) + "enc.onnx", big_sha or sha(big), None, len(big)),
        ),
        approx_total=len(big) + len(SMALL) + len(CFG),
    )


class Resp:
    def __init__(self, status, headers, data, on_read=None, drop_after=None, chunk=1000):
        self.status, self.headers = status, headers
        self._d, self._pos, self._on_read, self._drop, self._chunk = data, 0, on_read, drop_after, chunk

    def read(self, n):
        if self._on_read:
            self._on_read()
        if self._drop is not None and self._pos >= self._drop:
            raise ConnectionResetError("connection dropped")
        piece = self._d[self._pos:self._pos + min(n, self._chunk)]
        self._pos += len(piece)
        return piece

    def close(self):
        pass


class FakeServer:
    def __init__(self, files, honor_range=True, drop_first_at=None, corrupt_times=0, on_read=None,
                 close_early_first=False):
        self.files = {"https://example.test/" + k: v for k, v in files.items()}
        self.honor_range, self.drop_first_at, self.corrupt_times = honor_range, drop_first_at, corrupt_times
        self.on_read, self.close_early_first = on_read, close_early_first
        self.requests: list[tuple[str, str]] = []
        self.big_requests = 0

    def __call__(self, url, headers):
        rng = headers.get("Range", "")
        self.requests.append((url, rng))
        data = self.files[url]
        is_big = url.endswith("enc.onnx")
        if is_big:
            self.big_requests += 1
            if self.corrupt_times > 0 and not rng:
                self.corrupt_times -= 1
                data = bytes([data[0] ^ 0xFF]) + data[1:]
        start = int(rng.split("=")[1].rstrip("-")) if (rng and self.honor_range) else 0
        if start >= len(data) and rng and self.honor_range:
            return Resp(416, {"Content-Range": f"bytes */{len(data)}"}, b"")
        body = data[start:]
        drop = None
        if is_big and self.big_requests == 1 and self.drop_first_at is not None:
            drop = self.drop_first_at
        if is_big and self.big_requests == 1 and self.close_early_first:
            body = body[: len(body) // 2]                      # server closes early, Content-Length says more
            return Resp(206 if start else 200, {"Content-Length": str(len(data) - start)}, body)
        if start:
            return Resp(206, {"Content-Length": str(len(body)), "Content-Range": f"bytes {start}-{len(data)-1}/{len(data)}"},
                        body, self.on_read, drop)
        return Resp(200, {"Content-Length": str(len(body))}, body, self.on_read, drop)


FILES = {"config.json": CFG, "vocab.txt": SMALL, "enc.onnx": BIG}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p1dl_"))
        self.dest = self.tmp / "model"
        self.sleeps = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mk(self, server, spec=None, retries=5, **kw):
        return ModelDownloader(spec or make_spec(), self.dest, opener=server, sleep=self.sleeps.append,
                               retries=retries, **kw)

    def run_dl(self, dl):
        self.assertTrue(dl.start())
        self.assertTrue(dl.wait(20))
        return dl.status()


class HappyPath(Base):
    def test_full_download(self):
        dl = self.mk(FakeServer(FILES))
        self.assertFalse(dl.status()["installed"])
        st = self.run_dl(dl)
        self.assertEqual(st["state"], "ready")
        self.assertEqual(st["progress"], 1.0)
        self.assertTrue(st["installed"])
        self.assertEqual((self.dest / "enc.onnx").read_bytes(), BIG)
        self.assertEqual((self.dest / "config.json").read_bytes(), CFG)
        self.assertTrue(dl.is_installed())
        self.assertFalse(list(self.dest.glob("*.part")))
        self.assertEqual(dl.verify_installed(), (True, ""))

    def test_second_manager_sees_the_install(self):
        self.run_dl(self.mk(FakeServer(FILES)))
        again = self.mk(FakeServer({}))
        self.assertTrue(again.is_installed())
        self.assertEqual(again.status()["state"], "ready")
        self.assertFalse(again.start())                      # nothing to do

    def test_on_done_callback(self):
        seen = []
        dl = self.mk(FakeServer(FILES), on_done=lambda ok, msg: seen.append((ok, msg)))
        self.run_dl(dl)
        self.assertEqual(seen, [(True, "")])

    def test_progress_never_goes_backwards_and_stays_below_one_until_done(self):
        values = []
        holder = {}
        server = FakeServer(FILES, on_read=lambda: values.append(holder["dl"].status()["progress"]))
        dl = self.mk(server)
        holder["dl"] = dl
        self.run_dl(dl)
        self.assertTrue(values)
        self.assertEqual(values, sorted(values))
        self.assertLess(max(values), 1.0)

    def test_single_start_while_running(self):
        gate = threading.Event()
        server = FakeServer(FILES, on_read=lambda: gate.wait(5))
        dl = self.mk(server)
        self.assertTrue(dl.start())
        self.assertFalse(dl.start())
        self.assertTrue(dl.status()["downloading"])
        gate.set()
        dl.wait(20)
        self.assertEqual(dl.status()["state"], "ready")


class ResumeAndRecovery(Base):
    def test_connection_drop_resumes_with_range(self):
        server = FakeServer(FILES, drop_first_at=20_000)
        st = self.run_dl(self.mk(server))
        self.assertEqual(st["state"], "ready")
        enc_reqs = [r for r in server.requests if r[0].endswith("enc.onnx")]
        self.assertEqual(len(enc_reqs), 2)
        self.assertTrue(enc_reqs[1][1].startswith("bytes=") and enc_reqs[1][1] != "bytes=0-")
        self.assertEqual((self.dest / "enc.onnx").read_bytes(), BIG)

    def test_server_closing_early_is_resumed(self):
        server = FakeServer(FILES, close_early_first=True)
        st = self.run_dl(self.mk(server))
        self.assertEqual(st["state"], "ready")
        self.assertEqual(server.big_requests, 2)

    def test_resume_from_existing_part_file(self):
        self.dest.mkdir(parents=True)
        (self.dest / "enc.onnx.part").write_bytes(BIG[:30_000])
        server = FakeServer(FILES)
        st = self.run_dl(self.mk(server))
        self.assertEqual(st["state"], "ready")
        enc = [r for r in server.requests if r[0].endswith("enc.onnx")]
        self.assertEqual(enc[0][1], "bytes=30000-")
        self.assertEqual((self.dest / "enc.onnx").read_bytes(), BIG)

    def test_server_that_ignores_range_restarts_cleanly(self):
        self.dest.mkdir(parents=True)
        (self.dest / "enc.onnx.part").write_bytes(b"junk" * 100)
        st = self.run_dl(self.mk(FakeServer(FILES, honor_range=False)))
        self.assertEqual(st["state"], "ready")
        self.assertEqual((self.dest / "enc.onnx").read_bytes(), BIG)

    def test_part_already_complete_gets_416_and_is_verified(self):
        self.dest.mkdir(parents=True)
        (self.dest / "enc.onnx.part").write_bytes(BIG)
        st = self.run_dl(self.mk(FakeServer(FILES)))
        self.assertEqual(st["state"], "ready")
        self.assertEqual((self.dest / "enc.onnx").read_bytes(), BIG)

    def test_stale_oversized_part_is_discarded(self):
        self.dest.mkdir(parents=True)
        (self.dest / "enc.onnx.part").write_bytes(b"x" * (len(BIG) + 500))
        st = self.run_dl(self.mk(FakeServer(FILES)))
        self.assertEqual(st["state"], "ready")
        self.assertEqual((self.dest / "enc.onnx").read_bytes(), BIG)

    def test_corrupt_bytes_are_redownloaded_once(self):
        server = FakeServer(FILES, corrupt_times=1)
        st = self.run_dl(self.mk(server))
        self.assertEqual(st["state"], "ready")
        self.assertEqual(server.big_requests, 2)
        self.assertEqual((self.dest / "enc.onnx").read_bytes(), BIG)

    def test_corrupt_twice_is_an_error_and_nothing_corrupt_is_kept(self):
        server = FakeServer(FILES, corrupt_times=5)
        st = self.run_dl(self.mk(server))
        self.assertEqual(st["state"], "error")
        self.assertIn("checksum", st["error"].lower())
        self.assertFalse((self.dest / "enc.onnx").exists())
        self.assertFalse((self.dest / "enc.onnx.part").exists())
        self.assertFalse((self.dest / D.MARKER).exists())
        self.assertFalse(self.mk(FakeServer({})).is_installed())

    def test_network_errors_retry_then_give_up_with_a_clear_message(self):
        def boom(url, headers):
            raise urllib.error.URLError("no route to host")
        dl = self.mk(boom, retries=3)
        st = self.run_dl(dl)
        self.assertEqual(st["state"], "error")
        self.assertIn("Network problem", st["error"])
        self.assertEqual(len(self.sleeps), 2)                # waited between the 3 attempts

    def test_retry_after_error_succeeds(self):
        calls = {"n": 0}
        good = FakeServer(FILES)

        def flaky(url, headers):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise urllib.error.URLError("temporary")
            return good(url, headers)
        st = self.run_dl(self.mk(flaky, retries=5))
        self.assertEqual(st["state"], "ready")

    def test_http_error_status_is_retried_and_reported(self):
        def nope(url, headers):
            return Resp(503, {}, b"")
        st = self.run_dl(self.mk(nope, retries=2))
        self.assertEqual(st["state"], "error")
        self.assertIn("503", st["error"])


class CancelDeleteVerify(Base):
    def test_cancel_keeps_partial_and_next_start_resumes(self):
        holder, reads = {}, {"n": 0}

        def on_read():
            reads["n"] += 1
            if reads["n"] == 40:
                holder["dl"].cancel()
        server = FakeServer(FILES, on_read=on_read)
        dl = self.mk(server)
        holder["dl"] = dl
        st = self.run_dl(dl)
        self.assertEqual(st["state"], "cancelled")
        self.assertTrue(st["resumable"])
        self.assertTrue((self.dest / "enc.onnx.part").exists())
        self.assertFalse(dl.is_installed())
        server.on_read = None
        st = self.run_dl(dl)
        self.assertEqual(st["state"], "ready")
        self.assertEqual((self.dest / "enc.onnx").read_bytes(), BIG)

    def test_delete_removes_everything(self):
        dl = self.mk(FakeServer(FILES))
        self.run_dl(dl)
        unloaded = []
        dl._before_delete = lambda: unloaded.append(True)
        ok, msg = dl.delete()
        self.assertTrue(ok, msg)
        self.assertEqual(unloaded, [True])                   # the loaded model is released first
        self.assertFalse(self.dest.exists())
        self.assertEqual(dl.status()["state"], "idle")
        self.assertFalse(dl.is_installed())

    def test_delete_refused_while_downloading(self):
        gate = threading.Event()
        dl = self.mk(FakeServer(FILES, on_read=lambda: gate.wait(5)))
        dl.start()
        ok, msg = dl.delete()
        self.assertFalse(ok)
        self.assertIn("Cancel", msg)
        gate.set()
        dl.wait(20)

    def test_delete_when_nothing_is_installed_is_fine(self):
        ok, _ = self.mk(FakeServer({})).delete()
        self.assertTrue(ok)

    def test_verify_detects_a_flipped_byte(self):
        dl = self.mk(FakeServer(FILES))
        self.run_dl(dl)
        p = self.dest / "enc.onnx"
        raw = bytearray(p.read_bytes())
        raw[100] ^= 0x01
        p.write_bytes(bytes(raw))
        ok, msg = dl.verify_installed()
        self.assertFalse(ok)
        self.assertIn("enc.onnx", msg)

    def test_marker_for_another_revision_is_not_installed(self):
        dl = self.mk(FakeServer(FILES))
        self.run_dl(dl)
        m = json.loads((self.dest / D.MARKER).read_text())
        m["revision"] = "older"
        (self.dest / D.MARKER).write_text(json.dumps(m))
        self.assertFalse(self.mk(FakeServer({})).is_installed())

    def test_truncated_file_is_not_installed(self):
        dl = self.mk(FakeServer(FILES))
        self.run_dl(dl)
        (self.dest / "enc.onnx").write_bytes(BIG[:-1])
        self.assertFalse(self.mk(FakeServer({})).is_installed())


class Safety(Base):
    def test_non_https_url_is_refused(self):
        spec = make_spec(scheme_override="http://example.test/")
        st = self.run_dl(self.mk(FakeServer(FILES), spec=spec))
        self.assertEqual(st["state"], "error")
        self.assertIn("https", st["error"])

    def test_not_enough_disk_space(self):
        real = shutil.disk_usage
        shutil.disk_usage = lambda p: shutil._ntuple_diskusage(10**12, 10**12 - 1000, 1000)
        try:
            spec = ModelSpec("s", "s", "r", make_spec().files, approx_total=700_000_000)
            st = self.run_dl(self.mk(FakeServer(FILES), spec=spec))
        finally:
            shutil.disk_usage = real
        self.assertEqual(st["state"], "error")
        self.assertIn("disk space", st["error"])

    def test_invalid_json_config_is_rejected(self):
        files = dict(FILES, **{"config.json": b"<html>captive portal</html>"})
        st = self.run_dl(self.mk(FakeServer(files)))
        self.assertEqual(st["state"], "error")
        self.assertFalse((self.dest / "config.json").exists())

    def test_empty_download_is_rejected(self):
        files = dict(FILES, **{"vocab.txt": b""})
        st = self.run_dl(self.mk(FakeServer(files)))
        self.assertEqual(st["state"], "error")


class RealManifest(unittest.TestCase):
    """The shipped manifest itself: shape only (no network)."""

    def test_manifest_shape(self):
        spec = D.PARAKEET_V3_INT8
        names = {f.name for f in spec.files}
        # exactly the file names onnx-asr looks for with quantization="int8"
        self.assertEqual(names, {"config.json", "vocab.txt", "decoder_joint-model.int8.onnx",
                                 "encoder-model.int8.onnx"})
        for f in spec.files:
            self.assertTrue(f.url.startswith("https://huggingface.co/"), f.url)
            self.assertIn(spec.revision, f.url)                    # pinned commit, not "main"
            self.assertNotIn("/main/", f.url)
            if f.name.endswith(".onnx"):
                self.assertRegex(f.sha256 or "", r"^[0-9a-f]{64}$")
        self.assertEqual(len(spec.revision), 40)
        self.assertTrue(650_000_000 < spec.approx_total < 750_000_000)     # "about 700 MB"


if __name__ == "__main__":
    unittest.main()
