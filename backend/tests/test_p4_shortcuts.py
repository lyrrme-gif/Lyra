"""Phase 4: the shortcut recorder's pure logic (ui/src/design/shortcuts.ts), run offline with Node."""
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _node_can_strip_types() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    out = subprocess.run([node, "--experimental-strip-types", "-e", "1"], capture_output=True, text=True)
    return out.returncode == 0


@unittest.skipUnless(_node_can_strip_types(), "needs Node 22.6+ (--experimental-strip-types)")
class ShortcutLogic(unittest.TestCase):
    def test_logic(self):
        res = subprocess.run(
            ["node", "--experimental-strip-types", "--no-warnings", str(HERE / "p4_shortcuts_check.mjs")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("ok", res.stdout)


if __name__ == "__main__":
    unittest.main()
