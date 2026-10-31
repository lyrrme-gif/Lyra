"""Tests for guide_act.py. From the backend folder:  python -m unittest tests.test_guide_act -v

Everything that touches Windows (UI Automation, mouse, pynput) is replaced by FakeBackend, and the model
is replaced by a script of JSON replies, so no display, no mouse and no API key are needed. Real UI
Automation behaviour is NOT covered here."""
import json
import logging
import sys
import threading
import time
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

CHAT, EVENTS = [], []
_ui = types.ModuleType("ui"); _ui.log = logging.getLogger("t"); _ui.INPUT_MODE = "text"
_ui.trace = lambda m: None; _ui.set_state = lambda s: None
_ui.print_chat = lambda role, text, detail="": CHAT.append(text)
sys.modules["ui"] = _ui
_b = types.ModuleType("ui_bridge"); _b._push_event = lambda ev: EVENTS.append(ev); sys.modules["ui_bridge"] = _b
_s = types.ModuleType("settings_store")
_s.LEVEL = "step"; _s.GUIDE_ON = True
_s.get = lambda k, d=None: _s.LEVEL if k == "guide_level" else d
_s.feature_on = lambda n: _s.GUIDE_ON
sys.modules["settings_store"] = _s

import guide_act  # noqa: E402
from computer.uia import Ctl, Scan, WinInfo  # noqa: E402


def wait(cond, t=5.0):
    t0 = time.time()
    while time.time() - t0 < t:
        if cond():
            return True
        time.sleep(0.01)
    return False


def btn(name, x=100, y=100, type_="Button", actionable=True):
    return Ctl(name, type_, (x, y, x + 80, y + 24), True, actionable, object())


class FakeBackend:
    def __init__(self, ctls=None, title="Settings", process="settings.exe", elevated=False, texts=None,
                 password=False):
        self.ctls = ctls if ctls is not None else [btn("Privacy", 100, 100, "TabItem"), btn("Save", 100, 200),
                                                   btn("Delete", 100, 300), btn("Help", 100, 400)]
        self.title, self.process, self.elevated = title, process, elevated
        self.texts, self.password = texts or [], password
        self.acts, self.glides, self.shade, self.pos = [], [], 10, None
        self.change_on_act = True            # does an act change the picture?
        self.act_ok = True
        self.esc_cb = None
        self.esc_stopped = False
        self.on_frame = None
        self.pointer_override = None

    def available(self): return True
    def window(self): return WinInfo(self.title, self.process, 1, self.elevated, (0, 0, 1920, 1080), object())
    def scan(self, win): return Scan(list(self.ctls), list(self.texts), self.password)
    def state(self, ctl): return None
    def frame(self, win):
        if self.on_frame: self.on_frame()
        return bytes([self.shade]) * 2304
    def frac(self, x, y): return (x / 1920, y / 1080)
    def glide(self, x, y): self.glides.append((x, y)); self.pos = (x, y)
    def pointer(self): return self.pointer_override or self.pos
    def act(self, ctl, action, direction, win, physical=False):
        self.acts.append((ctl.name if ctl else None, action, physical))
        if self.act_ok and self.change_on_act:
            self.shade += 40
            self.title = self.title + "."          # a changed window title counts as a visible change
        return self.act_ok
    def listen_esc(self, cb):
        self.esc_cb = cb
        return lambda: setattr(self, "esc_stopped", True)


class Base(unittest.TestCase):
    def setUp(self):
        CHAT.clear(); EVENTS.clear()
        guide_act._act = None; guide_act._goal = None
        self.be = FakeBackend()
        guide_act._backend_factory = lambda: self.be
        self.replies = []
        self._c = guide_act._complete
        guide_act._complete = lambda prompt, max_tokens: (self.replies.pop(0), None) if self.replies else ("", "none left")
        self._vals = (guide_act.SETTLE_S, guide_act.MAX_ACT_STEPS, guide_act.CONFIRM_WAIT_S)
        guide_act.SETTLE_S = 0.01
        _s.LEVEL = "step"; _s.GUIDE_ON = True

    def tearDown(self):
        a = guide_act._act
        if a:
            a.cancel.set()
            if a.waiting: a.waiting.event.set()
        wait(lambda: guide_act._act is None or guide_act._act.ended, 2)
        guide_act._backend_factory = None
        guide_act._complete = self._c
        guide_act.SETTLE_S, guide_act.MAX_ACT_STEPS, guide_act.CONFIRM_WAIT_S = self._vals
        guide_act._act = None

    def say_json(self, **kw):
        self.replies.append(json.dumps(kw))

    def click(self, target, say="Clicking it."):
        self.say_json(say=say, action="click", target=target, direction=None)

    def done(self, say="All done."):
        self.say_json(say=say, action="done", target=None, direction=None)

    def ended(self): return guide_act._act is None
    def yes(self): return guide_act.answer("yes")


class Policy(unittest.TestCase):
    def w(self, **kw):
        d = dict(title="Settings", process="settings.exe", elevated=False)
        d.update(kw)
        return WinInfo(d["title"], d["process"], 1, d["elevated"], (0, 0, 10, 10))

    def test_clean_window_is_allowed(self):
        self.assertIsNone(guide_act.refusal(self.w(), Scan([], ["Home", "Display"])))

    def test_password_screen_refused(self):
        self.assertIn("sign-in", guide_act.refusal(self.w(), Scan([], [], has_password=True)))
        self.assertIn("sign-in", guide_act.refusal(self.w(title="Sign in to GitHub - Chrome"), Scan([], [])))

    def test_payment_page_refused(self):
        r = guide_act.refusal(self.w(title="Checkout"), Scan([], ["Card number", "Expiry date"]))
        self.assertIn("payment", r)
        self.assertIn("payment", guide_act.refusal(self.w(), Scan([], ["Pay with PayPal"])))

    def test_pay_button_refused_even_on_a_normal_page(self):
        r = guide_act.refusal(self.w(), Scan([], []), btn("Place order"))
        self.assertIn("payment button", r)
        self.assertIsNotNone(guide_act.refusal(self.w(), Scan([], []), btn("Pay now")))
        self.assertIsNone(guide_act.refusal(self.w(), Scan([], []), btn("Save")))

    def test_admin_prompts_refused(self):
        self.assertIn("administrator", guide_act.refusal(self.w(title="User Account Control"), Scan([], [])))
        self.assertIn("administrator", guide_act.refusal(self.w(process="consent.exe"), Scan([], [])))
        self.assertIn("administrator", guide_act.refusal(self.w(elevated=True), Scan([], [])))

    def test_risky_targets_need_a_yes_even_in_auto(self):
        self.assertTrue(guide_act.needs_confirm("auto", btn("Delete all")))
        self.assertTrue(guide_act.needs_confirm("auto", btn("Send")))
        self.assertFalse(guide_act.needs_confirm("auto", btn("Privacy")))
        self.assertTrue(guide_act.needs_confirm("step", btn("Privacy")))
        self.assertFalse(guide_act.needs_confirm("auto", None))


class Names(unittest.TestCase):
    def test_matching(self):
        ctls = [btn("Save"), btn("Save as"), btn("Volume"), btn("Open file")]
        self.assertEqual(guide_act.find_ctl(ctls, "save").name, "Save")
        self.assertEqual(guide_act.find_ctl(ctls, "the volume icon").name, "Volume")
        self.assertEqual(guide_act.find_ctl(ctls, "Open").name, "Open file")
        self.assertIsNone(guide_act.find_ctl(ctls, "microphone"))

    def test_a_shorter_name_does_not_stand_in_for_a_longer_request(self):
        self.assertIsNone(guide_act.find_ctl([btn("Open")], "open the settings tab"))

    def test_duplicate_names_get_numbers_and_are_addressable(self):
        a, b = btn("Close", 10, 10), btn("Close", 10, 90)
        labels = guide_act._labels(Scan([a, b], []))
        self.assertEqual([l for l, _ in labels], ["Close", "Close #2"])
        self.assertIs(guide_act._resolve("Close #2", labels), b)
        self.assertIsNone(guide_act._resolve("Close #3", labels))

    def test_only_pressable_controls_are_offered(self):
        labels = guide_act._labels(Scan([btn("Title", type_="Text", actionable=False), btn("OK")], []))
        self.assertEqual([l for l, _ in labels], ["OK"])

    def test_parse_decision(self):
        d = guide_act.parse_decision('```json\n{"say":"Opening it.","action":"CLICK","target":"Save"}\n```')
        self.assertEqual((d["action"], d["target"], d["direction"]), ("click", "Save", "down"))
        self.assertIsNone(guide_act.parse_decision('{"action":"type","target":"x"}'))   # she cannot type
        self.assertIsNone(guide_act.parse_decision("no json"))
        self.assertIsNone(guide_act.parse_decision('{"action":"run","target":"cmd.exe"}'))


class Levels(Base):
    def test_look_never_acts(self):
        _s.LEVEL = "look"
        self.assertIn("show only", guide_act.start("open privacy"))
        self.assertTrue(self.ended())
        self.assertEqual(self.be.acts, [])
        self.assertIsNone(self.be.esc_cb)               # nothing even listens

    def test_unknown_level_means_look(self):
        _s.LEVEL = "yolo"
        self.assertEqual(guide_act.level(), "look")

    def test_refuses_to_start_in_a_password_window(self):
        self.be.password = True
        self.assertIn("sign-in", guide_act.start("sign me in"))
        self.assertTrue(self.ended())
        self.assertEqual(self.be.acts, [])

    def test_no_pynput_means_no_acting(self):
        def boom(cb): raise ImportError("pynput")
        self.be.listen_esc = boom
        self.assertIn("pynput", guide_act.start("open privacy"))
        self.assertTrue(self.ended())

    def test_guide_switched_off(self):
        _s.GUIDE_ON = False
        self.assertIn("switched off", guide_act.start("open privacy"))


class StepMode(Base):
    def test_asks_then_does_exactly_that_step(self):
        self.click("Privacy", "I'll open the Privacy tab."); self.done("The Privacy tab is open.")
        guide_act.start("open the privacy tab")
        self.assertTrue(wait(guide_act.awaiting))
        self.assertEqual(self.be.acts, [])               # nothing happened before the yes
        self.assertIn("Yes or no?", CHAT[-1])
        self.assertEqual(self.yes(), "Okay.")
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [("Privacy", "click", False)])
        self.assertIn("The Privacy tab is open.", CHAT)
        self.assertTrue(self.be.esc_stopped)             # the Esc listener is released at the end

    def test_no_means_no(self):
        self.click("Privacy")
        guide_act.start("open the privacy tab")
        self.assertTrue(wait(guide_act.awaiting))
        self.assertEqual(guide_act.answer("no"), "Okay, I won't.")
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])

    def test_each_step_asks_again(self):
        self.click("Privacy"); self.click("Save"); self.done()
        guide_act.start("save the privacy settings")
        for expected in (["Privacy"], ["Privacy", "Save"]):
            self.assertTrue(wait(guide_act.awaiting))
            self.yes()
            self.assertTrue(wait(lambda: [a[0] for a in self.be.acts] == expected))
        self.assertTrue(wait(self.ended))

    def test_silence_times_out_without_acting(self):
        guide_act.CONFIRM_WAIT_S = 0.2
        self.click("Privacy")
        guide_act.start("open the privacy tab")
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])
        self.assertIn("didn't hear back", CHAT[-1])

    def test_unrelated_words_are_not_an_answer(self):
        self.click("Privacy")
        guide_act.start("open the privacy tab")
        self.assertTrue(wait(guide_act.awaiting))
        self.assertIsNone(guide_act.answer("what's the weather"))
        self.assertTrue(guide_act.awaiting())

    def test_cursor_is_shown_before_asking(self):
        self.click("Privacy")
        guide_act.start("open the privacy tab")
        self.assertTrue(wait(guide_act.awaiting))
        cur = [e for e in EVENTS if e.get("cmd") == "cursor" and e.get("kind") != "hide"]
        self.assertTrue(cur)
        self.assertEqual(cur[0]["kind"], "click")
        self.assertIn("Privacy", cur[0]["label"])
        self.assertTrue(0 <= cur[0]["x"] <= 1 and 0 <= cur[0]["y"] <= 1)
        self.assertEqual(self.be.glides[0], (140, 112))   # the real pointer went to the control's centre
        self.be.esc_cb()

    def test_cursor_is_hidden_at_the_end(self):
        self.done("Nothing to do.")
        guide_act.start("anything")
        self.assertTrue(wait(self.ended))
        self.assertEqual(EVENTS[-2:][0]["cmd"], "cursor"); self.assertEqual(EVENTS[-2]["kind"], "hide")


class AutoMode(Base):
    def setUp(self):
        super().setUp()
        _s.LEVEL = "auto"

    def test_one_go_ahead_then_whole_task(self):
        self.click("Privacy"); self.click("Save"); self.done("Saved.")
        reply = guide_act.start("save the privacy settings")
        self.assertIn("Yes to start", reply)
        self.assertTrue(guide_act.awaiting())
        time.sleep(0.15)
        self.assertEqual(self.be.acts, [])               # nothing before the go-ahead
        self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual([a[0] for a in self.be.acts], ["Privacy", "Save"])
        self.assertIn("Saved.", CHAT)

    def test_declining_the_start_does_nothing(self):
        self.click("Privacy")
        guide_act.start("x")
        self.assertEqual(guide_act.answer("no"), "Okay, I won't.")
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])

    def test_destructive_step_still_asks(self):
        self.click("Delete", "I'll delete it."); self.done()
        guide_act.start("clean up"); self.yes()
        self.assertTrue(wait(guide_act.awaiting))        # asked even though this is auto
        self.assertEqual(self.be.acts, [])
        guide_act.answer("no")
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])

    def test_step_cap(self):
        guide_act.MAX_ACT_STEPS = 3
        for _ in range(8):
            self.click("Help")
        guide_act.start("loop forever"); self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(len(self.be.acts), 3)
        self.assertIn("limit", CHAT[-1])

    def test_two_actions_that_change_nothing_stop_the_task(self):
        self.be.change_on_act = False
        for _ in range(6):
            self.click("Help")
        guide_act.start("press help"); self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(len(self.be.acts), 2)
        self.assertIn("nothing changed", CHAT[-1])

    def test_password_screen_that_appears_mid_task_stops_it(self):
        self.click("Help"); self.click("Save"); self.done()
        guide_act.start("go"); 
        orig = self.be.act
        def act(*a, **k):
            r = orig(*a, **k); self.be.password = True; return r
        self.be.act = act
        self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(len(self.be.acts), 1)           # the sign-in page appeared: nothing more was pressed
        self.assertIn("sign-in", CHAT[-1])

    def test_pay_button_is_never_pressed(self):
        self.be.ctls.append(btn("Place order", 300, 300))
        self.click("Place order"); self.done()
        guide_act.start("finish up"); self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])
        self.assertIn("payment", CHAT[-1])

    def test_model_cannot_press_a_control_that_is_not_listed(self):
        self.click("Format C:")
        guide_act.start("go"); self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])

    def test_user_grabbing_the_mouse_stops_her(self):
        self.click("Help")
        guide_act.start("go")
        self.be.pointer_override = (5, 5)                # far from where she put the pointer
        self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])
        self.assertIn("moved the mouse", CHAT[-1])

    def test_real_click_is_the_fallback_when_no_pattern_worked(self):
        self.click("Help"); self.done()
        guide_act.start("go")
        calls = []
        orig = self.be.act
        def act(ctl, action, direction, win, physical=False):
            calls.append(physical)
            return orig(ctl, action, direction, win, physical) if physical else False
        self.be.act = act
        self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(calls, [False, True])

    def test_scroll_has_no_target(self):
        self.say_json(say="Scrolling down.", action="scroll", target=None, direction="down"); self.done()
        guide_act.start("scroll"); self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [(None, "scroll", False)])


class Esc(Base):
    def test_esc_while_waiting_for_a_yes(self):
        _s.LEVEL = "step"
        self.click("Privacy")
        guide_act.start("go")
        self.assertTrue(wait(guide_act.awaiting))
        self.be.esc_cb()
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])
        self.assertIn("Stopped.", CHAT)
        self.assertTrue(self.be.esc_stopped)

    def test_esc_right_before_the_action_wins(self):
        _s.LEVEL = "auto"
        self.click("Help"); self.click("Save"); self.done()
        guide_act.start("go")
        self.be.on_frame = lambda: self.be.esc_cb()      # Esc lands while she is taking the 'before' screenshot
        self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])

    def test_esc_between_steps(self):
        _s.LEVEL = "auto"
        self.click("Help"); self.click("Save"); self.done()
        guide_act.start("go")
        orig = self.be.act
        def act(*a, **k):
            r = orig(*a, **k); self.be.esc_cb(); return r
        self.be.act = act
        self.yes()
        self.assertTrue(wait(self.ended))
        self.assertEqual(len(self.be.acts), 1)

    def test_voice_or_typed_stop(self):
        _s.LEVEL = "step"
        self.click("Privacy")
        guide_act.start("go")
        self.assertTrue(wait(guide_act.awaiting))
        self.assertEqual(guide_act.control("stop"), "Okay, stopped.")
        self.assertTrue(wait(self.ended))
        self.assertEqual(self.be.acts, [])

    def test_stop_when_nothing_runs(self):
        self.assertIsNone(guide_act.stop())
        self.assertIsNone(guide_act.control("stop"))


class Phrases(unittest.TestCase):
    def setUp(self):
        guide_act._goal = None
        import guide
        self.g = guide

    def test_for_me_on_screen(self):
        r = self.g._parse("open the privacy tab for me on screen")
        self.assertEqual(r["kind"], "act")
        self.assertEqual(r["goal"], "open the privacy tab")

    def test_do_it_for_me_needs_something_to_refer_to(self):
        self.assertIsNone(self.g._parse("do it for me"))
        guide_act.remember("turn on dark mode in Settings")
        self.assertEqual(self.g._parse("can you do it for me?")["goal"], "turn on dark mode in Settings")

    def test_do_it_for_me_expires(self):
        guide_act._goal = ("old goal", time.monotonic() - guide_act.GOAL_TTL_S - 1)
        self.assertIsNone(self.g._parse("do it for me"))

    def test_plain_click_and_everyday_for_me_are_untouched(self):
        for c in ("click save", "click the save button in chrome", "buy milk for me", "play lo-fi for me"):
            self.assertIsNone(self.g._parse(c), c)

    def test_look_only_phrases_still_work(self):
        self.assertEqual(self.g._parse("show me where the volume icon is")["kind"], "where")
        self.assertEqual(self.g._parse("walk me through the privacy settings")["kind"], "plan")


class Highlight(Base):
    def test_locate_by_name(self):
        pos = guide_act.locate("the save button")
        self.assertEqual(pos, ((100 + 40) / 1920, (200 + 12) / 1080))
        self.assertIsNone(guide_act.locate("the microphone"))

    def test_thin_tree_falls_back_to_vision(self):
        self.assertIsNone(guide_act.plan_tour("explain this"))

    def test_tour_points_at_listed_names_only(self):
        self.be.ctls = [btn(n, 100, 40 * i) for i, n in enumerate(["Home", "Display", "Sound", "Privacy", "Update", "Apps"])]
        self.replies.append(json.dumps([
            {"text": "This is Privacy.", "target": "Privacy"},
            {"text": "Nothing here for that.", "target": None},
            {"text": "Made up.", "target": "Warp drive"}]))
        steps = guide_act.plan_tour("explain")
        self.assertEqual([s["text"] for s in steps], ["This is Privacy.", "Nothing here for that.", "Made up."])
        self.assertIn("x", steps[0]); self.assertNotIn("x", steps[1]); self.assertNotIn("x", steps[2])


if __name__ == "__main__":
    unittest.main()
