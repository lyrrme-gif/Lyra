"""Phase 8 tests for updater.py. Offline: every network call goes through updater._open, replaced here.
Run on its own:  python -m unittest tests.test_p8_updater
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import updater as U  # noqa: E402

REPO = "Acme/Lyra"
BASE = f"https://github.com/{REPO}/releases/download/v1.2.0/"
EXE = b"MZ" + os.urandom(5000)               # a fake installer that "looks like" a Windows program
EXE_SHA = hashlib.sha256(EXE).hexdigest()
INSTALLER = "Lyra.v1.0_1.2.0_x64-setup.exe"   # GitHub turns spaces into dots in uploaded names


class Resp:
    def __init__(self, body=b"", status=200, headers=None):
        self._b = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, headers=None, url="https://api.github.com/x"):
    return urllib.error.HTTPError(url, code, "x", headers or {}, io.BytesIO(b""))


def release(tag="v1.2.0", *, digest=True, sums=True, sidecar=False, draft=False, pre=False, assets=None, body="Notes"):
    a = []
    if assets is None:
        a.append({"name": INSTALLER, "size": len(EXE), "browser_download_url": BASE + INSTALLER,
                  "digest": f"sha256:{EXE_SHA}" if digest else None})
        if sums:
            a.append({"name": "SHA256SUMS", "size": 100, "browser_download_url": BASE + "SHA256SUMS", "digest": None})
        if sidecar:
            a.append({"name": INSTALLER + ".sha256", "size": 80, "browser_download_url": BASE + INSTALLER + ".sha256"})
    else:
        a = assets
    return {"tag_name": tag, "name": "Lyra " + tag, "body": body, "published_at": "2026-01-01T00:00:00Z",
            "html_url": f"https://github.com/{REPO}/releases/tag/{tag}", "prerelease": pre, "draft": draft, "assets": a}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lyra-p8-")
        os.environ["LYRA_DATA_DIR"] = self.tmp
        self._open = U._open
        self.calls = []
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, U, "_open", self._open)

    def net(self, handler):
        def fake(req, timeout, hosts):
            self.calls.append((req.full_url, {k.lower(): v for k, v in req.headers.items()}))
            return handler(req)
        U._open = fake


class Versions(unittest.TestCase):
    def test_order(self):
        for newer, older in [("1.0.1", "1.0.0"), ("1.10.0", "1.9.9"), ("2.0.0", "1.99.99"), ("1.0.0", "1.0.0-rc.1"),
                             ("1.0.0-rc.2", "1.0.0-rc.1"), ("1.0.0-rc.10", "1.0.0-rc.2"), ("1.0.0-beta", "1.0.0-alpha"),
                             ("v1.0.1", "1.0.0")]:
            self.assertTrue(U.is_newer(newer, older), (newer, older))
            self.assertFalse(U.is_newer(older, newer), (older, newer))

    def test_equal_and_junk_are_not_newer(self):
        for a, b in [("1.0.0", "1.0.0"), ("1.0.0+build9", "1.0.0"), ("nightly", "1.0.0"), ("", "1.0.0"), ("1.0.1", "junk")]:
            self.assertFalse(U.is_newer(a, b), (a, b))

    def test_clean_version(self):
        self.assertEqual(U.clean_version(" v1.2.3 "), "1.2.3")


class Repo(unittest.TestCase):
    def test_accepts(self):
        self.assertEqual(U.normalize_repo("Acme/Lyra"), "Acme/Lyra")
        self.assertEqual(U.normalize_repo("https://github.com/Acme/Lyra"), "Acme/Lyra")
        self.assertEqual(U.normalize_repo("https://github.com/Acme/Lyra.git"), "Acme/Lyra")
        self.assertEqual(U.normalize_repo("github.com/Acme/Lyra/releases/tag/v1"), "Acme/Lyra")

    def test_rejects(self):
        for bad in ["", None, "acme", "a/b/c", "../x/y", "x/..", "a b/c", "https://evil.com/a/b", "a/b;rm", ".x/y", "x/.y"]:
            self.assertEqual(U.normalize_repo(bad), "", bad)


class Checksums(unittest.TestCase):
    H = "ab" * 32

    def test_sha256sum_and_dotted_names(self):
        t = f"{self.H}  Lyra v1.0_1.2.0_x64-setup.exe\n{'cd'*32}  other.zip\n"
        self.assertEqual(U.parse_checksum_text(t, INSTALLER), self.H)

    def test_binary_marker_and_paths_and_case(self):
        self.assertEqual(U.parse_checksum_text(f"{self.H.upper()} *dist\\{INSTALLER.upper()}", INSTALLER), self.H)

    def test_bsd_style(self):
        self.assertEqual(U.parse_checksum_text(f"SHA256 ({INSTALLER}) = {self.H}", INSTALLER), self.H)

    def test_bare_hash_sidecar(self):
        self.assertEqual(U.parse_checksum_text(self.H + "\n", INSTALLER), self.H)

    def test_missing_or_malformed(self):
        self.assertIsNone(U.parse_checksum_text(f"{self.H}  other.exe", INSTALLER))
        self.assertIsNone(U.parse_checksum_text("zz" * 32 + f"  {INSTALLER}", INSTALLER))
        self.assertIsNone(U.parse_checksum_text("", INSTALLER))
        self.assertIsNone(U.parse_checksum_text(f"{self.H[:60]}  {INSTALLER}", INSTALLER))


class BuildUpdate(unittest.TestCase):
    def build(self, rel, repo=REPO):
        return U.build_update(U._trim_release(rel), repo)

    def test_verifiable_with_digest_and_sums(self):
        u = self.build(release())
        self.assertTrue(u["verifiable"])
        self.assertEqual(u["version"], "1.2.0")
        self.assertEqual(len(u["checksum_sources"]), 2)

    def test_repo_match_is_case_insensitive(self):
        self.assertTrue(self.build(release(), "acme/lyra")["verifiable"])

    def test_no_checksum_is_not_installable(self):
        u = self.build(release(digest=False, sums=False))
        self.assertFalse(u["verifiable"])
        self.assertIn("checksum", u["problem"])

    def test_no_installer(self):
        u = self.build(release(assets=[{"name": "source.zip", "size": 5, "browser_download_url": BASE + "source.zip"}]))
        self.assertIsNone(u["installer"])
        self.assertFalse(u["verifiable"])

    def test_foreign_download_url_is_not_trusted(self):
        a = [{"name": INSTALLER, "size": 10, "browser_download_url": "https://evil.example/" + INSTALLER, "digest": f"sha256:{EXE_SHA}"}]
        u = self.build(release(assets=a))
        self.assertIsNone(u["installer"])
        self.assertFalse(u["verifiable"])

    def test_same_owner_other_repo_is_not_trusted(self):
        a = [{"name": INSTALLER, "size": 10, "browser_download_url": f"https://github.com/Acme/Other/releases/download/v1/{INSTALLER}",
              "digest": f"sha256:{EXE_SHA}"}]
        self.assertIsNone(self.build(release(assets=a))["installer"])

    def test_unreasonable_size(self):
        for size in (0, -1, U.MAX_INSTALLER_BYTES + 1):
            a = [{"name": INSTALLER, "size": size, "browser_download_url": BASE + INSTALLER, "digest": f"sha256:{EXE_SHA}"}]
            self.assertFalse(self.build(release(assets=a))["verifiable"], size)

    def test_prefers_x64_and_skips_blockmap(self):
        a = [{"name": "Lyra_1.2.0_arm64-setup.exe", "size": 9, "browser_download_url": BASE + "Lyra_1.2.0_arm64-setup.exe", "digest": f"sha256:{EXE_SHA}"},
             {"name": "Lyra_1.2.0_x64-setup.exe.blockmap", "size": 9, "browser_download_url": BASE + "m"},
             {"name": "Lyra_1.2.0_x64-setup.exe", "size": 9, "browser_download_url": BASE + "Lyra_1.2.0_x64-setup.exe", "digest": f"sha256:{EXE_SHA}"}]
        self.assertEqual(self.build(release(assets=a))["installer"]["name"], "Lyra_1.2.0_x64-setup.exe")

    def test_foreign_html_url_dropped(self):
        r = release()
        r["html_url"] = "https://evil.example/x"
        self.assertEqual(self.build(r)["html_url"], "")

    def test_notes_are_plain_text(self):
        u = self.build(release(body="Hi <script>alert(1)</script>\x00\x07 there\n\n\n\n" + "x" * 5000))
        self.assertNotIn("<script>", u["notes"])
        self.assertNotIn("\x00", u["notes"])
        self.assertLessEqual(len(u["notes"]), U.NOTES_MAX_CHARS + 3)


class HostChecks(unittest.TestCase):
    def test_rules(self):
        ok = lambda u, h=U.DOWNLOAD_HOSTS: U._host_ok(u, h)
        self.assertTrue(ok("https://github.com/a/b/releases/download/v1/x.exe"))
        self.assertTrue(ok("https://objects.githubusercontent.com/x"))
        self.assertFalse(ok("http://github.com/x"))
        self.assertFalse(ok("https://github.com.evil.com/x"))
        self.assertFalse(ok("https://evil.com/github.com"))
        self.assertFalse(ok("https://user:pw@github.com/x"))
        self.assertFalse(ok("https://api.github.com/x"))                 # api host is not a download host
        self.assertFalse(ok("file:///etc/passwd"))
        self.assertFalse(ok("ftp://github.com/x"))

    def test_insecure_test_switch_is_off_by_default(self):
        self.assertFalse(U._ALLOW_INSECURE_FOR_TESTS)
        self.assertEqual(U._EXTRA_TEST_HOSTS, ())

    def test_open_refuses_bad_hosts_before_any_network(self):
        with self.assertRaises(U.UpdateError) as c:
            U._open(U.urllib.request.Request("http://127.0.0.1:1/x"), 1, U.DOWNLOAD_HOSTS)
        self.assertEqual(c.exception.code, "insecure_url")


class Check(Base):
    def latest(self, rel, etag='"e1"'):
        return lambda req: Resp(json.dumps(rel).encode(), 200, {"ETag": etag})

    def test_available(self):
        self.net(self.latest(release("v1.2.0")))
        r = U.check(REPO, "1.0.0")
        self.assertEqual(r["status"], "available")
        self.assertEqual(r["update"]["version"], "1.2.0")
        url, headers = self.calls[0]
        self.assertTrue(url.endswith("/repos/Acme/Lyra/releases/latest"))
        self.assertTrue(headers["user-agent"].startswith("Lyra-Updater/"))

    def test_up_to_date_and_older_release(self):
        self.net(self.latest(release("v1.0.0")))
        self.assertEqual(U.check(REPO, "1.0.0")["status"], "up_to_date")
        self.net(self.latest(release("v0.9.0")))
        self.assertEqual(U.check(REPO, "1.0.0")["status"], "up_to_date")

    def test_etag_revalidation_uses_cache_on_304(self):
        self.net(self.latest(release("v1.2.0")))
        U.check(REPO, "1.0.0")

        def second(req):
            self.assertEqual({k.lower(): v for k, v in req.headers.items()}.get("if-none-match"), '"e1"')
            raise http_error(304)
        self.net(second)
        self.assertEqual(U.check(REPO, "1.0.0")["update"]["version"], "1.2.0")

    def test_prereleases_only_when_asked(self):
        lst = [release("v1.3.0-beta.1", pre=True), release("v1.2.0"), release("v1.9.0", draft=True)]
        self.net(lambda req: Resp(json.dumps(lst).encode(), 200, {"ETag": '"l"'}))
        self.assertEqual(U.check(REPO, "1.0.0", prereleases=True)["update"]["version"], "1.3.0-beta.1")
        self.assertIn("per_page", self.calls[-1][0])
        self.net(self.latest(release("v1.2.0")))
        self.assertEqual(U.check(REPO, "1.0.0", prereleases=False)["update"]["version"], "1.2.0")

    def test_prerelease_list_never_offers_drafts_or_junk_tags(self):
        lst = [release("v9.0.0", draft=True), release("nightly"), release("v1.1.0")]
        self.net(lambda req: Resp(json.dumps(lst).encode(), 200, {}))
        self.assertEqual(U.check(REPO, "1.0.0", prereleases=True)["update"]["version"], "1.1.0")

    def test_rate_limited_with_reset_header(self):
        reset = str(int(time.time()) + 600)
        self.net(lambda req: (_ for _ in ()).throw(http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})))
        with self.assertRaises(U.UpdateError) as c:
            U.check(REPO, "1.0.0")
        self.assertEqual(c.exception.code, "rate_limited")
        self.assertAlmostEqual(c.exception.retry_at, float(reset), delta=1)

    def test_rate_limited_with_retry_after_and_429(self):
        self.net(lambda req: (_ for _ in ()).throw(http_error(429, {"Retry-After": "120"})))
        with self.assertRaises(U.UpdateError) as c:
            U.check(REPO, "1.0.0")
        self.assertEqual(c.exception.code, "rate_limited")
        self.assertGreater(c.exception.retry_at, time.time() + 100)

    def test_plain_403_is_forbidden_not_rate_limit(self):
        self.net(lambda req: (_ for _ in ()).throw(http_error(403)))
        with self.assertRaises(U.UpdateError) as c:
            U.check(REPO, "1.0.0")
        self.assertEqual(c.exception.code, "forbidden")

    def test_404_5xx_offline_timeout_badjson(self):
        cases = [(http_error(404), "not_found"), (http_error(503), "github_error"),
                 (urllib.error.URLError("no route"), "offline"), (TimeoutError(), "offline"), (ConnectionResetError(), "offline")]
        for exc, code in cases:
            self.net(lambda req, e=exc: (_ for _ in ()).throw(e))
            with self.assertRaises(U.UpdateError) as c:
                U.check(REPO, "1.0.0")
            self.assertEqual(c.exception.code, code, repr(exc))
        self.net(lambda req: Resp(b"<html>not json</html>", 200, {}))
        with self.assertRaises(U.UpdateError) as c:
            U.check(REPO, "1.0.0")
        self.assertEqual(c.exception.code, "bad_response")

    def test_oversized_reply_is_refused(self):
        self.net(lambda req: Resp(b"x" * (U.MAX_API_BYTES + 10), 200, {}))
        with self.assertRaises(U.UpdateError) as c:
            U.check(REPO, "1.0.0")
        self.assertEqual(c.exception.code, "bad_response")

    def test_bad_inputs_make_no_request(self):
        self.net(lambda req: self.fail("no request expected"))
        for repo, cur, code in [("nope", "1.0.0", "bad_repo"), ("", "1.0.0", "bad_repo"), (REPO, "weird", "bad_version")]:
            with self.assertRaises(U.UpdateError) as c:
                U.check(repo, cur)
            self.assertEqual(c.exception.code, code)
        self.assertEqual(self.calls, [])


class Download(Base):
    def prep(self, **kw):
        rel = release(**kw)
        return U.build_update(U._trim_release(rel), REPO)

    def serve(self, body=EXE, sums_text=None, on_exe=None, range_ok=True):
        sums = sums_text if sums_text is not None else f"{EXE_SHA}  Lyra v1.0_1.2.0_x64-setup.exe\n"

        def handler(req):
            url = req.full_url
            hdr = {k.lower(): v for k, v in req.headers.items()}
            if url.endswith("SHA256SUMS") or url.endswith(".sha256"):
                return Resp(sums.encode(), 200, {})
            if on_exe:
                r = on_exe(req, hdr)
                if r is not None:
                    return r
            rng = hdr.get("range")
            if rng and range_ok:
                start = int(rng.split("=")[1].split("-")[0])
                return Resp(body[start:], 206, {})
            return Resp(body, 200, {})
        self.net(handler)

    def run_dl(self, upd, **kw):
        return U.download(upd, REPO, "1.0.0", sleep=lambda s: None, **kw)

    def ready_json(self):
        return (U.updates_dir() / "ready.json").exists()

    def test_happy_path(self):
        upd = self.prep()
        self.serve()
        seen = []
        rec = self.run_dl(upd, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(rec["sha256"], EXE_SHA)
        self.assertEqual(rec["version"], "1.2.0")
        self.assertTrue((U.updates_dir() / rec["file"]).is_file())
        self.assertEqual(seen[-1], (len(EXE), len(EXE)))
        self.assertEqual(len(rec["verified_against"]), 2)
        self.assertEqual(U.verify_ready("1.0.0")["sha256"], EXE_SHA)

    def test_sidecar_checksum_works(self):
        upd = self.prep(digest=False, sums=False, sidecar=True)
        self.serve(sums_text=EXE_SHA + "\n")
        self.assertEqual(self.run_dl(upd)["sha256"], EXE_SHA)

    def test_digest_only_works(self):
        self.serve()
        self.assertEqual(self.run_dl(self.prep(sums=False))["sha256"], EXE_SHA)

    def test_hash_mismatch_deletes_everything(self):
        bad = b"MZ" + b"\x00" * (len(EXE) - 2)
        self.serve(body=bad)
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(self.prep())
        self.assertEqual(c.exception.code, "checksum_mismatch")
        self.assertEqual([p.name for p in U.updates_dir().glob("*") if p.name != ".keep"], [])
        self.assertFalse(self.ready_json())

    def test_published_checksums_disagreeing_refuses(self):
        self.serve(sums_text=("0" * 64) + "  Lyra v1.0_1.2.0_x64-setup.exe\n")
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(self.prep())
        self.assertEqual(c.exception.code, "checksum_mismatch")
        self.assertFalse(self.ready_json())

    def test_checksum_file_not_listing_installer_refuses(self):
        self.serve(sums_text=f"{EXE_SHA}  something-else.exe\n")
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(self.prep())
        self.assertEqual(c.exception.code, "no_checksum")

    def test_no_published_checksum_never_downloads(self):
        self.net(lambda req: self.fail("must not touch the network"))
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(self.prep(digest=False, sums=False))
        self.assertEqual(c.exception.code, "no_checksum")

    def test_not_a_windows_program_is_deleted(self):
        html = b"<html>" + b"x" * (len(EXE) - 6)
        sha = hashlib.sha256(html).hexdigest()
        a = [{"name": INSTALLER, "size": len(html), "browser_download_url": BASE + INSTALLER, "digest": f"sha256:{sha}"}]
        self.serve(body=html)
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(U.build_update(U._trim_release(release(assets=a)), REPO))
        self.assertEqual(c.exception.code, "bad_response")
        self.assertFalse(self.ready_json())

    def test_stream_longer_than_declared_is_stopped(self):
        self.serve(body=EXE + b"extra" * 100)
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(self.prep())
        self.assertEqual(c.exception.code, "too_large")
        self.assertEqual([p.name for p in U.updates_dir().glob("*.part")], [])

    def test_truncated_download_resumes_with_range(self):
        state = {"n": 0}

        def on_exe(req, hdr):
            state["n"] += 1
            if state["n"] == 1:
                return Resp(EXE[:1500], 200, {})                 # connection "ends" early
            return None
        self.serve(on_exe=on_exe)
        rec = self.run_dl(self.prep())
        self.assertEqual(rec["sha256"], EXE_SHA)
        ranges = [h.get("range") for u, h in self.calls if u.endswith(".exe")]
        self.assertEqual(ranges[0], None)
        self.assertEqual(ranges[1], "bytes=1500-")

    def test_server_ignoring_range_restarts_from_zero(self):
        (U.updates_dir() / "Lyra-1.2.0-setup.part").write_bytes(b"MZ" + b"junk" * 50)
        self.serve(range_ok=False)
        self.assertEqual(self.run_dl(self.prep())["sha256"], EXE_SHA)

    def test_416_discards_the_partial_and_retries(self):
        (U.updates_dir() / "Lyra-1.2.0-setup.part").write_bytes(b"MZ" + b"z" * 200)
        state = {"n": 0}

        def on_exe(req, hdr):
            state["n"] += 1
            if state["n"] == 1:
                raise http_error(416, url=req.full_url)
        self.serve(on_exe=on_exe)
        self.assertEqual(self.run_dl(self.prep())["sha256"], EXE_SHA)

    def test_transient_error_then_success(self):
        state = {"n": 0}

        def on_exe(req, hdr):
            state["n"] += 1
            if state["n"] < 3:
                raise urllib.error.URLError("flaky wifi")
        self.serve(on_exe=on_exe)
        self.assertEqual(self.run_dl(self.prep())["sha256"], EXE_SHA)

    def test_gives_up_after_retries(self):
        self.serve(on_exe=lambda req, hdr: (_ for _ in ()).throw(urllib.error.URLError("down")))
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(self.prep())
        self.assertEqual(c.exception.code, "offline")
        self.assertFalse(self.ready_json())

    def test_404_and_rate_limit_are_not_retried(self):
        self.serve(on_exe=lambda req, hdr: (_ for _ in ()).throw(http_error(404, url=req.full_url)))
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(self.prep())
        self.assertEqual(c.exception.code, "not_found")
        self.assertEqual(len([1 for u, h in self.calls if u.endswith(".exe")]), 1)

    def test_cancel_keeps_partial_for_resume_and_writes_no_ready(self):
        cancel = threading.Event()

        def on_exe(req, hdr):
            class Slow(Resp):
                def read(s, n=-1):
                    cancel.set()
                    return super().read(1000)
            return Slow(EXE, 200, {})
        self.serve(on_exe=on_exe)
        with self.assertRaises(U.UpdateError) as c:
            self.run_dl(self.prep(), cancel=cancel)
        self.assertEqual(c.exception.code, "cancelled")
        self.assertFalse(self.ready_json())

    def test_disk_full(self):
        self.serve()
        real = shutil.disk_usage
        shutil.disk_usage = lambda p: shutil._ntuple_diskusage(100, 99, 1)
        try:
            with self.assertRaises(U.UpdateError) as c:
                self.run_dl(self.prep())
        finally:
            shutil.disk_usage = real
        self.assertEqual(c.exception.code, "disk_full")

    def test_a_new_download_clears_the_old_ready_marker(self):
        self.serve()
        self.run_dl(self.prep())
        self.serve(body=b"MZ" + b"\x01" * (len(EXE) - 2))          # now the server serves a different file
        with self.assertRaises(U.UpdateError):
            self.run_dl(self.prep())
        self.assertFalse(self.ready_json())


class VerifyReady(Base):
    def make_ready(self, version="1.2.0"):
        d = U.updates_dir()
        f = d / U._safe_name(version)
        f.write_bytes(EXE)
        rec = {"version": version, "file": f.name, "sha256": EXE_SHA, "size": len(EXE)}
        (d / "ready.json").write_text(json.dumps(rec))
        return f

    def test_ok(self):
        self.make_ready()
        self.assertEqual(U.verify_ready("1.0.0")["sha256"], EXE_SHA)

    def test_tampered_after_verification(self):
        f = self.make_ready()
        f.write_bytes(EXE[:-1] + b"X")                             # same size, different bytes
        with self.assertRaises(U.UpdateError) as c:
            U.verify_ready("1.0.0")
        self.assertEqual(c.exception.code, "checksum_mismatch")

    def test_truncated_file(self):
        f = self.make_ready()
        f.write_bytes(EXE[:100])
        with self.assertRaises(U.UpdateError) as c:
            U.verify_ready("1.0.0")
        self.assertEqual(c.exception.code, "checksum_mismatch")

    def test_not_newer_than_running_version(self):
        self.make_ready("1.2.0")
        for cur in ("1.2.0", "1.3.0"):
            with self.assertRaises(U.UpdateError) as c:
                U.verify_ready(cur)
            self.assertEqual(c.exception.code, "not_newer")

    def test_missing_file_or_marker(self):
        with self.assertRaises(U.UpdateError) as c:
            U.verify_ready("1.0.0")
        self.assertEqual(c.exception.code, "not_ready")
        f = self.make_ready()
        f.unlink()
        with self.assertRaises(U.UpdateError) as c:
            U.verify_ready("1.0.0")
        self.assertEqual(c.exception.code, "not_ready")

    def test_ready_json_cannot_point_outside_updates_folder(self):
        outside = Path(self.tmp) / "evil.exe"
        outside.write_bytes(EXE)
        (U.updates_dir() / "ready.json").write_text(json.dumps(
            {"version": "1.2.0", "file": "../evil.exe", "sha256": EXE_SHA, "size": len(EXE)}))
        with self.assertRaises(U.UpdateError) as c:
            U.verify_ready("1.0.0")
        self.assertEqual(c.exception.code, "not_ready")

    def test_cleanup_stale(self):
        f = self.make_ready("1.0.5")
        U.cleanup_stale("1.0.5")                                   # installed already: leftovers go
        self.assertFalse(f.exists())
        f = self.make_ready("1.9.0")
        old = time.time() - 9 * 86400
        os.utime(f, (old, old))
        (U.updates_dir() / "ready.json").unlink()
        U.cleanup_stale("1.0.0")                                   # orphan older than a week
        self.assertFalse(f.exists())

    def test_safe_name_ignores_hostile_versions(self):
        n = U._safe_name("1.2.0/../../x")
        self.assertNotIn("/", n)
        self.assertTrue(n.endswith("-setup.exe"))


class State(Base):
    def test_defaults_roundtrip_and_garbage(self):
        self.assertFalse(U.load_state()["enabled"])                # opt-in: off by default
        self.assertEqual(U.load_state()["repo"], "")
        U.update_state(enabled=True, repo="https://github.com/Acme/Lyra")
        st = U.load_state()
        self.assertTrue(st["enabled"])
        self.assertEqual(st["repo"], "Acme/Lyra")
        (Path(self.tmp) / "updater.json").write_text("{not json")
        self.assertFalse(U.load_state()["enabled"])
        (Path(self.tmp) / "updater.json").write_text(json.dumps({"enabled": "yes", "repo": "../../x"}))
        st = U.load_state()
        self.assertFalse(st["enabled"])                            # wrong type is ignored, not trusted
        self.assertEqual(st["repo"], "")


if __name__ == "__main__":
    unittest.main()
