"""Phase 6 tests: the start-up animation setting (p6_startup_anim).
From the backend folder:  python -m unittest tests.test_p6_startup_setting -v
Offline; settings.json is redirected to a temp folder."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settings_store  # noqa: E402


class StartupSetting(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lyra-p6-"))
        self._path, self._cache = settings_store.SETTINGS_PATH, settings_store._cache
        settings_store.SETTINGS_PATH = self.tmp / "settings.json"
        settings_store._cache = None

    def tearDown(self):
        settings_store.SETTINGS_PATH, settings_store._cache = self._path, self._cache

    def test_default_is_on(self):
        self.assertIs(settings_store.get("p6_startup_anim"), True)
        self.assertIs(settings_store.get_group("features")["p6_startup_anim"], True)

    def test_can_be_turned_off_and_persists(self):
        out = settings_store.update_group("features", {"p6_startup_anim": False})
        self.assertIs(out["p6_startup_anim"], False)
        self.assertIs(json.loads(settings_store.SETTINGS_PATH.read_text("utf-8"))["p6_startup_anim"], False)
        settings_store._cache = None                       # a restart
        self.assertIs(settings_store.get("p6_startup_anim"), False)

    def test_only_true_false_accepted(self):
        for bad in ("yes", 1, None, "false"):
            with self.assertRaises(ValueError):
                settings_store.update_group("features", {"p6_startup_anim": bad})
        self.assertIs(settings_store.get("p6_startup_anim"), True)

    def test_reset_prefs_turns_it_back_on(self):
        settings_store.update_group("features", {"p6_startup_anim": False})
        settings_store.reset_prefs()
        self.assertIs(settings_store.get("p6_startup_anim"), True)

    def test_other_features_untouched(self):
        before = settings_store.get("feature_clipboard")
        settings_store.update_group("features", {"p6_startup_anim": False})
        self.assertEqual(settings_store.get("feature_clipboard"), before)


if __name__ == "__main__":
    unittest.main()
