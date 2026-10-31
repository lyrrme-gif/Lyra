"""Phase 1 tests: benchmark maths + harness with fake engines (no models). python -m unittest tests.test_p1_bench"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stt_engines import bench  # noqa: E402


class Wer(unittest.TestCase):
    def test_values(self):
        self.assertEqual(bench.wer("open spotify", "Open Spotify!"), 0.0)
        self.assertAlmostEqual(bench.wer("a b c d", "a x c"), 0.5)
        self.assertEqual(bench.wer("a b", ""), 1.0)
        self.assertEqual(bench.wer("", ""), 0.0)
        self.assertGreater(bench.wer("a", "a b c"), 1.0)


class Harness(unittest.TestCase):
    def test_run_reports_per_engine(self):
        clips = [("c1", np.zeros(16000, np.float32), "hello world"), ("c2", np.zeros(8000, np.float32), "good day")]
        calls = []

        def fake(audio, language=None, engine=None):
            calls.append(engine)
            if engine == "parakeet":
                return {"text": "hello world" if len(audio) == 16000 else "good day", "engine": "parakeet"}
            return {"text": "hello word", "engine": "whisper"}
        rep = bench.run(clips, ["whisper", "parakeet"], "en", 2, transcribe=fake)
        self.assertEqual(rep["parakeet"]["wer"], 0.0)
        self.assertGreater(rep["whisper"]["wer"], 0.0)
        self.assertEqual(rep["parakeet"]["errors"], 0)
        self.assertEqual(calls.count("parakeet"), 1 + 4)           # one warm-up + 2 clips x 2 runs

    def test_fallback_is_counted(self):
        clips = [("c", np.zeros(16000, np.float32), "x")]
        rep = bench.run(clips, ["parakeet"], "en", 1, transcribe=lambda a, language=None, engine=None: {"text": "x", "engine": "whisper"})
        self.assertEqual(rep["parakeet"]["errors"], 1)


if __name__ == "__main__":
    unittest.main()
