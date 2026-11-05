"""Cross-phase integration checks for the merged Lyra (phases L, 0-8 + the screen-record pill).

Each phase has its own tests; these catch what no single phase could: two phases wrapping the same HTTP handler,
a setting one phase writes but the shared store drops on restart, and contracts between phases.
Offline. Run on its own:  python -m unittest tests.test_merge_integration
"""
import glob
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
os.environ["LYRA_DATA_DIR"] = tempfile.mkdtemp(prefix="lyra-merge-")

try:
    from engine.bridge import ui_bridge
    _ERR = None
except Exception as e:                                   # pragma: no cover
    ui_bridge, _ERR = None, e

from engine.core import settings_store  # noqa: E402

OWN = "tauri://localhost"
FOREIGN = ("https://evil.example", "http://127.0.0.1:9999", "null")


class _Headers(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def call(method, path, origin=None, body=b"{}"):
    class Fake(ui_bridge.Handler):
        def __init__(self):
            self.path, self.command = path, method
            self.headers = _Headers({"Content-Length": str(len(body))})
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
    raw = f.wfile.getvalue()
    try:
        return f.codes[0], json.loads(raw or b"{}")
    except ValueError:
        return f.codes[0], raw


# One read route per phase API, and its most sensitive write routes.
READ_ROUTES = ["/p0/secrets/status", "/p1/voice/status", "/p2/status", "/p2/settings", "/dictation/stats?range=week",
               "/p5/wake", "/p7/notetaker/status", "/p8/status"]
WRITE_ROUTES = ["/p0/secrets/migrate", "/p0/secrets/confirm", "/p1/voice/download", "/p1/voice/delete", "/p2/erase-everything",
                "/p2/paste-last", "/dictation/stats/clear", "/p5/wake/eleven-key", "/p7/notetaker/start",
                "/p7/notetaker/delete-all", "/p8/config", "/p8/download", "/p8/install"]


@unittest.skipIf(ui_bridge is None, f"ui_bridge not importable here: {_ERR}")
class OneBridgeManyPhases(unittest.TestCase):
    def test_every_phase_route_answers_for_lyras_own_windows(self):
        for route in READ_ROUTES:
            code, obj = call("GET", route, OWN)
            self.assertEqual(code, 200, route)
            self.assertIsInstance(obj, dict, route)

    def test_every_phase_route_refuses_foreign_web_pages(self):
        for origin in FOREIGN:
            for route in READ_ROUTES:
                self.assertEqual(call("GET", route, origin)[0], 403, (origin, route))
            for route in WRITE_ROUTES:
                self.assertEqual(call("POST", route, origin, b'{"confirm": true}')[0], 403, (origin, route))

    def test_wrapping_the_handler_did_not_break_the_original_routes(self):
        for route in ("/health", "/settings/features"):
            code, _ = call("GET", route, OWN)
            self.assertEqual(code, 200, route)
        self.assertEqual(call("GET", "/definitely/not/a/route", OWN)[0], 404)

    def test_tokens_in_the_keychain_are_not_readable_by_a_web_page(self):
        settings_store.set_many({"ha_token": "SECRET-HA-TOKEN-123"})
        code, obj = call("GET", "/settings/features", "https://evil.example")
        self.assertEqual(code, 403)
        self.assertNotIn("SECRET-HA-TOKEN-123", json.dumps(obj))
        self.assertEqual(call("POST", "/settings/features", "https://evil.example", b'{"ha_token": "x"}')[0], 403)
        for origin in (OWN, "http://tauri.localhost", "http://localhost:1420", None):          # Lyra's own windows still work
            self.assertEqual(call("GET", "/settings/features", origin)[0], 200, origin)

    def test_destructive_routes_still_need_their_own_confirmation(self):
        code, obj = call("POST", "/p2/erase-everything", OWN, b"{}")
        self.assertNotEqual(code, 200 if obj == {"ok": True} else 999)
        self.assertFalse(isinstance(obj, dict) and obj.get("ok") is True and code == 200 and obj.get("erased"))
        code, obj = call("POST", "/p8/install", OWN, b'{"confirm": true, "version": "9.9.9"}')
        self.assertEqual((code, obj["ok"]), (200, False))            # nothing verified is waiting

    def test_settings_export_contains_no_tokens(self):
        import types
        settings_store.set_many({"ha_token": "SECRET-HA-TOKEN-123", "ntfy_token": "SECRET-NTFY-456"})
        fake = types.ModuleType("engine.shortcuts")                          # the real one needs the Windows audio stack
        fake.settings_api = types.SimpleNamespace(list_all=lambda: {"shortcuts": []})
        saved = {k: sys.modules.get(k) for k in ("engine.shortcuts", "engine.shortcuts.settings_api")}
        sys.modules["engine.shortcuts"], sys.modules["engine.shortcuts.settings_api"] = fake, fake.settings_api
        try:
            bundle = ui_bridge._export_bundle()
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertNotIn("SECRET-HA-TOKEN-123", json.dumps(bundle))
        self.assertNotIn("SECRET-NTFY-456", json.dumps(bundle))
        self.assertTrue(bundle["prefs"]["features"])                  # the rest of the group is still exported

    def test_screen_record_pill_is_wired_end_to_end(self):
        self.assertTrue(callable(getattr(ui_bridge, "push_record", None)))
        from engine.features import screen_record
        pushed = []
        orig = ui_bridge.push_record
        ui_bridge.push_record = lambda rec, at=0: pushed.append((rec, at))
        try:
            screen_record._push()
        finally:
            ui_bridge.push_record = orig
        self.assertEqual(pushed, [(False, 0)])                       # not recording: tells the island to hide the pill


class SettingsSurviveRestart(unittest.TestCase):
    def test_all_phase_settings_are_registered(self):
        used = set()
        for f in glob.glob(os.path.join(BACKEND, "**", "*.py"), recursive=True):
            if os.sep + "tests" + os.sep in f:
                continue
            used |= set(re.findall(r'["\'](p[0-9]_[a-z0-9_]+)["\']', open(f, encoding="utf-8", errors="ignore").read()))
        missing = sorted(k for k in used if k not in settings_store._DEFAULTS)
        self.assertEqual(missing, [], "written to disk but dropped by settings_store on the next start")

    def test_phase2_defaults_match_the_registered_ones(self):
        from engine.voice import dictation_clean
        for k, v in dictation_clean.P2_DEFAULTS.items():
            self.assertIn(k, settings_store._DEFAULTS, k)
            self.assertEqual(settings_store._DEFAULTS[k], v, k)

    def test_a_dictation_setting_survives_a_real_restart(self):
        data = tempfile.mkdtemp(prefix="lyra-restart-")
        env = dict(os.environ, LYRA_DATA_DIR=data, PYTHONDONTWRITEBYTECODE="1")
        w = "from engine.core import settings_store as s; s.set_many({'p2_hold_key': 'f9', 'p2_output': 'paste', 'p2_ai_cleanup': True})"
        r = "from engine.core import settings_store as s; print(s.get('p2_hold_key'), s.get('p2_output'), s.get('p2_ai_cleanup'))"
        subprocess.run([sys.executable, "-c", w], cwd=os.path.dirname(BACKEND), env=env, check=True)
        out = subprocess.run([sys.executable, "-c", r], cwd=os.path.dirname(BACKEND), env=env, check=True, capture_output=True, text=True)
        self.assertEqual(out.stdout.split(), ["f9", "paste", "True"])

    def test_no_duplicate_default_keys(self):
        src = open(os.path.join(BACKEND, "core", "settings_store.py"), encoding="utf-8").read()
        body = re.search(r"_DEFAULTS[^=]*=\s*\{(.*?)\n\}\n", src, re.S).group(1)
        keys = re.findall(r'^\s*"([A-Za-z0-9_]+)"\s*:', body, re.M)
        self.assertEqual(len(keys), len(set(keys)))


class Contracts(unittest.TestCase):
    def test_c1_stt_engines(self):
        from engine.voice import stt_engines
        self.assertEqual(list(inspect.signature(stt_engines.transcribe).parameters)[:3], ["audio", "language", "hints"])
        for fn in ("status", "set_engine"):
            self.assertTrue(callable(getattr(stt_engines, fn)), fn)

    def test_c2_secrets_store(self):
        from engine.core import secrets_store
        for fn in ("get", "set", "delete", "has"):
            self.assertTrue(callable(getattr(secrets_store, fn)), fn)

    def test_c3_dictation_stats_matches_the_way_dictation_calls_it(self):
        from engine.voice import dictation_stats
        params = list(inspect.signature(dictation_stats.record).parameters)
        self.assertEqual(params, ["words", "seconds", "app", "engine", "language"])
        src = open(os.path.join(BACKEND, "voice", "dictation.py"), encoding="utf-8").read()
        self.assertIn("dictation_stats.record(len(text.split()), seconds, app, engine, language)", src)

    def test_c4_dictionary_and_history(self):
        from engine.voice import dictionary_store
        from engine.voice import history_store
        self.assertTrue(callable(dictionary_store.hints) and callable(dictionary_store.apply_replacements))
        self.assertEqual(list(inspect.signature(history_store.add).parameters)[:5], ["text", "app", "engine", "language", "seconds"])


if __name__ == "__main__":
    unittest.main()
