"""Tests for Phase 3 (dictation statistics): dictation_stats.py and api_p3.py.
From the backend folder:  python -m unittest tests.test_p3_stats -v

Everything uses a throw-away SQLite file in a temp folder: no network, no microphone, nothing in the
real data dir.
"""
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api_p3  # noqa: E402
import dictation_stats as ds  # noqa: E402

TODAY = date(2026, 10, 20)  # a Tuesday


def at(d: date, hour: int = 12) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc)


def ago(n: int) -> date:
    return TODAY - timedelta(days=n)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ds.Store(Path(self._tmp.name) / "stats.db")

    def add(self, days_ago: int, words: int, seconds: float = 10, app: str = "Notepad", **kw):
        return self.store.record(words, seconds, app, kw.get("engine", "whisper"), kw.get("language", "en"),
                                 now=at(ago(days_ago)))

    def summary(self, rng="week", **kw):
        return self.store.summary(rng, today=TODAY, **kw)


class MathTests(unittest.TestCase):
    def test_typing_time_at_45_wpm(self):
        self.assertAlmostEqual(ds.typing_seconds(45), 60.0)
        self.assertAlmostEqual(ds.typing_seconds(90), 120.0)
        self.assertEqual(ds.typing_seconds(-5), 0)

    def test_time_saved(self):
        # 90 words = 120 s of typing; spoke for 30 s -> 90 s saved
        self.assertAlmostEqual(ds.time_saved_seconds(90, 30), 90.0)

    def test_time_saved_never_negative(self):
        self.assertEqual(ds.time_saved_seconds(5, 600), 0.0)
        self.assertEqual(ds.time_saved_seconds(0, 0), 0.0)

    def test_words_per_minute(self):
        self.assertEqual(ds.words_per_minute(150, 60), 150.0)
        self.assertEqual(ds.words_per_minute(50, 20), 150.0)
        self.assertEqual(ds.words_per_minute(50, 0), 0.0)     # unknown duration -> no made-up number
        self.assertEqual(ds.words_per_minute(0, 30), 0.0)

    def test_normalize_app(self):
        self.assertEqual(ds.normalize_app("  chrome.EXE "), "chrome")
        self.assertEqual(ds.normalize_app(""), ds.UNKNOWN_APP)
        self.assertEqual(ds.normalize_app(None), ds.UNKNOWN_APP)
        self.assertEqual(ds.normalize_app(".exe"), ds.UNKNOWN_APP)
        self.assertEqual(len(ds.normalize_app("x" * 500)), ds.MAX_APP_LEN)


class StreakTests(unittest.TestCase):
    def iso(self, *offsets):
        return [ago(n).isoformat() for n in offsets]

    def test_empty(self):
        self.assertEqual(ds.streaks([], TODAY), (0, 0))

    def test_today_only(self):
        self.assertEqual(ds.streaks(self.iso(0), TODAY), (1, 1))

    def test_consecutive_days_ending_today(self):
        self.assertEqual(ds.streaks(self.iso(0, 1, 2, 3), TODAY), (4, 4))

    def test_streak_alive_until_midnight(self):
        # nothing yet today, but yesterday and the day before count
        self.assertEqual(ds.streaks(self.iso(1, 2), TODAY), (2, 2))

    def test_streak_broken(self):
        # last dictation two days ago -> current is 0, longest remembered
        self.assertEqual(ds.streaks(self.iso(2, 3, 4), TODAY), (0, 3))

    def test_gap_and_longest(self):
        self.assertEqual(ds.streaks(self.iso(0, 1, 5, 6, 7, 8), TODAY), (2, 4))

    def test_duplicates_ignored(self):
        self.assertEqual(ds.streaks(self.iso(0, 0, 1, 1), TODAY), (2, 2))

    def test_across_month_and_year_boundary(self):
        today = date(2027, 1, 2)
        days = ["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"]
        self.assertEqual(ds.streaks(days, today), (4, 4))


class RecordTests(Base):
    def test_records_and_reads_back(self):
        self.assertTrue(self.add(0, 42, 18, app="Chrome.exe"))
        t = self.summary()
        self.assertEqual(t["totals"]["words"], 42)
        self.assertEqual(t["totals"]["dictations"], 1)
        self.assertEqual(t["apps"][0]["app"], "Chrome")

    def test_ignores_empty_dictations(self):
        self.assertFalse(self.store.record(0, 5, "x", "e", "en"))
        self.assertFalse(self.store.record(-3, 5, "x", "e", "en"))
        self.assertFalse(self.store.record("nonsense", 5, "x", "e", "en"))
        self.assertFalse(self.summary()["ever_dictated"])

    def test_bad_seconds_become_unknown_duration(self):
        for bad in (None, "abc", float("nan"), -4):
            self.store.record(10, bad, "App", "e", "en", now=at(TODAY))
        t = self.summary()
        self.assertEqual(t["totals"]["words"], 40)
        self.assertEqual(t["totals"]["seconds"], 0)
        self.assertEqual(t["totals"]["wpm"], 0.0)

    def test_absurd_duration_is_clamped(self):
        self.store.record(10, 10 ** 9, "App", "e", "en", now=at(TODAY))
        self.assertEqual(self.summary()["totals"]["seconds"], float(ds.MAX_SECONDS))

    def test_record_never_raises_even_if_database_is_unusable(self):
        bad = ds.Store(Path(self._tmp.name) / "no" / "such" / "dir" / "x.db")
        self.assertFalse(bad.record(5, 2, "a", "b", "c"))

    def test_module_level_contract_signature(self):
        # C3: record(words, seconds, app, engine, language) positional
        import inspect
        names = list(inspect.signature(ds.record).parameters)[:5]
        self.assertEqual(names, ["words", "seconds", "app", "engine", "language"])

    def test_disabled_records_nothing_but_keeps_history(self):
        self.add(0, 10)
        self.store.set_enabled(False)
        self.assertFalse(self.add(0, 99))
        t = self.summary()
        self.assertFalse(t["enabled"])
        self.assertEqual(t["totals"]["words"], 10)
        self.store.set_enabled(True)
        self.assertTrue(self.add(0, 5))

    def test_clear(self):
        self.add(0, 10)
        self.add(1, 20)
        self.assertEqual(self.store.clear(), 2)
        self.assertFalse(self.summary("all")["ever_dictated"])


class AggregationTests(Base):
    def test_empty_state(self):
        t = self.summary()
        self.assertFalse(t["has_data"])
        self.assertFalse(t["ever_dictated"])
        self.assertEqual(t["totals"]["words"], 0)
        self.assertEqual(t["streak"], {"current": 0, "longest": 0})
        self.assertEqual(t["apps"], [])
        self.assertEqual(len(t["series"]["points"]), 7)      # still a drawable (flat) chart

    def test_totals_wpm_and_time_saved(self):
        self.add(0, 90, 30)                # 120 s of typing vs 30 s spoken
        self.add(0, 45, 15)                # 60 s of typing vs 15 s spoken
        t = self.summary()["totals"]
        self.assertEqual(t["words"], 135)
        self.assertEqual(t["dictations"], 2)
        self.assertEqual(t["seconds"], 45)
        self.assertEqual(t["wpm"], 180.0)  # 135 words / 0.75 min
        self.assertEqual(t["saved_seconds"], 180 - 45)
        self.assertEqual(t["avg_words"], 67.5)

    def test_unknown_duration_rows_count_words_but_not_speed_or_savings(self):
        self.add(0, 90, 30)
        self.add(0, 1000, 0)               # duration unknown
        t = self.summary()["totals"]
        self.assertEqual(t["words"], 1090)
        self.assertEqual(t["wpm"], 180.0)
        self.assertEqual(t["saved_seconds"], 90)

    def test_week_is_last_seven_days_including_today(self):
        self.add(0, 1)
        self.add(6, 2)                     # inside
        self.add(7, 4)                     # outside
        t = self.summary("week")
        self.assertEqual(t["totals"]["words"], 3)
        pts = t["series"]["points"]
        self.assertEqual(len(pts), 7)
        self.assertEqual(pts[0]["key"], ago(6).isoformat())
        self.assertEqual(pts[-1]["key"], TODAY.isoformat())

    def test_month_is_last_thirty_days(self):
        self.add(29, 5)
        self.add(30, 7)
        t = self.summary("month")
        self.assertEqual(t["totals"]["words"], 5)
        self.assertEqual(len(t["series"]["points"]), 30)

    def test_all_includes_everything(self):
        self.add(0, 1)
        self.add(60, 2)
        t = self.summary("all")
        self.assertEqual(t["totals"]["words"], 3)
        self.assertEqual(t["series"]["granularity"], "day")
        self.assertEqual(len(t["series"]["points"]), 61)
        self.assertEqual(t["first_day"], ago(60).isoformat())

    def test_all_switches_to_months_for_long_history(self):
        self.add(0, 10)
        self.add(200, 20)
        self.add(201, 5)
        t = self.summary("all")
        pts = t["series"]["points"]
        self.assertEqual(t["series"]["granularity"], "month")
        self.assertEqual(sum(p["words"] for p in pts), 35)
        self.assertEqual(pts[-1]["key"], "2026-10")
        keys = [p["key"] for p in pts]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))          # no month twice, none skipped
        self.assertEqual(len(keys), 7)                      # Apr..Oct 2026

    def test_words_per_day_zero_filled(self):
        self.add(0, 10)
        self.add(0, 5)
        self.add(2, 7)
        pts = {p["key"]: p for p in self.summary("week")["series"]["points"]}
        self.assertEqual(pts[TODAY.isoformat()]["words"], 15)
        self.assertEqual(pts[TODAY.isoformat()]["dictations"], 2)
        self.assertEqual(pts[ago(1).isoformat()]["words"], 0)
        self.assertEqual(pts[ago(2).isoformat()]["words"], 7)

    def test_today_counter(self):
        self.add(0, 12)
        self.add(1, 99)
        self.assertEqual(self.summary()["today"], {"words": 12, "dictations": 1})

    def test_per_app_breakdown_sorted_with_shares(self):
        self.add(0, 60, app="Word")
        self.add(1, 30, app="chrome.exe")
        self.add(2, 10, app="Chrome")              # same app, different spelling
        apps = self.summary()["apps"]
        self.assertEqual([a["app"] for a in apps], ["Word", "chrome"])
        self.assertEqual([a["words"] for a in apps], [60, 40])
        self.assertAlmostEqual(apps[0]["share"], 0.6)
        self.assertAlmostEqual(sum(a["share"] for a in apps), 1.0, places=3)

    def test_missing_app_is_unknown(self):
        self.add(0, 5, app="")
        self.assertEqual(self.summary()["apps"][0]["app"], "Unknown")

    def test_app_list_collapses_into_other(self):
        for i in range(6):
            self.add(0, 100 - i * 10, app=f"App{i}")
        apps = self.summary(app_limit=4)["apps"]
        self.assertEqual(len(apps), 4)
        self.assertEqual(apps[-1]["app"], "Other")
        self.assertTrue(apps[-1]["other"])
        self.assertEqual(sum(a["words"] for a in apps), sum(100 - i * 10 for i in range(6)))
        self.assertAlmostEqual(sum(a["share"] for a in apps), 1.0, places=2)

    def test_streak_ignores_selected_range(self):
        for n in range(0, 12):
            self.add(n, 5)
        t = self.summary("week")
        self.assertEqual(t["streak"], {"current": 12, "longest": 12})

    def test_engine_breakdown(self):
        self.add(0, 10, engine="whisper")
        self.add(0, 30, engine="vosk")
        self.store.record(4, 2, "A", "", "en", now=at(TODAY))     # unknown engine is not listed
        engines = self.summary()["engines"]
        self.assertEqual([e["engine"] for e in engines], ["vosk", "whisper"])

    def test_unknown_range_falls_back_to_week(self):
        self.assertEqual(self.summary("decade")["range"], "week")

    def test_only_counts_are_stored(self):
        self.add(0, 5, app="Notepad")
        import sqlite3
        con = sqlite3.connect(str(Path(self._tmp.name) / "stats.db"))
        cols = {r[1] for r in con.execute("PRAGMA table_info(dictations)")}
        con.close()
        self.assertEqual(cols, {"id", "ts", "day", "words", "seconds", "app", "engine", "language"})


class ApiTests(Base):
    def setUp(self):
        super().setUp()
        self._orig = ds._default
        ds._default = self.store
        self.addCleanup(lambda: setattr(ds, "_default", self._orig))

    def test_not_our_path(self):
        self.assertIsNone(api_p3.http("GET", "/stats", {}, None))
        self.assertIsNone(api_p3.http("GET", "/dictation/apply", {}, None))
        self.assertIsNone(api_p3.http("POST", "/dictation/statsx", {}, {}))

    def test_get_summary(self):
        ds.record(20, 8, "Word", "whisper", "en")
        code, body = api_p3.http("GET", "/dictation/stats", {"range": ["all"]}, None)
        self.assertEqual(code, 200)
        self.assertEqual(body["range"], "all")
        self.assertEqual(body["totals"]["words"], 20)

    def test_enable_and_clear(self):
        ds.record(20, 8, "Word", "whisper", "en")
        self.assertEqual(api_p3.http("POST", "/dictation/stats/enabled", {}, {"enabled": False})[1]["enabled"], False)
        self.assertFalse(ds.record(5, 2, "Word", "whisper", "en"))
        code, body = api_p3.http("POST", "/dictation/stats/clear", {}, {})
        self.assertEqual((code, body["removed"]), (200, 1))

    def test_unknown_subpath(self):
        self.assertEqual(api_p3.http("GET", "/dictation/stats/nope", {}, None)[0], 404)

    def test_register_wraps_handler_once_and_falls_through(self):
        calls = []

        class H:
            path = "/health"
            headers = {}

            def do_GET(self):
                calls.append("orig_get")

            def do_POST(self):
                calls.append("orig_post")

        api_p3.register(H)
        api_p3.register(H)                     # idempotent
        h = H()
        h.do_GET()
        h.do_POST()
        self.assertEqual(calls, ["orig_get", "orig_post"])

    def test_register_serves_stats_and_enforces_origin(self):
        sent = []

        class H:
            def __init__(self, path, origin=None):
                self.path = path
                self.headers = {"Content-Length": "2"} | ({"Origin": origin} if origin else {})
                self.rfile = type("R", (), {"read": staticmethod(lambda n: b"{}")})()

            def do_GET(self):
                sent.append("orig")

            do_POST = do_GET

            def _own_origin(self, path):
                return self.headers.get("Origin") in (None, "tauri://localhost")

            def _json(self, code, obj):
                sent.append((code, obj.get("ok", obj.get("error"))))

        api_p3.register(H)
        H("/dictation/stats").do_GET()
        H("/dictation/stats", origin="https://evil.example").do_GET()
        H("/dictation/stats/clear").do_POST()
        H("/dictation/stats/clear", origin="https://evil.example").do_POST()
        self.assertEqual(sent, [(200, True), (403, "forbidden"), (200, True), (403, "forbidden")])


class RealBridgeTests(Base):
    """The real ui_bridge.Handler on a random local port (skipped where ui_bridge cannot be imported)."""

    def setUp(self):
        super().setUp()
        try:
            import ui_bridge
        except Exception as e:  # pragma: no cover - depends on the machine's installed packages
            self.skipTest(f"ui_bridge not importable here: {e}")
        if not hasattr(ui_bridge, "Handler"):   # another test module left a stub in sys.modules
            self.skipTest("ui_bridge is a test stub in this run; run this file on its own")
        import threading
        from http.server import ThreadingHTTPServer
        self._orig = ds._default
        ds._default = self.store
        self.addCleanup(lambda: setattr(ds, "_default", self._orig))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ui_bridge.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path, body=None, origin=None):
        import json
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_summary_over_real_http(self):
        self.add(0, 33, 12, app="Word.exe")
        code, body = self.call("/dictation/stats?range=week")
        self.assertEqual(code, 200)
        self.assertEqual(body["totals"]["words"], 33)
        self.assertEqual(body["apps"][0]["app"], "Word")

    def test_own_window_origin_allowed_and_web_pages_refused(self):
        self.assertEqual(self.call("/dictation/stats", origin="tauri://localhost")[0], 200)
        self.assertEqual(self.call("/dictation/stats", origin="https://evil.example")[0], 403)
        self.assertEqual(self.call("/dictation/stats/clear", {}, origin="https://evil.example")[0], 403)

    def test_post_clear_over_real_http(self):
        self.add(0, 5)
        self.assertEqual(self.call("/dictation/stats/clear", {})[1]["removed"], 1)

    def test_other_routes_still_work(self):
        code, body = self.call("/health")
        self.assertEqual((code, body.get("service")), (200, "lyra-ui-bridge"))
        self.assertEqual(self.call("/definitely-not-a-route")[0], 404)


if __name__ == "__main__":
    unittest.main()
