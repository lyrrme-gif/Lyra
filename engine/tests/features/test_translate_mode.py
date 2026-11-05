"""Tests for translate_mode.py. Run from the backend folder:

    python -m unittest tests.test_translate_mode -v

The language model is a FAKE (a small phrase book that answers the way the real prompts ask), so
these tests check the logic around it: direction, guards against mixed-up directions, voice
selection, card events, stop / feature switch and the one-shot command. They say nothing about the
quality of a real model's translations.
"""
import json
import re
import sys
import types
import unittest

# ── stubs for the rest of Lyra (installed before translate_mode is imported) ───────────────────
EVENTS: list = []
CLIP = {"text": "", "selection": ""}


def _install_stubs():
    ui_bridge = types.ModuleType("engine.bridge.ui_bridge")
    ui_bridge.push_search_results = lambda q, r, p="", card=None: EVENTS.append(("card", q, p, card))
    ui_bridge.push_search_dismiss = lambda: EVENTS.append(("dismiss",))
    sys.modules["engine.bridge.ui_bridge"] = ui_bridge

    ss = types.ModuleType("engine.core.settings_store")
    ss._on = {"translate": True}
    ss.feature_on = lambda n: ss._on.get(n, True)
    ss.get = lambda k, d=None: d
    sys.modules["engine.core.settings_store"] = ss

    stt = types.ModuleType("engine.voice.stt")
    stt.language_override = None
    sys.modules["engine.voice.stt"] = stt

    brain = types.ModuleType("engine.brain.brain")
    brain.FAKE = FakeLLM()
    brain._get_client = lambda: (brain.FAKE, "fake-model")
    brain._fallback_client = lambda: None
    sys.modules["engine.brain.brain"] = brain

    # actions.screen_tools (selection helpers)
    actions = types.ModuleType("engine.actions")
    st = types.ModuleType("engine.actions.screen_tools")
    state = {"clip": ""}

    def _get_clip():
        return state["clip"]

    def _set_clip(t):
        state["clip"] = t
        return True

    def _ctrl_c():                       # "copies" the selection into the clipboard if there is one
        if CLIP["selection"]:
            state["clip"] = CLIP["selection"]
    st._get_clip, st._set_clip, st._ctrl_c = _get_clip, _set_clip, _ctrl_c
    st._state = state
    actions.screen_tools = st
    sys.modules["engine.actions"], sys.modules["engine.actions.screen_tools"] = actions, st


# Phrase book: (language, text) -> {target language: translation}
BOOK = [
    ("en", "Where is the train station?", {"ar": "أين محطة القطار؟", "fr": "Où est la gare ?"}),
    ("ar", "أين محطة القطار؟", {"en": "Where is the train station?"}),
    ("en", "I would like a coffee please.", {"ar": "أريد قهوة من فضلك.", "fr": "Je voudrais un café s'il vous plaît."}),
    ("ar", "أريد قهوة من فضلك.", {"en": "I would like a coffee please."}),
    ("en", "How much does this cost?", {"ar": "كم يكلف هذا؟", "fr": "Combien ça coûte ?"}),
    ("ar", "كم يكلف هذا؟", {"en": "How much does this cost?"}),
    ("fr", "Je voudrais un café s'il vous plaît.", {"en": "I would like a coffee please."}),
    ("fr", "Merci", {"en": "Thank you"}),
    ("en", "Thank you", {"fr": "Merci", "ar": "شكرا"}),
]
LANG_CODE = {"English": "en", "French": "fr", "Arabic": "ar", "Spanish": "es"}


class FakeLLM:
    """Answers the two prompt shapes translate_mode uses. `misbehave` simulates a sloppy model."""

    def __init__(self):
        self.calls = []
        self.misbehave = None   # None | "unchanged_first" | "wrong_lang_code" | "raise"
        self._n = 0
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, model, messages, temperature=0, timeout=0):
        system, user = messages[0]["content"], messages[1]["content"]
        self.calls.append((system, user))
        self._n += 1
        if self.misbehave == "raise":
            raise RuntimeError("Connection error.")
        entry = next((b for b in BOOK if b[1] == user), None)
        text = ""
        if '"from"' in system:                         # one-shot: {"from": ..., "translation": ...}
            m = re.search(r"Translate the text into (\w+)\.", system)
            dst = LANG_CODE.get(m.group(1), "xx")
            name = {v: k for k, v in LANG_CODE.items()}.get(entry[0], "English") if entry else "English"
            tr = entry[2].get(dst, "?") if entry else "?"
            text = json.dumps({"from": name, "translation": tr}, ensure_ascii=False)
        elif "Reply with ONLY JSON" in system:
            src = entry[0] if entry else "en"
            codes = [c for c in LANG_CODE.values() if f"code {c}" in system]
            dst = next(c for c in codes if c != src)
            tr = entry[2].get(dst, "?") if entry else "?"
            if self.misbehave == "wrong_lang_code":
                text = json.dumps({"lang": dst, "translation": user})     # claims the opposite, returns the input
            else:
                text = "```json\n" + json.dumps({"lang": src, "translation": tr}, ensure_ascii=False) + "\n```"
        else:
            # "The text is in X. Translate it into Y." / "Translate the text into Y."
            m = re.search(r"Translate it into (\w+)\.", system) or re.search(r"Translate the text into (\w+)\.", system)
            dst = LANG_CODE[m.group(1)]
            if self.misbehave == "unchanged_first" and self._n == 1:
                text = user
            else:
                text = entry[2].get(dst, "?") if entry else "?"
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])


_install_stubs()
from engine.voice import tts_lang          # noqa: E402  (the real one, with the hint() addition from MERGE_NOTES_STEP10)
import engine.features.translate_mode as tm   # noqa: E402

ARIA, HAMED = "en-US-AriaNeural", "ar-SA-HamedNeural"


def card_texts():
    return [e[3] for e in EVENTS if e[0] == "card"]


class TranslateModeTests(unittest.TestCase):
    def setUp(self):
        EVENTS.clear()
        tm.stop()
        EVENTS.clear()
        CLIP.update(text="", selection="")
        sys.modules["engine.actions.screen_tools"]._state["clip"] = ""
        fake = sys.modules["engine.brain.brain"].FAKE
        fake.calls.clear()
        fake.misbehave = None
        fake._n = 0
        sys.modules["engine.core.settings_store"]._on["translate"] = True
        tts_lang._HINTS.clear()

    # ── patterns ──────────────────────────────────────────────────────────────────────────────
    def test_start_phrases(self):
        for phrase, pair in [
            ("translation mode Arabic and English", ("ar", "en")),
            ("Hey Lyra, start translation mode between French and English", ("fr", "en")),
            ("translate between English and Arabic", ("en", "ar")),
            ("interpret between Spanish and German", ("es", "de")),
            ("translation mode, arabic and english.", ("ar", "en")),     # how Whisper punctuates
        ]:
            tm.stop()
            r = tm.handle_turn(phrase)
            self.assertIsNotNone(r, phrase)
            self.assertEqual(tm.current_pair(), pair, phrase)

    def test_start_sets_stt_auto_and_stop_restores(self):
        stt = sys.modules["engine.voice.stt"]
        tm.handle_turn("translation mode Arabic and English")
        self.assertEqual(stt.language_override, "")
        self.assertEqual(tm.handle_turn("stop translating"), "Translation mode off.")
        self.assertIsNone(stt.language_override)
        self.assertFalse(tm.is_active())
        self.assertIn(("dismiss",), EVENTS)

    def test_unsupported_language_in_session(self):
        r = tm.handle_turn("translation mode Japanese and English")
        self.assertFalse(tm.is_active())
        self.assertIn("Japanese", r)

    def test_not_for_us(self):
        for c in ["what time is it", "open spotify", "translate the clipboard to French",
                  "translation", "add milk to my to-do list"]:
            self.assertIsNone(tm.handle_turn(c), c)
        self.assertEqual(tm.handle_turn("translation mode"), "Which two languages? For example: translation mode Arabic and English.")

    def test_stop_variants(self):
        for phrase in ["stop translating", "Stop translating.", "stop translating please", "exit translation mode",
                       "end the translation", "translation mode off", "توقف عن الترجمة"]:
            tm.handle_turn("translation mode Arabic and English")
            self.assertTrue(tm.is_active())
            self.assertEqual(tm.handle_turn(phrase), "Translation mode off.", phrase)
            self.assertFalse(tm.is_active(), phrase)

    def test_stop_when_not_active_is_not_ours(self):
        self.assertIsNone(tm.handle_turn("stop translating"))

    # ── the 6-sentence back-and-forth (Arabic <-> English) ────────────────────────────────────
    def test_six_sentences_both_directions(self):
        tm.handle_turn("translation mode Arabic and English")
        said = [
            ("Where is the train station?", "أين محطة القطار؟", "ar"),
            ("أين محطة القطار؟", "Where is the train station?", "en"),
            ("I would like a coffee please.", "أريد قهوة من فضلك.", "ar"),
            ("أريد قهوة من فضلك.", "I would like a coffee please.", "en"),
            ("How much does this cost?", "كم يكلف هذا؟", "ar"),
            ("كم يكلف هذا؟", "How much does this cost?", "en"),
        ]
        for heard, expect, lang in said:
            out = tm.handle_turn(heard)
            self.assertEqual(out, expect)
            voice = tts_lang.pick_voice(out, ARIA)          # what voice.speak() would use
            self.assertTrue(voice.startswith(lang + "-"), f"{out!r} -> {voice}")
        cards = card_texts()
        self.assertEqual(len(cards), 6)
        self.assertEqual(cards[0]["title"], "English \u2192 Arabic")
        self.assertEqual(cards[1]["title"], "Arabic \u2192 English")
        self.assertEqual(cards[1]["subtitle"], "أين محطة القطار؟")
        self.assertEqual(cards[0]["extract"], "أين محطة القطار؟")

    def test_arabic_english_direction_decided_without_llm_detection(self):
        tm.handle_turn("translation mode Arabic and English")
        tm.handle_turn("كم يكلف هذا؟")
        system = sys.modules["engine.brain.brain"].FAKE.calls[-1][0]
        self.assertNotIn("Reply with ONLY JSON", system)        # script decided it
        self.assertIn("The text is in Arabic", system)

    # ── Latin pair: the model detects the language ────────────────────────────────────────────
    def test_french_english_both_ways_incl_short_phrases(self):
        tm.handle_turn("translation mode French and English")
        self.assertEqual(tm.handle_turn("Je voudrais un café s'il vous plaît."), "I would like a coffee please.")
        out = tm.handle_turn("Thank you")
        self.assertEqual(out, "Merci")
        # "Merci" is one word: word-list detection alone is unsure, the hint makes the voice French.
        self.assertTrue(tts_lang.pick_voice(out, ARIA).startswith("fr-"))
        self.assertEqual(tm.handle_turn("Merci"), "Thank you")
        self.assertTrue(tts_lang.pick_voice("Thank you", "fr-FR-DeniseNeural").startswith("en-"))

    def test_model_claims_wrong_direction_and_returns_input_unchanged(self):
        tm.handle_turn("translation mode French and English")
        sys.modules["engine.brain.brain"].FAKE.misbehave = "wrong_lang_code"
        out = tm.handle_turn("Je voudrais un café s'il vous plaît.")
        # first answer was the input itself -> flipped and asked again
        self.assertNotEqual(out, "Je voudrais un café s'il vous plaît.")

    def test_untranslated_answer_is_retried(self):
        tm.handle_turn("translation mode Arabic and English")
        sys.modules["engine.brain.brain"].FAKE.misbehave = "unchanged_first"
        sys.modules["engine.brain.brain"].FAKE._n = 0
        out = tm.handle_turn("I would like a coffee please.")
        self.assertEqual(out, "أريد قهوة من فضلك.")

    # ── errors, switch ────────────────────────────────────────────────────────────────────────
    def test_llm_failure_is_reported_and_mode_stays_on(self):
        tm.handle_turn("translation mode Arabic and English")
        sys.modules["engine.brain.brain"].FAKE.misbehave = "raise"
        r = tm.handle_turn("Where is the train station?")
        self.assertTrue(r.startswith("I couldn't translate that"))
        self.assertIn("Connection error", r)
        self.assertTrue(tm.is_active())

    def test_feature_switch_off(self):
        sys.modules["engine.core.settings_store"]._on["translate"] = False
        self.assertIsNone(tm.handle_turn("translation mode Arabic and English"))
        self.assertIsNone(tm.handle_turn("translate this to French"))
        self.assertFalse(tm.is_active())

    def test_switch_off_while_running_ends_session(self):
        tm.handle_turn("translation mode Arabic and English")
        sys.modules["engine.core.settings_store"]._on["translate"] = False
        self.assertIsNone(tm.handle_turn("Where is the train station?"))
        self.assertFalse(tm.is_active())

    def test_try_translate_never_raises(self):
        orig = tm.handle_turn
        tm.handle_turn = lambda c: 1 / 0
        try:
            self.assertIsNone(tm.try_translate("anything"))
        finally:
            tm.handle_turn = orig

    # ── one-shot ──────────────────────────────────────────────────────────────────────────────
    def test_one_shot_uses_selection_and_restores_clipboard(self):
        st = sys.modules["engine.actions.screen_tools"]
        st._state["clip"] = "my old clipboard"
        CLIP["selection"] = "Thank you"
        out = tm.handle_turn("translate this to French")
        self.assertEqual(out, "Merci")
        self.assertEqual(st._state["clip"], "my old clipboard")
        self.assertEqual(card_texts()[-1]["title"], "English \u2192 French")

    def test_one_shot_falls_back_to_clipboard(self):
        st = sys.modules["engine.actions.screen_tools"]
        st._state["clip"] = "Thank you"
        self.assertEqual(tm.handle_turn("translate that into French"), "Merci")

    def test_one_shot_nothing_to_translate(self):
        r = tm.handle_turn("translate this to French")
        self.assertIn("selected text", r)
        self.assertEqual(card_texts(), [])

    def test_one_shot_extra_language_has_no_voice_hint(self):
        CLIP["selection"] = "Thank you"
        out = tm.handle_turn("translate this to Japanese")   # fake book returns "?"; we only check routing
        self.assertIsNotNone(out)
        self.assertEqual(tts_lang._HINTS, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
