"""Tests for alarms / text-less reminders (schedule_parse + scheduler) and game mode. From the backend folder:

    python -m unittest tests.test_alarm_gamemode -v

Windows APIs, the notification bridge and the chime are fakes; these check the logic (parsing, roll-over to
tomorrow, persistence across a restart, firing, the "can't do that yet" rule, and that game mode stops and
restores exactly the key listeners that were running).
"""
import json
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import engine.features.schedule_parse as sp

NOW = datetime(2026, 10, 18, 16, 30)


class ParseAlarm(unittest.TestCase):
    def due(self, text, now=NOW):
        r = sp.parse_alarm(text, now)
        self.assertIsNotNone(r, text)
        return r

    def test_alarm_rolls_to_tomorrow(self):
        r = self.due("set an alarm at 4pm")
        self.assertEqual(r["due"], datetime(2026, 10, 19, 16, 0))
        self.assertTrue(r["alarm"])
        self.assertEqual(sp.say_clock(r["due"]) + sp.say_day(r["due"], NOW), "4:00 PM tomorrow")

    def test_reminder_without_text(self):
        r = self.due("remind me at 5pm")
        self.assertEqual((r["due"], r["text"], r["alarm"]), (datetime(2026, 10, 18, 17, 0), "Reminder", False))

    def test_wake_me_is_morning(self):
        self.assertEqual(self.due("wake me up at 6:30")["due"], datetime(2026, 10, 19, 6, 30))

    def test_relative(self):
        self.assertEqual(self.due("set an alarm in 20 minutes")["due"], datetime(2026, 10, 18, 16, 50))

    def test_missing_time_asks(self):
        self.assertIn("error", sp.parse_alarm("set an alarm", NOW))
        self.assertIn("error", sp.parse_alarm("remind me tomorrow", NOW))

    def test_not_ours(self):
        for t in ("remind me to call Mom at 5pm", "remind me to call mum in 20 minutes", "set the alarm to away",
                  "what alarms do I have", "alarm settings", "remind me every day at 9 to take pills"):
            self.assertIsNone(sp.parse_alarm(t, NOW), t)


class SchedulerAlarm(unittest.TestCase):
    def setUp(self):
        from engine.core import paths; from engine.features import scheduler
        self.sched = scheduler
        self.tmp = Path(tempfile.mkdtemp())
        self._data_path = paths.data_path
        paths.data_path = lambda name: self.tmp / name
        scheduler.reset_for_tests()
        self.sent, self.chimes = [], []
        scheduler._notify = lambda app, kind, sender, text, force=False: self.sent.append((sender, text, force))
        scheduler._publish_cb = None
        from engine.features import timers
        timers._player = lambda path: self.chimes.append(path)

    def tearDown(self):
        from engine.core import paths; from engine.features import timers
        paths.data_path = self._data_path
        timers._player = None
        self.sched.reset_for_tests()

    def test_create_persist_fire(self):
        s = self.sched
        now = datetime.now().replace(second=0, microsecond=0)
        due = now + timedelta(hours=2)
        clock = f"{due.hour}:{due.minute:02d}"
        reply = s.handle_command(f"remind me at {clock}", now)
        self.assertTrue(reply.startswith("Reminder set for "), reply)
        self.assertIn("PM" if due.hour >= 12 else "AM", reply)
        # survives a restart: drop memory, reload from disk
        s.reset_for_tests()
        items = json.loads((self.tmp / "schedule.json").read_text())["items"]
        self.assertEqual(len(items), 1)
        s._tick(items[0]["due"] + 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "Reminder")
        self.assertEqual(len(self.chimes), 1)
        self.assertEqual(s._load()["items"], [])

    def test_alarm_is_forced_and_cancel(self):
        s = self.sched
        now = datetime.now().replace(second=0, microsecond=0)
        due = now + timedelta(hours=3)
        self.assertTrue(s.handle_command(f"set an alarm for {due.hour}:{due.minute:02d}", now).startswith("Alarm set for "))
        self.assertEqual(s.handle_command("cancel the alarm", now), "Cancelled 1 alarm.")
        s.handle_command(f"set an alarm for {due.hour}:{due.minute:02d}", now)
        item = s._load()["items"][0]
        s._tick(item["due"] + 1)
        self.assertEqual(self.sent[0], ("Alarm", f"{due.hour % 12 or 12}:{due.minute:02d} {'AM' if due.hour < 12 else 'PM'}", True))


class CantDoYet(unittest.TestCase):
    def test_rule(self):
        try:
            from engine.brain import brain
        except ImportError as e:                      # openai / audio packages are not installed here
            self.skipTest(str(e))
        f = brain._is_unsupported_action
        self.assertTrue(f("fold my laundry", "action"))
        self.assertTrue(f("can you please fold my laundry", "action"))
        self.assertFalse(f("how do I fold a shirt", "action"))
        self.assertFalse(f("fold my laundry", "chat"))
        self.assertFalse(f("who won the match", "research"))


class GameMode(unittest.TestCase):
    def setUp(self):
        from engine.core import game_mode
        self.gm = game_mode
        self.log = []
        self.mods = {}
        for name in ("ptt", "circle_ask", "tab_wheel"):
            m = types.ModuleType(name)
            m.running = name != "tab_wheel"          # the tab wheel was switched off by the user
            m.is_running = lambda m=m: m.running
            m.stop = lambda m=m, n=name: (setattr(m, "running", False), self.log.append(("stop", n)))
            m.start = lambda m=m, n=name: (setattr(m, "running", True), self.log.append(("start", n)))
            self.mods[name] = m
            sys.modules[game_mode._MODULE_PATHS[name]] = m
        self.events = []
        gm = game_mode
        self._wake = (gm._wake_start, gm._wake_stop)
        gm._wake_start = lambda: self.log.append(("wake", "on"))
        gm._wake_stop = lambda: self.log.append(("wake", "off"))
        gm._push = self.events.append

    def tearDown(self):
        gm = self.gm
        gm._wake_start, gm._wake_stop = self._wake
        gm._active = False
        gm._suspended, gm._push = [], None
        gm._deferred.clear()
        for n in ("ptt", "circle_ask", "tab_wheel"):
            sys.modules.pop(self.gm._MODULE_PATHS[n], None)

    def test_enter_leave_restores_only_what_ran(self):
        gm = self.gm
        gm.enter("cs2.exe")
        self.assertTrue(gm.is_active())
        self.assertEqual([x for x in self.log if x[0] == "stop"], [("stop", "ptt"), ("stop", "circle_ask")])
        self.assertTrue(all(not m.running for m in self.mods.values()))
        self.assertEqual(self.events[-1]["on"], True)
        gm.leave()
        self.assertEqual([x for x in self.log if x[0] == "start"], [("start", "ptt"), ("start", "circle_ask")])
        self.assertFalse(self.mods["tab_wheel"].running)
        self.assertEqual(self.events[-1]["on"], False)

    def test_deferred_start_runs_after_game(self):
        gm = self.gm
        gm.enter("x.exe")
        gm.defer("tab_wheel")
        gm.leave()
        self.assertIn(("start", "tab_wheel"), self.log)

    def test_game_list(self):
        from engine.core import settings_store
        old = settings_store.get
        settings_store.get = lambda k, d=None: "CS2, Valorant.exe" if k == "game_mode_apps" else old(k, d)
        try:
            self.assertEqual(self.gm.game_list(), {"cs2", "cs2.exe", "valorant.exe"})
        finally:
            settings_store.get = old


if __name__ == "__main__":
    unittest.main()
