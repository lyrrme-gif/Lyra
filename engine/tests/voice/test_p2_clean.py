"""Phase 2: preferences, per-app styles and AI clean-up (no network: the LLM is a fake)."""
import sys
import types
import unittest


import engine.voice.dictation_clean as dc  # noqa: E402
from engine.tests.voice.test_p2_env import isolated  # noqa: E402


class FakeLLM:
    """Stands in for brain: records what it was sent."""
    def __init__(self, reply=None, boom=False, has_client=True):
        self.reply, self.boom, self.has_client, self.calls = reply, boom, has_client, []

    def install(self):
        fake = types.ModuleType("engine.brain.brain")
        fake._get_client = lambda: ((types.SimpleNamespace(with_options=lambda **kw: "client") if self.has_client else None), "m")

        def chat(client, model, msgs):
            self.calls.append(msgs)
            if self.boom:
                raise RuntimeError("offline")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.reply))])
        fake._chat_completion = chat
        self._old = sys.modules.get("engine.brain.brain")
        sys.modules["engine.brain.brain"] = fake
        return self

    def remove(self):
        if self._old is None:
            sys.modules.pop("engine.brain.brain", None)
        else:
            sys.modules["engine.brain.brain"] = self._old


class PrefTests(unittest.TestCase):
    def test_defaults_and_privacy_default(self):
        with isolated():
            p = dc.all_prefs()
            self.assertIs(p["p2_ai_cleanup"], False)                 # OFF by default
            self.assertEqual((p["p2_output"], p["p2_pill_position"], p["p2_history_retention_days"]), ("paste", "bottom", 30))
            self.assertEqual(set(p), set(dc.P2_DEFAULTS))

    def test_set_validates_and_persists(self):
        with isolated() as (_, store):
            dc.set_prefs({"p2_output": "paste", "p2_history_retention_days": 90, "p2_pill_position": "left"})
            self.assertEqual(store["p2_output"], "paste")
            self.assertEqual(dc.pref("p2_history_retention_days"), 90)

    def test_bad_values_are_rejected_with_the_key_name(self):
        with isolated():
            for key, bad in (("p2_output", "shout"), ("p2_pill_position", "top"), ("p2_ai_cleanup", "yes"),
                             ("p2_history_retention_days", 14), ("p2_history_retention_days", True),
                             ("p2_dictation_key", "esc"), ("p2_talk_key", "f9"), ("p2_app_styles", "{oops")):
                with self.assertRaises(ValueError, msg=key) as cm:
                    dc.set_prefs({key: bad})
                self.assertTrue(str(cm.exception).startswith(key + ":"))

    def test_unknown_keys_are_ignored_and_a_bad_stored_value_falls_back(self):
        with isolated({"p2_output": "garbage"}) as (_, store):
            dc.set_prefs({"not_ours": 1})
            self.assertNotIn("not_ours", store)
            self.assertEqual(dc.pref("p2_output"), "paste")

    def test_hotkey_prefs_are_normalised_and_may_be_cleared(self):
        with isolated():
            dc.set_prefs({"p2_dictation_key": " F9 "})
            self.assertEqual(dc.pref("p2_dictation_key"), "f9")
            dc.set_prefs({"p2_dictation_key": ""})
            self.assertEqual(dc.pref("p2_dictation_key"), "lalt")          # cleared = back to Left Alt
            with self.assertRaises(ValueError):
                dc.set_prefs({"p2_talk_key": "lalt"})                      # already the dictation key

    def test_reset(self):
        with isolated({"p2_dictation_key": "f9"}):
            dc.reset_prefs()
            self.assertEqual(dc.pref("p2_dictation_key"), "lalt")


class StyleTests(unittest.TestCase):
    def test_app_key(self):
        self.assertEqual(dc.app_key("WINWORD.EXE"), "winword")
        self.assertEqual(dc.app_key(" Discord "), "discord")

    def test_builtin_then_default(self):
        with isolated({"p2_cleanup_style": "polished"}):
            self.assertEqual(dc.style_for_app("discord.exe"), "natural")       # chat app
            self.assertEqual(dc.style_for_app("OUTLOOK.EXE"), "polished")
            self.assertEqual(dc.style_for_app("randomapp.exe"), "polished")    # falls back to the default
            self.assertEqual(dc.style_for_app(""), "polished")

    def test_your_choice_beats_the_builtin(self):
        with isolated():
            dc.set_prefs({"p2_app_styles": '{"Discord.exe": "polished", "x": "weird"}'})
            self.assertEqual(dc.user_styles(), {"discord": "polished"})        # invalid style dropped, key normalised
            self.assertEqual(dc.style_for_app("discord.exe"), "polished")


class RuleCleanupTests(unittest.TestCase):
    def test_fillers_repeats_and_spacing(self):
        self.assertEqual(dc.rule_cleanup("um the the meeting is uh tomorrow , ok", "natural"), "the meeting is tomorrow, ok")

    def test_meaningful_repeats_survive(self):
        self.assertEqual(dc.rule_cleanup("that that is what I said", "natural"), "that that is what I said")
        self.assertEqual(dc.rule_cleanup("it was very very good", "natural"), "it was very very good")

    def test_polished_capitalises_and_closes_the_sentence_natural_does_not(self):
        self.assertEqual(dc.rule_cleanup("see you at the station tomorrow", "polished"), "See you at the station tomorrow.")
        self.assertEqual(dc.rule_cleanup("see you at the station tomorrow", "natural"), "see you at the station tomorrow")
        self.assertEqual(dc.rule_cleanup("ok thanks", "polished"), "Ok thanks")        # short: no full stop forced

    def test_lists_are_left_alone(self):
        self.assertEqual(dc.rule_cleanup("1. milk\n2. eggs", "polished"), "1. milk\n2. eggs")

    def test_empty(self):
        self.assertEqual(dc.rule_cleanup("", "polished"), "")


class AiCleanupTests(unittest.TestCase):
    def test_off_by_default_the_llm_is_never_called(self):
        fake = FakeLLM(reply="Cleaned text here.").install()
        try:
            with isolated():
                self.assertIsNone(dc.ai_cleanup("um hello there my friend"))
                self.assertEqual(fake.calls, [])
        finally:
            fake.remove()

    def test_on_sends_only_text_and_uses_the_style_prompt(self):
        fake = FakeLLM(reply="Hello there, my friend.").install()
        try:
            with isolated({"p2_ai_cleanup": True}):
                out = dc.ai_cleanup("hello there my friend", "polished")
                self.assertEqual(out, "Hello there, my friend.")
                system, user = fake.calls[0]
                self.assertIn("POLISHED", system["content"])
                self.assertEqual(user["content"], "hello there my friend")
                self.assertEqual(len(fake.calls[0]), 2)                          # system + text, nothing else
        finally:
            fake.remove()

    def test_every_failure_means_none_not_an_error(self):
        for fake in (FakeLLM(boom=True), FakeLLM(has_client=False), FakeLLM(reply=""), FakeLLM(reply="x" * 5000)):
            fake.install()
            try:
                with isolated({"p2_ai_cleanup": True}):
                    self.assertIsNone(dc.ai_cleanup("hello there my friend"))
            finally:
                fake.remove()

    def test_one_word_or_huge_text_is_not_sent(self):
        fake = FakeLLM(reply="Hi.").install()
        try:
            with isolated({"p2_ai_cleanup": True}):
                self.assertIsNone(dc.ai_cleanup("hello"))
                self.assertIsNone(dc.ai_cleanup("word " * 2000))
                self.assertEqual(fake.calls, [])
        finally:
            fake.remove()

    def test_cleanup_combines_rules_and_ai_and_falls_back(self):
        fake = FakeLLM(reply="I will see you tomorrow.").install()
        try:
            with isolated({"p2_ai_cleanup": True}):
                self.assertEqual(dc.cleanup("um i will see you tomorrow", "notepad.exe"), ("I will see you tomorrow.", "ai"))
                fake.boom = True
                text, how = dc.cleanup("um i will see you tomorrow", "notepad.exe")
                self.assertEqual((text, how), ("i will see you tomorrow", "rules"))
            with isolated():                                                     # AI off: rules only
                self.assertEqual(dc.cleanup("fine as is", "notepad.exe"), ("fine as is", "none"))
        finally:
            fake.remove()


if __name__ == "__main__":
    unittest.main()
