"""hotkey_spec: reading Settings' key combos for the listeners (pure logic, runs anywhere)."""
import unittest

import engine.core.hotkey_spec as hs  # noqa: E402


def keys(*vks):
    held = set(vks)
    return lambda v: v in held


class ParseTests(unittest.TestCase):
    def test_wheel_default(self):
        s = hs.parse("ctrl+capslock")
        self.assertEqual((s.key_vk, sorted(s.families)), (0x14, ["ctrl"]))
        self.assertTrue(s.all_down(keys(0xA3, 0x14)))                  # right Ctrl counts for "ctrl"
        self.assertFalse(s.all_down(keys(0xA2)))
        self.assertTrue(s.extra_mods_down(keys(0xA2, 0x14, 0xA0)))     # Shift is not part of it
        self.assertFalse(s.extra_mods_down(keys(0xA2, 0x14)))

    def test_one_specific_modifier(self):
        s = hs.parse("lctrl")
        self.assertTrue(s.all_down(keys(0xA2)))
        self.assertFalse(s.all_down(keys(0xA3)))
        self.assertIsNone(s.register_args())                           # RegisterHotKey cannot do left-only
        self.assertNotIn(0xA3, s.keys)

    def test_recording_defaults_register_with_windows(self):
        self.assertEqual(hs.parse("ctrl+alt+r").register_args(), (0x0002 | 0x0001, 0x52))
        self.assertEqual(hs.parse("ctrl+shift+f9").register_args(), (0x0002 | 0x0004, 0x78))
        self.assertIsNone(hs.parse("ctrl+meta").register_args())       # modifiers only

    def test_unusable_combos(self):
        for bad in ("", None, "ctrl", "r+t", "ctrl+banana", "lctrl+r", "ctrl+ctrl+r", "+"):
            self.assertIsNone(hs.parse(bad), bad)

    def test_other_keys_exclude_the_combo(self):
        s = hs.parse("ctrl+alt+d")
        o = set(s.other_vks)
        self.assertNotIn(0x44, o)
        self.assertNotIn(0xA2, o)
        self.assertNotIn(0x11, o)
        self.assertIn(0x41, o)
        self.assertIn(0x5B, hs.parse("lctrl").other_vks)               # Win is "something else" unless the combo has it


if __name__ == "__main__":
    unittest.main()
