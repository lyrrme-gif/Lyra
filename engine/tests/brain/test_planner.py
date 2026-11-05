"""Tests for planner.py and tools/planner_tools.py. From the backend folder:

    python -m unittest tests.test_planner -v

The language model and every tool are FAKES (the model answers with a plan prepared by the test), so
these check the machinery: when a plan is made, validation, the plan shown before running, the progress
chip, passing text between steps, CONFIRM / BLOCKED handling, stop, failures and the note + read-back.
They say nothing about the quality of a real model's plans.
"""
import json
import sys
import threading
import time
import types
import unittest

EVENTS: list = []     # everything the island would get, in order
SPOKEN: list = []
CALLS: list = []      # (tool, args) in execution order

from engine.tools.base import ToolDefinition, ToolParameter, ToolCategory, ToolResult  # real, pure module


class FakeRegistry:
    def __init__(self):
        self.tools = {}

    def get_tool(self, i):
        return self.tools.get(i)

    def list_tools(self, category=None):
        return list(self.tools.values())


REG = FakeRegistry()


def _install():
    r = types.ModuleType("engine.tools.registry")
    r.get_registry = lambda: REG
    r.register_tool = lambda t: REG.tools.__setitem__(t.id, t)

    def call_tool(tid, **kw):
        t = REG.tools[tid]
        CALLS.append((tid, kw))
        return t.execute(**kw)
    r.call_tool = call_tool
    sys.modules["engine.tools.registry"] = r

    ub = types.ModuleType("engine.bridge.ui_bridge")
    ub.push_activity = lambda kind, title, phase="info", subtitle=None, duration_ms=0: EVENTS.append(("chip", title, phase))
    ub.push_search_results = lambda q, res, prov="", card=None: EVENTS.append(("plan", card))
    sys.modules["engine.bridge.ui_bridge"] = ub

    ui = types.ModuleType("engine.bridge.ui")
    ui.print_chat = lambda who, text: None
    ui.set_state = lambda s: None
    ui.log = types.SimpleNamespace(warning=lambda *a, **k: None)
    sys.modules["engine.bridge.ui"] = ui

    ss = types.ModuleType("engine.core.settings_store")
    ss._on = {"planner": True}
    ss.feature_on = lambda n: ss._on.get(n, True)
    sys.modules["engine.core.settings_store"] = ss

    v = types.ModuleType("engine.voice.voice")
    v.speak = lambda t, *a, **k: (SPOKEN.append(t), "")[1]
    sys.modules["engine.voice.voice"] = v

    perm = types.ModuleType("engine.core.permissions")

    class PM:
        _rules = {"delete_path": types.SimpleNamespace(value="confirm")}
    perm.PermissionManager = PM
    sys.modules["engine.core.permissions"] = perm

    notes = types.ModuleType("engine.actions.notes")
    notes.SAVED = []
    notes.try_notes = lambda phrase: (notes.SAVED.append(phrase), "Noted.")[1]
    actions = types.ModuleType("engine.actions")
    actions.notes = notes
    sys.modules["engine.actions"], sys.modules["engine.actions.notes"] = actions, notes

    brain = types.ModuleType("engine.brain.brain")
    brain.FAKE = FakeLLM()
    brain._get_client = lambda: (brain.FAKE, "fake")
    brain._fallback_client = lambda: None
    sys.modules["engine.brain.brain"] = brain


class FakeLLM:
    def __init__(self):
        self.plan = ""
        self.systems = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, model, messages, temperature=0, timeout=0):
        system, user = messages[0]["content"], messages[1]["content"]
        self.systems.append(system)
        if "Lyra's planner" in system:
            text = self.plan if isinstance(self.plan, str) else json.dumps(self.plan)
        elif "Summarize the text" in system:
            text = "SUMMARY[" + user[:20] + "]"
        else:
            text = "PROCESSED[" + user[:20] + "]"
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])


_install()
import engine.brain.planner as pl               # noqa: E402
import engine.tools.planner_tools  # FIXME-dotted         # noqa: E402,F401  (registers summarize_text / process_text / speak_text)

P = ToolParameter
GATE = threading.Event()


def _reg(tid, params, level="SAFE", fn=None, desc="x"):
    REG.tools[tid] = ToolDefinition(tid, tid, desc, ToolCategory.SYSTEM, params, level, fn or (lambda **k: ToolResult(True, f"{tid} ok")))


def _fake_tools():
    _reg("web_search", [P("query", "string", "q")], fn=lambda query, **k: ToolResult(True, "1. Solar panels convert light\n2. Costs fell 90%"))
    _reg("add_note", [P("text", "string", "t")], fn=lambda text: (pl_notes(text), ToolResult(True, "Noted."))[1])
    _reg("delete_path", [P("path", "string", "p")], level="CONFIRM", fn=lambda path: ToolResult(True, "deleted " + path))
    _reg("nuke", [], level="BLOCKED", fn=lambda: ToolResult(True, "boom"))
    _reg("slow", [], fn=lambda: (GATE.wait(5), ToolResult(True, "slow done"))[1])
    _reg("broken", [], fn=lambda: ToolResult(False, "no network"))


def pl_notes(text):
    sys.modules["engine.actions.notes"].SAVED.append("ADD_NOTE:" + text)


REQ = "Research solar panels, summarize it, save a note and read it back"
PLAN4 = {"title": "Solar panel research", "steps": [
    {"tool": "web_search", "args": {"query": "solar panels"}, "title": "Search the web"},
    {"tool": "summarize_text", "args": {"text": "{{1}}"}, "title": "Summarize"},
    {"tool": "add_note", "args": {"text": "{{2}}"}, "title": "Save a note"},
    {"tool": "speak_text", "args": {"text": "{{2}}"}, "title": "Read it back"}]}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        pl.abort()
        pl.wait_finished(5)
        pl._run = None
        pl.START_DELAY_S = 0
        pl.AUTO_SAVE_RESULT = True
        EVENTS.clear(); SPOKEN.clear(); CALLS.clear(); GATE.clear()
        sys.modules["engine.actions.notes"].SAVED.clear()
        sys.modules["engine.core.settings_store"]._on["planner"] = True
        sys.modules["engine.brain.brain"].FAKE.plan = PLAN4
        sys.modules["engine.brain.brain"].FAKE.systems.clear()
        REG.tools.clear()
        _fake_tools()
        import importlib; import engine.tools.planner_tools as t
        importlib.reload(t)          # re-registers its 3 tools into the fresh registry

    def go(self, request=REQ):
        r = pl.handle_turn(request)
        return r

    # ── when ──────────────────────────────────────────────────────────────────────────────────
    def test_should_plan(self):
        yes = [REQ, "Look up how vaccines work and summarize it for me", "research black holes then write it down",
               "make a plan to learn spanish and write it down"]
        no = ["open chrome", "what time is it", "summarize my clipboard", "research solar panels",
              "take a note that I need to research and summarize taxes", "remind me to search and save the report",
              "play music and tell me what is on", "translate this and read it back"]
        for c in yes:
            self.assertTrue(pl.should_plan(c), c)
        for c in no:
            self.assertFalse(pl.should_plan(c), c)

    # ── the 4-step run ────────────────────────────────────────────────────────────────────────
    def test_four_step_plan_shows_first_then_runs_and_passes_text(self):
        say = self.go()
        self.assertIn("4 steps", say)
        self.assertEqual(EVENTS[0][0], "plan")                         # shown BEFORE anything runs
        card = EVENTS[0][1]
        self.assertEqual(card["title"], "Plan \u00b7 4 steps")
        self.assertIn("1. Search the web", card["extract"])
        self.assertIn("4. Read it back", card["extract"])
        self.assertTrue(pl.wait_finished(10))
        self.assertEqual([c[0] for c in CALLS], ["web_search", "summarize_text", "add_note", "speak_text"])
        # {{1}} became the search output, {{2}} the summary
        self.assertIn("Solar panels convert light", CALLS[1][1]["text"])
        self.assertTrue(CALLS[2][1]["text"].startswith("SUMMARY["))
        self.assertTrue(CALLS[3][1]["text"].startswith("SUMMARY["))
        self.assertTrue(any(s.startswith("SUMMARY[") for s in SPOKEN))     # read back
        self.assertEqual([s.status for s in pl.current_steps()], ["done"] * 4)
        chips = [e for e in EVENTS if e[0] == "chip"]
        self.assertEqual([c[1] for c in chips[:4]], ["Step 1/4: Search the web", "Step 2/4: Summarize",
                                                     "Step 3/4: Save a note", "Step 4/4: Read it back"])
        self.assertEqual(chips[-1][1:], ("Plan done", "success"))
        self.assertFalse(any(s.startswith("take a note") for s in sys.modules["engine.actions.notes"].SAVED))  # no duplicate note

    def test_result_is_saved_as_note_when_plan_has_no_note_step(self):
        sys.modules["engine.brain.brain"].FAKE.plan = {"title": "Solar", "steps": PLAN4["steps"][:2]}
        self.go("Research solar panels and summarize it")
        pl.wait_finished(10)
        saved = sys.modules["engine.actions.notes"].SAVED
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].startswith("take a note: Solar: SUMMARY["))
        self.assertTrue(any("saved it to your notes" in s for s in SPOKEN))

    # ── stop ──────────────────────────────────────────────────────────────────────────────────
    def test_stop_mid_plan(self):
        sys.modules["engine.brain.brain"].FAKE.plan = {"title": "t", "steps": [
            {"tool": "web_search", "args": {"query": "x"}, "title": "Search"},
            {"tool": "slow", "args": {}, "title": "Slow step"},
            {"tool": "add_note", "args": {"text": "{{1}}"}, "title": "Save"}]}
        self.go()
        for _ in range(100):
            if any(c[0] == "slow" for c in CALLS):
                break
            time.sleep(0.02)
        self.assertTrue(pl.is_running())
        t0 = time.time()
        self.assertEqual(pl.handle_turn("stop"), "Okay, stopped.")
        self.assertTrue(pl.wait_finished(5))
        self.assertLess(time.time() - t0, 1.5)                         # did not wait for the busy tool (5 s)
        self.assertNotIn("add_note", [c[0] for c in CALLS])            # step 3 never ran
        self.assertEqual([s.status for s in pl.current_steps()], ["done", "stopped", "waiting"])
        self.assertEqual([e for e in EVENTS if e[0] == "chip"][-1][1:], ("Plan stopped", "fail"))
        self.assertFalse(pl.is_running())

    def test_stop_when_nothing_runs_is_not_ours(self):
        self.assertIsNone(pl.handle_turn("stop"))

    # ── permissions ───────────────────────────────────────────────────────────────────────────
    def _confirm_plan(self):
        sys.modules["engine.brain.brain"].FAKE.plan = {"title": "Clean", "steps": [
            {"tool": "web_search", "args": {"query": "x"}, "title": "Search"},
            {"tool": "delete_path", "args": {"path": "C:/tmp/x"}, "title": "Delete the file"},
            {"tool": "add_note", "args": {"text": "done"}, "title": "Save a note"}]}

    def _wait_confirm(self):
        for _ in range(200):
            r = pl._run
            if r and r.waiting_for_confirm:
                return
            time.sleep(0.02)
        self.fail("never asked")

    def test_confirm_tier_asks_and_yes_runs(self):
        self._confirm_plan(); self.go(); self._wait_confirm()
        self.assertNotIn("delete_path", [c[0] for c in CALLS])         # asked first
        self.assertTrue(any("needs your OK" in s for s in SPOKEN))
        self.assertEqual(pl.handle_turn("yes"), "")
        pl.wait_finished(10)
        self.assertEqual([c[0] for c in CALLS], ["web_search", "delete_path", "add_note"])

    def test_confirm_tier_no_stops_plan(self):
        self._confirm_plan(); self.go(); self._wait_confirm()
        pl.handle_turn("no")
        pl.wait_finished(10)
        self.assertEqual([c[0] for c in CALLS], ["web_search"])
        self.assertTrue(any("stopped there" in s for s in SPOKEN))

    def test_blocked_tool_never_offered_and_never_runs(self):
        self.go()
        self.assertNotIn("nuke", sys.modules["engine.brain.brain"].FAKE.systems[0])
        pl.wait_finished(10)
        sys.modules["engine.brain.brain"].FAKE.plan = {"title": "t", "steps": [
            {"tool": "web_search", "args": {"query": "x"}, "title": "S"}, {"tool": "nuke", "args": {}, "title": "N"}]}
        self.assertIsNone(self.go("Research solar panels and summarize it"))   # rejected: falls back to the old agent
        self.assertNotIn("nuke", [c[0] for c in CALLS])

    def test_blocked_at_run_time_is_refused(self):
        self.go(); pl.wait_finished(10); CALLS.clear(); SPOKEN.clear()
        steps = [pl.Step(1, "web_search", {"query": "x"}, "S"), pl.Step(2, "nuke", {}, "Nuke")]
        run = pl.Run("r", "t", steps); pl._run = run
        pl._runner(run)
        self.assertEqual([c[0] for c in CALLS], ["web_search"])
        self.assertEqual(steps[1].status, "blocked")
        self.assertTrue(any("not something I'm allowed" in s for s in SPOKEN))

    # ── failures and validation ───────────────────────────────────────────────────────────────
    def test_failed_step_stops_and_says_why(self):
        sys.modules["engine.brain.brain"].FAKE.plan = {"title": "t", "steps": [
            {"tool": "web_search", "args": {"query": "x"}, "title": "Search"},
            {"tool": "broken", "args": {}, "title": "Broken"},
            {"tool": "add_note", "args": {"text": "{{1}}"}, "title": "Save"}]}
        self.go(); pl.wait_finished(10)
        self.assertEqual([c[0] for c in CALLS], ["web_search", "broken"])
        self.assertTrue(any("step 2 (Broken) failed: no network" in s for s in SPOKEN))

    def test_invalid_plans_are_rejected(self):
        bad = [
            {"title": "t", "steps": [{"tool": "web_search", "args": {"query": "x"}}, {"tool": "ghost", "args": {}}]},   # unknown tool
            {"title": "t", "steps": [{"tool": "web_search", "args": {}}, {"tool": "add_note", "args": {"text": "a"}}]},  # missing param
            {"title": "t", "steps": [{"tool": "add_note", "args": {"text": "{{1}}"}}, {"tool": "add_note", "args": {"text": "b"}}]},  # forward/self ref
            {"title": "t", "steps": [{"tool": "web_search", "args": {"query": "x"}}]},                                   # a single step
            {"title": "", "steps": []},
            {"title": "t", "steps": [{"tool": "wait", "args": {}}] * 9},                                                  # too many
        ]
        for plan in bad:
            sys.modules["engine.brain.brain"].FAKE.plan = plan
            self.assertIsNone(pl.handle_turn(REQ), plan)
            self.assertFalse(pl.is_running())
        sys.modules["engine.brain.brain"].FAKE.plan = "not json at all"
        self.assertIsNone(pl.handle_turn(REQ))

    def test_eight_steps_allowed(self):
        _reg("wait", [], fn=lambda: ToolResult(True, "w"))
        sys.modules["engine.brain.brain"].FAKE.plan = {"title": "t", "steps": [{"tool": "wait", "args": {}, "title": f"W{i}"} for i in range(8)]}
        self.go(); self.assertTrue(pl.wait_finished(10))
        self.assertEqual(len(CALLS), 8)

    def test_llm_error_falls_back(self):
        brain = sys.modules["engine.brain.brain"]
        orig = brain._get_client
        brain._get_client = lambda: (None, None)
        try:
            self.assertIsNone(pl.handle_turn(REQ))
        finally:
            brain._get_client = orig

    def test_feature_switch(self):
        sys.modules["engine.core.settings_store"]._on["planner"] = False
        self.assertIsNone(pl.handle_turn(REQ))
        self.assertFalse(pl.is_running())

    def test_other_commands_pass_through_while_running(self):
        sys.modules["engine.brain.brain"].FAKE.plan = {"title": "t", "steps": [
            {"tool": "slow", "args": {}, "title": "Slow"}, {"tool": "web_search", "args": {"query": "x"}, "title": "S"}]}
        self.go()
        self.assertIsNone(pl.handle_turn("what time is it"))
        GATE.set(); pl.wait_finished(10)

    # ── the registry tools ────────────────────────────────────────────────────────────────────
    def test_planner_tools(self):
        self.assertEqual({"summarize_text", "process_text", "speak_text"} - set(REG.tools), set())
        r = REG.tools["summarize_text"].execute(text="hello world " * 5)
        self.assertTrue(r.success and r.message.startswith("SUMMARY["))
        self.assertFalse(REG.tools["summarize_text"].execute(text="  ").success)
        self.assertTrue(REG.tools["process_text"].execute(instruction="list key points", text="abc").success)
        self.assertTrue(REG.tools["speak_text"].execute(text="Hi there").success)
        self.assertIn("Hi there", SPOKEN)
        self.assertFalse(REG.tools["speak_text"].execute(text="").success)


if __name__ == "__main__":
    unittest.main(verbosity=2)
