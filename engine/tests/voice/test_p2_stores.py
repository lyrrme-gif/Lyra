"""Phase 2: dictionary_store, history_store and the snippet helpers (temp files only)."""
import time
import unittest


import engine.voice.dictionary_store as ds  # noqa: E402
import engine.voice.history_store as hs  # noqa: E402
from engine.tests.voice.test_p2_env import isolated  # noqa: E402


class DictionaryTests(unittest.TestCase):
    def test_words_and_hints(self):
        with isolated():
            self.assertTrue(ds.add_word("Kubernetes"))
            self.assertFalse(ds.add_word("kubernetes"))            # duplicate, any case
            self.assertFalse(ds.add_word("   "))
            self.assertEqual(ds.hints(), ["Kubernetes"])
            self.assertTrue(ds.remove_word("KUBERNETES"))
            self.assertEqual(ds.hints(), [])

    def test_replacements_apply_to_whole_words(self):
        with isolated():
            ds.set_replacement("gonna", "going to")
            self.assertEqual(ds.apply_replacements("I'm gonna go. Gonna!"), "I'm going to go. going to!")
            self.assertEqual(ds.apply_replacements("gonnabe"), "gonnabe")

    def test_set_replacement_overwrites_same_trigger(self):
        with isolated():
            ds.set_replacement("brb", "be right back")
            ds.set_replacement("BRB", "back soon")
            self.assertEqual(ds.replacements(), [{"from": "BRB", "to": "back soon"}])

    def test_capitalised_words_fix_the_case_of_what_the_engine_wrote(self):
        with isolated():
            ds.add_word("GitHub")
            ds.add_word("plain")                                    # all lower case: no automatic fix
            self.assertEqual(ds.apply_replacements("push to github and plain"), "push to GitHub and plain")

    def test_explicit_replacement_beats_case_fix(self):
        with isolated():
            ds.add_word("GitHub")
            ds.set_replacement("github", "the forge")
            self.assertEqual(ds.apply_replacements("github"), "the forge")

    def test_persists_and_survives_a_corrupt_file(self):
        with isolated() as (tmp, _):
            ds.add_word("Anouk")
            ds.reload()
            self.assertEqual(ds.words(), ["Anouk"])
            (tmp / "dictionary.json").write_text("{not json", encoding="utf-8")
            ds.reload()
            self.assertEqual(ds.words(), [])

    def test_clear(self):
        with isolated():
            ds.add_word("x1"); ds.set_replacement("a", "b")
            ds.clear()
            self.assertEqual((ds.words(), ds.replacements()), ([], []))

    def test_limits(self):
        with isolated():
            for i in range(ds.MAX_WORDS):
                ds._load()["words"].append(f"w{i}")
            self.assertFalse(ds.add_word("one-too-many"))


class HistoryTests(unittest.TestCase):
    def test_add_search_last_delete(self):
        with isolated():
            a = hs.add("buy milk and eggs", "notepad", "streaming", "en", 2.5)
            b = hs.add("call the dentist", "outlook", "whisper", "en", 1.0)
            self.assertEqual(hs.count(), 2)
            self.assertEqual(hs.last()["text"], "call the dentist")
            self.assertEqual([r["id"] for r in hs.search("milk")], [a])
            self.assertEqual([r["id"] for r in hs.search("OUTLOOK")], [b])       # app names are searchable too
            self.assertEqual(hs.search("milk")[0]["words"], 4)
            self.assertTrue(hs.delete(a))
            self.assertFalse(hs.delete(a))
            self.assertEqual(hs.count(), 1)

    def test_search_treats_percent_and_underscore_literally(self):
        with isolated():
            hs.add("100% sure", "", "", "", 0)
            hs.add("sure thing", "", "", "", 0)
            self.assertEqual(len(hs.search("100%")), 1)
            self.assertEqual(len(hs.search("_")), 0)

    def test_newest_first_and_paging(self):
        with isolated():
            for i in range(5):
                hs.add(f"entry {i}", "", "", "", 0)
            self.assertEqual([r["text"] for r in hs.search("", limit=2)], ["entry 4", "entry 3"])
            self.assertEqual([r["text"] for r in hs.search("", limit=2, offset=2)], ["entry 2", "entry 1"])

    def test_blank_text_is_not_stored(self):
        with isolated():
            self.assertIsNone(hs.add("   ", "", "", "", 0))
            self.assertEqual(hs.count(), 0)

    def test_history_can_be_switched_off(self):
        with isolated({"p2_history_enabled": False}):
            self.assertIsNone(hs.add("secret words here", "", "", "", 0))
            self.assertEqual(hs.count(), 0)

    def test_retention_purges_old_entries_only(self):
        with isolated({"p2_history_retention_days": 7}):
            hs.add("fresh", "", "", "", 0)
            hs.add("stale", "", "", "", 0)
            con = hs._connect()
            con.execute("UPDATE entries SET ts = ? WHERE text = 'stale'", (time.time() - 8 * 86400,))
            con.commit(); con.close()
            self.assertEqual(hs.purge_expired(), 1)
            self.assertEqual([r["text"] for r in hs.search("")], ["fresh"])

    def test_forever_never_purges(self):
        with isolated({"p2_history_retention_days": 0}):
            hs.add("old", "", "", "", 0)
            con = hs._connect()
            con.execute("UPDATE entries SET ts = 1"); con.commit(); con.close()
            self.assertEqual(hs.purge_expired(), 0)
            self.assertEqual(hs.count(), 1)

    def test_clear_erases_everything(self):
        with isolated():
            hs.add("one", "", "", "", 0); hs.add("two", "", "", "", 0)
            self.assertEqual(hs.clear(), 2)
            self.assertEqual(hs.count(), 0)


class SnippetTests(unittest.TestCase):
    def sn(self):
        return ds.snippets_module()

    def test_trigger_defaults_to_the_name_and_text_is_kept_exactly(self):
        with isolated():
            ok, key = self.sn().set_snippet("Sign Off", "Best,\nAnna")
            self.assertEqual((ok, key), (True, "sign off"))
            self.assertEqual(self.sn().trigger_map(), {"sign off": "Best,\nAnna"})

    def test_voice_made_snippets_have_no_dictation_trigger(self):
        with isolated():
            self.sn().try_snippets("save snippet email as me@example.com")
            self.assertEqual(self.sn().trigger_map(), {})           # the word "email" must never fire
            self.assertEqual(self.sn().all_snippets()[0]["trigger"], "")

    def test_voice_commands_still_find_both_storage_shapes(self):
        with isolated():
            sn = self.sn()
            sn.try_snippets("save snippet email as me@example.com")
            sn.set_snippet("my phone", "555-0100", "snippet phone")
            self.assertEqual(sn.delete_snippet("my phone"), True)   # exact name with "my"
            self.assertIn("Deleted", sn.try_snippets("delete snippet email"))

    def test_validation(self):
        with isolated():
            sn = self.sn()
            self.assertFalse(sn.set_snippet("", "x")[0])
            self.assertFalse(sn.set_snippet("ok", "   ")[0])
            self.assertFalse(sn.set_snippet("ok", "x" * (sn.MAX_TEXT + 1))[0])
            self.assertFalse(sn.set_snippet("bad<name>", "x")[0])
            self.assertFalse(sn.set_snippet("ok", "x", "ab")[0])    # trigger too short
            self.assertFalse(sn.set_snippet("ok", "x", "one two three four five six seven")[0])

    def test_trigger_must_be_unique(self):
        with isolated():
            sn = self.sn()
            self.assertTrue(sn.set_snippet("a one", "A", "snippet one")[0])
            self.assertFalse(sn.set_snippet("b two", "B", "snippet one")[0])
            self.assertTrue(sn.set_snippet("a one", "A2", "snippet one")[0])   # same snippet may keep it

    def test_clear_all(self):
        with isolated():
            sn = self.sn()
            sn.set_snippet("one", "1"); sn.set_snippet("two", "2")
            self.assertEqual(sn.clear_all(), 2)
            self.assertEqual(sn.all_snippets(), [])


if __name__ == "__main__":
    unittest.main()
