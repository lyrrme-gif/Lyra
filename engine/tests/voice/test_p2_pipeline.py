"""Phase 2: what happens after you stop dictating (paste, engine pass, clean-up, history, stats,
cancel, hands-free). Keyboard, engines and stats are fakes: no Windows, mic or network."""
import sys
import types
import unittest


import engine.computer.keyboard as kb  # noqa: E402
from engine.voice import dictation  # noqa: E402
import engine.voice.dictionary_store as ds  # noqa: E402
import engine.voice.history_store as hs  # noqa: E402
from engine.tests.voice.test_p2_env import isolated  # noqa: E402
from engine.tests.voice.test_p2_clean import FakeLLM  # noqa: E402


class Rig:
    """Replaces the keyboard, the notifier and the optional Phase 1 / 3 modules for one test."""
    def __init__(self, app="notepad.exe", engine=None, engine_boom=False):
        self.app, self.pasted, self.notes, self.pills, self.stats = app, [], [], [], []
        self.engine, self.engine_boom, self.engine_calls = engine, engine_boom, []
        self.paste_ok = True

    def __enter__(self):
        self._kb = {n: getattr(kb, n) for n in ("focus_snapshot", "process_name", "release_modifiers", "paste_text")}
        kb.focus_snapshot = lambda: {"hwnd": 1, "pid": 42, "focus": 1, "caret_hwnd": 1, "caret": (0, 0, 0, 0)}
        kb.process_name = lambda pid: self.app
        kb.release_modifiers = lambda: False
        kb.paste_text = lambda text, restore=True: (self.pasted.append((text, restore)) or self.paste_ok)
        self._d = (dictation._notify, dictation._pill, dictation._last_text)
        dictation._notify = lambda title, phase, ms: self.notes.append((title, phase))
        dictation._pill = lambda state: self.pills.append(state)
        dictation._last_text = ""
        self._mods = {n: sys.modules.get(n) for n in ("stt_engines", "dictation_stats")}
        if self.engine is not None or self.engine_boom:
            m = types.ModuleType("engine.voice.stt_engines")
            m.status = lambda: {"ready": True}

            def transcribe(audio, language=None, hints=None):
                self.engine_calls.append((len(audio), language, hints))
                if self.engine_boom:
                    raise RuntimeError("model crashed")
                return {"text": self.engine, "language": "fr", "engine": "whisper-test", "ms": 5}
            m.transcribe = transcribe
            sys.modules["engine.voice.stt_engines"] = m
        else:
            sys.modules["engine.voice.stt_engines"] = None                  # import raises ImportError: "not merged yet"
        s = types.ModuleType("engine.voice.dictation_stats")
        s.record = lambda *a: self.stats.append(a)
        sys.modules["engine.voice.dictation_stats"] = s
        return self

    def __exit__(self, *exc):
        for n, f in self._kb.items():
            setattr(kb, n, f)
        dictation._notify, dictation._pill, dictation._last_text = self._d
        for n, m in self._mods.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
        dictation._session = None


def session(output="paste", words=("Hello world",), audio=False):
    sess = dictation._Session()
    sess.output = output
    sess.buffer = list(words)
    if audio:
        sess.audio = [b"\x00\x01" * 1600]
    return sess


class PasteFinishTests(unittest.TestCase):
    def test_pastes_records_history_stats_and_restores_clipboard(self):
        with isolated(), Rig() as rig:
            sess = session(words=("Hello", " world again"))
            sess._finish()
            self.assertEqual(rig.pasted, [("Hello world again", True)])          # restore=True: clipboard comes back
            self.assertEqual(hs.last()["text"], "Hello world again")
            self.assertEqual(hs.last()["app"], "notepad")
            self.assertEqual(rig.stats[0][0], 3)                                  # words
            self.assertEqual(rig.stats[0][3:], ("streaming", "en"))               # engine, language
            self.assertEqual(dictation._last_text, "Hello world again")
            self.assertIn(("Dictation pasted", "success"), rig.notes)

    def test_phase1_engine_text_replaces_the_live_text_and_gets_dictionary_hints(self):
        with isolated(), Rig(engine="bonjour a tous gonna go") as rig:
            ds.add_word("Anouk"); ds.set_replacement("gonna", "going to")
            sess = session(words=("Bonjour tous",), audio=True)
            sess._finish()
            self.assertEqual(rig.pasted[0][0], "bonjour a tous going to go")
            self.assertEqual(rig.engine_calls[0][2], ["Anouk"])                   # C4 hints reach the engine
            self.assertEqual((hs.last()["engine"], hs.last()["language"]), ("whisper-test", "fr"))
            self.assertEqual(rig.stats[0][3:], ("whisper-test", "fr"))

    def test_engine_failure_falls_back_to_the_live_text(self):
        with isolated(), Rig(engine_boom=True) as rig:
            sess = session(words=("Live text here",), audio=True)
            sess._finish()
            self.assertEqual(rig.pasted[0][0], "Live text here")

    def test_missing_engine_module_is_fine(self):
        with isolated(), Rig() as rig:                                            # stt_engines = ImportError
            session(words=("Works alone",), audio=True)._finish()
            self.assertEqual(rig.pasted[0][0], "Works alone")

    def test_audio_and_words_are_dropped_when_run_ends_even_if_finishing_fails(self):
        with isolated(), Rig() as rig:
            for boom in (False, True):
                sess = dictation._Session()
                sess.output = "paste"

                def listen(sess=sess):
                    sess.audio.append(b"\x00\x01" * 100)
                    sess.audio_bytes = 200
                    sess.buffer.append("some words here")
                sess._listen_and_type = listen
                sess._silence_lyra = lambda: None
                if boom:
                    sess._finish = lambda: (_ for _ in ()).throw(RuntimeError("paste exploded"))
                dictation._session = sess
                sess.run()
                self.assertEqual((sess.audio, sess.buffer), ([], []))
                self.assertIsNone(dictation._session)
                self.assertEqual(rig.pills[-1], "idle")

    def test_style_follows_the_app_you_paste_into(self):
        with isolated():
            with Rig(app="winword.exe") as rig:
                session(words=("see you at the station tomorrow",))._finish()
                self.assertEqual(rig.pasted[0][0], "See you at the station tomorrow.")   # polished (built-in for Word)
            with Rig(app="discord.exe") as rig:
                session(words=("see you at the station tomorrow",))._finish()
                self.assertEqual(rig.pasted[0][0], "see you at the station tomorrow")    # natural

    def test_ai_cleanup_off_means_no_llm_call(self):
        fake = FakeLLM(reply="SHOULD NOT APPEAR").install()
        try:
            with isolated(), Rig() as rig:
                session(words=("um hello there friend",))._finish()
                self.assertEqual(fake.calls, [])
                self.assertEqual(rig.pasted[0][0], "hello there friend")
        finally:
            fake.remove()

    def test_ai_cleanup_on_sends_text_and_pastes_the_result(self):
        fake = FakeLLM(reply="Hello there, friend.").install()
        try:
            with isolated({"p2_ai_cleanup": True}), Rig() as rig:
                session(words=("hello there friend",))._finish()
                self.assertEqual(rig.pasted[0][0], "Hello there, friend.")
                self.assertEqual(len(fake.calls), 1)
        finally:
            fake.remove()

    def test_nothing_heard_pastes_nothing_and_saves_nothing(self):
        with isolated(), Rig() as rig:
            session(words=("  ",))._finish()
            self.assertEqual((rig.pasted, hs.count(), rig.stats), ([], 0, []))
            self.assertIn(("Nothing heard", "fail"), rig.notes)

    def test_failed_paste_is_not_recorded(self):
        with isolated(), Rig() as rig:
            rig.paste_ok = False
            session()._finish()
            self.assertEqual((hs.count(), dictation._last_text), (0, ""))
            self.assertIn(("Could not paste the dictation", "fail"), rig.notes)

    def test_history_switched_off_still_remembers_the_last_one_in_memory_only(self):
        with isolated({"p2_history_enabled": False}), Rig():
            session()._finish()
            self.assertEqual(hs.count(), 0)
            self.assertEqual(dictation._last_text, "Hello world")


class CancelAndHandsFreeTests(unittest.TestCase):
    def test_cancelled_dictation_pastes_and_saves_nothing(self):
        with isolated(), Rig() as rig:
            sess = session()
            sess.cancelled = True
            sess._finish()
            self.assertEqual((rig.pasted, hs.count(), rig.stats, dictation._last_text), ([], 0, [], ""))
            self.assertIn(("Dictation cancelled", "fail"), rig.notes)

    def test_cancel_sets_flags_on_the_running_session(self):
        with Rig():
            sess = dictation._Session()
            dictation._session = sess
            dictation.cancel()
            self.assertTrue(sess.cancelled and sess.stop_evt.is_set())

    def test_cancel_with_no_session_is_harmless(self):
        with Rig():
            dictation._session = None
            dictation.cancel()

    def test_paste_mode_ignores_words_after_a_cancel(self):
        with Rig():
            sess = session(words=())
            sess.cancelled = True
            sess._emit("late words")
            self.assertEqual(sess.buffer, [])

    def test_emit_types_in_live_mode_and_collects_in_paste_mode(self):
        with Rig():
            typed = []
            live, paste = session("live", ()), session("paste", ())
            live._type = typed.append
            paste._type = typed.append
            live._emit("a"); paste._emit("b")
            self.assertEqual((typed, paste.buffer), (["a"], ["b"]))

    def test_handsfree_switches_to_paste_if_nothing_was_typed_yet(self):
        with Rig() as rig:
            sess = dictation._Session()
            sess.output = "live"
            dictation._session = sess
            dictation.set_handsfree()
            self.assertEqual((sess.handsfree, sess.output), (True, "paste"))
            self.assertIn("handsfree", rig.pills)

    def test_handsfree_keeps_live_if_words_are_already_typed(self):
        with Rig():
            sess = dictation._Session()
            sess.output, sess.typed = "live", ["Hello"]
            dictation._session = sess
            dictation.set_handsfree()
            self.assertEqual(sess.output, "live")

    def test_live_dictation_is_recorded_with_what_was_typed(self):
        with isolated(), Rig() as rig:
            sess = session("live", ())
            sess.typed = ["Hello", " there"]
            sess.target = {"hwnd": 1, "pid": 42}
            sess.polish_ok = True
            sess._finish()
            self.assertEqual(hs.last()["text"], "Hello there")
            self.assertEqual(rig.pasted, [])                                      # live mode never pastes


class PhraseMapTests(unittest.TestCase):
    def test_dictionary_and_snippets_combine_and_snippets_can_be_switched_off(self):
        with isolated() as (_, store):
            ds.set_replacement("gonna", "going to")
            ds.snippets_module().set_snippet("sign off", "Best,\nAnna", "snippet sign off")
            pm = dictation._phrase_map()
            self.assertEqual(pm.match_at(["gonna"], 0), (1, "going to"))
            self.assertEqual(pm.match_at("snippet sign off".split(), 0), (3, "Best,\nAnna"))
            store["p2_snippets_in_dictation"] = False
            pm = dictation._phrase_map()
            self.assertIsNone(pm.match_at("snippet sign off".split(), 0))
            self.assertIsNotNone(pm.match_at(["gonna"], 0))

    def test_app_of_uses_the_process_name(self):
        with Rig(app="WINWORD.EXE"):
            self.assertEqual(dictation._app_of({"pid": 1}), "winword")
            self.assertEqual(dictation._app_of(None), "")


if __name__ == "__main__":
    unittest.main()
