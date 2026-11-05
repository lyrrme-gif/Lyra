"""Offline tests for guide.py (no Windows, no network): python -m unittest test_guide -v
Vision, TTS, the screen and the mascot bus are replaced by stubs; the code under test is guide.py."""

import logging, sys, time, types, unittest

from PIL import Image


def _install_stubs(state):
    ui = types.ModuleType("engine.bridge.ui"); ui.log = logging.getLogger("t"); ui.set_state = lambda s: None; ui.trace = lambda m: None
    bridge = types.ModuleType("engine.bridge.ui_bridge"); bridge._push_event = lambda e: state["events"].append(e)
    ss = types.ModuleType("engine.core.settings_store"); ss.feature_on = lambda n: state["feature"]
    mixer = types.SimpleNamespace(get_init=lambda: True,
        music=types.SimpleNamespace(get_busy=lambda: state["busy"], stop=lambda: state.update(busy=False, stopped=state["stopped"] + 1)))
    voice = types.ModuleType("engine.voice.voice"); voice.pygame = types.SimpleNamespace(mixer=mixer)
    def speak(text, *a, **k):
        state["spoken"].append(text); state["busy"] = True
        t0 = time.monotonic()
        while state["busy"] and time.monotonic() - t0 < state["speech_s"]:
            time.sleep(0.01)
        state["busy"] = False
    voice.speak = speak
    sc = types.ModuleType("engine.features.screen_capture")
    sc.monitors = lambda: [{"index": 0, "left": 0, "top": 0, "width": 1000, "height": 500},
                           {"index": 1, "left": 1000, "top": 0, "width": 2000, "height": 1000}]
    sc.capture_monitor = lambda i: Image.new("RGB", (1000, 500) if i == 0 else (2000, 1000), "white")
    import re
    apps = types.ModuleType("engine.actions.apps")
    apps.GRID_COLS, apps.GRID_ROWS = 12, 8
    apps.CELL_LABEL_PATTERN = re.compile(r"\b([A-L a-l])\s?([1-8])\b", re.X)
    apps._vision_candidates = lambda: [("x",)]
    def vc(messages, max_tokens=0):
        state["vision_calls"] += 1
        prompt = messages[0]["content"][0]["text"]
        if "JSON array" in prompt: return state["plan_reply"], None
        if "crosshair" in prompt: return "This is the volume icon.", None
        for key, val in state["locate"].items():
            if key in prompt: return val, None
        return "NONE|not found|low", None
    apps._vision_complete = vc
    apps._refine_point = lambda clean, W, H, c, r, cw, ch, d: None
    for n in ("click_on_screen", "type_text", "scroll_screen", "_perform_click_at"):
        setattr(apps, n, lambda *a, **k: state.update(clicked=True))
    pkg = types.ModuleType("engine.actions"); pkg.apps = apps
    pa = types.ModuleType("pyautogui"); pa.position = lambda: types.SimpleNamespace(x=state["cursor"][0], y=state["cursor"][1])
    sys.modules.update({"engine.bridge.ui": ui, "engine.bridge.ui_bridge": bridge, "engine.core.settings_store": ss, "engine.voice.voice": voice, "engine.features.screen_capture": sc,
                        "engine.actions": pkg, "engine.actions.apps": apps, "pyautogui": pa})
    sys.modules.pop("engine.features.guide", None)
    from engine.features import guide
    return guide


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.s = dict(events=[], spoken=[], busy=False, stopped=0, feature=True, speech_s=0.05, vision_calls=0,
                      clicked=False, cursor=(1500, 500), plan_reply="", locate={})
        self.g = _install_stubs(self.s)
        self.g._INTRO_WAIT_S = 0.2
        self.g._step_ms = lambda t: 150

    def tearDown(self):
        if self.g.active(): self.g._control("stop")

    def wait_idle(self, t=5):
        t0 = time.monotonic()
        while self.g.active() and time.monotonic() - t0 < t: time.sleep(0.02)

    def cmds(self): return [e["cmd"] for e in self.s["events"]]

    def test_phrases(self):
        p = self.g._parse
        self.assertEqual(p("show me where the volume icon is")["what"], "volume icon")
        self.assertEqual(p("Can you show me where to find the settings button on my screen?")["what"], "settings button")
        self.assertEqual(p("explain this screen")["kind"], "plan")
        self.assertEqual(p("walk me through exporting a PDF")["kind"], "plan")
        self.assertEqual(p("what is this button")["kind"], "cursor")
        self.assertIsNone(p("what is the capital of France"))
        self.assertIsNone(p("how do I cook rice"))
        self.assertIsNone(p("next"))

    def test_where_found_normalized_on_cursor_monitor(self):
        self.s["locate"] = {"volume icon": "L8|speaker icon|high"}   # last cell of monitor 1 (2000x1000)
        self.assertEqual(self.g.try_guide("show me where the volume icon is"), "Okay, follow me.")
        self.wait_idle()
        tour = [e for e in self.s["events"] if e["cmd"] == "tour"]
        self.assertEqual(len(tour), 1)
        st = tour[0]["steps"][0]
        self.assertAlmostEqual(st["x"], 11.5 / 12, 3); self.assertAlmostEqual(st["y"], 7.5 / 8, 3)
        self.assertEqual(st["emote"], "point")
        self.assertEqual(self.cmds()[-2:], ["home", "emote"])
        self.assertEqual(self.s["events"][-1]["name"], "happy")
        self.assertEqual(self.s["spoken"], ["Here's the volume icon."])

    def test_where_not_found_no_tour(self):
        r = self.g.try_guide("show me where the flux capacitor is")
        self.assertIn("can't find", r); self.assertEqual(self.s["events"], [])

    def test_plan_mixed_steps_text_only_when_unfound(self):
        self.s["plan_reply"] = ('```json\n[{"text":"Open the menu.","target":"menu button"},'
                                '{"text":"Pick export.","target":"export entry"},{"text":"Done then.","target":null}]\n```')
        self.s["locate"] = {"menu button": "A1|menu|high"}
        self.g.try_guide("walk me through exporting"); self.wait_idle()
        steps = [e for e in self.s["events"] if e["cmd"] == "tour"][0]["steps"]
        self.assertIn("x", steps[0]); self.assertNotIn("x", steps[1]); self.assertNotIn("x", steps[2])
        self.assertEqual(self.s["spoken"], ["Open the menu.", "Pick export.", "Done then."])

    def test_max_six_steps(self):
        import json
        self.s["plan_reply"] = json.dumps([{"text": f"s{i}", "target": None} for i in range(9)])
        self.g.try_guide("explain this screen"); self.wait_idle()
        self.assertEqual(len([e for e in self.s["events"] if e["cmd"] == "tour"][0]["steps"]), 6)

    def test_stop_cancels(self):
        import json
        self.g._step_ms = lambda t: 5000; self.s["speech_s"] = 0.3
        self.s["plan_reply"] = json.dumps([{"text": f"s{i}", "target": None} for i in range(4)])
        self.g.try_guide("explain this screen"); time.sleep(0.6)
        self.assertEqual(self.g.try_guide("stop"), "Okay, stopped.")
        self.wait_idle(); n = len(self.s["spoken"])
        self.assertLess(n, 4); self.assertIn("stop", self.cmds()); self.assertNotIn("happy", [e.get("name") for e in self.s["events"]])
        time.sleep(0.4); self.assertEqual(len(self.s["spoken"]), n)

    def test_next_and_repeat_restart_tour(self):
        import json
        self.g._step_ms = lambda t: 5000
        self.s["plan_reply"] = json.dumps([{"text": f"s{i}", "target": None} for i in range(3)])
        self.g.try_guide("explain this screen"); time.sleep(0.5)
        self.assertEqual(self.g.try_guide("next"), "Next."); time.sleep(0.6)
        tours = [e for e in self.s["events"] if e["cmd"] == "tour"]
        self.assertEqual([t["text"] for t in tours[-1]["steps"]][0], "s1")
        self.g.try_guide("repeat"); time.sleep(0.6)
        tours = [e for e in self.s["events"] if e["cmd"] == "tour"]
        self.assertEqual(tours[-1]["steps"][0]["text"], "s1")

    def test_controls_ignored_without_tour(self):
        for w in ("stop", "next", "repeat"): self.assertIsNone(self.g.try_guide(w))

    def test_feature_switch_off(self):
        self.s["feature"] = False
        self.assertIsNone(self.g.try_guide("show me where the volume icon is")); self.assertEqual(self.s["vision_calls"], 0)

    def test_cursor_mode_points_at_cursor(self):
        self.g.try_guide("what is this button"); self.wait_idle()
        st = [e for e in self.s["events"] if e["cmd"] == "tour"][0]["steps"][0]
        self.assertAlmostEqual(st["x"], 0.25, 3); self.assertAlmostEqual(st["y"], 0.5, 3)

    def test_never_clicks(self):
        self.s["locate"] = {"x": "A1|x|high"}
        self.g.try_guide("show me where x is"); self.wait_idle()
        self.assertFalse(self.s["clicked"])


if __name__ == "__main__":
    unittest.main()
