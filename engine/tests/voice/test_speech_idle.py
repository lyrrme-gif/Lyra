"""The speech models give their memory back after idling, and never while something is being dictated."""
import sys
import time
import types
import unittest

from engine.voice import stt_engines


class FakeEngine:
    def __init__(self, idle_s):
        self.last_use = time.monotonic() - idle_s
        self._loaded = True
        self.unloads = 0

    def loaded(self):
        return self._loaded

    def unload(self):
        self._loaded = False
        self.unloads += 1


class IdleUnload(unittest.TestCase):
    def setUp(self):
        self._old_engine = stt_engines._engine
        self._old_setting = stt_engines._setting
        stt_engines._setting = lambda k, d: {"speech_idle_unload_min": 5}.get(k, d)
        # no streaming model / dictation / ptt in the test process
        self._saved = {n: sys.modules.get(n) for n in ("streaming_stt", "dictation", "ptt")}
        sys.modules["engine.voice.streaming_stt"] = types.SimpleNamespace(loaded=lambda: False, idle_for=lambda: 0.0, unload=lambda: False)
        sys.modules["engine.voice.dictation"] = types.SimpleNamespace(is_active=lambda: False)
        sys.modules["engine.voice.ptt"] = types.SimpleNamespace(_holding=False)

    def tearDown(self):
        stt_engines._engine = self._old_engine
        stt_engines._setting = self._old_setting
        for n, m in self._saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m

    def test_unloads_after_the_limit(self):
        stt_engines._engine = FakeEngine(idle_s=6 * 60)
        self.assertEqual(stt_engines.unload_idle(), ["parakeet"])
        self.assertFalse(stt_engines._engine.loaded())

    def test_keeps_a_recently_used_model(self):
        stt_engines._engine = FakeEngine(idle_s=60)
        self.assertEqual(stt_engines.unload_idle(), [])
        self.assertTrue(stt_engines._engine.loaded())

    def test_never_while_dictating(self):
        sys.modules["engine.voice.dictation"] = types.SimpleNamespace(is_active=lambda: True)
        stt_engines._engine = FakeEngine(idle_s=60 * 60)
        self.assertEqual(stt_engines.unload_idle(), [])
        self.assertTrue(stt_engines._engine.loaded())

    def test_zero_means_never(self):
        stt_engines._setting = lambda k, d: 0
        stt_engines._engine = FakeEngine(idle_s=60 * 60)
        self.assertEqual(stt_engines.unload_idle(), [])


if __name__ == "__main__":
    unittest.main()
