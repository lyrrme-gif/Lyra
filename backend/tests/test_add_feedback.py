"""Item 14: add requests (to-do, reminder, alarm, timer, event) tell the island what happened + how it went."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tab_sync  # noqa: E402


class AddResult(unittest.TestCase):
    def test_success_kinds(self):
        cases = [
            ("add milk to my to-do list", "Added milk to your to-do list.", "", ("todo", "todo", "ok")),
            ("set a timer for 5 minutes", "Timer set for 5 minutes.", "timer", ("timer", "timer", "ok")),
            ("set a pasta timer for 9 minutes", "Pasta timer set for 9 minutes.", "timer", ("timer", "timer", "ok")),
            ("start a stopwatch", "Stopwatch started.", "timer", ("stopwatch", "timer", "ok")),
            ("start a focus session", "Focus session started: 25 minutes, then a 5 minute break.", "timer", ("focus", "timer", "ok")),
            ("remind me to call mom tomorrow at 9", "Okay, I'll remind you to call mom tomorrow at 9:00.", "schedule", ("reminder", "calendar", "ok")),
            ("set an alarm for 7", "Alarm set for 7:00 AM.", "schedule", ("alarm", "calendar", "ok")),
            ("add dentist to my calendar tomorrow at 9", "Added dentist to your calendar, tomorrow at 9:00.", "schedule", ("event", "calendar", "ok")),
        ]
        for cmd, reply, hint, (kind, view, status) in cases:
            r = tab_sync.add_result(cmd, reply, hint)
            self.assertIsNotNone(r, cmd)
            self.assertEqual((r["kind"], r["view"], r["status"]), (kind, view, status), cmd)

    def test_failures(self):
        cases = [
            ("set a timer for 30 hours", "I can set timers up to 24 hours.", "timer", ("timer", "timer")),
            ("set a timer for 5 minutes", "That's too many timers — cancel one first.", "timer", ("timer", "timer")),
            ("remind me to x yesterday", "That time has already passed.", "schedule", ("reminder", "calendar")),
            ("set an alarm for 7", "That's already scheduled.", "schedule", ("alarm", "calendar")),
            ("add milk to my to-do list", "Sorry, I couldn't add that.", "", ("todo", "todo")),
        ]
        for cmd, reply, hint, (kind, view) in cases:
            r = tab_sync.add_result(cmd, reply, hint)
            self.assertIsNotNone(r, cmd)
            self.assertEqual((r["kind"], r["view"], r["status"]), (kind, view, "fail"), cmd)
            self.assertTrue(r["text"])

    def test_not_an_add(self):
        # questions, edits and clarifying prompts never play an add / fail animation
        self.assertIsNone(tab_sync.add_result("whats on my todo list", "You have 2 tasks.", ""))
        self.assertIsNone(tab_sync.add_result("what's the weather", "Sorry, I couldn't reach the weather service.", ""))
        self.assertIsNone(tab_sync.add_result("set a timer", "How long should I set the timer for?", "timer"))
        self.assertIsNone(tab_sync.add_result("cancel the timer", "Cancelled the timer.", "timer"))
        self.assertIsNone(tab_sync.add_result("how much time is left", "3 minutes left on the timer.", "timer"))
        self.assertIsNone(tab_sync.add_result("set a timer", "", "timer"))

    def test_added_for_is_compatible(self):
        self.assertEqual(tab_sync.added_for("add milk to my to-do list", "Added milk to your to-do list."), ("todo", "Added: milk"))
        self.assertIsNone(tab_sync.added_for("set a timer for 99 hours", "I can set timers up to 24 hours.", "timer"))

    def test_open_for_pushes_fail_and_ok(self):
        import ui_bridge
        with mock.patch.object(ui_bridge, "_push_event") as push:
            tab_sync.open_for("set a timer for 5 minutes", "Timer set for 5 minutes.", "timer")
            tab_sync.open_for("set a timer for 5 minutes", "That's too many timers — cancel one first.", "timer")
            tab_sync.open_for("what's the weather", "Sorry, no.", "")
        ev = [c.args[0] for c in push.call_args_list]
        self.assertEqual(len(ev), 2)
        self.assertEqual((ev[0]["type"], ev[0]["view"], ev[0]["kind"], ev[0]["status"]), ("added", "timer", "timer", "ok"))
        self.assertEqual((ev[1]["view"], ev[1]["kind"], ev[1]["status"]), ("timer", "timer", "fail"))

    def test_push_added_sanitises(self):
        import ui_bridge
        with mock.patch.object(ui_bridge, "_push_event") as push:
            ui_bridge.push_added("todo", "x", "bogus", "weird")
            ui_bridge.push_added("nope", "x")
        self.assertEqual(push.call_count, 1)
        ev = push.call_args.args[0]
        self.assertEqual((ev["kind"], ev["status"]), ("", "ok"))


if __name__ == "__main__":
    unittest.main()
