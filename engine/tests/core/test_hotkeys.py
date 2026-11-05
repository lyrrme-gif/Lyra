"""Tests for hotkey_defs / hotkeys (action shortcuts, Settings -> Shortcuts).
From the backend folder:  python -m unittest tests.test_hotkeys -v   (offline; settings go to a temp folder)"""
import re
import sys
import tempfile
import unittest
from pathlib import Path


sys.modules.pop("engine.core.settings_store", None)   # test_dev (when it runs first) leaves a fake one behind
import engine.core.hotkey_defs as d  # noqa: E402
from engine.core import hotkey_spec  # noqa: E402
from engine.core import hotkeys  # noqa: E402
from engine.core import settings_store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent.parent


class DefsTests(unittest.TestCase):
    def test_every_default_is_a_registrable_combo_and_unique(self):
        seen = set()
        for a, (_label, combo) in d.ACTIONS.items():
            spec = hotkey_spec.parse(combo)
            self.assertIsNotNone(spec, a)
            self.assertTrue(spec.registrable, a)             # modifiers + one key: Windows can take it
            self.assertNotIn(combo, seen, a)
            seen.add(combo)
        self.assertNotIn("ctrl+alt+r", seen)                  # the recording keys
        self.assertNotIn("ctrl+alt+p", seen)
        self.assertNotIn("ctrl+alt+l", seen)                  # game mode's wake key

    def test_open_actions_point_at_real_views(self):
        src = (ROOT / "engine" / "bridge" / "ui_bridge.py").read_text(encoding="utf-8")
        views = src.split("OPEN_VIEWS =")[1].split(")")[0]
        for a, view in d.OPEN_VIEW.items():
            self.assertIn(a, d.ACTIONS)
            self.assertIn(f'"{view}"', views, view)

    def test_the_settings_window_lists_the_same_actions_and_defaults(self):
        ts = (ROOT / "desktop" / "src" / "shared" / "design" / "shortcuts.ts").read_text(encoding="utf-8")
        for a, (label, combo) in d.ACTIONS.items():
            m = re.search(r'\{ id: "%s", label: "([^"]+)".*?def: "([^"]+)"' % a, ts)
            self.assertIsNotNone(m, f"{a} missing in design/shortcuts.ts")
            self.assertEqual(m.group(1), label)
            self.assertEqual(m.group(2), combo)
        lst = (ROOT / "desktop" / "src" / "windows" / "settings" / "shortcutList.ts").read_text(encoding="utf-8")
        for a in d.ACTIONS:
            self.assertIn(f'{a}: "shortcut_{a}_combo"', lst, a)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        sys.modules["engine.core.settings_store"] = settings_store   # test_dev swaps in a fake one: use the real one here
        self.tmp = Path(tempfile.mkdtemp(prefix="lyra-hk-"))
        self._path, self._cache = settings_store.SETTINGS_PATH, settings_store._cache
        settings_store.SETTINGS_PATH = self.tmp / "settings.json"
        settings_store._cache = None

    def tearDown(self):
        settings_store.SETTINGS_PATH, settings_store._cache = self._path, self._cache

    def test_keys_exist_with_defaults_and_can_be_saved(self):
        self.assertEqual(settings_store.get("shortcut_media_play_combo"), "ctrl+alt+space")
        self.assertIn("shortcut_media_next_combo", settings_store.PREF_GROUPS["features"])
        settings_store.update_group("features", {"shortcut_media_next_combo": "ctrl+shift+f9"})
        self.assertEqual(hotkeys.combos()["media_next"], "ctrl+shift+f9")

    def test_active_map_follows_settings_and_empty_means_off(self):
        self.assertIn("media_play", hotkeys.active_map())
        settings_store.set_many({"shortcut_media_play_combo": ""})
        self.assertNotIn("media_play", hotkeys.active_map())
        settings_store.set_many({"shortcut_media_prev_combo": "lctrl"})        # left/right-specific: Windows can't register it
        self.assertNotIn("media_prev", hotkeys.active_map())

    def test_status_reports_keys_and_defaults(self):
        st = hotkeys.status()
        self.assertEqual(set(st["keys"]), set(d.ACTIONS))
        self.assertEqual(st["defaults"]["volume_mute"], "ctrl+alt+m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
