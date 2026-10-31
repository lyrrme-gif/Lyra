"""Phase 2: hotkey parsing and the hold / double-tap / hands-free gesture machine (no hooks, no Windows)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation_hotkey import Gesture, HotkeySpec, parse_hotkey  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_function_keys_need_no_modifier(self):
        self.assertEqual(parse_hotkey("f9"), HotkeySpec(frozenset(), 0x78, frozenset()))
        self.assertEqual(parse_hotkey("F24").main, 0x87)
        self.assertIsNone(parse_hotkey("f25"))

    def test_modifier_plus_key(self):
        spec = parse_hotkey("Ctrl + Alt + D")
        self.assertEqual((spec.mods, spec.main), (frozenset({"ctrl", "alt"}), 0x44))
        self.assertEqual(parse_hotkey("ctrl+shift+space").main, 0x20)

    def test_bare_letters_and_shift_only_letters_are_refused(self):
        for bad in ("d", "a", "5", "space", "shift+a", "shift+space"):
            self.assertIsNone(parse_hotkey(bad), bad)

    def test_never_esc_win_or_altgr(self):
        for bad in ("esc", "ctrl+esc", "win+d", "ralt", "ctrl+ralt", "alt+ralt", "lwin"):
            self.assertIsNone(parse_hotkey(bad), bad)

    def test_chords_of_specific_keys(self):
        self.assertEqual(parse_hotkey("ctrl+alt").chord, frozenset({0xA2, 0xA4}))     # the classic preset
        self.assertEqual(parse_hotkey("lctrl+lalt").chord, frozenset({0xA2, 0xA4}))
        self.assertEqual(parse_hotkey("rctrl").chord, frozenset({0xA3}))
        self.assertEqual(parse_hotkey("rctrl+rshift").chord, frozenset({0xA3, 0xA1}))

    def test_a_lone_everyday_modifier_is_refused(self):
        for bad in ("ctrl", "alt", "shift", "lctrl", "lshift"):
            self.assertIsNone(parse_hotkey(bad), bad)

    def test_left_alt_alone_is_the_default_dictation_key(self):
        self.assertIsNotNone(parse_hotkey("lalt"))

    def test_chords_can_be_disallowed(self):
        self.assertIsNone(parse_hotkey("rctrl", allow_chord=False))
        self.assertIsNotNone(parse_hotkey("ctrl+alt+v", allow_chord=False))

    def test_garbage(self):
        for bad in ("", "  ", "+", "ctrl+ctrl", "f1+f2", "ctrl+banana", "rctrl+f9", None):
            self.assertIsNone(parse_hotkey(bad), repr(bad))


class GestureTests(unittest.TestCase):
    def test_plain_hold(self):
        g = Gesture()
        self.assertEqual(g.on_press(0.0), ["start"])
        self.assertEqual(g.on_release(2.0), ["stop"])
        self.assertEqual(g.state, "idle")

    def test_double_tap_goes_hands_free_and_a_tap_pastes(self):
        g = Gesture()
        self.assertEqual(g.on_press(0.00), ["start"])
        self.assertEqual(g.on_release(0.10), [])                 # a tap: keep listening for a moment
        self.assertEqual(g.on_press(0.25), ["handsfree"])
        self.assertEqual(g.on_release(0.35), [])                 # released, still listening
        self.assertEqual(g.state, "handsfree")
        self.assertEqual(g.on_tick(30.0), [])                    # nothing times out while hands-free
        self.assertEqual(g.on_press(31.0), ["stop"])             # tap = stop + paste
        self.assertEqual(g.on_release(31.1), [])                 # its release is ignored
        self.assertEqual(g.state, "idle")

    def test_single_quick_tap_is_discarded_after_the_window(self):
        g = Gesture()
        g.on_press(0.0); g.on_release(0.1)
        self.assertEqual(g.on_tick(0.3), [])                     # still waiting for a second tap
        self.assertEqual(g.on_tick(0.7), ["cancel"])
        self.assertEqual(g.state, "idle")

    def test_late_second_press_starts_a_fresh_dictation(self):
        g = Gesture()
        g.on_press(0.0); g.on_release(0.1)
        self.assertEqual(g.on_press(2.0), ["cancel", "start"])
        self.assertEqual(g.on_release(4.0), ["stop"])

    def test_handsfree_can_be_switched_off(self):
        g = Gesture(handsfree=False)
        g.on_press(0.0)
        self.assertEqual(g.on_release(0.1), ["stop"])            # a short hold is simply a short dictation

    def test_esc_cancels_in_every_running_state_and_swallows_the_release(self):
        g = Gesture()
        g.on_press(0.0)
        self.assertEqual(g.on_cancel(), ["cancel"])              # key still down
        self.assertEqual(g.on_release(1.0), [])                  # must not "stop" a dictation that is gone
        self.assertEqual(g.state, "idle")
        g.on_press(2.0); g.on_release(2.1); g.on_press(2.2); g.on_release(2.3)   # hands-free, key up
        self.assertEqual(g.on_cancel(), ["cancel"])
        self.assertEqual(g.on_press(3.0), ["start"])             # next press is a normal new dictation
        self.assertEqual(g.on_cancel(), ["cancel"]); g.on_release(3.1)
        self.assertEqual(g.on_cancel(), [])                      # nothing to cancel when idle

    def test_chord_timing_gives_a_longer_tap(self):
        g = Gesture(); g.use_chord_timing(True)
        g.on_press(0.0)
        self.assertEqual(g.on_release(0.5), [])                  # 0.5 s touch (chords need 0.3 s to register)
        self.assertEqual(g.on_press(0.9), ["handsfree"])
        g2 = Gesture(); g2.use_chord_timing(False)
        g2.on_press(0.0)
        self.assertEqual(g2.on_release(0.5), ["stop"])           # for a normal key 0.5 s is a real hold

    def test_repeated_press_while_held_does_nothing(self):
        g = Gesture()
        g.on_press(0.0)
        self.assertEqual(g.on_press(0.05), [])


if __name__ == "__main__":
    unittest.main()
