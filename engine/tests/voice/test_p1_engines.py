"""Phase 1 tests: C1 API, routing with fallback, bridge routes, stt.py / streaming_stt.py wiring.

    python -m unittest tests.test_p1_engines -v

Engines are FAKES; settings are an in-memory dict; no model, network or microphone.
"""
import sys
import types
import unittest

import numpy as np


if "faster_whisper" not in sys.modules:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        fw = types.ModuleType("faster_whisper")
        fw.WhisperModel = object
        sys.modules["faster_whisper"] = fw

from engine.core import settings_store  # noqa: E402
from engine.voice import stt_engines  # noqa: E402
from engine.bridge import api_p1  # noqa: E402
from engine.voice import streaming_stt  # noqa: E402
from engine.voice import stt  # noqa: E402
from engine.voice.stt_engines import whisper_engine  # noqa: E402

AUDIO = (np.sin(np.linspace(0, 400, 16000 * 2)) * 0.3).astype(np.float32)


class FakeParakeet:
    def __init__(self, text="Open Spotify.", fail=False):
        self.text, self.fail, self.calls, self.last_hints = text, fail, 0, None

    def transcribe(self, audio, hints=None, fuzzy_hints=False):
        self.calls += 1
        self.last_hints = hints
        if self.fail:
            raise RuntimeError("onnx exploded")
        return self.text

    def loaded(self):
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self._orig = (settings_store.get, settings_store.set_many, stt_engines.parakeet_ready,
                      stt_engines._parakeet, whisper_engine.transcribe, whisper_engine.model_present)
        settings_store.get = lambda k, d=None: self.store.get(k, d)
        settings_store.set_many = lambda u: self.store.update(u)
        self.ready = True
        self.pk = FakeParakeet()
        self.wh_calls = []
        stt_engines.parakeet_ready = lambda: self.ready
        stt_engines._parakeet = lambda: self.pk

        def fake_whisper(a, code, hints):
            self.wh_calls.append((code, hints))
            return "whisper text", code or "xx"
        whisper_engine.transcribe = fake_whisper
        whisper_engine.model_present = lambda: True

    def tearDown(self):
        (settings_store.get, settings_store.set_many, stt_engines.parakeet_ready,
         stt_engines._parakeet, whisper_engine.transcribe, whisper_engine.model_present) = self._orig


class C1(Base):
    def test_result_shape(self):
        r = stt_engines.transcribe(AUDIO, language="en")
        self.assertEqual(set(["text", "language", "engine", "ms"]) - set(r), set())
        self.assertEqual((r["text"], r["engine"], r["language"]), ("Open Spotify.", "parakeet", "en"))

    def test_unsupported_language_uses_whisper(self):
        r = stt_engines.transcribe(AUDIO, language="ar")
        self.assertEqual((r["engine"], r["text"]), ("whisper", "whisper text"))
        self.assertEqual(self.pk.calls, 0)

    def test_parakeet_failure_falls_back_to_whisper(self):
        self.pk.fail = True
        r = stt_engines.transcribe(AUDIO, language="en")
        self.assertEqual(r["engine"], "whisper")
        self.assertIn("fallback", r)

    def test_model_missing_uses_whisper(self):
        self.ready = False
        self.assertEqual(stt_engines.transcribe(AUDIO, language="en")["engine"], "whisper")

    def test_hints_reach_the_engine(self):
        stt_engines.transcribe(AUDIO, language="en", hints=["Lyra"])
        self.assertEqual(self.pk.last_hints, ["Lyra"])

    def test_whisper_hints_applied_to_text(self):
        whisper_engine.transcribe = lambda a, c, h: ("open spotify", "en")
        r = stt_engines.transcribe(AUDIO, language="ar", hints=["Spotify"])
        self.assertEqual(r["text"], "open Spotify")

    def test_both_engines_failing_never_raises(self):
        self.pk.fail = True

        def boom(*a):
            raise RuntimeError("no whisper")
        whisper_engine.transcribe = boom
        r = stt_engines.transcribe(AUDIO, language="en")
        self.assertEqual(r["text"], "")
        self.assertIn("error", r)

    def test_engine_override_and_bad_audio(self):
        self.assertEqual(stt_engines.transcribe(AUDIO, language="en", engine="whisper")["engine"], "whisper")
        self.assertIn("error", stt_engines.transcribe(object(), language="en"))

    def test_set_engine(self):
        # Lyra works with Parakeet only: any valid request is accepted but the engine stays Parakeet.
        self.assertEqual(stt_engines.set_engine("Whisper"), "parakeet")
        self.assertEqual(stt_engines.mode(), "parakeet")
        with self.assertRaises(ValueError):
            stt_engines.set_engine("deepspeech")
        self.assertEqual(self.store["p1_stt_engine"], "parakeet")


class Status(Base):
    def fake_dl(self, installed, downloading=False, progress=0.0):
        class D:
            def status(s):
                return {"installed": installed, "downloading": downloading, "progress": progress, "state": "x"}
        stt_engines._downloader = D()

    def tearDown(self):
        stt_engines._downloader = None
        super().tearDown()

    def test_ready_pill(self):
        self.fake_dl(True)
        from engine.voice.stt_engines import parakeet
        orig, parakeet.available = parakeet.available, lambda: True
        try:
            s = stt_engines.status()
        finally:
            parakeet.available = orig
        self.assertTrue(s["ready"])
        self.assertEqual((s["pill"], s["engine"]), ("Voice model ready", "parakeet"))
        self.assertEqual(len(s["languages"]), 25)

    def test_not_downloaded_pill(self):
        self.fake_dl(False)
        s = stt_engines.status()
        self.assertFalse(s["ready"])
        self.assertEqual(s["pill"], "Download the voice model to dictate")
        self.assertEqual(s["engine"], "whisper")
        self.assertEqual(s["languages"], ["*"])

    def test_downloading_pill(self):
        self.fake_dl(False, True, 0.42)
        s = stt_engines.status()
        self.assertTrue(s["downloading"])
        self.assertIn("42%", s["pill"])

    def test_whisper_mode_is_gone(self):
        self.fake_dl(False)
        stt_engines.set_engine("whisper")                # ignored: still Parakeet, so without the model it is not ready
        self.assertEqual(stt_engines.status()["mode"], "parakeet")
        self.assertFalse(stt_engines.status()["ready"])


class Api(Base):
    def test_paths(self):
        self.assertIsNone(api_p1.dispatch("GET", "/health"))
        self.assertEqual(api_p1.dispatch("GET", "/p1/voice/nope")[0], 405)

    def test_engine_route(self):
        code, obj = api_p1.dispatch("POST", "/p1/voice/engine", {"engine": "parakeet"})
        self.assertEqual((code, obj["mode"]), (200, "parakeet"))
        code, obj = api_p1.dispatch("POST", "/p1/voice/engine", {"engine": "zzz"})
        self.assertEqual(code, 400)
        self.assertFalse(obj["ok"])

    def test_status_route(self):
        code, obj = api_p1.dispatch("GET", "/p1/voice/status")
        self.assertEqual(code, 200)
        self.assertIn("pill", obj)

    def test_register_wraps_and_passes_through(self):
        class H:
            def __init__(s, path):
                s.path, s.headers, s.out = path, {}, None

            def _json(s, code, obj):
                s.out = (code, obj)

            def do_GET(s):
                s.out = ("orig", s.path)

            def do_POST(s):
                s.out = ("orig", s.path)
        api_p1.register(H)
        api_p1.register(H)                                   # idempotent
        h = H("/status")
        h.do_GET()
        self.assertEqual(h.out, ("orig", "/status"))
        h = H("/p1/voice/status")
        h.do_GET()
        self.assertEqual(h.out[0], 200)
        h = H("/chat")
        h.do_POST()
        self.assertEqual(h.out, ("orig", "/chat"))

    def test_foreign_origin_refused(self):
        class H:
            def __init__(s):
                s.path, s.headers, s.out = "/p1/voice/delete", {"Origin": "https://evil.example"}, None

            def _json(s, code, obj):
                s.out = (code, obj)

            def do_GET(s): ...
            def do_POST(s): ...
        api_p1.register(H)
        h = H()
        h.do_POST()
        self.assertEqual(h.out[0], 403)


class SttWiring(Base):
    class Model:
        def __init__(s):
            s.calls = []

        def transcribe(s, audio, **kw):
            s.calls.append(kw)
            seg = types.SimpleNamespace(text=" hello there ", no_speech_prob=0.0, compression_ratio=1.0)
            return [seg], types.SimpleNamespace(language="en")

    def setUp(self):
        super().setUp()
        self.model = self.Model()
        self._o = (stt._load_shared_whisper, stt.language_override)
        stt._load_shared_whisper = lambda: self.model
        stt._preload_started = True
        self.rec = stt.SpeechRecognizer()
        self.pcm = (AUDIO * 32767).astype(np.int16).tobytes()

    def tearDown(self):
        stt._load_shared_whisper, stt.language_override = self._o
        super().tearDown()

    def test_parakeet_path_skips_whisper(self):
        self.assertEqual(self.rec.transcribe(self.pcm), ("ok", "open spotify."))
        self.assertEqual(self.model.calls, [])
        self.assertIn("Spotify", self.pk.last_hints)

    def test_fallback_when_parakeet_fails(self):
        self.pk.fail = True
        self.assertEqual(self.rec.transcribe(self.pcm), ("ok", "whisper text"))     # via C1's Whisper

    def test_not_ready_uses_original_whisper_path(self):
        self.ready = False
        self.assertEqual(self.rec.transcribe(self.pcm), ("ok", "hello there"))
        self.assertEqual(self.model.calls[0]["language"], "en")

    def test_language_override_auto_detects(self):
        self.ready = False
        stt.language_override = ""
        self.rec.transcribe(self.pcm)
        self.assertIsNone(self.model.calls[0]["language"])

    def test_empty_parakeet_text_is_unrecognized(self):
        self.pk.text = "  "
        self.assertEqual(self.rec.transcribe(self.pcm), ("unrecognized", ""))

    def test_silence_never_reaches_an_engine(self):
        self.assertEqual(self.rec.transcribe(bytes(32000))[0], "unrecognized")
        self.assertEqual(self.pk.calls, 0)

    def test_whisper_request_still_uses_parakeet(self):
        stt_engines.set_engine("whisper")
        self.rec.transcribe(self.pcm)
        self.assertEqual(self.pk.calls, 1)


class FakeRec:
    def create_stream(self):
        return types.SimpleNamespace(accept_waveform=lambda *a: None)

    def is_ready(self, s):
        return False

    def get_result(self, s):
        return "HI"

    def is_endpoint(self, s):
        return False

    def reset(self, s):
        pass


class StreamingRefine(Base):
    def make(self):
        return streaming_stt.Session(FakeRec())

    def pcm(self, seconds):
        return (np.ones(int(16000 * seconds)) * 3000).astype(np.int16).tobytes()

    def test_refine_uses_parakeet_text(self):
        s = self.make()
        s.feed(self.pcm(1.0))
        self.assertEqual(s.refine("en"), "Open Spotify.")

    def test_refine_none_when_not_routed_or_too_short(self):
        s = self.make()
        s.feed(self.pcm(0.05))
        self.assertIsNone(s.refine("en"))
        s.feed(self.pcm(1.0))
        self.assertIsNone(s.refine("ar"))
        self.ready = False
        self.assertIsNone(s.refine("en"))

    def test_buffer_is_capped_and_cleared_on_reset(self):
        s = self.make()
        for _ in range(5):
            s.feed(self.pcm(20))
        self.assertLessEqual(s.segment_seconds(), streaming_stt.MAX_BUFFER_SECONDS + 0.01)
        s.reset()
        self.assertEqual(s.segment_seconds(), 0.0)

    def test_refine_failure_returns_none(self):
        self.pk.fail = True
        s = self.make()
        s.feed(self.pcm(1.0))
        self.assertIsNone(s.refine("en"))


if __name__ == "__main__":
    unittest.main()
