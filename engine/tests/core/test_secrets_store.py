"""Phase 0 tests: secrets_store (keychain, fallback, migration) and llm_store on top of it.
From the backend folder:  python -m unittest tests.test_secrets_store -v
Offline: a fake in-memory keychain replaces Windows Credential Manager."""
import json
import unittest


from engine.tests.core._p0_helpers import FakeKeyring, P0Case  # noqa: E402
import engine.core.secrets_store as ss  # noqa: E402
from engine.brain import llm_store  # noqa: E402

KEY = "gsk_TESTKEY0123456789abcdef"
KEY2 = "sk-or-v1-OTHERKEY0123456789"


class KeychainBasics(P0Case):
    def test_roundtrip_and_nothing_on_disk(self):
        self.assertTrue(ss.set("groq_api_key", KEY))
        self.assertEqual(ss.get("groq_api_key"), KEY)
        self.assertTrue(ss.has("groq_api_key"))
        self.assertEqual(ss.where("groq_api_key"), "keychain")
        self.assertNotIn(KEY, self.all_disk_text())            # only the name index is on disk
        self.assertEqual(ss.names(), ["groq_api_key"])
        self.assertTrue(ss.delete("groq_api_key"))
        self.assertFalse(ss.has("groq_api_key"))
        self.assertEqual(ss.names(), [])
        self.assertEqual(self.kr.values(), [])

    def test_empty_value_deletes(self):
        ss.set("a_token", "value-123456")
        self.assertTrue(ss.set("a_token", "  "))
        self.assertFalse(ss.has("a_token"))

    def test_bad_names_rejected(self):
        for bad in ("", "a b", "x/y", "a" * 101, None):
            with self.assertRaises(ValueError):
                ss.get(bad)

    def test_long_value_is_chunked_and_shrinks(self):
        self.kr.max_len = 1200                                  # a blob limit smaller than the value
        big = "A1b2" * 700                                      # 2800 chars
        self.assertTrue(ss.set("plugin.x.tokens", big))
        ss._cache.clear()
        self.assertEqual(ss.get("plugin.x.tokens"), big)
        self.assertTrue(ss.set("plugin.x.tokens", "short-value"))
        ss._cache.clear()
        self.assertEqual(ss.get("plugin.x.tokens"), "short-value")
        self.assertEqual(len([k for k in self.kr.d if "#" in k[1]]), 0)   # leftover chunks removed

    def test_delete_prefix(self):
        ss.set("plugin.a.tokens", "tok-aaaaaaaa")
        ss.set("plugin.a.client_secret", "sec-aaaaaaaa")
        ss.set("plugin.b.tokens", "tok-bbbbbbbb")
        self.assertEqual(ss.delete_prefix("plugin.a."), 2)
        self.assertEqual(ss.names(), ["plugin.b.tokens"])


class FallbackFile(P0Case):
    use_keychain = False

    def test_no_keychain_uses_fallback_file(self):
        self.assertFalse(ss.keyring_available())
        self.assertEqual(ss.backend(), "file")
        self.assertTrue(ss.set("groq_api_key", KEY))
        self.assertEqual(ss.get("groq_api_key"), KEY)
        self.assertEqual(ss.where("groq_api_key"), "file")
        self.assertEqual(self.read_json(self.tmp / "secrets_fallback.json"), {"groq_api_key": KEY})
        self.assertTrue(ss.delete("groq_api_key"))
        self.assertFalse((self.tmp / "secrets_fallback.json").exists())

    def test_keychain_write_failure_falls_back_to_file(self):
        secrets_store = ss
        secrets_store._use_adapter_for_tests(self.kr)
        self.kr.fail_set = True
        self.assertTrue(ss.set("ha_token", "ha-token-123456"))
        self.assertEqual(ss.where("ha_token"), "file")
        self.kr.fail_set = False
        ss._cache.clear()
        self.assertEqual(ss.get("ha_token"), "ha-token-123456")   # still readable
        ss.set("ha_token", "ha-token-654321")                      # keychain is back: plain copy is dropped
        self.assertEqual(ss.where("ha_token"), "keychain")
        self.assertFalse((self.tmp / "secrets_fallback.json").exists())

    def test_keychain_read_failure_does_not_raise(self):
        secrets_store = ss
        secrets_store._use_adapter_for_tests(self.kr)
        ss.set("ha_token", "ha-token-123456")
        ss._cache.clear()
        self.kr.fail_get = True
        self.assertEqual(ss.get("ha_token"), "")                   # locked keychain = "not available"

    def test_env_forces_file_backend(self):
        import os
        ss._reset_for_tests()
        os.environ["LYRA_SECRETS_BACKEND"] = "file"
        try:
            self.assertFalse(ss.keyring_available())
        finally:
            del os.environ["LYRA_SECRETS_BACKEND"]


class Migration(P0Case):
    def _legacy(self):
        return self.write_json("llm_keys.json", {
            "provider": "groq", "groq_api_key": KEY, "openrouter_api_key": KEY2, "gemini_api_key": "",
            "groq_model": "m1", "spotify_client_id": "cid"})

    def test_migrate_moves_verifies_blanks_and_backs_up(self):
        p = self._legacy()
        original = p.read_bytes()
        res = ss.migrate_json_file("llm", p, ["groq_api_key", "openrouter_api_key", "gemini_api_key"])
        self.assertEqual(res["state"], "migrated")
        self.assertEqual(res["moved"], ["groq_api_key", "openrouter_api_key"])
        after = self.read_json(p)
        self.assertEqual(after["groq_api_key"], "")
        self.assertEqual(after["openrouter_api_key"], "")
        self.assertEqual((after["provider"], after["groq_model"], after["spotify_client_id"]), ("groq", "m1", "cid"))
        self.assertEqual(ss.get("groq_api_key"), KEY)
        self.assertEqual((self.tmp / "llm_keys.json.bak-keychain").read_bytes(), original)   # exact backup
        self.assertNotIn(KEY, p.read_text())
        self.assertEqual(ss.migration_state()["llm"]["state"], "migrated")

    def test_migrate_is_idempotent(self):
        p = self._legacy()
        ss.migrate_json_file("llm", p, ["groq_api_key"])
        again = ss.migrate_json_file("llm", p, ["groq_api_key"])
        self.assertEqual(again["moved"], [])
        self.assertEqual(ss.get("groq_api_key"), KEY)

    def test_failed_write_leaves_file_untouched(self):
        p = self._legacy()
        before = p.read_bytes()
        self.kr.fail_set = True
        res = ss.migrate_json_file("llm", p, ["groq_api_key", "openrouter_api_key"])
        self.assertEqual(res["state"], "failed")
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(ss.migration_state()["llm"]["state"], "failed")

    def test_unverifiable_write_leaves_file_untouched(self):
        p = self._legacy()
        before = p.read_bytes()
        self.kr.drop_writes = True                                 # "succeeds" but stores nothing
        res = ss.migrate_json_file("llm", p, ["groq_api_key"])
        self.assertEqual(res["state"], "failed")
        self.assertEqual(p.read_bytes(), before)

    def test_failed_migration_is_throttled_then_retryable(self):
        p = self._legacy()
        self.kr.fail_set = True
        ss.migrate_json_file("llm", p, ["groq_api_key"])
        self.kr.fail_set = False
        self.assertEqual(ss.migrate_json_file("llm", p, ["groq_api_key"])["state"], "failed")   # throttled
        ss.retry_failed()
        self.assertEqual(ss.migrate_json_file("llm", p, ["groq_api_key"])["state"], "migrated")

    def test_no_keychain_skips_and_keeps_file(self):
        ss._use_adapter_for_tests(None)
        p = self._legacy()
        before = p.read_bytes()
        res = ss.migrate_json_file("llm", p, ["groq_api_key"])
        self.assertEqual(res["state"], "skipped")
        self.assertEqual(p.read_bytes(), before)
        self.assertFalse((self.tmp / "llm_keys.json.bak-keychain").exists())

    def test_backup_confirmed_on_next_launch_only(self):
        p = self._legacy()
        ss.migrate_json_file("llm", p, ["groq_api_key", "openrouter_api_key"])
        bak = self.tmp / "llm_keys.json.bak-keychain"
        self.assertEqual(ss.housekeeping(), {})                    # same launch: backup stays
        self.assertTrue(bak.exists())
        ss._BOOT = "test-boot-2"                                   # "next launch"
        self.assertEqual(ss.housekeeping(), {"llm": "confirmed"})
        self.assertFalse(bak.exists())
        self.assertEqual(ss.migration_state()["llm"]["state"], "confirmed")
        self.assertNotIn(KEY, self.all_disk_text())

    def test_backup_kept_when_keychain_no_longer_matches(self):
        p = self._legacy()
        ss.migrate_json_file("llm", p, ["groq_api_key"])
        ss.set("groq_api_key", "someone-else-changed-it-1234")     # keychain no longer equals the backup...
        ss._BOOT = "test-boot-2"
        self.assertEqual(ss.confirm(), {"llm": "mismatch"})        # ...and nobody told the backup: keep it
        self.assertTrue((self.tmp / "llm_keys.json.bak-keychain").exists())

    def test_forget_backup_fields_then_confirm(self):
        p = self._legacy()
        ss.migrate_json_file("llm", p, ["groq_api_key"])
        ss.set("groq_api_key", "brand-new-key-123456")
        ss.forget_backup_fields("llm", ["groq_api_key"])           # what llm_store.save does
        self.assertEqual(ss.confirm(), {"llm": "confirmed"})

    def test_rollback_restores_the_original_file(self):
        p = self._legacy()
        original = p.read_bytes()
        ss.migrate_json_file("llm", p, ["groq_api_key"])
        self.assertTrue(ss.rollback("llm"))
        self.assertEqual(p.read_bytes(), original)
        self.assertFalse((self.tmp / "llm_keys.json.bak-keychain").exists())
        self.assertEqual(ss.migrate_json_file("llm", p, ["groq_api_key"])["state"], "rolled_back")  # no re-migration


class LlmStoreOnKeychain(P0Case):
    def setUp(self):
        super().setUp()
        self._path = llm_store.PATH
        llm_store.PATH = self.tmp / "llm_keys.json"
        llm_store._cache = None

    def tearDown(self):
        llm_store.PATH = self._path
        llm_store._cache = None
        super().tearDown()

    def test_public_api_unchanged_and_load_migrates(self):
        self.write_json("llm_keys.json", {"provider": "gemini", "gemini_api_key": KEY, "spotify_client_secret": KEY2})
        data = llm_store.load()
        self.assertEqual(data["gemini_api_key"], KEY)
        self.assertEqual(data["spotify_client_secret"], KEY2)
        self.assertEqual(data["provider"], "gemini")
        self.assertEqual(llm_store.get()["gemini_api_key"], KEY)
        on_disk = self.read_json(self.tmp / "llm_keys.json")
        self.assertEqual((on_disk["gemini_api_key"], on_disk["spotify_client_secret"]), ("", ""))
        self.assertEqual(ss.get("gemini_api_key"), KEY)

    def test_save_new_key_goes_to_keychain_not_file(self):
        out = llm_store.save({"groq_api_key": KEY, "provider": "groq"})
        self.assertEqual(out["groq_api_key"], KEY)
        self.assertEqual(ss.where("groq_api_key"), "keychain")
        self.assertNotIn(KEY, (self.tmp / "llm_keys.json").read_text())
        self.assertEqual(llm_store.load()["groq_api_key"], KEY)

    def test_save_other_setting_keeps_keys(self):
        llm_store.save({"groq_api_key": KEY})
        llm_store.save({"groq_model": "other/model"})
        self.assertEqual(llm_store.load()["groq_api_key"], KEY)
        self.assertEqual(llm_store.load()["groq_model"], "other/model")

    def test_clear_key_removes_it_everywhere(self):
        llm_store.save({"groq_api_key": KEY})
        llm_store.save({"groq_api_key": ""})
        self.assertEqual(llm_store.load()["groq_api_key"], "")
        self.assertFalse(ss.has("groq_api_key"))
        self.assertEqual(self.kr.values(), [])

    def test_changed_key_is_scrubbed_from_backup(self):
        self.write_json("llm_keys.json", {"groq_api_key": KEY})
        llm_store.load()
        llm_store.save({"groq_api_key": KEY2})
        bak = self.read_json(self.tmp / "llm_keys.json.bak-keychain")
        self.assertEqual(bak["groq_api_key"], "")
        self.assertNotIn(KEY, self.all_disk_text())

    def test_key_only_in_file_is_never_dropped_when_migration_failed(self):
        self.write_json("llm_keys.json", {"groq_api_key": KEY})
        self.kr.fail_set = True
        self.assertEqual(llm_store.load()["groq_api_key"], KEY)    # keeps working from the file
        out = llm_store.save({"groq_model": "x"})                  # a later, unrelated save
        self.assertEqual(out["groq_api_key"], KEY)
        self.kr.fail_set = False
        llm_store._cache = None
        self.assertEqual(llm_store.load()["groq_api_key"], KEY)    # still there (file or fallback), never dropped

    def test_keychain_refuses_new_key_keeps_old_file_behaviour(self):
        ss.set("probe_name", "probe-value-1234")                   # keychain works...
        self.kr.fail_set = True                                    # ...until it does not
        llm_store.save({"groq_api_key": KEY})
        self.assertEqual(llm_store.load()["groq_api_key"], KEY)    # via secrets_fallback.json
        self.assertEqual(ss.where("groq_api_key"), "file")


class LlmStoreWithoutKeychain(P0Case):
    use_keychain = False

    def setUp(self):
        super().setUp()
        self._path = llm_store.PATH
        llm_store.PATH = self.tmp / "llm_keys.json"
        llm_store._cache = None

    def tearDown(self):
        llm_store.PATH = self._path
        llm_store._cache = None
        super().tearDown()

    def test_exactly_the_old_behaviour(self):
        self.write_json("llm_keys.json", {"groq_api_key": KEY})
        self.assertEqual(llm_store.load()["groq_api_key"], KEY)
        llm_store.save({"gemini_api_key": KEY2})
        on_disk = self.read_json(self.tmp / "llm_keys.json")
        self.assertEqual((on_disk["groq_api_key"], on_disk["gemini_api_key"]), (KEY, KEY2))
        self.assertFalse((self.tmp / "secrets_fallback.json").exists())
        self.assertFalse((self.tmp / "llm_keys.json.bak-keychain").exists())


if __name__ == "__main__":
    unittest.main()
