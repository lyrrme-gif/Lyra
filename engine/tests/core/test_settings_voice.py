"""Tests for settings_voice (voice/typed island-theme + behaviour commands).
From the backend folder:  python -m unittest tests.test_settings_voice -v
Offline; settings.json is redirected to a temp folder, the UI push and Windows switch are faked."""
import sys
import tempfile
import types
import unittest
from pathlib import Path


from engine.core import settings_store  # noqa: E402
import engine.core.settings_voice as sv  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lyra-sv-"))
        self._path, self._cache = settings_store.SETTINGS_PATH, settings_store._cache
        settings_store.SETTINGS_PATH = self.tmp / "settings.json"
        settings_store._cache = None
        self.pushed = []
        self.win = []
        ub = types.ModuleType("engine.bridge.ui_bridge")
        ub.push_appearance = lambda f: self.pushed.append(dict(f))
        ctl = types.ModuleType("engine.features.controls")
        ctl.get_dark = lambda: False
        ctl.toggle = lambda name, on=None: (self.win.append((name, on)) or {"ok": True, name: on})
        self._mods = {k: sys.modules.get(k) for k in ("ui_bridge", "controls")}
        sys.modules["engine.bridge.ui_bridge"], sys.modules["engine.features.controls"] = ub, ctl

    def tearDown(self):
        settings_store.SETTINGS_PATH, settings_store._cache = self._path, self._cache
        for k, v in self._mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class Appearance(_Base):
    def test_dark_and_light_phrases(self):
        for text, dark in (("set dark mode", True), ("Set dark mode.", True), ("turn on dark mode", True),
                           ("switch to light mode", False), ("turn off dark mode", False), ("dark mode off", False),
                           ("enable dark mode please", True), ("turn off light mode", True), ("hey lyra, set light mode", False),
                           ("set dark mode back", True), ("set light mode again", False), ("go back to dark mode", True),
                           ("switch to dark mode instead", True)):
            self.pushed.clear(); self.win.clear()
            reply = sv.handle_command(text)
            self.assertTrue(reply, text)
            self.assertEqual(self.win, [("dark", dark)], text)
            self.assertEqual(self.pushed, [], text)           # Lyra's own windows have a fixed look: only Windows changes

    def test_toggle_flips_current(self):
        sv.handle_command("toggle dark mode")
        self.assertEqual(self.win, [("dark", True)])      # fake PC is light -> dark

    def test_island_theme(self):
        cases = {"set island theme to frost": {"island_theme": "frost"},
                 "change the island theme to black": {"island_theme": "pure-black"}}
        for text, want in cases.items():
            self.pushed.clear()
            self.assertTrue(sv.handle_command(text), text)
            self.assertEqual(self.pushed, [want], text)
        self.assertEqual(self.win, [])                    # none of these touch Windows

    def test_removed_appearance_commands_are_not_commands(self):
        # Accent, density, motion and the settings theme no longer exist, so these fall through to the assistant.
        for text in ("set accent to blue", "compact mode", "reduce motion", "set theme to light", "set theme to auto"):
            self.assertIsNone(sv.parse(text), text)

    def test_reset_appearance(self):
        sv.handle_command("reset appearance")
        self.assertEqual(self.pushed, [{"island_theme": "midnight"}])


class Behaviour(_Base):
    def test_reply_style(self):
        for text, want in (("set reply style to short", "short"), ("be more detailed", "detailed"),
                           ("answer shorter", "short"), ("set response style to balanced", "balanced")):
            sv.handle_command(text)
            self.assertEqual(settings_store.get("reply_style"), want, text)

    def test_voice_speed_is_not_a_setting_any_more(self):
        for text in ("speak faster", "speak slower", "set voice speed to 35", "speak normally"):
            self.assertIsNone(sv.parse(text), text)

    def test_acknowledgements(self):
        sv.handle_command("set acknowledgements to both"); self.assertEqual(settings_store.get("mochi_sound_mode"), "both")
        sv.handle_command("set acknowledgements to voice"); self.assertEqual(settings_store.get("mochi_sound_mode"), "speak")
        sv.handle_command("set acknowledgements to mochi sounds"); self.assertEqual(settings_store.get("mochi_sound_mode"), "mochi")

    def test_notifications(self):
        sv.handle_command("turn off discord notifications"); self.assertIs(settings_store.get("notify_discord"), False)
        sv.handle_command("turn on discord notifications"); self.assertIs(settings_store.get("notify_discord"), True)
        sv.handle_command("disable whatsapp notifications"); self.assertIs(settings_store.get("notify_whatsapp"), False)
        sv.handle_command("turn off reminder notifications"); self.assertIs(settings_store.get("notify_reminders"), False)
        sv.handle_command("set notification time to 8 seconds"); self.assertEqual(settings_store.get("notify_toast_ms"), 8000)
        sv.handle_command("set notification time to 99 seconds"); self.assertEqual(settings_store.get("notify_toast_ms"), 15000)
        sv.handle_command("calls only on"); self.assertIs(settings_store.get("notify_calls_only"), True)

    def test_quiet_hours(self):
        sv.handle_command("turn on quiet hours"); self.assertIs(settings_store.get("notify_quiet_enabled"), True)
        sv.handle_command("turn off quiet hours"); self.assertIs(settings_store.get("notify_quiet_enabled"), False)
        r = sv.handle_command("set quiet hours from 10 pm to 7 am")
        self.assertEqual((settings_store.get("notify_quiet_start"), settings_store.get("notify_quiet_end")), ("22:00", "07:00"))
        self.assertIs(settings_store.get("notify_quiet_enabled"), True)
        self.assertIn("22:00", r)
        sv.handle_command("set quiet hours 23:30 to 06:15")
        self.assertEqual((settings_store.get("notify_quiet_start"), settings_store.get("notify_quiet_end")), ("23:30", "06:15"))

    def test_switches(self):
        sv.handle_command("turn off the timer sound"); self.assertIs(settings_store.get("timer_alarm_sound"), False)
        sv.handle_command("turn on the timer sound"); self.assertIs(settings_store.get("timer_alarm_sound"), True)
        sv.handle_command("turn off proactive tips"); self.assertIs(settings_store.get("proactive_enabled"), False)
        sv.handle_command("turn off dictation cleanup"); self.assertIs(settings_store.get("dictation_polish"), False)
        sv.handle_command("turn off the tab wheel"); self.assertIs(settings_store.get("feature_tab_wheel"), False)
        sv.handle_command("turn off circle to ask"); self.assertIs(settings_store.get("feature_circle_ask"), False)
        sv.handle_command("hide mochi"); self.assertIs(settings_store.get("feature_mascot"), False)
        sv.handle_command("set mochi size to large"); self.assertEqual(settings_store.get("mascot_size"), "large")
        sv.handle_command("set music recognition to automatic"); self.assertEqual(settings_store.get("music_recognition_mode"), "automatic")

    def test_persists_across_restart(self):
        sv.handle_command("set reply style to detailed")
        settings_store._cache = None
        self.assertEqual(settings_store.get("reply_style"), "detailed")


class NotACommand(_Base):
    def test_ordinary_sentences_pass_through(self):
        for text in ("what is dark mode", "play some music", "set a timer for 10 minutes", "open discord",
                     "set accent to banana", "set theme to purple rain", "compact", "make it dark in here",
                     "turn off the lights", "what's the weather", "set volume to 20", "", "speak"):
            self.assertIsNone(sv.parse(text), text)
            self.assertIsNone(sv.handle_command(text), text)
        self.assertEqual(self.pushed, [])
