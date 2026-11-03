"""The Downloads watcher looks at the folder and ONE level of sub-folders, not the whole tree."""
import os
import tempfile
import unittest
from pathlib import Path

import downloads_watch as dw


class Snapshot(unittest.TestCase):
    def test_depth_partials_and_ignores(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.zip").write_bytes(b"x" * 10)
            (root / "movie.mp4.crdownload").write_bytes(b"y" * 5)
            (root / "desktop.ini").write_text("ignore me")
            (root / "sub").mkdir()
            (root / "sub" / "b.txt").write_text("hi")
            (root / "sub" / "deep").mkdir()
            (root / "sub" / "deep" / "c.txt").write_text("too deep")
            snap = dw._snapshot(root)
            self.assertEqual(set(snap), {"a.zip", "movie.mp4.crdownload", "sub" + os.sep + "b.txt"})
            self.assertEqual(snap["a.zip"][0], 10)
            self.assertFalse(snap["a.zip"][2])
            self.assertTrue(snap["movie.mp4.crdownload"][2])      # a partial file = an active download

    def test_missing_folder_is_empty(self):
        self.assertEqual(dw._snapshot(Path(tempfile.gettempdir()) / "does-not-exist-lyra"), {})


if __name__ == "__main__":
    unittest.main()
