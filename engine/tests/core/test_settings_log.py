"""The Settings log: parsing, per-tab accounting, hostile input and the two HTTP routes. Offline, no Windows needed.
Run on its own:  python -m unittest tests.test_settings_log
"""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["LYRA_DATA_DIR"] = tempfile.mkdtemp(prefix="lyra-slog-")
os.environ["LYRA_TRACE"] = "1"              # the trace (and so the Settings log) is off by default; these tests need it on
os.environ["LYRA_TRACE_RES"] = "0"          # no resource-monitor thread from dbg.py in the test run

import engine.core.settings_log as sl                    # noqa: E402

try:
    from engine.bridge import ui_bridge
    _ERR = None
    if not hasattr(ui_bridge, "Handler"):      # another test file replaced the module with a stub in this run
        ui_bridge, _ERR = None, "ui_bridge is a stub in this test run (run this file on its own)"
except Exception as e:                       # pragma: no cover - optional deps missing on this machine
    ui_bridge, _ERR = None, e


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def snap(tab="general", vis=True, focus=True, **kw):
    d = {"tab": tab, "vis": vis, "focus": focus, "building": "", "pending": [], "lazy": {}, "inflight": [], "done": [],
         "ev": [], "perf": None, "canvas": []}
    d.update(kw)
    return d


class Parsing(unittest.TestCase):
    CSV = ('"(PDH-CSV 4.0)","\\\\PC\\GPU Engine(pid_100_luid_0x0_phys_0_eng_0_engtype_3D)\\Utilization Percentage",'
           '"\\\\PC\\GPU Engine(pid_100_luid_0x0_phys_0_eng_1_engtype_Copy)\\Utilization Percentage",'
           '"\\\\PC\\GPU Engine(pid_200_luid_0x0_phys_0_eng_0_engtype_3D)\\Utilization Percentage",'
           '"\\\\PC\\GPU Engine(pid_300_luid_0x0_phys_0_eng_0_engtype_3D)\\Utilization Percentage"\n'
           '"10/27/2026 10:00:00.000","6.5","1.5","12.25","0"\n')

    def test_gpu_csv_sums_engines_per_pid_and_drops_zero(self):
        out = sl.parse_gpu_csv(self.CSV)
        self.assertEqual(out[100], {"3D": 6.5, "Copy": 1.5})
        self.assertEqual(out[200], {"3D": 12.25})
        self.assertNotIn(300, out)

    def test_gpu_csv_garbage_is_empty(self):
        self.assertEqual(sl.parse_gpu_csv(""), {})
        self.assertEqual(sl.parse_gpu_csv("nothing useful\nat all"), {})

    def test_renderer_needs_a_clear_winner(self):
        idle = {1: 2.0, 2: 9.0, 3: 1.0}
        self.assertEqual(sl.pick_settings_renderer(idle, {1: 3.0, 2: 10.0, 3: 31.0}), 3)
        self.assertIsNone(sl.pick_settings_renderer(idle, {1: 3.0, 2: 10.0, 3: 5.0}))      # nobody rose enough
        self.assertIsNone(sl.pick_settings_renderer(idle, {1: 22.0, 2: 10.0, 3: 24.0}))    # two similar: refuse to guess
        self.assertIsNone(sl.pick_settings_renderer({}, {}))


class Accounting(unittest.TestCase):
    def setUp(self):
        sl.reset()
        sl._started = True                   # never start the sampler thread in tests
        self.clock = Clock()
        p = mock.patch.object(sl.time, "monotonic", self.clock)
        p.start()
        self.addCleanup(p.stop)

    def test_time_is_charged_to_the_shown_tab_and_unfocused_time_is_split_out(self):
        sl.ingest(snap("general"))
        self.clock.t += 4
        sl.ingest(snap("ai"))                                 # 4 s on general
        self.clock.t += 6
        sl.ingest(snap("ai", focus=False))                    # 6 s on ai, focused until now
        self.clock.t += 5
        sl.ingest(snap("ai", focus=False))                    # 5 s on ai, unfocused
        rep = sl.report()
        self.assertAlmostEqual(rep["tabs"]["general"]["shown_s"], 4.0, 1)
        self.assertAlmostEqual(rep["tabs"]["ai"]["shown_s"], 11.0, 1)
        self.assertAlmostEqual(rep["tabs"]["ai"]["unfocused_s"], 5.0, 1)

    def test_a_hidden_window_costs_no_shown_time(self):
        sl.ingest(snap("voice"))
        self.clock.t += 3
        sl.ingest(snap("voice", vis=False))
        self.clock.t += 10                                   # parked: nothing is charged
        sl.ingest(snap("voice", vis=False))
        self.assertAlmostEqual(sl.report()["tabs"]["voice"]["shown_s"], 3.0, 1)

    def test_requests_are_counted_per_tab_and_background_ones_are_flagged(self):
        sl.ingest(snap("general", done=[
            {"m": "GET", "p": "/stats?x=1", "o": "build:statistics:idle", "tab": "statistics", "kind": "build", "bg": True, "ms": 395, "st": 200},
            {"m": "GET", "p": "/about", "o": "build:about:idle", "tab": "about", "kind": "build", "bg": True, "ms": 12, "st": 200},
            {"m": "GET", "p": "/settings/llm", "o": "", "tab": "general", "kind": "user", "bg": False, "ms": 30, "st": 200},
        ], maxin=5))
        rep = sl.report()
        st = rep["tabs"]["statistics"]
        self.assertEqual((st["requests"], st["requests_for_tabs_not_shown"], st["request_max_ms"]), (1, 1, 395))
        self.assertEqual(st["requests_by"], {"build": 1})
        self.assertEqual(rep["tabs"]["general"]["requests_for_tabs_not_shown"], 0)
        self.assertEqual(rep["max_requests_in_flight"], 5)
        self.assertTrue(any("/stats" in line and "395" in line for line in rep["recent"]))     # slow ones are logged...
        self.assertFalse(any("?x=1" in line for line in rep["recent"]))                         # ...without the query string

    def test_page_numbers_average_per_tab_and_mixed_samples_are_skipped(self):
        sl.ingest(snap("ai", perf={"ok": True, "tab": "ai", "raf": 100, "main": 20, "anims": 10, "endless": 4}))
        sl.ingest(snap("ai", perf={"ok": True, "tab": "ai", "raf": 60, "main": 10, "anims": 6, "endless": 2}))
        sl.ingest(snap("ai", perf={"ok": False, "tab": "ai", "raf": 999, "main": 99, "anims": 99, "endless": 99}))
        t = sl.report()["tabs"]["ai"]
        self.assertEqual((t["raf_per_s"], t["main_thread_pct"], t["animations"], t["endless_animations"]), (80.0, 15.0, 8.0, 3.0))

    def test_build_time_is_recorded(self):
        sl.ingest(snap("general", ev=[{"k": "built", "m": "tab voice built", "tab": "voice", "ms": 41.26, "why": "idle fill"}]))
        t = sl.report()["tabs"]["voice"]
        self.assertEqual((t["built_ms"], t["built_by"]), (41.3, "idle fill"))

    def test_table_has_a_row_per_tab_and_explains_units(self):
        sl.ingest(snap("general"))
        self.clock.t += 3
        sl.ingest(snap("ai"))
        txt = sl.table_text()
        self.assertIn("general", txt)
        self.assertIn("100% = a whole core", txt)

    def test_hostile_bodies_never_raise(self):
        for body in (None, [], "x", 5, {}, {"tab": 5, "vis": "yes", "done": "no", "ev": {"a": 1}, "perf": [1], "inflight": 7,
                                              "lazy": [1], "pending": "abc", "canvas": 3, "maxin": "many"},
                     {"tab": "A" * 500, "done": [None, 1, {"ms": "x", "tab": "!!", "kind": "<b>"}], "ev": [None, {"k": 1}]},
                     {"tab": "ai", "vis": True, "perf": {"ok": True, "raf": float("nan"), "main": "x"}}):
            with self.subTest(body=str(body)[:40]):
                sl.ingest(body)
        sl.report()
        sl.table_text()
        sl._state_line(sl._ui if isinstance(sl._ui, dict) else {})

    def test_state_line_says_what_is_loading_and_what_waits(self):
        line = sl._state_line(snap("ai", building="voice", pending=["files", "about"], lazy={"ai": 2},
                                   inflight=[{"m": "GET", "p": "/stats", "o": "build:statistics:idle", "ms": 120}],
                                   perf={"ok": True, "raf": 99, "main": 22, "anims": 14, "endless": 6, "where": {"hero": 9, "sidebar": 2}},
                                   canvas=["hero drawn 1152x185 shown 1152x185 ON screen"]))
        for needle in ("building now: voice", "in flight 1", "/stats", "tabs still to build 2", "ai:2", "rAF 99/s", "hero 9", "1152x185"):
            self.assertIn(needle, line)


class _Me:
    def cpu_percent(self, _):
        return 3.0

    def memory_info(self):
        return type("M", (), {"rss": 1230 * 1048576})()


class _FakeP:
    renderer, how, me = 7, "test", _Me()

    def poll(self):
        return {7: (31.0, "renderer"), 8: (5.0, "gpu"), 9: (2.0, "browser")}

    def settle_renderer(self, cpu):
        pass


class _FakePs:
    @staticmethod
    def cpu_percent(_):
        return 40.0

    @staticmethod
    def Process(pid):
        return type("P", (), {"name": lambda self: "dwm.exe" if pid == 500 else "other.exe"})()


class Sampling(unittest.TestCase):
    def setUp(self):
        sl.reset()
        sl._started = True
        self.clock = Clock()
        p = mock.patch.object(sl.time, "monotonic", self.clock)
        p.start()
        self.addCleanup(p.stop)

    def gpu(self):
        return {"data": {7: {"3D": 4.0}, 8: {"3D": 10.0}, 500: {"3D": 12.0}, 600: {"3D": 2.0}}, "at": self.clock.t}

    def test_sample_is_charged_to_the_shown_tab(self):
        sl.ingest(snap("ai"))
        self.clock.t += 3
        sl._sample(_FakeP(), self.gpu(), _FakePs)
        t = sl.report()["tabs"]["ai"]
        self.assertEqual((t["samples"], t["cpu_settings_renderer_pct"], t["cpu_webview_total_pct"]), (1, 31.0, 38.0))
        self.assertEqual((t["gpu_webview_pct"], t["gpu_settings_renderer_pct"], t["gpu_dwm_pct"]), (14.0, 4.0, 12.0))
        self.assertTrue(any("SAMPLE" in e and "Settings renderer 31%" in e and "DWM 12%" in e for e in sl.report()["recent"]))

    def test_a_sample_that_spans_two_tabs_is_not_blamed_on_either(self):
        sl.ingest(snap("general"))
        self.clock.t += 1
        sl.ingest(snap("ai"))
        self.clock.t += 2
        sl.ingest(snap("ai"))
        self.clock.t += 0.1
        sl.ingest(snap("voice"))                              # 1 s general, 2 s ai: ai has 67% < 70%
        sl._sample(_FakeP(), self.gpu(), _FakePs)
        self.assertEqual(sl.report()["tabs"]["ai"]["samples"], 0)

    def test_without_gpu_numbers_the_cpu_still_counts(self):
        sl.ingest(snap("ai"))
        self.clock.t += 3
        sl._sample(_FakeP(), {"data": None, "at": 0.0}, _FakePs)
        t = sl.report()["tabs"]["ai"]
        self.assertEqual(t["cpu_settings_renderer_pct"], 31.0)
        self.assertIsNone(t["gpu_webview_pct"])


class Off(unittest.TestCase):
    def test_off_switch(self):
        with mock.patch.object(sl, "_OFF", True):
            self.assertEqual(sl.ingest(snap()), {"ok": True, "off": True})
            self.assertFalse(sl.http_get()["on"])


def _call(method, path, origin=None, body=b"{}"):
    class _H(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

    class Fake(ui_bridge.Handler):
        def __init__(self):
            self.path, self.command = path, method
            self.headers = _H({"Content-Length": str(len(body))})
            if origin:
                self.headers["Origin"] = origin
            self.rfile, self.wfile, self.codes = io.BytesIO(body), io.BytesIO(), []

        def send_response(self, code, message=None):
            self.codes.append(code)

        def send_header(self, *a):
            pass

        def end_headers(self):
            pass
    f = Fake()
    getattr(f, "do_" + method)()
    return f.codes[0], json.loads(f.wfile.getvalue() or b"{}")


@unittest.skipIf(ui_bridge is None, f"ui_bridge not importable here: {_ERR}")
class Routes(unittest.TestCase):
    def setUp(self):
        sl.reset()
        sl._started = True

    def test_guarded_prefix(self):
        self.assertIn("/settings-log", ui_bridge._GUARDED)

    def test_post_then_get(self):
        code, obj = _call("POST", "/settings-log", "tauri://localhost", json.dumps(snap("ai")).encode())
        self.assertEqual((code, obj), (200, {"ok": True, "off": False}))
        code, obj = _call("GET", "/settings-log", "http://tauri.localhost")
        self.assertEqual(code, 200)
        self.assertTrue(obj["open"])
        self.assertEqual(obj["shown_tab"], "ai")
        self.assertIn("table", obj)

    def test_foreign_web_pages_are_refused(self):
        self.assertEqual(_call("GET", "/settings-log", "https://evil.example")[0], 403)
        self.assertEqual(_call("POST", "/settings-log", "https://evil.example", b"{}")[0], 403)


if __name__ == "__main__":
    unittest.main()
