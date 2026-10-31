"""Phase L tests: chat pre-gate, shared LLM client, per-turn counters, no settle-sleep after the last action.
Offline (no network, no mic). Run on its own:  python -m unittest tests.test_latency
"""
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)
os.environ.setdefault("LYRA_DATA_DIR", tempfile.mkdtemp(prefix="lyra-test-"))

from tools_bench import _stubs  # noqa: E402

_stubs.install()
fastpath = _stubs.import_with_stubs("fastpath")
perf = _stubs.import_with_stubs("perf")
brain = _stubs.import_with_stubs("brain")
agent = _stubs.import_with_stubs("agent")


class ChatPregate(unittest.TestCase):
    CHAT = ["hi", "Hello", "hey there", "how are you", "how are you today?", "what's up",
            "thanks", "thank you", "thank you so much", "thanks lyra", "tell me a joke",
            "tell me another joke", "make me laugh", "cheer me up", "write me a poem",
            "I'm bored", "who are you", "hey lyra, how are you?", "okay lyra tell me a joke!"]
    NOT_CHAT = [
        "", "   ", "open spotify", "play lofi", "pause", "volume up", "set a timer for 5 minutes",
        "thanks now open discord", "hello open chrome", "hi play some music", "good morning",
        "good night", "what can you do", "how are you doing open spotify", "tell me a joke about my boss and email it",
        "tell me a joke then play music", "write a poem and save it to a file", "remind me to call mom",
        "what's the weather", "what's the latest news", "who won the match last night", "what time is it",
        "search for cats", "send a message to ahmed saying thanks", "thank you for opening that, now close it",
        "tell me about the moon", "explain how rainbows form", "i'm tired, turn off the lights",
        "hey lyra open notepad", "hey lyra", "lyra", "hello " * 20, "x" * 200,
    ]

    def test_chat_phrases_are_claimed(self):
        for s in self.CHAT:
            self.assertTrue(fastpath.is_plain_chat(s), s)

    def test_anything_actionable_is_left_to_the_planner(self):
        for s in self.NOT_CHAT:
            self.assertFalse(fastpath.is_plain_chat(s), repr(s))

    def test_never_raises(self):
        for bad in (None, 5, [], object()):
            try:
                self.assertFalse(fastpath.is_plain_chat(bad))
            except Exception as e:                       # pragma: no cover
                self.fail(f"raised on {bad!r}: {e}")


class SharedClient(unittest.TestCase):
    def setUp(self):
        self.made = []

        class Fake:
            def __init__(s, **kw):
                s.kw = kw
                self.made.append(s)

        self._orig = brain.OpenAI
        brain.OpenAI = Fake
        brain._CLIENT_CACHE.clear()
        import llm_store
        self._llm_get = llm_store.get
        self.cfg = {"provider": "groq", "groq_api_key": "k1", "groq_model": "m"}
        llm_store.get = lambda: self.cfg

    def tearDown(self):
        brain.OpenAI = self._orig
        import llm_store
        llm_store.get = self._llm_get
        brain._CLIENT_CACHE.clear()

    def test_same_key_reuses_one_client(self):
        c1, m1 = brain._get_client()
        c2, m2 = brain._get_client()
        self.assertIs(c1, c2)
        self.assertEqual(len(self.made), 1)
        self.assertEqual(m1, "m")

    def test_changing_the_key_in_settings_applies_without_restart(self):
        c1, _ = brain._get_client()
        self.cfg["groq_api_key"] = "k2"
        c2, _ = brain._get_client()
        self.assertIsNot(c1, c2)
        self.assertEqual(c2.kw["api_key"], "k2")
        self.assertEqual(len(self.made), 2)

    def test_explicit_provider_never_borrows_another_providers_key(self):
        self.cfg.update({"gemini_api_key": "", "gemini_model": "g"})
        self.assertEqual(brain._get_client("gemini"), (None, None))

    def test_timeouts_and_retries_are_set_when_httpx_is_available(self):
        c, _ = brain._get_client()
        if "timeout" in c.kw:                      # httpx is installed with the real openai package
            self.assertEqual(c.kw["max_retries"], 1)

    def test_cache_does_not_grow_forever(self):
        for i in range(20):
            self.cfg["groq_api_key"] = f"key{i}"
            brain._get_client()
        self.assertLessEqual(len(brain._CLIENT_CACHE), 8)


class PerfCounters(unittest.TestCase):
    def test_counts_land_in_the_summary(self):
        perf.begin("text", "hello")
        perf.count("llm_calls")
        perf.count("llm_calls")
        perf.count("llm_prompt_chars", 1234)
        s = perf.summary(perf._turn)
        self.assertEqual(s["llm_calls"], 2)
        self.assertEqual(s["llm_prompt_chars"], 1234)
        self.assertIn("llm calls 2", perf.format_line(s))

    def test_count_without_a_turn_is_harmless(self):
        perf._turn = None
        perf.count("llm_calls")            # must not raise


class SettleSleep(unittest.TestCase):
    """The agent used to sleep 250 ms after EVERY action, including the last one of a finished plan."""

    def run_plan(self, n_actions):
        plan = {"intent": "action", "done": True, "reply": "",
                "actions": [{"tool": "open_app", "args": {"name": f"a{i}"}} for i in range(n_actions)]}
        import json
        raw = json.dumps(plan)
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
            create=lambda **kw: types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=raw))]))))
        sleeps = []
        saved = (agent._llm, agent._execute_step, agent._asked_for, agent._permissions.check_permission,
                 agent.time.sleep)
        agent._llm = lambda: (client, "m")
        agent._execute_step = lambda step: types.SimpleNamespace(success=True, verified=None, message="ok")
        agent._asked_for = lambda step, command: True
        agent._permissions.check_permission = lambda *a, **k: (True, "")
        agent.time.sleep = lambda s: sleeps.append(s)
        try:
            reply, route = agent.route_and_run("open things", None, force=True)
        finally:
            (agent._llm, agent._execute_step, agent._asked_for, agent._permissions.check_permission,
             agent.time.sleep) = saved
        return reply, route, sleeps

    def test_single_action_has_no_settle_sleep(self):
        reply, route, sleeps = self.run_plan(1)
        self.assertEqual(route, "handled")
        self.assertEqual(sleeps, [])

    def test_chain_waits_between_steps_but_not_after_the_last(self):
        reply, route, sleeps = self.run_plan(3)
        self.assertEqual(route, "handled")
        self.assertEqual(sleeps, [0.25, 0.25])


if __name__ == "__main__":
    unittest.main()
