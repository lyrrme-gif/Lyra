"""The chat shortcut (default Ctrl+Win, set in Settings -> Shortcuts) opens Lyra's chat bar from anywhere
(ptt.py keyboard hook). Offline: no hook, no Windows.
From the backend folder:   python -m pytest tests/test_ctrl_t_chat.py -q
"""
import sys
import types
import unittest
from unittest import mock

from engine.voice import ptt  # noqa: E402

CTRL, T, SHIFT, ALT, WIN = 0xA2, 0x54, 0x10, 0x12, 0x5B


class WinAltT(unittest.TestCase):
    def setUp(self):
        ptt._down.clear()
        ptt._chat_held = False
        ptt._chat_spec = ptt.parse_chat_combo("alt+meta+t")
        ptt._hook_hold = False
        while not ptt._events.empty():
            ptt._events.get_nowait()
        self.held = set()
        p = mock.patch.object(ptt, "_async_down", side_effect=lambda vk: vk in self.held)
        p.start()
        self.addCleanup(p.stop)

    def _posted(self):
        out = []
        while not ptt._events.empty():
            out.append(ptt._events.get_nowait()[0])
        return out

    def _press(self, *vks):
        for vk in vks:
            self.held.add(vk)
            ptt._on_key(True, vk)

    def test_win_alt_t_posts_chat(self):
        self._press(WIN, ALT)
        ptt._on_key(True, T)
        self.assertEqual(self._posted(), ["chat"])

    def test_key_repeat_posts_once(self):
        self._press(WIN, ALT)
        for _ in range(5):
            ptt._on_key(True, T)
        self.assertEqual(self._posted(), ["chat"])
        ptt._on_key(False, T)
        ptt._on_key(True, T)
        self.assertEqual(self._posted(), ["chat"])

    def test_plain_ctrl_t_is_left_alone_by_default(self):
        self._press(CTRL)
        ptt._on_key(True, T)
        self.assertEqual(self._posted(), [])

    def test_t_alone_win_t_alt_t_do_nothing(self):
        for mods in ((), (WIN,), (ALT,)):
            ptt._down.clear(); ptt._chat_held = False; self.held = set()
            self._press(*mods)
            ptt._on_key(True, T)
            ptt._on_key(False, T)
            self.assertEqual(self._posted(), [], mods)

    def test_combos_with_ctrl_or_shift_do_nothing(self):
        for extra in (CTRL, SHIFT):
            ptt._down.clear(); ptt._chat_held = False; self.held = set()
            self._press(WIN, ALT, extra)
            ptt._on_key(True, T)
            ptt._on_key(False, T)
            self.assertNotIn("chat", self._posted(), extra)

    def test_injected_t_is_ignored(self):
        self._press(WIN, ALT)
        ptt._on_key(True, T, injected=True)
        self.assertEqual(self._posted(), [])

    def test_opens_the_chat_view_whatever_is_in_front(self):
        bridge = mock.Mock()
        with mock.patch.dict(sys.modules, {"engine.bridge.ui_bridge": bridge}):
            ptt._open_chat_bar()
        bridge.push_open_view.assert_called_once_with("prompt")

    def test_a_recorded_combo_is_used(self):
        ptt._chat_spec = ptt.parse_chat_combo("ctrl+t")      # the user recorded Ctrl+T in Settings
        self._press(CTRL)
        ptt._on_key(True, T)
        self.assertEqual(self._posted(), ["chat"])
        ptt._on_key(False, T)
        self.held.clear(); ptt._down.clear()
        self._press(WIN, ALT)                                # the old default no longer fires
        ptt._on_key(True, T)
        self.assertEqual(self._posted(), [])

    def test_switched_off_does_nothing(self):
        ptt._chat_spec = None
        self._press(WIN, ALT)
        ptt._on_key(True, T)
        self.assertEqual(self._posted(), [])

    def test_parse(self):
        self.assertEqual(ptt.parse_chat_combo("alt+meta+t"), (frozenset({"alt", "meta"}), 0x54))
        self.assertEqual(ptt.parse_chat_combo("Ctrl + Shift + F5"), (frozenset({"ctrl", "shift"}), 0x74))
        self.assertEqual(ptt.parse_chat_combo("ctrl+space"), (frozenset({"ctrl"}), 0x20))
        for bad in ("", "t", "ctrl", "ctrl+a+b", "ctrl+f13", "alt+???"):
            self.assertIsNone(ptt.parse_chat_combo(bad), bad)

    def test_settings_read_and_off_switch(self):
        from engine.core import settings_store
        with mock.patch.object(settings_store, "get", side_effect=lambda k, d=None: {"shortcut_typing_enabled": False}.get(k, d)):
            ptt._refresh_chat_spec()
        self.assertIsNone(ptt._chat_spec)
        with mock.patch.object(settings_store, "get", side_effect=lambda k, d=None: {"shortcut_typing_combo": "ctrl+alt+j"}.get(k, d)):
            ptt._refresh_chat_spec()
        self.assertEqual(ptt._chat_spec, (frozenset({"ctrl", "alt"}), ord("J")))
        with mock.patch.object(settings_store, "get", side_effect=lambda k, d=None: None):
            ptt._refresh_chat_spec()
        self.assertEqual(ptt._chat_spec, (frozenset({"ctrl", "meta"}), 0))   # default: Ctrl+Win


if __name__ == "__main__":
    unittest.main()
