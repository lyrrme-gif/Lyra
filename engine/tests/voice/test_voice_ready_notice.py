"""The "voice models ready" notice is shown ONCE, after the last voice-model download has finished.

    python -m unittest engine.tests.voice.test_voice_ready_notice -v

No model, network or microphone: the downloads are fakes.
"""
import sys
import types
import unittest
from unittest import mock

if "faster_whisper" not in sys.modules:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        fw = types.ModuleType("faster_whisper")
        fw.WhisperModel = object
        sys.modules["faster_whisper"] = fw

from engine.voice import stt_engines as se  # noqa: E402
from engine.voice import tts_offline  # noqa: E402
from engine.voice import polite_load  # noqa: E402


class ReadyNotice(unittest.TestCase):
    def setUp(self):
        se._ready_pending = False
        se._last_ready_toast = None
        self.toasts = []
        self.patches = [
            mock.patch.object(se, "_toast", side_effect=self.toasts.append),
            mock.patch.object(se, "parakeet_installed", return_value=True),
        ]
        for p in self.patches:
            p.start()
        self.stt_busy = False
        self.tts_busy = False
        self.tts_installed = False
        self.patches += [
            mock.patch.object(se, "get_downloader", return_value=types.SimpleNamespace(status=lambda: {"downloading": self.stt_busy})),
            mock.patch.object(tts_offline, "status", side_effect=lambda: {"downloading": self.tts_busy}),
            mock.patch.object(tts_offline, "installed", side_effect=lambda: self.tts_installed),
        ]
        for p in self.patches[2:]:
            p.start()

    def tearDown(self):
        mock.patch.stopall()

    def test_one_download_one_notice(self):
        se.voice_download_finished(True)
        self.assertEqual(self.toasts, [se.PILL_READY])

    def test_waits_for_the_other_download_then_shows_once(self):
        self.tts_busy = True                      # the offline voice is still downloading when speech recognition ends
        se.voice_download_finished(True)
        self.assertEqual(self.toasts, [])         # not yet: another model is on its way
        self.tts_busy, self.tts_installed = False, True
        se.voice_download_finished(True)          # the offline voice finishes
        self.assertEqual(self.toasts, ["Voice models ready"])

    def test_never_twice_in_a_row(self):
        se.voice_download_finished(True)
        se.voice_download_finished(True)
        se.voice_download_finished(True)
        self.assertEqual(len(self.toasts), 1)

    def test_no_notice_if_the_required_model_is_missing(self):
        se.parakeet_installed.return_value = False   # e.g. speech recognition was cancelled, only the optional voice finished
        se.voice_download_finished(True)
        self.assertEqual(self.toasts, [])

    def test_failed_download_alone_shows_nothing_ready(self):
        se.voice_download_finished(False)
        self.assertEqual(self.toasts, [])

    def test_download_done_hook_uses_it(self):
        with mock.patch.object(se.threading, "Thread"):          # no real preload thread
            se._on_download_done(True, "")
        self.assertEqual(self.toasts, [se.PILL_READY])


class PoliteLoad(unittest.TestCase):
    def test_gentle_runs_one_load_at_a_time(self):
        order = []
        import threading
        started = threading.Event()

        def first():
            with polite_load.gentle(quiet_wait_s=0):
                order.append("a-in")
                started.set()
                threading.Event().wait(0.15)
                order.append("a-out")

        def second():
            started.wait(2)
            with polite_load.gentle(quiet_wait_s=0):
                order.append("b-in")
                order.append("b-out")

        ts = [threading.Thread(target=first), threading.Thread(target=second)]
        [t.start() for t in ts]
        [t.join(5) for t in ts]
        self.assertEqual(order, ["a-in", "a-out", "b-in", "b-out"])

    def test_gentle_restores_priority_and_survives_an_error(self):
        calls = []
        with mock.patch.object(polite_load, "_process_priority", side_effect=lambda c: (calls.append(c), 0x20)[1]):
            with self.assertRaises(RuntimeError):
                with polite_load.gentle(quiet_wait_s=0):
                    raise RuntimeError("load failed")
        self.assertEqual(calls, [polite_load._BELOW_NORMAL, 0x20])   # lowered, then put back even though the load failed
        self.assertTrue(polite_load._gate.acquire(False))            # and the gate was released
        polite_load._gate.release()

    def test_session_options_turn_spinning_off(self):
        added = {}

        class SO:
            def add_session_config_entry(self, k, v):
                added[k] = v
        fake = types.ModuleType("onnxruntime")
        fake.SessionOptions = SO
        with mock.patch.dict(sys.modules, {"onnxruntime": fake}):
            so = polite_load.session_options(3)
        self.assertEqual((so.intra_op_num_threads, so.inter_op_num_threads), (3, 1))
        self.assertEqual(added, {"session.intra_op.allow_spinning": "0", "session.inter_op.allow_spinning": "0"})


if __name__ == "__main__":
    unittest.main()
