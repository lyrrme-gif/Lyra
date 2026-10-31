"""Phase 8 tests for api_p8.py (routes, opt-in rules, install hand-off). Offline.
Run on its own:  python -m unittest tests.test_p8_api
"""
import hashlib
import http.server
import io
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import time
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import api_p8 as A  # noqa: E402
import updater as U  # noqa: E402
from tests.test_p8_updater import BASE, EXE, EXE_SHA, INSTALLER, REPO, Resp, http_error, release  # noqa: E402


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lyra-p8api-")
        os.environ["LYRA_DATA_DIR"] = self.tmp
        os.environ.pop("LYRA_APP_VERSION", None)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._open = U._open
        self.addCleanup(setattr, U, "_open", self._open)
        A._S.update(phase="idle", update=None, error=None, done=0, total=0, current="", throttled=False)
        A._CANCEL.clear()
        self.urls = []
        self.rel = release("v1.2.0")
        self.fail_next = None

        def fake(req, timeout, hosts):
            self.urls.append(req.full_url)
            if self.fail_next:
                e, self.fail_next = self.fail_next, None
                raise e
            u = req.full_url
            if "api.github.com" in u:
                return Resp(json.dumps(self.rel).encode(), 200, {"ETag": '"e"'})
            if u.endswith("SHA256SUMS"):
                return Resp(f"{EXE_SHA}  Lyra v1.0_1.2.0_x64-setup.exe\n".encode(), 200, {})
            hdr = {k.lower(): v for k, v in req.headers.items()}
            rng = hdr.get("range")
            if rng:
                return Resp(self.exe_body[int(rng.split("=")[1].split("-")[0]):], 206, {})
            return Resp(self.exe_body, 200, {})
        self.exe_body = EXE
        U._open = fake

    def post(self, path, **body):
        return A.http("POST", path, body)

    def wait_for(self, phase, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if A.snapshot()["phase"] == phase:
                return
            time.sleep(0.01)
        self.fail(f"never reached {phase}: {A.snapshot()['phase']} {A.snapshot()['error']}")

    def configure(self, **kw):
        kw.setdefault("repo", REPO)
        code, snap = self.post("/p8/config", **kw)
        self.assertEqual(code, 200, snap)
        return snap

    # ---- opt-in / config ------------------------------------------------------------------------

    def test_off_by_default_and_no_network(self):
        snap = A.snapshot()
        self.assertFalse(snap["enabled"])
        self.assertEqual(snap["repo"], "")
        self.assertEqual(self.urls, [])

    def test_background_thread_does_nothing_unless_enabled(self):
        A._S["current"] = "1.0.0"
        U.update_state(repo=REPO, enabled=False, last_check=0)
        A._STOP.clear()
        orig = A._STOP.wait
        calls = {"n": 0}

        def fast_wait(t):                         # run the loop body a couple of times without sleeping
            calls["n"] += 1
            return calls["n"] > 3
        A._STOP.wait = fast_wait
        try:
            A._bg_loop()
        finally:
            A._STOP.wait = orig
        self.assertEqual(self.urls, [])

    def test_background_check_only_checks_never_downloads_or_installs(self):
        self.configure(enabled=True)
        A._S["current"] = "1.0.0"
        orig, calls = A._STOP.wait, {"n": 0}
        A._STOP.wait = lambda t: (calls.__setitem__("n", calls["n"] + 1) or calls["n"] > 3)
        try:
            A._bg_loop()
        finally:
            A._STOP.wait = orig
        self.assertEqual(A.snapshot()["phase"], "available")
        self.assertTrue(all("api.github.com" in u for u in self.urls), self.urls)       # no installer / checksum fetched
        self.assertEqual([p.name for p in U.updates_dir().glob("*") if p.name != ".keep"], [])

    def test_background_respects_interval_and_rate_limit_window(self):
        self.configure(enabled=True)
        A._S["current"] = "1.0.0"
        U.update_state(last_check=time.time())                       # checked a moment ago
        orig, calls = A._STOP.wait, {"n": 0}
        A._STOP.wait = lambda t: (calls.__setitem__("n", calls["n"] + 1) or calls["n"] > 2)
        try:
            A._bg_loop()
        finally:
            A._STOP.wait = orig
        self.assertEqual(self.urls, [])
        U.update_state(last_check=0, next_allowed=time.time() + 3600)
        calls["n"] = 0
        A._STOP.wait = lambda t: (calls.__setitem__("n", calls["n"] + 1) or calls["n"] > 2)
        try:
            A._bg_loop()
        finally:
            A._STOP.wait = orig
        self.assertEqual(self.urls, [])

    def test_config_validation(self):
        code, snap = self.post("/p8/config", repo="not a repo")
        self.assertEqual(code, 400)
        self.assertEqual(snap["error"]["code"], "bad_repo")
        self.assertEqual(A.snapshot()["repo"], "")
        snap = self.configure(repo="https://github.com/Acme/Lyra.git", enabled=True, prereleases=True)
        self.assertEqual(snap["repo"], "Acme/Lyra")
        self.assertTrue(snap["enabled"] and snap["prereleases"])

    def test_changing_repo_resets_everything(self):
        self.configure()
        A._S["current"] = "1.0.0"
        self.post("/p8/check", current="1.0.0")
        self.post("/p8/skip", version="1.2.0")
        self.configure(repo="Other/Thing")
        snap = A.snapshot()
        self.assertEqual((snap["phase"], snap["update"], snap["skipped"]), ("idle", None, ""))

    # ---- check ---------------------------------------------------------------------------------

    def test_manual_check_works_even_when_auto_check_is_off(self):
        self.configure(enabled=False)
        code, snap = self.post("/p8/check", current="1.0.0")
        self.assertEqual(snap["phase"], "available")
        self.assertEqual(snap["update"]["version"], "1.2.0")
        self.assertNotIn("url", json.dumps(snap["update"]).replace("html_url", ""))

    def test_check_without_repo_is_a_calm_error(self):
        code, snap = self.post("/p8/check", current="1.0.0")
        self.assertEqual((code, snap["phase"], snap["error"]["code"]), (200, "error", "bad_repo"))
        self.assertEqual(self.urls, [])

    def test_up_to_date(self):
        self.configure()
        self.rel = release("v1.0.0")
        _, snap = self.post("/p8/check", current="1.0.0")
        self.assertEqual((snap["phase"], snap["update"]), ("up_to_date", None))

    def test_rate_limit_sets_a_window_and_blocks_more_requests(self):
        self.configure()
        self.fail_next = http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 900)})
        _, snap = self.post("/p8/check", current="1.0.0")
        self.assertEqual(snap["error"]["code"], "rate_limited")
        n = len(self.urls)
        _, snap = self.post("/p8/check", current="1.0.0")
        self.assertEqual(snap["error"]["code"], "rate_limited")
        self.assertEqual(len(self.urls), n)                           # second click made no request

    def test_offline_message(self):
        self.configure()
        self.fail_next = urllib.error.URLError("down")
        _, snap = self.post("/p8/check", current="1.0.0")
        self.assertEqual((snap["phase"], snap["error"]["code"]), ("error", "offline"))

    def test_manual_check_cooldown(self):
        self.configure()
        self.post("/p8/check", current="1.0.0")
        n = len(self.urls)
        _, snap = self.post("/p8/check", current="1.0.0")
        self.assertTrue(snap["throttled"])
        self.assertEqual(len(self.urls), n)

    def test_skipped_version_is_quiet_for_auto_but_visible_to_manual(self):
        self.configure()
        self.post("/p8/skip", version="1.2.0")
        A.run_check("1.0.0", manual=False)
        self.assertEqual(A.snapshot()["phase"], "up_to_date")
        A._S["throttled"] = False
        U.update_state(last_check=0)
        self.post("/p8/check", current="1.0.0")
        self.assertEqual(A.snapshot()["phase"], "available")

    def test_hostile_release_notes_stay_plain_text(self):
        self.configure()
        self.rel = release("v1.2.0", body='<img src=x onerror=alert(1)> hello')
        _, snap = self.post("/p8/check", current="1.0.0")
        self.assertNotIn("<img", snap["update"]["notes"])

    # ---- download + install hand-off -----------------------------------------------------------

    def get_ready(self):
        self.configure()
        self.post("/p8/check", current="1.0.0")
        self.post("/p8/download", current="1.0.0")
        self.wait_for("ready")

    def test_download_then_install_handoff(self):
        self.get_ready()
        snap = A.snapshot()
        self.assertEqual(snap["ready_version"], "1.2.0")
        self.assertEqual(snap["progress"]["done"], len(EXE))
        _, res = self.post("/p8/install", version="1.2.0", confirm=True, current="1.0.0")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["sha256"], EXE_SHA)
        self.assertTrue(os.path.isfile(res["path"]))
        self.assertEqual(os.path.dirname(res["path"]), str(U.updates_dir()))

    def test_install_needs_explicit_confirm_and_the_right_version(self):
        self.get_ready()
        for body in ({"version": "1.2.0", "current": "1.0.0"},
                     {"version": "1.2.0", "confirm": "yes", "current": "1.0.0"},
                     {"version": "1.2.0", "confirm": 1, "current": "1.0.0"}):
            _, res = self.post("/p8/install", **body)
            self.assertFalse(res["ok"], body)
            self.assertEqual(res["error"]["code"], "not_confirmed")
        _, res = self.post("/p8/install", version="9.9.9", confirm=True, current="1.0.0")
        self.assertEqual(res["error"]["code"], "version_changed")

    def test_install_without_any_download_is_refused(self):
        self.configure()
        self.post("/p8/check", current="1.0.0")
        _, res = self.post("/p8/install", version="1.2.0", confirm=True, current="1.0.0")
        self.assertEqual((res["ok"], res["error"]["code"]), (False, "not_ready"))

    def test_install_refuses_a_tampered_file(self):
        self.get_ready()
        f = next(U.updates_dir().glob("*-setup.exe"))
        f.write_bytes(EXE[:-1] + b"Z")
        _, res = self.post("/p8/install", version="1.2.0", confirm=True, current="1.0.0")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "checksum_mismatch")
        self.assertEqual(A.snapshot()["phase"], "error")

    def test_install_survives_an_app_restart(self):
        self.get_ready()
        A._S.update(phase="idle", update=None)                       # the process restarted
        _, res = self.post("/p8/install", version="1.2.0", confirm=True, current="1.0.0")
        self.assertTrue(res["ok"], res)

    def test_nothing_is_installable_once_we_are_that_version(self):
        self.get_ready()
        _, res = self.post("/p8/install", version="1.2.0", confirm=True, current="1.2.0")
        self.assertEqual(res["error"]["code"], "not_newer")

    def test_corrupt_download_ends_in_error_and_leaves_nothing(self):
        self.exe_body = b"MZ" + b"\x00" * (len(EXE) - 2)
        self.configure()
        self.post("/p8/check", current="1.0.0")
        self.post("/p8/download", current="1.0.0")
        self.wait_for("error")
        self.assertEqual(A.snapshot()["error"]["code"], "checksum_mismatch")
        self.assertEqual([p.name for p in U.updates_dir().glob("*") if p.name != ".keep"], [])
        _, res = self.post("/p8/install", version="1.2.0", confirm=True, current="1.0.0")
        self.assertFalse(res["ok"])

    def test_unverifiable_release_is_never_downloaded(self):
        self.configure()
        self.rel = release("v1.2.0", digest=False, sums=False)
        self.post("/p8/check", current="1.0.0")
        n = len(self.urls)
        _, snap = self.post("/p8/download", current="1.0.0")
        self.assertEqual(snap["error"]["code"], "no_checksum")
        self.assertEqual(len(self.urls), n)

    def test_download_without_a_check_is_refused(self):
        self.configure()
        _, snap = self.post("/p8/download", current="1.0.0")
        self.assertFalse(snap["ok"])
        self.assertEqual(snap["error"]["code"], "no_update")

    def test_cancel_returns_to_available(self):
        self.configure()
        self.post("/p8/check", current="1.0.0")
        gate = threading.Event()
        inner = U._open

        def slow(req, timeout, hosts):
            r = inner(req, timeout, hosts)
            if req.full_url.endswith(".exe"):
                class S(Resp):
                    def read(s, n=-1):
                        gate.wait(2)
                        return super().read(500)
                return S(EXE, 200, {})
            return r
        U._open = slow
        self.post("/p8/download", current="1.0.0")
        self.wait_for("downloading")
        self.post("/p8/cancel")
        gate.set()
        self.wait_for("available")

    # ---- dispatcher ----------------------------------------------------------------------------

    def test_unknown_routes_and_bad_bodies(self):
        self.assertEqual(A.http("GET", "/p8/nope", {})[0], 404)
        self.assertEqual(A.http("POST", "/p8/status", {})[0], 404)
        self.assertEqual(A.http("POST", "/p8/check", None)[0], 200)   # a non-dict body is treated as empty
        self.assertEqual(A.http("GET", "/p8/status?x=1/", {})[0], 200)

    def test_internal_errors_do_not_leak(self):
        orig = A.snapshot
        A.snapshot = lambda: (_ for _ in ()).throw(RuntimeError("secret path C:\\Users\\x"))
        try:
            code, obj = A.http("GET", "/p8/status", {})
        finally:
            A.snapshot = orig
        self.assertEqual(code, 500)
        self.assertNotIn("secret", json.dumps(obj))

    def test_status_never_exposes_download_urls_or_hashes_of_other_files(self):
        self.configure()
        self.post("/p8/check", current="1.0.0")
        text = json.dumps(A.snapshot())
        self.assertNotIn("browser_download_url", text)
        self.assertNotIn("releases/download", text)


class RedirectGuard(unittest.TestCase):
    """Real sockets: a redirect to a host outside the allow-list must be refused."""

    def test_redirect_to_other_host_is_blocked(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/ok":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"hi")
                else:
                    self.send_response(302)
                    self.send_header("Location", "https://evil.example/payload.exe")
                    self.end_headers()

            def log_message(self, *a):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        U._ALLOW_INSECURE_FOR_TESTS, U._EXTRA_TEST_HOSTS = True, ("127.0.0.1",)
        try:
            with U._open(U.urllib.request.Request(f"http://127.0.0.1:{port}/ok"), 3, U.DOWNLOAD_HOSTS) as r:
                self.assertEqual(r.read(), b"hi")
            with self.assertRaises(U.UpdateError) as c:
                U._open(U.urllib.request.Request(f"http://127.0.0.1:{port}/go"), 3, U.DOWNLOAD_HOSTS)
            self.assertEqual(c.exception.code, "insecure_url")
        finally:
            U._ALLOW_INSECURE_FOR_TESTS, U._EXTRA_TEST_HOSTS = False, ()
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
