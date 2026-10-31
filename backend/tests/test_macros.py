"""Tests for macros.py (Step 9). From the backend folder:  python -m unittest tests.test_macros -v

Uses the real permissions.PermissionManager (stub ui/voice) and a FAKE input backend, so no mouse or
keyboard is touched and no display is needed. Real pynput behaviour is NOT covered here."""
import logging
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

CHAT, ACTIVITY = [], []
_ui = types.ModuleType("ui"); _ui.log = logging.getLogger("t"); _ui.INPUT_MODE = "text"
_ui.trace = lambda m: None; _ui.set_state = lambda s: None
_ui.print_chat = lambda role, text, detail="": CHAT.append(text)
sys.modules["ui"] = _ui
_v = types.ModuleType("voice"); _v.speak = lambda *a, **k: None; sys.modules["voice"] = _v
_b = types.ModuleType("ui_bridge")
_b.push_activity = lambda kind, title, **kw: ACTIVITY.append((title, kw.get("phase"), kw.get("subtitle")))
sys.modules["ui_bridge"] = _b
_s = types.ModuleType("settings_store"); _s.ON = True; _s.feature_on = lambda n: _s.ON; sys.modules["settings_store"] = _s
_tools = types.ModuleType("tools"); _tools.__path__ = []
_reg = types.ModuleType("tools.registry"); _reg.get_tool = lambda t: None
sys.modules["tools"] = _tools; sys.modules["tools.registry"] = _reg

import macros  # noqa: E402
from permissions import PermissionManager  # noqa: E402


class FakeInput:
    def __init__(self, pw=False, size=(1920, 1080)):
        self.pw, self.size = pw, size
        self.emit = None; self.esc_cb = None
        self.done = []; self.released = None; self.stopped = False
    def start_recording(self, emit):
        self.emit = emit
        return lambda: setattr(self, "stopped", True)
    def listen_esc(self, cb):
        self.esc_cb = cb
        return lambda: None
    def do(self, ev):
        self.done.append((round(time.monotonic(), 3), ev))
        if ev["k"] == "key" and ev["key"] == "Key.esc" and ev["d"] and self.esc_cb:
            self.esc_cb(False)               # the OS listener hears replay's own Esc too
    def release_all(self, keys, btns): self.released = (set(keys), set(btns))
    def screen_size(self): return self.size
    def password_focused(self): return self.pw


def wait(cond, t=5):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond(): return True
        time.sleep(0.02)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lyra-macro-")).resolve()
        self.store = self.tmp / "macros.json"
        self._sp = macros._store_path; macros._store_path = lambda: self.store
        self.fake = FakeInput()
        macros._input_factory = lambda: self.fake
        macros._rec = None; macros._play = None
        self._lead = macros.LEAD_IN_S; macros.LEAD_IN_S = 0.05
        _s.ON = True
        CHAT.clear(); ACTIVITY.clear()
        import permissions; permissions._pending_text_confirmation = None
    def tearDown(self):
        macros._store_path = self._sp; macros._input_factory = None; macros.LEAD_IN_S = self._lead
        if macros._rec: macros._rec._alive = False
        if macros._play: macros._play.abort.set(); wait(lambda: macros._play is None)
        shutil.rmtree(self.tmp, ignore_errors=True)
    def typed(self, c):
        return macros.try_macro(c, {"confirm_callback": lambda cmd=c: macros.try_macro(cmd, {"confirmed": True})})
    def yes(self): return PermissionManager.resolve_text_confirmation("yes")
    def no(self): return PermissionManager.resolve_text_confirmation("no")
    def seed(self, name="demo", events=None, screen=(1920, 1080)):
        ev = events or [
            {"k": "click", "x": 10, "y": 20, "b": "left", "d": True, "t": 0.0},
            {"k": "click", "x": 10, "y": 20, "b": "left", "d": False, "t": 0.05},
            {"k": "key", "key": "h", "d": True, "t": 0.1}, {"k": "key", "key": "h", "d": False, "t": 0.15}]
        d = macros._load(); d["macros"][name.lower()] = {"name": name, "screen": list(screen) if screen else None,
                                                         "duration": ev[-1]["t"], "events": ev}
        macros._save(d)


class Recording(Base):
    def record(self, name="demo"):
        self.assertEqual(self.typed(f"record a macro called {name}"), "Awaiting typed confirmation.")
        self.assertIsNone(macros._rec)                       # nothing hooked before the yes
        self.assertIn("Recording", self.yes())
        self.assertIsNotNone(macros._rec)

    def test_record_stop_typed_trims_stop_click_and_typing(self):
        self.record()
        f = self.fake
        time.sleep(0.05); f.emit({"k": "click", "x": 5, "y": 6, "b": "left", "d": True})
        f.emit({"k": "click", "x": 5, "y": 6, "b": "left", "d": False})
        f.emit({"k": "key", "key": "a", "d": True}); f.emit({"k": "key", "key": "Key.shift", "d": True})  # shift left held
        f.emit({"k": "click", "x": 900, "y": 900, "b": "left", "d": True})      # click on the island chat box
        f.emit({"k": "key", "key": "s", "d": True}); f.emit({"k": "key", "key": "s", "d": False})
        r = macros.try_macro("stop recording", {})
        self.assertIn("Saved the macro demo: 6 steps", r)   # 4 recorded + 2 releases; self.assertIn("left out the last 3", r)
        ev = macros._load()["macros"]["demo"]["events"]
        self.assertEqual(ev[0]["t"], 0.0)
        self.assertEqual([e["k"] for e in ev], ["click", "click", "key", "key", "key", "key"])
        self.assertNotIn(("s", True), {(e.get("key"), e.get("d")) for e in ev})   # the typed "stop recording" is gone
        # shift and 'a' were still down: releases appended so nothing sticks on replay
        self.assertEqual({(e["key"], e["d"]) for e in ev if e["k"] == "key"},
                         {("a", True), ("Key.shift", True), ("a", False), ("Key.shift", False)})
        self.assertTrue(self.fake.stopped)
        self.assertEqual(macros._load()["macros"]["demo"]["screen"], [1920, 1080])

    def test_voice_stop_does_not_trim(self):
        self.record()
        self.fake.emit({"k": "click", "x": 1, "y": 1, "b": "left", "d": True})
        self.fake.emit({"k": "click", "x": 1, "y": 1, "b": "left", "d": False})
        r = macros.try_macro("stop recording", {"recognizer": 1, "source": 1})
        self.assertIn("2 steps", r); self.assertNotIn("left out", r)

    def test_decline_never_records(self):
        self.typed("record a macro called x"); self.assertEqual(self.no(), "Okay, cancelled.")
        self.assertIsNone(macros._rec); self.assertIsNone(self.fake.emit)

    def test_warning_text_mentions_passwords(self):
        self.typed("record a macro called x")
        import permissions
        self.assertIn("passwords", permissions._pending_text_confirmation["description"].lower())
        self.no()

    def test_password_keys_dropped(self):
        self.fake.pw = True
        self.record(); time.sleep(0.25)                      # poller notices the password box
        self.fake.emit({"k": "click", "x": 1, "y": 1, "b": "left", "d": True})
        self.fake.emit({"k": "key", "key": "p", "d": True}); self.fake.emit({"k": "key", "key": "p", "d": False})
        self.fake.emit({"k": "click", "x": 1, "y": 1, "b": "left", "d": False})
        r = macros.try_macro("stop recording", {"recognizer": 1, "source": 1})
        self.assertIn("skipped 2 keystrokes", r)
        keys = [e for e in macros._load()["macros"]["demo"]["events"] if e["k"] == "key"]
        self.assertEqual(keys, [])

    def test_no_detection_is_disclosed(self):
        self.fake.pw = None
        self.typed("record a macro called x")
        import permissions
        self.assertIn("can't detect password fields", permissions._pending_text_confirmation["description"])
        self.assertIn("don't type any", self.yes())

    def test_duplicate_and_empty(self):
        self.seed("demo")
        self.assertIn("already have a macro called Demo", macros.try_macro("record a macro called Demo", {}))
        self.record("fresh")
        self.assertIn("Nothing was recorded", macros.try_macro("stop recording", {"recognizer": 1, "source": 1}))
        self.assertNotIn("fresh", macros._load()["macros"])

    def test_event_cap_autosaves(self):
        old = macros.MAX_EVENTS; macros.MAX_EVENTS = 3
        try:
            self.record()
            for i in range(6): self.fake.emit({"k": "scroll", "x": 1, "y": 1, "dx": 0, "dy": 1})
            self.assertTrue(wait(lambda: "demo" in macros._load()["macros"]))
            self.assertEqual(len(macros._load()["macros"]["demo"]["events"]), 3)
            self.assertTrue(any("length limit" in c for c in CHAT))
        finally:
            macros.MAX_EVENTS = old

    def test_stop_when_idle_is_not_ours(self):
        self.assertIsNone(macros.try_macro("stop recording", {}))


class Replay(Base):
    def test_asks_first_then_replays_in_order_with_timing(self):
        self.seed()
        self.assertEqual(self.typed("run macro demo"), "Awaiting typed confirmation.")
        time.sleep(0.2); self.assertEqual(self.fake.done, [])          # nothing before the yes
        self.assertIn("Esc twice", self.yes())
        self.assertTrue(wait(lambda: macros._play is None))
        self.assertEqual([e["k"] + str(e.get("key", "")) for _, e in self.fake.done], ["click", "click", "keyh", "keyh"])
        ts = [t for t, _ in self.fake.done]
        self.assertGreater(ts[-1] - ts[0], 0.1)                        # recorded gaps respected (0.15 s)
        self.assertTrue(any("finished" in c for c in CHAT))
        self.assertEqual(ACTIVITY[-1][1], "success")

    def test_declined_never_replays(self):
        self.seed(); self.typed("play macro demo"); self.no(); time.sleep(0.2)
        self.assertEqual(self.fake.done, []); self.assertIsNone(macros._play)

    def test_esc_twice_aborts_and_releases(self):
        ev = [{"k": "key", "key": "Key.shift", "d": True, "t": 0.0}, {"k": "key", "key": "a", "d": True, "t": 0.05},
              {"k": "key", "key": "a", "d": False, "t": 6.0}]
        self.seed(events=ev); self.typed("run macro demo"); self.yes()
        self.assertTrue(wait(lambda: len(self.fake.done) >= 2))
        self.fake.esc_cb(False); self.assertIsNotNone(macros._play)    # one Esc is not enough
        self.fake.esc_cb(False)
        self.assertTrue(wait(lambda: macros._play is None, 3))
        self.assertEqual(len(self.fake.done), 2)                       # the 6 s step never ran
        self.assertEqual(self.fake.released[0], {"Key.shift", "a"})
        self.assertTrue(any("Stopped the macro demo after 2 of 3" in c for c in CHAT))

    def test_escs_inside_the_macro_do_not_abort(self):
        ev = [{"k": "key", "key": "Key.esc", "d": True, "t": 0.0}, {"k": "key", "key": "Key.esc", "d": False, "t": 0.02},
              {"k": "key", "key": "Key.esc", "d": True, "t": 0.04}, {"k": "key", "key": "Key.esc", "d": False, "t": 0.06},
              {"k": "key", "key": "x", "d": True, "t": 0.5}, {"k": "key", "key": "x", "d": False, "t": 0.52}]
        self.seed(events=ev); self.typed("run macro demo"); self.yes()
        self.assertTrue(wait(lambda: macros._play is None))
        self.assertEqual(len(self.fake.done), 6)

    def test_long_gaps_are_capped(self):
        old = macros.MAX_GAP_S; macros.MAX_GAP_S = 0.2
        try:
            ev = [{"k": "key", "key": "a", "d": True, "t": 0.0}, {"k": "key", "key": "a", "d": False, "t": 30.0}]
            self.seed(events=ev); self.typed("run macro demo"); self.yes()
            self.assertTrue(wait(lambda: macros._play is None, 4))
            self.assertEqual(len(self.fake.done), 2)
        finally:
            macros.MAX_GAP_S = old

    def test_screen_mismatch_refused_before_asking(self):
        self.seed(screen=(1366, 768))
        r = macros.try_macro("run macro demo", {})
        self.assertIn("recorded at 1366 by 768", r); self.assertEqual(self.fake.done, [])

    def test_unknown_macro_and_blocked_while_recording(self):
        self.assertIn("don't have any macros", macros.try_macro("run macro nope", {}))
        self.seed(); self.assertIn("I don't have a macro called nope", macros.try_macro("run macro nope", {}))
        macros._rec = types.SimpleNamespace(name="other")
        self.assertIn("recording other", macros.try_macro("run macro demo", {}))
        macros._rec = None

    def test_fuzzy_names_and_second_form(self):
        self.seed("Open Notepad")
        for p in ["run macro open notepad", "replay macro opennotepad", "run the open notepad macro", "play macro open note"]:
            self.assertEqual(self.typed(p), "Awaiting typed confirmation.", p); self.no()


class ManageAndRouting(Base):
    def test_list_rename_delete(self):
        self.assertIn("don't have any macros", macros.try_macro("list my macros", {}))
        self.seed("one"); self.seed("two")
        self.assertIn("one (4 steps)", macros.try_macro("list macros", {}))
        self.assertIn("Renamed one to uno", macros.try_macro("rename macro one to uno", {}))
        self.assertIn("already have a macro called two", macros.try_macro("rename macro uno to two", {}))
        self.assertEqual(self.typed("delete macro uno"), "Awaiting typed confirmation.")
        self.assertIn("uno", macros._load()["macros"]); self.yes()
        self.assertNotIn("uno", macros._load()["macros"])
        self.typed("delete macro two"); self.no(); self.assertIn("two", macros._load()["macros"])

    def test_not_ours(self):
        self.seed()
        for p in ["run chrome", "run the routine morning", "play some music", "delete macro zzz", "delete the file x.txt",
                  "stop recording", "record my screen", "rename my file to x"]:
            self.assertIsNone(macros.try_macro(p, {}), p)

    def test_feature_off(self):
        _s.ON = False; self.seed(); self.assertIsNone(macros.try_macro("list macros", {}))

    def test_routine_action_text(self):
        """A routine stores 'run macro X' as text and replays it through the same entry point."""
        try:
            import schedule_parse as sp
        except Exception as e:
            self.skipTest(f"schedule_parse not importable: {e}")
        r = sp.parse_routine("every morning at 9:00 run macro open notepad")
        self.assertEqual(r["actions"], ["run macro open notepad"])
        self.seed("open notepad")
        self.assertEqual(self.typed(r["actions"][0]), "Awaiting typed confirmation.")   # what scheduler -> chat chain does
        self.yes(); self.assertTrue(wait(lambda: macros._play is None))
        self.assertEqual(len(self.fake.done), 4)

    def test_key_codec(self):
        K = types.SimpleNamespace
        self.assertEqual(macros.encode_key(K(char="a")), "a")
        self.assertEqual(macros.encode_key(K(char=None, name="enter")), "Key.enter")
        self.assertEqual(macros.encode_key(K(char="\x01", name=None, vk=65)), "vk:65")
        kb = K(Key=K(enter="ENTER"), KeyCode=K(from_vk=lambda v: ("vk", v)))
        self.assertEqual(macros.decode_key("Key.enter", kb), "ENTER")
        self.assertEqual(macros.decode_key("vk:65", kb), ("vk", 65))
        self.assertEqual(macros.decode_key("z", kb), "z")

    def test_confirm_ids_are_confirm_tier(self):
        pm = PermissionManager()
        for pid in ("macro_record", "macro_run", "macro_delete"):
            self.assertNotIn(pid, pm._rules)


class Privacy(Base):
    def test_recording_never_starts_without_an_explicit_yes(self):
        # the global permission table lets unknown actions through; macros must not rely on it
        self.assertEqual(macros.try_macro("record a macro called x", {"confirm_callback": lambda: None}),
                         "Awaiting typed confirmation.")
        self.assertIsNone(macros._rec); self.assertIsNone(self.fake.emit)

    def test_replay_and_delete_also_need_a_yes(self):
        self.seed("demo")
        self.assertEqual(self.typed("run macro demo"), "Awaiting typed confirmation."); self.no()
        self.assertEqual(self.typed("delete macro demo"), "Awaiting typed confirmation."); self.no()
        self.assertEqual(len(macros.list_macros()), 1)

    def test_what_do_you_record_tells_the_rules_and_where(self):
        self.seed("demo")
        r = macros.try_macro("what do you record")
        for word in ("clicks", "plain text", "this PC", "never uploaded", "delete all macros", str(self.store)):
            self.assertIn(word, r)
        self.assertIn("1 saved macro.", r)
        self.assertIn("never uploaded", macros.try_macro("where are my macros stored"))

    def test_delete_all_asks_then_wipes_the_file(self):
        self.seed("a"); self.seed("b")
        self.assertEqual(self.typed("delete all macros"), "Awaiting typed confirmation.")
        self.assertTrue(self.store.exists())              # nothing deleted before the yes
        self.assertIn("Deleted 2 macros", self.yes())
        self.assertFalse(self.store.exists())
        self.assertEqual(macros.list_macros(), [])

    def test_delete_all_with_nothing_saved(self):
        self.assertIn("don't have any", macros.try_macro("delete all macros"))

    def test_delete_all_decline_keeps_everything(self):
        self.seed("a")
        self.typed("delete all macros"); self.no()
        self.assertEqual(len(macros.list_macros()), 1)

    def test_settings_helpers(self):
        self.seed("a"); self.seed("b")
        info = macros.privacy_info()
        self.assertEqual(info["path"], str(self.store)); self.assertEqual(len(info["macros"]), 2)
        self.assertTrue(macros.delete_macro("a")); self.assertFalse(macros.delete_macro("zzz"))
        self.assertEqual([m["name"] for m in macros.list_macros()], ["b"])
        self.assertEqual(macros.delete_all(), 1); self.assertFalse(self.store.exists())

    def test_saved_file_is_owner_only(self):
        import os, stat
        if os.name == "nt":
            self.skipTest("POSIX permissions")
        self.seed("a")
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
