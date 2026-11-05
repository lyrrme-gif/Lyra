"""circle_ask.DoubleTapDetector: Circle to ask = tap Ctrl, press it again and hold (pure timing logic)."""
import unittest

import engine.features.circle_ask as ca  # noqa: E402


def run(events):
    """events: (time, ctrl_down, other_key_down). Returns the times at which the detector fired."""
    det = ca.DoubleTapDetector()
    return [t for t, ctrl, other in events if det.update(t, ctrl, other)]


def held(t0, t1, step=0.05, other=False):
    out, t = [], t0
    while t <= t1 + 1e-9:
        out.append((round(t, 3), True, other))
        t += step
    return out


class TapThenHoldTests(unittest.TestCase):
    def test_tap_then_hold_fires_once_while_held(self):
        ev = held(0.0, 0.1) + [(0.15, False, False)] + held(0.25, 1.0)
        fired = run(ev)
        self.assertEqual(len(fired), 1)
        self.assertGreaterEqual(fired[0], 0.25 + ca.HOLD_SECONDS - 1e-9)    # only after the second press is held a moment

    def test_two_quick_taps_without_holding_do_nothing(self):
        ev = held(0.0, 0.1) + [(0.15, False, False)] + held(0.25, 0.3) + [(0.35, False, False), (1.0, False, False)]
        self.assertEqual(run(ev), [])

    def test_one_tap_or_one_hold_does_nothing(self):
        self.assertEqual(run(held(0.0, 0.1) + [(0.2, False, False), (1.0, False, False)]), [])
        self.assertEqual(run(held(0.0, 1.0)), [])

    def test_a_long_first_press_is_not_a_tap(self):
        ev = held(0.0, 0.6) + [(0.65, False, False)] + held(0.75, 1.5)
        self.assertEqual(run(ev), [])

    def test_second_press_too_late_is_a_first_tap_again(self):
        ev = held(0.0, 0.1) + [(0.15, False, False)] + held(1.0, 1.1) + [(1.15, False, False)] + held(1.25, 2.0)
        self.assertEqual(len(run(ev)), 1)                          # the pair (1.0 tap, 1.25 hold) works; (0.0, 1.0) did not

    def test_ctrl_with_another_key_never_counts(self):
        ev = held(0.0, 0.1, other=True) + [(0.15, False, False)] + held(0.25, 1.0)          # Ctrl+C first
        self.assertEqual(run(ev), [])
        ev = held(0.0, 0.1) + [(0.15, False, False)] + held(0.25, 1.0, other=True)          # tap, then Ctrl+C held
        self.assertEqual(run(ev), [])

    def test_letting_go_and_holding_again_needs_a_new_tap(self):
        ev = held(0.0, 0.1) + [(0.15, False, False)] + held(0.25, 0.8) + [(0.85, False, False)] + held(0.95, 1.6)
        self.assertEqual(len(run(ev)), 1)

    def test_both_ctrl_keys_are_accepted(self):
        self.assertEqual(sorted(ca._CTRL), [0xA2, 0xA3])


if __name__ == "__main__":
    unittest.main()
