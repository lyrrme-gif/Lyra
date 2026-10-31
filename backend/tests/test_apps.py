"""Tests for app plugins (Phase 4): plugins/apps.py and the app part of plugins/loader.py.
From the backend folder:

    python -m unittest tests.test_apps -v

No network, no login, no real app: the plugins here are fakes, so these check the framework (what is
accepted, what is cleaned, what is isolated, what is shown once), not Spotify or WhatsApp.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from plugins import apps, loader


def make_app(**over):
    state = {"connected": True}
    app = {
        "id": "fake", "title": "Fake", "icon": "chat",
        "status": lambda: {"connected": state["connected"], "account": "me"},
        "view": lambda route, params: {"type": "list", "title": route or "Home", "items": [{"id": "1", "title": "One"}]},
        "action": lambda name, params: {"ok": True, "message": f"did {name}"},
        "poll": lambda: [],
    }
    app.update(over)
    return app, state


def install(app):
    """Put one app plugin straight into the loader, as if its file had been read."""
    plugin = loader._validate({"name": "Fake plugin", "description": "x", "app": app}, Path("fake.py"), "user")
    loader._plugins.clear()
    loader._plugins[plugin.name] = plugin
    loader._loaded = True
    return plugin


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = (dict(loader._plugins), loader._loaded)
        p = mock.patch.object(loader, "feature_enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(loader, "disabled_names", return_value=set())
        q.start()
        self.addCleanup(q.stop)
        apps._seen.clear()
        apps._primed.clear()

    def tearDown(self):
        loader._plugins.clear()
        loader._plugins.update(self._saved[0])
        loader._loaded = self._saved[1]


class Validation(Base):
    def test_app_only_plugin_needs_no_patterns_or_handler(self):
        plugin = install(make_app()[0])
        self.assertEqual(plugin.patterns, [])
        self.assertIsNone(plugin.handler)
        self.assertEqual(plugin.app["id"], "fake")

    def test_plain_plugin_still_needs_patterns(self):
        with self.assertRaises(ValueError):
            loader._validate({"name": "X", "handler": lambda c: None}, Path("x.py"), "user")

    def test_bad_app_dicts_are_rejected(self):
        for bad in ({"id": "Has Space", "title": "T", "view": lambda r, p: {}},
                    {"id": "ok", "title": "", "view": lambda r, p: {}},
                    {"id": "ok", "title": "T"},
                    {"id": "ok", "title": "T", "view": lambda r, p: {}, "poll": "nope"}):
            with self.assertRaises(ValueError):
                apps.validate_app(bad)

    def test_poll_seconds_is_clamped(self):
        self.assertEqual(apps.validate_app({"id": "a", "title": "A", "view": lambda r, p: {}, "poll_seconds": 1})["poll_seconds"], 10.0)

    def test_app_plugin_does_not_become_an_agent_tool_without_a_handler(self):
        # Stand-ins for tools.base / tools.registry: other tests replace those modules, so do not depend on them.
        import sys
        import types
        registered = {}
        base = types.ModuleType("tools.base")
        base.ToolCategory = types.SimpleNamespace(SYSTEM="system", UTILITY="utility", INFO="info", OTHER="other")
        base.ToolParameter = lambda *a, **k: (a, k)
        base.ToolResult = lambda *a, **k: (a, k)
        base.ToolDefinition = lambda **k: types.SimpleNamespace(**k)
        reg = types.ModuleType("tools.registry")
        reg.get_registry = lambda: types.SimpleNamespace(tools=registered)
        reg.register_tool = lambda d: registered.__setitem__(getattr(d, "id", None) or getattr(d, "name", "?"), d)
        pkg = types.ModuleType("tools")
        pkg.__path__ = []
        voice = loader._validate({"name": "Voice only", "description": "x", "patterns": ["hello"], "handler": lambda c: "hi"},
                                 Path("v.py"), "user")
        app_only = install(make_app()[0])
        loader._plugins[voice.name] = voice
        with mock.patch.dict(sys.modules, {"tools": pkg, "tools.base": base, "tools.registry": reg}):
            loader._sync_tools()
        names = " ".join(str(k) + " " + str(getattr(v, "name", "")) for k, v in registered.items())
        self.assertIn("Voice only".lower().replace(" ", "_"), names.lower().replace(" ", "_"))
        self.assertNotIn(app_only.name.lower().replace(" ", "_"), names.lower().replace(" ", "_"))


class Views(Base):
    def test_list_view_round_trip(self):
        install(make_app()[0])
        out = apps.get_view("fake", "Mine")
        self.assertTrue(out["ok"])
        self.assertEqual(out["view"]["title"], "Mine")
        self.assertEqual(out["view"]["items"][0]["title"], "One")

    def test_unknown_view_type_becomes_a_message(self):
        v = apps.sanitize_view({"type": "html", "text": "<script>"})
        self.assertEqual(v["type"], "message")
        self.assertNotIn("<script>", str(v))

    def test_only_known_keys_and_https_images(self):
        v = apps.sanitize_view({"type": "list", "evil": 1, "items": [
            {"id": 1, "title": "a" * 500, "image": "http://x/y.png", "onclick": "x"},
            {"title": "b", "image": "https://x/y.png"}, "junk"]})
        self.assertNotIn("evil", v)
        self.assertEqual(len(v["items"]), 2)
        self.assertEqual(len(v["items"][0]["title"]), 80)
        self.assertEqual(v["items"][0]["image"], "")
        self.assertEqual(v["items"][1]["image"], "https://x/y.png")
        self.assertNotIn("onclick", v["items"][0])

    def test_lists_are_capped(self):
        v = apps.sanitize_view({"type": "list", "items": [{"title": str(i)} for i in range(500)]})
        self.assertEqual(len(v["items"]), apps.MAX_ITEMS)

    def test_thread_view_keeps_compose_only_when_valid(self):
        good = apps.sanitize_view({"type": "thread", "messages": [{"text": "hi", "mine": 1}],
                                   "compose": {"action": "send", "params": {"to": "x"}}})
        self.assertEqual(good["compose"]["action"], "send")
        self.assertTrue(good["messages"][0]["mine"])
        self.assertNotIn("compose", apps.sanitize_view({"type": "thread", "compose": {"action": 5}}))

    def test_missing_or_disabled_app(self):
        self.assertFalse(apps.get_view("nope")["ok"])
        install(make_app()[0])
        with mock.patch.object(loader, "feature_enabled", return_value=False):
            self.assertFalse(apps.get_view("fake")["ok"])
            self.assertEqual(apps.list_apps()["apps"], [])

    def test_switched_off_plugin_is_hidden(self):
        install(make_app()[0])
        with mock.patch.object(loader, "disabled_names", return_value={"Fake plugin"}):
            self.assertFalse(apps.get_view("fake")["ok"])


class AppSettings(Base):
    def test_toggle_and_text_settings_round_trip(self):
        app, _ = make_app(settings=[{"key": "toasts", "type": "toggle", "default": True, "label": "Toasts"},
                                    {"key": "client_id", "label": "Client ID"}])
        install(app)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(apps, "store", lambda i: apps.AppStore(i, Path(d))):
            fields = {f["key"]: f for f in apps.list_apps()["apps"][0]["settings"]}
            self.assertIs(fields["toasts"]["value"], True)           # the default shows until the user chooses
            self.assertFalse(fields["client_id"]["set"])
            apps.save_settings("fake", {"toasts": 0, "client_id": " abc ", "evil": 1})
            self.assertIs(apps.setting("fake", "toasts"), False)
            self.assertEqual(apps.setting("fake", "client_id"), "abc")
            self.assertIsNone(apps.setting("fake", "evil"))          # only declared keys are stored

    def test_description_and_accent_are_cleaned(self):
        a = apps.validate_app({"id": "a", "title": "A", "view": lambda r, p: {}, "accent": "red", "description": "d" * 500})
        self.assertEqual(a["accent"], "")
        self.assertEqual(len(a["description"]), 140)
        self.assertEqual(apps.validate_app({"id": "a", "title": "A", "view": lambda r, p: {}, "accent": "#2fd16f"})["accent"], "#2fd16f")


class Isolation(Base):
    def test_a_raising_plugin_never_raises_into_lyra(self):
        def boom(route, params):
            raise RuntimeError("secret token abc")
        install(make_app(view=boom)[0])
        out = apps.get_view("fake")
        self.assertFalse(out["ok"])
        self.assertNotIn("secret", out["error"])     # the reason goes to the log, not to the screen

    def test_a_slow_plugin_is_abandoned(self):
        install(make_app(view=lambda r, p: time.sleep(3))[0])
        with mock.patch.object(apps, "CALL_TIMEOUT_S", 0.2):
            t = time.time()
            self.assertFalse(apps.get_view("fake")["ok"])
            self.assertLess(time.time() - t, 2)

    def test_status_failure_means_not_connected(self):
        def bad():
            raise ValueError("x")
        install(make_app(status=bad)[0])
        self.assertFalse(apps.list_apps()["apps"][0]["connected"])


class Actions(Base):
    def test_action_and_view_in_reply(self):
        install(make_app(action=lambda n, p: {"ok": True, "message": "sent", "view": {"type": "message", "text": "done"}})[0])
        out = apps.run_action("fake", "send", {"text": "hi"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["view"]["text"], "done")

    def test_connect_only_gives_https_urls(self):
        install(make_app(connect=lambda: {"url": "file:///c:/windows"})[0])
        self.assertEqual(apps.connect("fake")["url"], "")
        install(make_app(connect=lambda: {"url": "https://accounts.example/login"})[0])
        self.assertEqual(apps.connect("fake")["url"], "https://accounts.example/login")

    def test_app_without_sign_in(self):
        install(make_app()[0])
        self.assertFalse(apps.connect("fake")["ok"])


class Polling(Base):
    def test_backlog_is_not_replayed_then_new_items_show_once(self):
        feed = [{"id": "a", "sender": "Sara", "text": "old"}]
        app, _ = make_app(poll=lambda: list(feed))
        install(app)
        shown = []
        push = lambda *a: shown.append(a)
        self.assertEqual(apps.poll_once(app, push), 0)          # first look = backlog
        feed.append({"id": "b", "sender": "Sara", "text": "new"})
        self.assertEqual(apps.poll_once(app, push), 1)
        self.assertEqual(apps.poll_once(app, push), 0)          # same item is not shown twice
        self.assertEqual(shown, [("fake", "message", "Sara", "new")])

    def test_disconnected_app_is_not_polled(self):
        called = []
        app, state = make_app(poll=lambda: called.append(1) or [])
        install(app)
        state["connected"] = False
        apps.poll_once(app, lambda *a: None)
        self.assertEqual(called, [])


class Store(unittest.TestCase):
    def test_store_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            s = apps.AppStore("x", Path(d))
            self.assertIsNone(s.get("token"))
            s.set("token", "t")
            self.assertEqual(apps.AppStore("x", Path(d)).get("token"), "t")
            s.delete("token")
            self.assertIsNone(s.get("token"))


if __name__ == "__main__":
    unittest.main()
