"""Phase 0 tests: settings.json tokens, sealed JSON blobs (plugin data) and the /p0 status endpoint.
From the backend folder:  python -m unittest tests.test_secrets_hooks -v"""
import json
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _p0_helpers import P0Case  # noqa: E402
import secrets_store as ss  # noqa: E402
import settings_store  # noqa: E402
import llm_store  # noqa: E402
import api_p0  # noqa: E402

HA = "ha-long-lived-token-0123456789"
NTFY = "tk_ntfy_0123456789"


class SettingsTokens(P0Case):
    def setUp(self):
        super().setUp()
        self._path, self._cache = settings_store.SETTINGS_PATH, settings_store._cache
        settings_store.SETTINGS_PATH = self.tmp / "settings.json"
        settings_store._cache = None

    def tearDown(self):
        settings_store.SETTINGS_PATH, settings_store._cache = self._path, self._cache
        super().tearDown()

    def on_disk(self):
        return self.read_json(self.tmp / "settings.json")

    def test_existing_plain_tokens_migrate_on_first_load(self):
        self.write_json("settings.json", {"wake_name": "Mira", "ha_token": HA, "ntfy_token": NTFY, "ha_url": "http://ha"})
        self.assertEqual(settings_store.get("ha_token"), HA)         # still works for smarthome.py
        self.assertEqual(settings_store.get("ntfy_token"), NTFY)
        disk = self.on_disk()
        self.assertEqual((disk["ha_token"], disk["ntfy_token"]), ("", ""))
        self.assertEqual((disk["wake_name"], disk["ha_url"]), ("Mira", "http://ha"))
        self.assertEqual(ss.get("ha_token"), HA)
        self.assertTrue((self.tmp / "settings.json.bak-keychain").exists())
        self.assertNotIn(HA, (self.tmp / "settings.json").read_text())

    def test_new_token_goes_to_keychain_not_settings_file(self):
        settings_store.set_many({"ha_token": HA, "wake_name": "Mira"})
        self.assertEqual(settings_store.get("ha_token"), HA)
        self.assertEqual(self.on_disk()["ha_token"], "")
        self.assertEqual(self.on_disk()["wake_name"], "Mira")
        self.assertEqual(ss.where("ha_token"), "keychain")
        self.assertNotIn(HA, self.all_disk_text())

    def test_pref_group_roundtrip_through_update_group(self):
        settings_store.update_group("features", {"ha_token": HA})
        self.assertEqual(settings_store.get_group("features")["ha_token"], HA)
        self.assertNotIn(HA, (self.tmp / "settings.json").read_text())

    def test_clearing_a_token(self):
        settings_store.set_many({"ntfy_token": NTFY})
        settings_store.set_many({"ntfy_token": ""})
        self.assertEqual(settings_store.get("ntfy_token"), "")
        self.assertFalse(ss.has("ntfy_token"))

    def test_other_settings_are_not_affected(self):
        settings_store.set_many({"wake_name": "Mira", "dictation_polish": False})
        self.assertEqual(settings_store.get("wake_name"), "Mira")
        self.assertIs(settings_store.get("dictation_polish"), False)
        self.assertEqual(ss.names(), [])

    def test_get_all_never_carries_tokens(self):
        settings_store.set_many({"ha_token": HA})
        self.assertEqual(settings_store.get_all()["ha_token"], "")

    def test_migration_failure_keeps_tokens_working_from_the_file(self):
        self.write_json("settings.json", {"ha_token": HA})
        self.kr.fail_set = True
        self.assertEqual(settings_store.get("ha_token"), HA)
        self.assertEqual(self.on_disk()["ha_token"], HA)


class SettingsTokensWithoutKeychain(SettingsTokens.__bases__[0]):
    use_keychain = False

    def setUp(self):
        super().setUp()
        self._path, self._cache = settings_store.SETTINGS_PATH, settings_store._cache
        settings_store.SETTINGS_PATH = self.tmp / "settings.json"
        settings_store._cache = None

    def tearDown(self):
        settings_store.SETTINGS_PATH, settings_store._cache = self._path, self._cache
        super().tearDown()

    def test_old_behaviour(self):
        settings_store.set_many({"ha_token": HA})
        self.assertEqual(settings_store.get("ha_token"), HA)
        self.assertEqual(self.read_json(self.tmp / "settings.json")["ha_token"], HA)
        self.assertFalse((self.tmp / "secrets_fallback.json").exists())


class SealedBlobs(P0Case):
    def test_seal_replaces_secrets_and_unseal_restores_them(self):
        data = {"tokens": {"access": "acc-123456", "refresh": "ref-123456"}, "settings": {"client_id": "cid", "client_secret": "sec-123456"},
                "theme": "dark", "has_token": True}
        sealed = ss.seal_dict("plugin.spotify", data)
        self.assertEqual(sealed["tokens"], ss.SENTINEL)
        self.assertEqual(sealed["settings"]["client_secret"], ss.SENTINEL)
        self.assertEqual((sealed["settings"]["client_id"], sealed["theme"], sealed["has_token"]), ("cid", "dark", True))
        self.assertNotIn("acc-123456", json.dumps(sealed))
        self.assertEqual(data["tokens"]["access"], "acc-123456")              # input untouched
        back, legacy = ss.unseal_dict("plugin.spotify", sealed)
        self.assertEqual(back, data)
        self.assertFalse(legacy)

    def test_explicit_secret_keys(self):
        sealed = ss.seal_dict("plugin.x", {"pin": "1234", "name": "n"}, secret_keys=["pin"])
        self.assertEqual((sealed["pin"], sealed["name"]), (ss.SENTINEL, "n"))

    def test_plain_legacy_secrets_are_reported_for_rewrite(self):
        back, legacy = ss.unseal_dict("plugin.x", {"tokens": {"a": "plain-secret"}})
        self.assertEqual(back, {"tokens": {"a": "plain-secret"}})
        self.assertTrue(legacy)

    def test_missing_keychain_item_behaves_as_not_set(self):
        back, _ = ss.unseal_dict("plugin.x", {"tokens": ss.SENTINEL, "keep": 1})
        self.assertEqual(back, {"keep": 1})

    def test_empty_values_are_not_sealed(self):
        sealed = ss.seal_dict("plugin.x", {"tokens": {}, "client_secret": ""})
        self.assertEqual(sealed, {"tokens": {}, "client_secret": ""})
        self.assertEqual(ss.names(), [])


class SealedBlobsWithoutKeychain(P0Case):
    use_keychain = False

    def test_unchanged_when_there_is_no_keychain(self):
        data = {"tokens": {"a": "b"}}
        self.assertEqual(ss.seal_dict("plugin.x", data), data)
        self.assertEqual(ss.unseal_dict("plugin.x", data), (data, False))


class FakeHandler:
    """Just enough of ui_bridge.Handler to test the wrapper."""
    def __init__(self, path, origin=None, body=b"{}"):
        self.path, self.sent, self.called = path, None, None
        self.headers = {"Origin": origin} if origin else {}
        self.headers = types.SimpleNamespace(get=lambda k, d=None, h=self.headers: h.get(k, d))
        import io
        self.rfile = io.BytesIO(body)

    def _json(self, code, obj):
        self.sent = (code, obj)

    def do_GET(self):
        self.called = "orig-get"

    def do_POST(self):
        self.called = "orig-post"


class StatusEndpoint(P0Case):
    def setUp(self):
        super().setUp()
        self._lp, self._lc = llm_store.PATH, llm_store._cache
        llm_store.PATH = self.tmp / "llm_keys.json"
        llm_store._cache = None
        self._sp, self._sc = settings_store.SETTINGS_PATH, settings_store._cache
        settings_store.SETTINGS_PATH = self.tmp / "settings.json"
        settings_store._cache = None

    def tearDown(self):
        llm_store.PATH, llm_store._cache = self._lp, self._lc
        settings_store.SETTINGS_PATH, settings_store._cache = self._sp, self._sc
        super().tearDown()

    def item(self, st, name):
        return next(i for i in st["items"] if i["name"] == name)

    def test_green_and_red_dots_and_no_values(self):
        llm_store.save({"groq_api_key": "gsk_STATUSKEY0123456789abc"})
        code, st = api_p0.handle("GET", "/p0/secrets/status")
        self.assertEqual(code, 200)
        self.assertEqual(st["backend"], "keyring")
        g = self.item(st, "groq_api_key")
        self.assertEqual((g["stored"], g["where"], g["dot"], g["secure"]), (True, "keychain", "green", True))
        r = self.item(st, "gemini_api_key")
        self.assertEqual((r["stored"], r["where"], r["dot"]), (False, "none", "red"))
        self.assertNotIn("gsk_STATUSKEY0123456789abc", json.dumps(st))

    def test_plain_file_copy_is_reported_as_file(self):
        ss._use_adapter_for_tests(None)
        self.write_json("llm_keys.json", {"openrouter_api_key": "sk-or-v1-FILEKEY0123456789"})
        st = api_p0.status()
        o = self.item(st, "openrouter_api_key")
        self.assertEqual((o["where"], o["dot"], o["secure"]), ("file", "green", False))
        self.assertFalse(st["keychain"])
        self.assertTrue(st["warnings"])
        self.assertNotIn("FILEKEY", json.dumps(st))

    def test_sealed_plugin_secrets_are_listed(self):
        ss.seal_dict("plugin.spotify", {"tokens": {"a": "t-123456789"}})
        st = api_p0.status()
        self.assertEqual(self.item(st, "plugin.spotify.tokens")["dot"], "green")

    def test_migrate_and_confirm_routes(self):
        self.write_json("llm_keys.json", {"groq_api_key": "gsk_MIGRATEKEY0123456789ab"})
        code, st = api_p0.handle("POST", "/p0/secrets/migrate", {})
        self.assertEqual(code, 200)
        self.assertEqual(st["migration"]["llm"]["state"], "migrated")
        self.assertTrue(any("backed up" in w for w in st["warnings"]))
        code, st = api_p0.handle("POST", "/p0/secrets/confirm", {})
        self.assertEqual(st["confirmed"], {"llm": "confirmed"})
        self.assertEqual(api_p0.handle("GET", "/p0/nope")[0], 404)

    def test_errors_do_not_echo_details(self):
        real = ss.names
        ss.names = lambda: (_ for _ in ()).throw(RuntimeError("secret-in-message-123456"))
        try:
            code, obj = api_p0.handle("GET", "/p0/secrets/status")
        finally:
            ss.names = real
        self.assertEqual(code, 500)
        self.assertNotIn("secret-in-message", json.dumps(obj))

    def test_register_wraps_handler_and_guards_origin(self):
        bridge = types.SimpleNamespace(Handler=type("H", (FakeHandler,), {}), _OWN_ORIGINS={"tauri://localhost"})
        api_p0.register(bridge)
        api_p0.register(bridge)                                              # idempotent
        h = bridge.Handler("/p0/secrets/status")
        h.do_GET()
        self.assertEqual(h.sent[0], 200)
        self.assertIsNone(h.called)
        h = bridge.Handler("/p0/secrets/status", origin="https://evil.example")
        h.do_GET()
        self.assertEqual(h.sent[0], 403)
        h = bridge.Handler("/p0/secrets/migrate", origin="tauri://localhost")
        h.do_POST()
        self.assertEqual(h.sent[0], 200)
        h = bridge.Handler("/settings/llm")                                  # everything else goes through untouched
        h.do_GET()
        self.assertEqual((h.called, h.sent), ("orig-get", None))
        h = bridge.Handler("/chat")
        h.do_POST()
        self.assertEqual(h.called, "orig-post")


if __name__ == "__main__":
    unittest.main()
