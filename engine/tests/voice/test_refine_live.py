"""Live typing is corrected with the accurate engine when you stop (keyboard + engines are fakes)."""
import sys, types, unittest
import engine.computer.keyboard as kb  # noqa: E402
from engine.voice import dictation  # noqa: E402
import engine.voice.dictation_hotkey as hk  # noqa: E402


def make(raw="T smith at seven to morrow ", engine="Let's meet tomorrow at 7."):
    s = dictation._Session(); s.output = "live"
    s.typed = [raw]; s.audio = [b"\x00\x00" * 1600]
    snap = {"hwnd": 1, "pid": 42, "focus": 1, "caret_hwnd": 1, "caret": (0, 0, 0, 0)}
    s.target = snap; s.end_snap = snap; s.end_input = 0
    return s


class T(unittest.TestCase):
    def setUp(self):
        self.o = (kb.focus_snapshot, kb.process_name, hk.mouse_hook_ok, hk.input_count,
                  dictation._replace, dictation._offer, dictation._notify,
                  sys.modules.get("engine.voice.stt_engines"))
        kb.focus_snapshot = lambda: {"hwnd": 1, "pid": 42, "focus": 1, "caret_hwnd": 1, "caret": (0, 0, 0, 0)}
        kb.process_name = lambda pid: "notepad.exe"
        hk.mouse_hook_ok = lambda: True; hk.input_count = lambda: 0
        self.replaced, self.offered = [], []
        dictation._replace = lambda raw, new: (self.replaced.append((raw, new)) or True)
        dictation._offer = lambda raw, new, hwnd: self.offered.append((raw, new))
        dictation._notify = lambda *a: None
        m = types.ModuleType("engine.voice.stt_engines"); m.status = lambda: {"ready": True}
        m.transcribe = lambda a, language=None, hints=None: {"text": "Let's meet tomorrow at 7", "engine": "parakeet", "language": "en"}
        sys.modules["engine.voice.stt_engines"] = m

    def tearDown(self):
        (kb.focus_snapshot, kb.process_name, hk.mouse_hook_ok, hk.input_count,
         dictation._replace, dictation._offer, dictation._notify, e) = self.o
        sys.modules["engine.voice.stt_engines"] = e

    def test_replaces_when_untouched(self):
        s = make(); out = s._refine_live(s.typed[0])
        self.assertTrue(out and out.startswith("Let's meet tomorrow at 7") and out.endswith(" "))
        self.assertEqual(len(self.replaced), 1); self.assertEqual(self.replaced[0][0], "T smith at seven to morrow ")

    def test_never_nags_when_user_moved(self):
        s = make(); hk.input_count = lambda: 5
        self.assertIsNone(s._refine_live(s.typed[0]))
        self.assertEqual(self.replaced, []); self.assertEqual(self.offered, [])   # no Apply button for it

    def test_dismiss_forgets_the_offer(self):
        dictation._pending = {"raw": "a", "polished": "b", "hwnd": 1, "ts": 0}
        dictation.dismiss_pending()
        self.assertIsNone(dictation._pending)
        self.assertEqual(dictation.apply_pending(), (False, "Nothing to apply"))

    def test_off_switch(self):
        s = make(); old = dictation._refine_on; dictation._refine_on = lambda: False
        try: self.assertIsNone(s._refine_live(s.typed[0]))
        finally: dictation._refine_on = old
        self.assertEqual(self.replaced, [])

    def test_skips_protected_apps_and_no_audio(self):
        s = make(); s.audio = []
        self.assertIsNone(s._refine_live(s.typed[0]))
        s = make(); kb.process_name = lambda pid: next(iter(dictation._NO_REPLACE_APPS))
        self.assertIsNone(s._refine_live(s.typed[0]))


_EARLY = dictation._early_on


class Early(unittest.TestCase):
    """Sentences decoded during the dictation are reused; a failed one falls back to the whole audio."""
    def setUp(self):
        self.calls = []
        m = types.ModuleType("engine.voice.stt_engines"); m.status = lambda: {"ready": True}
        def tr(a, language=None, hints=None):
            self.calls.append(len(a)); return {"text": "whole", "engine": "parakeet", "language": "en"}
        m.transcribe = tr
        self.old = sys.modules.get("engine.voice.stt_engines"); sys.modules["engine.voice.stt_engines"] = m

    def tearDown(self):
        sys.modules["engine.voice.stt_engines"] = self.old
        dictation._early_on = _EARLY

    def _fut(self, v):
        from concurrent.futures import Future
        f = Future(); f.set_result(v); return f

    def test_uses_early_sentences(self):
        s = make(); s.audio = [b"\x00\x00" * 16000]
        s.seg_jobs = [self._fut({"text": "Let's meet at 7.", "engine": "parakeet", "language": "en"}),
                      self._fut({"text": "Bring bread.", "engine": "parakeet", "language": "en"})]
        self.assertEqual(s._engine_text()[0], "Let's meet at 7. Bring bread.")
        self.assertEqual(self.calls, [])            # nothing decoded again at the end

    def test_falls_back_when_one_failed(self):
        s = make(); s.audio = [b"\x00\x00" * 16000]
        s.seg_jobs = [self._fut({"text": "ok"}), self._fut(None)]
        self.assertEqual(s._engine_text()[0], "whole"); self.assertEqual(len(self.calls), 1)

    def test_submit_skips_silence_and_short(self):
        s = make(); s.output = "live"; dictation._early_on = lambda: True
        s.seg_chunks = [b"\x00\x00" * 16000]; s._submit_segment(False); self.assertEqual(s.seg_jobs, [])
        s.seg_chunks = [b"\x00\x00" * 100]; s._submit_segment(True); self.assertEqual(s.seg_jobs, [])
        s.seg_chunks = [b"\x00\x00" * 16000]; s._submit_segment(True); self.assertEqual(len(s.seg_jobs), 1)
        s.seg_jobs[0].result(timeout=5); s._pool.shutdown()

class Safety(unittest.TestCase):
    def test_early_decode_is_off_by_default(self):
        self.assertFalse(_EARLY())
        s = make(); s.output = "live"; s.seg_chunks = [b"\x00\x00" * 16000]; s._submit_segment(True)
        self.assertEqual(s.seg_jobs, [])

    def test_idle_limits(self):
        self.assertLessEqual(dictation.IDLE_STOP_S, 15); self.assertLessEqual(dictation.HANDSFREE_IDLE_S, dictation.IDLE_STOP_S)

    def test_mark_alt_used_sends_dummy_only_with_alt(self):
        sent = []; o = (kb.held_modifiers, kb._send)
        try:
            kb._send = lambda ev: sent.append(ev) or True
            kb.held_modifiers = lambda: set(); self.assertFalse(kb.mark_alt_used()); self.assertEqual(sent, [])
            kb.held_modifiers = lambda: {0xA2}; self.assertFalse(kb.mark_alt_used())          # Ctrl only
            kb.held_modifiers = lambda: {0xA2, 0xA4}; self.assertTrue(kb.mark_alt_used())     # Ctrl + Alt
            self.assertTrue(any(e[0] == kb.VK_DUMMY for e in sent[0]))
        finally:
            kb.held_modifiers, kb._send = o


if __name__ == "__main__":
    unittest.main()
