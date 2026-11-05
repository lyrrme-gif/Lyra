"""Phase 2: the /p2/ API (handle() directly, plus one pass through the real ui_bridge Handler)."""
import http.server
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request


from engine.bridge import api_p2  # noqa: E402
from engine.voice import dictation  # noqa: E402
import engine.voice.history_store as hs  # noqa: E402
from engine.tests.voice.test_p2_env import isolated  # noqa: E402


def call(method, path, body=None, query=None):
    return api_p2.handle(method, path, query or {}, body or {})


class SettingsApiTests(unittest.TestCase):
    def test_get_and_post(self):
        with isolated():
            code, obj = call("GET", "/p2/settings")
            self.assertEqual((code, obj["settings"]["p2_ai_cleanup"]), (200, False))
            code, obj = call("POST", "/p2/settings", {"p2_output": "paste", "p2_pill_position": "right"})
            self.assertEqual((code, obj["settings"]["p2_output"], obj["settings"]["p2_pill_position"]), (200, "paste", "right"))

    def test_bad_value_is_a_400_naming_the_key_and_saves_nothing(self):
        with isolated() as (_, store):
            code, obj = call("POST", "/p2/settings", {"p2_output": "paste", "p2_dictation_key": "esc"})
            self.assertEqual(code, 400)
            self.assertIn("p2_dictation_key", obj["error"])
            self.assertNotIn("p2_output", store)

    def test_unknown_route_is_404(self):
        with isolated():
            self.assertEqual(call("GET", "/p2/nope")[0], 404)
            self.assertEqual(call("GET", "/p2/erase-everything")[0], 404)       # POST only


class RemovedRoutesTests(unittest.TestCase):
    def test_dictionary_and_snippets_routes_are_gone(self):
        with isolated():
            for route in ("/p2/dictionary", "/p2/snippets"):
                self.assertEqual(call("GET", route)[0], 404, route)


class StylesApiTests(unittest.TestCase):
    def test_set_and_clear_a_style_for_an_app(self):
        with isolated():
            obj = call("POST", "/p2/styles", {"app": "WINWORD.EXE", "style": "natural"})[1]
            self.assertEqual(obj["styles"], {"winword": "natural"})
            self.assertEqual(obj["builtin"]["winword"], "polished")
            obj = call("POST", "/p2/styles", {"app": "winword", "style": ""})[1]
            self.assertEqual(obj["styles"], {})
            self.assertEqual(call("POST", "/p2/styles", {"app": "x", "style": "loud"})[0], 400)
            self.assertEqual(call("POST", "/p2/styles", {"style": "natural"})[0], 400)


class HistoryApiTests(unittest.TestCase):
    def test_search_delete_and_clear_needs_confirmation(self):
        with isolated():
            a = hs.add("buy milk", "notepad", "s", "en", 1)
            hs.add("call dentist", "outlook", "s", "en", 1)
            obj = call("GET", "/p2/history", query={"q": ["milk"]})[1]
            self.assertEqual([i["id"] for i in obj["items"]], [a])
            self.assertEqual((obj["count"], obj["enabled"], obj["retention_days"]), (2, True, 30))
            self.assertEqual(call("POST", "/p2/history", {"op": "delete", "id": a})[1]["count"], 1)
            code, obj = call("POST", "/p2/history", {"op": "clear"})
            self.assertEqual((code, hs.count()), (400, 1))                       # no confirmation, nothing erased
            code, obj = call("POST", "/p2/history", {"op": "clear", "confirm": "yes"})
            self.assertEqual((code, hs.count()), (400, 1))                       # only a real true counts
            code, obj = call("POST", "/p2/history", {"op": "clear", "confirm": True})
            self.assertEqual((code, obj["count"]), (200, 0))

    def test_paging_arguments_are_clamped(self):
        with isolated():
            hs.add("x one", "", "", "", 0)
            obj = call("GET", "/p2/history", query={"limit": ["99999"], "offset": ["-5"]})[1]
            self.assertEqual(len(obj["items"]), 1)


class EraseEverythingTests(unittest.TestCase):
    def test_needs_confirmation(self):
        with isolated():
            hs.add("keep me", "", "", "", 0)
            self.assertEqual(call("POST", "/p2/erase-everything", {})[0], 400)
            self.assertEqual(hs.count(), 1)

    def test_erases_history_dictionary_snippets_prefs_memory_and_stats(self):
        import types
        calls = []
        stats = types.ModuleType("engine.voice.dictation_stats")
        stats.erase = lambda: calls.append("erased")
        old = sys.modules.get("engine.voice.dictation_stats")
        sys.modules["engine.voice.dictation_stats"] = stats
        try:
            with isolated({"p2_output": "paste"}) as (_, store):
                hs.add("old text", "", "", "", 0)
                from engine.voice import dictionary_store
                dictionary_store.add_word("Anouk")
                dictionary_store.snippets_module().set_snippet("sign off", "x", None)
                dictation._last_text = "remember me"
                code, obj = call("POST", "/p2/erase-everything", {"confirm": True})
                self.assertEqual(code, 200)
                self.assertEqual(obj["erased"]["history"], 1)
                self.assertEqual((hs.count(), dictionary_store.words(), dictionary_store.snippets_module().all_snippets()),
                                 (0, [], []))
                self.assertEqual((store["p2_output"], dictation._last_text, calls), ("paste", "", ["erased"]))
        finally:
            if old is None:
                sys.modules.pop("engine.voice.dictation_stats", None)
            else:
                sys.modules["engine.voice.dictation_stats"] = old

    def test_works_without_phase3_stats(self):
        sys.modules["engine.voice.dictation_stats"] = None
        try:
            with isolated():
                code, obj = call("POST", "/p2/erase-everything", {"confirm": True})
                self.assertEqual((code, obj["erased"]["stats"]), (200, False))
        finally:
            sys.modules.pop("engine.voice.dictation_stats", None)


class StatusApiTests(unittest.TestCase):
    def test_status_without_the_other_phases(self):
        sys.modules["engine.voice.stt_engines"] = None
        sys.modules["engine.voice.dictation_stats"] = None
        try:
            with isolated():
                obj = call("GET", "/p2/status")[1]
                self.assertEqual((obj["engines"], obj["stats_available"], obj["ai_cleanup"]), (None, False, False))
        finally:
            sys.modules.pop("engine.voice.stt_engines", None)
            sys.modules.pop("engine.voice.dictation_stats", None)


class RealHandlerTests(unittest.TestCase):
    """The hook in ui_bridge.py really routes /p2/ and leaves everything else alone."""
    @classmethod
    def setUpClass(cls):
        from engine.bridge import ui_bridge
        cls.bridge = ui_bridge
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ui_bridge.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, method, path, body=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=headers,
                                   data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_registered_once_and_guarded(self):
        self.assertTrue(self.bridge.Handler._p2_wrapped)
        api_p2.register(self.bridge)                                   # a second call must not wrap twice
        self.assertEqual(self.bridge._GUARDED.count("/p2/"), 1)

    def test_routes_and_origin_guard(self):
        with isolated():
            self.assertEqual(self.req("GET", "/p2/settings")[0], 200)
            self.assertEqual(self.req("POST", "/p2/settings", {"p2_output": "paste"})[1]["settings"]["p2_output"], "paste")
            self.assertEqual(self.req("GET", "/p2/settings", origin="https://evil.example")[0], 403)
            self.assertEqual(self.req("POST", "/p2/erase-everything", {"confirm": True}, origin="https://evil.example")[0], 403)
            self.assertEqual(self.req("GET", "/p2/settings", origin="tauri://localhost")[0], 200)

    def test_other_routes_are_untouched(self):
        self.assertEqual(self.req("GET", "/health")[0], 200)

    def test_bad_json_body(self):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/p2/settings", method="POST", data=b"{nope",
                                   headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r)
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
