"""Tests for Light mode (Settings -> Features -> Light mode, feature_light_mode, on by default).
From the backend folder:   python -m unittest tests.test_light_mode -v

No microphone, no network, no model: audio is synthetic and the heavy libraries are stand-ins.
They check the LOGIC (what is loaded when, what is skipped on silence), not real CPU use.
"""
import importlib
import queue
import sys
import threading
import types
import unittest
from unittest import mock


import numpy as np  # noqa: E402

for _m in ("pyaudio", "pygame", "speech_recognition", "faster_whisper"):
    if _m not in sys.modules:
        try:
            __import__(_m)
        except Exception:
            sys.modules[_m] = types.ModuleType(_m)
if not hasattr(sys.modules["faster_whisper"], "WhisperModel"):
    sys.modules["faster_whisper"].WhisperModel = object
if not hasattr(sys.modules["speech_recognition"], "Recognizer"):
    sys.modules["speech_recognition"].Recognizer = object
    sys.modules["speech_recognition"].Microphone = object

class VoiceStartup(unittest.TestCase):
    """Microsoft's voice is the default and the only one used; the offline voice (Kokoro) is an OPTION that is
    loaded and used only when Settings -> Voice -> Voice engine says "offline"."""

    def _import_voice(self, light: bool, engine: str = "edge"):
        started = []
        real_thread = threading.Thread

        class Rec:
            def __init__(self, target=None, name="", **kw):
                self.target, self.name = target, name

            def start(self):
                started.append(self.name)

            def join(self, *a, **k):
                pass

            def is_alive(self):
                return False

        from engine.core import settings_store
        real_get = settings_store.get
        with mock.patch.object(settings_store, "feature_on", side_effect=lambda n: light if n == "light_mode" else True), \
                mock.patch.object(settings_store, "get", side_effect=lambda k, d=None: engine if k == "tts_engine" else real_get(k, d)), \
                mock.patch.object(threading, "Thread", Rec):
            sys.modules.pop("engine.voice.voice", None)
            sys.modules.pop("engine.voice.tts", None)          # the startup threads and the voices now live in tts.py
            sys.modules.pop("engine.voice.stt", None)
            voice = importlib.import_module("engine.voice.voice")
        threading.Thread = real_thread
        return sys.modules["engine.voice.tts"], started

    def test_light_mode_does_not_preload_kokoro(self):
        _, started = self._import_voice(True)
        self.assertNotIn("lyra-kokoro-preload", started)
        self.assertIn("lyra-edge-probe", started)

    def test_default_engine_never_preloads_kokoro_even_with_light_mode_off(self):
        _, started = self._import_voice(False)
        self.assertNotIn("lyra-kokoro-preload", started)
        self.assertIn("lyra-edge-probe", started)

    def test_offline_engine_preloads_kokoro_at_startup(self):
        _, started = self._import_voice(True, "offline")
        self.assertIn("lyra-kokoro-preload", started)

    def test_failed_probe_does_not_warm_kokoro_on_the_default_engine(self):
        voice, _ = self._import_voice(True)
        started = []

        class Rec:
            def __init__(self, target=None, name="", **kw):
                self.name = name

            def start(self):
                started.append(self.name)

        with mock.patch.object(voice, "_edge_with_deadline", side_effect=TimeoutError("down")), \
                mock.patch.object(threading, "Thread", Rec):
            voice._edge_probe()
        self.assertNotIn("lyra-kokoro-preload", started)

    def test_working_probe_leaves_kokoro_alone(self):
        voice, _ = self._import_voice(True)
        started = []

        class Rec:
            def __init__(self, target=None, name="", **kw):
                self.name = name

            def start(self):
                started.append(self.name)

        with mock.patch.object(voice, "_edge_with_deadline", return_value="/nonexistent.mp3"), \
                mock.patch.object(threading, "Thread", Rec):
            voice._edge_probe()
        self.assertNotIn("lyra-kokoro-preload", started)

    def test_synth_chunk_tries_microsoft_first(self):
        voice, _ = self._import_voice(True)
        voice._edge_down_until = 0.0
        with mock.patch.object(voice, "_edge_with_deadline", return_value="edge.mp3") as edge, \
                mock.patch.object(voice, "_synthesize_kokoro") as kok:
            self.assertEqual(voice._synth_chunk("hello there"), "edge.mp3")
        edge.assert_called_once()
        kok.assert_not_called()

    def test_synth_chunk_does_not_use_kokoro_on_the_default_engine(self):
        voice, _ = self._import_voice(True)
        voice._edge_down_until = 0.0
        voice._edge_fails = 0
        with mock.patch.object(voice, "_edge_with_deadline", side_effect=RuntimeError("403")), \
                mock.patch.object(voice, "_get_kokoro", side_effect=AssertionError("Kokoro must not load")) as load:
            self.assertIsNone(voice._synth_chunk("hello there"))   # None -> the Windows voice
        load.assert_not_called()

    def test_synth_chunk_uses_the_offline_voice_first_when_chosen_and_installed(self):
        voice, _ = self._import_voice(True)
        voice._edge_down_until = 0.0
        with mock.patch.object(voice, "_offline_chosen", return_value=True), \
                mock.patch.object(voice, "_kokoro_model_paths", return_value=("m.onnx", "v.bin")), \
                mock.patch.object(voice, "_edge_with_deadline", return_value="edge.mp3") as edge, \
                mock.patch.object(voice, "_synthesize_kokoro", return_value="kokoro.wav") as kok:
            self.assertEqual(voice._synth_chunk("hello there"), "kokoro.wav")
        kok.assert_called_once()
        edge.assert_not_called()

    def test_chosen_but_not_downloaded_still_speaks_with_microsoft(self):
        voice, _ = self._import_voice(True)
        voice._edge_down_until = 0.0
        with mock.patch.object(voice, "_offline_chosen", return_value=True), \
                mock.patch.object(voice, "_kokoro_model_paths", return_value=None), \
                mock.patch.object(voice, "_piper_model", return_value=None), \
                mock.patch.object(voice, "_edge_with_deadline", return_value="edge.mp3"):
            self.assertEqual(voice._synth_chunk("hello there"), "edge.mp3")


class ParakeetPreload(unittest.TestCase):
    def test_light_mode_skips_the_preload(self):
        from engine.voice import stt
        from engine.core import settings_store
        stt._preload_started = False
        with mock.patch.object(settings_store, "feature_on", return_value=True), \
                mock.patch("threading.Thread") as th:
            stt._preload_parakeet()
        th.assert_not_called()

    def test_light_mode_off_preloads(self):
        from engine.voice import stt
        from engine.core import settings_store
        stt._preload_started = False
        with mock.patch.object(settings_store, "feature_on", return_value=False), \
                mock.patch("threading.Thread") as th:
            stt._preload_parakeet()
        th.assert_called_once()


class Setting(unittest.TestCase):
    def test_light_mode_is_on_by_default_and_a_known_feature_key(self):
        from engine.core import settings_store
        self.assertTrue(settings_store._DEFAULTS.get("feature_light_mode"))
        self.assertIn("feature_light_mode", settings_store.PREF_GROUPS["features"])


if __name__ == "__main__":
    unittest.main()
