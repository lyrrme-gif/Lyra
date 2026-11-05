"""Tests for compound.py and its use in main.process_command_chain / messaging. From the backend folder:

    python -m unittest tests.test_compound -v

The loop tests run the REAL process_command_chain with fake handlers (actions, voice, ui, ...), so they
check the order, the app reuse and the stop-on-failure behaviour, not what Discord really does.
"""
import re
import sys
import time
import types
import unittest

from engine.core import compound
from engine.core.compound import Chain, app_of, inherit_app, looks_failed, split_compound


class SplitTests(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(split_compound(text), expected, text)

    def test_the_reported_bug(self):
        self.check("open discord and open anogashi's chat", ["open discord", "open anogashi's chat"])

    def test_any_number_of_steps_and_all_separators(self):
        self.check("open discord, then open anogashi's chat, and then play lofi on spotify",
                   ["open discord", "open anogashi's chat", "play lofi on spotify"])
        self.check("open spotify and play lofi; pause; close spotify",
                   ["open spotify", "play lofi", "pause", "close spotify"])
        self.check("open discord after that open chrome afterwards search cats and mute",
                   ["open discord", "open chrome", "search cats", "mute"])
        self.check("open a, open b, open c, open d, open e", ["open a", "open b", "open c", "open d", "open e"])

    def test_and_and_comma_need_a_verb_next(self):
        self.check("play salt and pepper", ["play salt and pepper"])
        self.check("play rock, pop and jazz", ["play rock, pop and jazz"])
        self.check("open youtube, chrome and spotify", ["open youtube, chrome and spotify"])
        self.check("open chrome and search for salt and pepper recipes",
                   ["open chrome", "search for salt and pepper recipes"])

    def test_filler_and_dangling_words(self):
        self.check("open discord and also open anogashi's chat", ["open discord", "open anogashi's chat"])
        self.check("then open discord", ["open discord"])
        self.check("open discord and", ["open discord"])

    def test_not_compound(self):
        self.check("open discord", ["open discord"])
        self.check("   ", [])
        self.check("", [])

    def test_quotes_and_message_text_are_never_cut(self):
        self.check('play "hide and seek" and open discord', ['play "hide and seek"', "open discord"])
        self.check("message sara saying open the door and close it",
                   ["message sara saying open the door and close it"])
        self.check("send sara: meet me at 5 and then we eat", ["send sara: meet me at 5 and then we eat"])
        self.check('message sara on discord saying "hi" then open spotify',
                   ['message sara on discord saying "hi"', "open spotify"])
        self.check("remind me to open the oven and close the window",
                   ["remind me to open the oven and close the window"])

    def test_a_message_that_names_its_app_can_be_followed_by_a_step(self):
        self.check("message sara on discord and open spotify", ["message sara on discord", "open spotify"])


class AppReuseTests(unittest.TestCase):
    def test_app_of(self):
        self.assertEqual(app_of("open discord"), "discord")
        self.assertEqual(app_of("launch Spotify"), "spotify")
        self.assertEqual(app_of("open anogashi's chat on whatsapp"), "whatsapp")
        self.assertIsNone(app_of("open anogashi's chat"))
        self.assertIsNone(app_of("play lofi"))

    def test_inherit_app(self):
        self.assertEqual(inherit_app("open anogashi's chat", "discord"), "open anogashi's chat on discord")
        self.assertEqual(inherit_app("open the chat with sara", "telegram"), "open the chat with sara on telegram")
        self.assertEqual(inherit_app("message sara", "discord"), "message sara on discord")
        self.assertEqual(inherit_app("play lofi", "spotify"), "play lofi on spotify")

    def test_inherit_app_leaves_other_steps_alone(self):
        self.assertEqual(inherit_app("open anogashi's chat on whatsapp", "discord"), "open anogashi's chat on whatsapp")
        self.assertEqual(inherit_app("open anogashi's chat", "chrome"), "open anogashi's chat")
        self.assertEqual(inherit_app("search cats", "chrome"), "search cats")
        self.assertEqual(inherit_app("play lofi on youtube", "spotify"), "play lofi on youtube")
        self.assertEqual(inherit_app("open anogashi's chat", None), "open anogashi's chat")


class ChainTests(unittest.TestCase):
    def test_app_travels_down_the_chain(self):
        ch = Chain(split_compound("open whatsapp and open sara's chat and message omar"))
        self.assertEqual([ch.next_step() for _ in range(3)],
                         ["open whatsapp", "open sara's chat on whatsapp", "message omar on whatsapp"])

    def test_failure_message_names_the_failed_and_finished_steps(self):
        ch = Chain(["open discord", "open anogashi's chat", "play lofi"])
        ch.next_step()
        ch.next_step()
        msg = ch.failure_message("I couldn't open Discord.")
        self.assertIn("step 2 of 3", msg)
        self.assertIn("open anogashi's chat on discord", msg)
        self.assertIn("Finished: open discord.", msg)
        self.assertIn("Not run: play lofi.", msg)

    def test_failure_of_the_first_step_and_of_the_last(self):
        ch = Chain(["a", "b"])
        ch.next_step()
        self.assertIn("Nothing finished", ch.failure_message("Sorry."))
        ch.next_step()
        msg = ch.failure_message("Sorry.", include_reply=False)
        self.assertNotIn("Sorry", msg)
        self.assertNotIn("Not run", msg)

    def test_looks_failed(self):
        for bad in ("I couldn't open Discord.", "I don't know how to open chats on .", "Which one — Sara, Sam?",
                    "Okay, I didn't send it.", "Sorry, that failed."):
            self.assertTrue(looks_failed(bad), bad)
        for ok in ("", "Opening Discord, sir.", "Opened Anogashi's chat on Discord.", "Sent to Sara on Discord."):
            self.assertFalse(looks_failed(ok), ok)


class ProfileTests(unittest.TestCase):
    def test_chat_apps_work_without_app_profiles_json(self):
        from engine.features import messaging
        for app in ("discord", "whatsapp", "telegram"):
            self.assertTrue(messaging._profile(app)["window"], app)
        self.assertIsNone(messaging._profile("nosuchapp"))


class OpenAllResultsTests(unittest.TestCase):
    def test_parse_open_all(self):
        from engine.features import yt_island
        for yes in ("open all those searches", "open all the results", "open them all", "open all of them",
                    "can you open all of these links please", "Open all those tabs."):
            self.assertTrue(yt_island.parse_open_all(yes), yes)
        for no in ("open all", "open discord", "open the first result", "open all my apps", "play them"):
            self.assertFalse(yt_island.parse_open_all(no), no)

    def test_open_all_opens_every_remembered_result_once(self):
        from engine.features import yt_island
        from unittest import mock
        yt_island.remember([{"url": "https://a.example"}, {"url": "https://b.example"}, {"url": "https://a.example"}])
        opened = []
        fake = types.ModuleType("engine.actions.apps")
        fake.open_url = opened.append
        with mock.patch.dict(sys.modules, {"engine.actions": types.ModuleType("engine.actions"), "engine.actions.apps": fake}), \
                mock.patch("time.sleep"):
            reply = yt_island.open_all()
        self.assertEqual(opened, ["https://a.example", "https://b.example"])
        self.assertEqual(reply, "Opened 2 results.")

    def test_nothing_to_open(self):
        from engine.features import yt_island
        yt_island.remember([])
        self.assertIn("don't have any recent results", yt_island.open_all())


class AgentGuardTests(unittest.TestCase):
    def setUp(self):
        try:
            from engine.brain import agent
        except Exception as e:                       # needs the full backend environment
            self.skipTest(f"agent not importable here: {e}")
        self.agent = agent

    def test_planner_cannot_invent_shortcuts_or_apps(self):
        a = self.agent._asked_for
        cmd = "open all those searches"
        self.assertFalse(a({"tool": "run_shortcut", "args": {"name": "Good night"}}, cmd))
        self.assertFalse(a({"tool": "run_shortcut", "args": {"name": "Gaming mode"}}, cmd))
        self.assertFalse(a({"tool": "open_app", "args": {"app_name": "ar"}}, cmd))

    def test_named_things_and_goals_still_run(self):
        a = self.agent._asked_for
        self.assertTrue(a({"tool": "run_shortcut", "args": {"name": "Good night"}}, "run good night"))
        self.assertTrue(a({"tool": "open_app", "args": {"app_name": "Spotify"}}, "open spotify"))
        self.assertTrue(a({"tool": "run_shortcut", "args": {"name": "Focus mode"}}, "get me ready for work"))
        self.assertTrue(a({"tool": "web_search", "args": {"query": "x"}}, "anything"))


class MessagingPatternTests(unittest.TestCase):
    def who_app(self, text):
        from engine.features import messaging
        for pat in messaging._OPEN_CHAT_PATTERNS:
            m = re.match(pat, text, re.I)
            if m:
                return m.group("who"), m.groupdict().get("app")
        return None

    def test_who_cannot_swallow_another_step(self):
        self.assertIsNone(self.who_app("open discord and open anogashi's chat"))
        self.assertIsNone(self.who_app("open discord then open sara chat"))
        self.assertIsNone(self.who_app("message sara and tom on discord"))

    def test_normal_open_chat_still_matches(self):
        self.assertEqual(self.who_app("open sara chat"), ("sara", None))
        self.assertEqual(self.who_app("open anogashi's chat on discord"), ("anogashi's", "discord"))
        self.assertEqual(self.who_app("open the chat with sara on discord"), ("sara", "discord"))
        self.assertEqual(self.who_app("message Sandra on discord"), ("Sandra", "discord"))     # "and" inside a name
        self.assertEqual(self.who_app("open Anand chat"), ("Anand", None))


# ---------------------------------------------------------------------------------------------
# The real loop, with fake handlers
# ---------------------------------------------------------------------------------------------
STUBBED = ("speech_recognition", "engine.bridge.ui", "engine.voice.voice", "engine.actions", "engine.brain.brain", "engine.brain.router", "engine.shortcuts", "wake", "engine.core.paths",
           "engine.core.config", "engine.features.messaging", "engine.core.main", "engine.core.command_pipeline", "engine.core.command_helpers")


class LoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {n: sys.modules.get(n) for n in STUBBED}
        cls.addClassCleanup(cls._restore_modules)       # also runs when this setUp fails half way
        for n in STUBBED:
            sys.modules.pop(n, None)

        def mod(name, **attrs):
            m = types.ModuleType(name)
            m.__dict__.update(attrs)
            sys.modules[name] = m
            return m

        cls.log = []        # (kind, text) in the order things happened
        log = cls.log

        def print_chat(role, text, detail=""):
            log.append((role, text))
            if role == "lyra":
                ui._last_lyra_chat = ((text or "").strip(), time.monotonic())

        import logging
        ui = mod("engine.bridge.ui", INPUT_MODE="text", HAS_CONSOLE=False, log=logging.getLogger("t"), print_chat=print_chat,
                 set_state=lambda *a, **k: None, trace=lambda *a, **k: None, _pop_trace=lambda: "",
                 _last_lyra_chat=None)
        never = re.compile(r"(?!x)x")
        mod("engine.voice.voice", speak=lambda text, r=None, s=None: log.append(("spoke", text)) or "",
            SWITCH_TO_TEXT_PATTERN=never, STANDBY_PATTERN=never, NEGATIVE_STANDBY_PATTERN=never, EXIT_PATTERN=never)
        cls.handled = {}    # step text -> reply of the fake action handler

        def try_handle_action(cmd, ctx=None):
            log.append(("action", cmd))
            return cls.handled.get(cmd)

        mod("engine.actions", try_handle_action=try_handle_action, normalize_command=lambda c: c, quick_reply=lambda c: None,
            try_optional_action=lambda c, ctx=None: None, try_learned_open=lambda c, u: None)
        mod("engine.brain.brain", ask_brain=lambda c, h, intent=None: f"(chat) {c}")
        mod("engine.brain.router", classify=lambda c: {"corrected": c, "intent": "chat"})
        mod("engine.shortcuts", wizard_active=lambda: False, try_start_wizard=lambda c: None,
            try_handle_shortcut=lambda c, e=None: None, try_handle_shortcut_exact=lambda c, e=None: None,
            list_shortcuts=lambda: [])
        mod("wake")
        mod("engine.core.paths", data_path=lambda *parts: "/nonexistent/" + "/".join(parts))
        mod("engine.core.config", CONVERSATION_TIMEOUT=1, PAUSE_THRESHOLD=1, LLM_FIRST=False, WAKE_FOLLOWUP_GRACE=1)
        mod("speech_recognition")

        from engine.features import messaging                       # the real patterns; only the chat opening itself is faked
        cls.chats = []

        def open_chat(contact, app, ctx=None):
            log.append(("open_chat", (contact, app)))
            return cls.chat_reply(contact, app)

        messaging.open_chat = open_chat
        cls.chat_reply = staticmethod(lambda contact, app: f"Opened {contact.title()}'s chat on {app.title()}.")
        from engine.core import main
        cls.main = main

    @classmethod
    def _restore_modules(cls):
        for n in STUBBED:
            sys.modules.pop(n, None)
            if cls.saved[n] is not None:
                sys.modules[n] = cls.saved[n]

    def setUp(self):
        self.log.clear()
        self.handled.clear()

    def run_cmd(self, text):
        self.main.process_command_chain(text, [])
        return [x for x in self.log if x[0] != "spoke"]

    def test_discord_then_chat(self):
        self.handled["open discord"] = "Opening Discord, sir."
        out = self.run_cmd("open discord and open anogashi's chat")
        self.assertEqual(out, [
            ("user", "open discord and open anogashi's chat"),
            ("action", "open discord"),
            ("lyra", "Opening Discord, sir."),
            ("open_chat", ("anogashi", "discord")),          # app reused, possessive removed
            ("lyra", "Opened Anogashi's chat on Discord."),
        ])

    def test_three_steps_run_in_order_one_after_the_other(self):
        self.handled["open discord"] = "Opening Discord, sir."
        self.handled["play lofi"] = "Playing lofi."
        out = self.run_cmd("open discord, then open anogashi's chat, then play lofi")
        steps = [x for x in out if x[0] in ("action", "open_chat")]
        self.assertEqual(steps, [("action", "open discord"), ("open_chat", ("anogashi", "discord")),
                                 ("action", "play lofi")])
        # each step's reply comes before the next step starts
        kinds = [x[0] for x in out]
        self.assertEqual(kinds, ["user", "action", "lyra", "open_chat", "lyra", "action", "lyra"])

    def test_failed_step_stops_the_rest_and_reports(self):
        self.handled["open discord"] = "Opening Discord, sir."
        type(self).chat_reply = staticmethod(lambda c, a: f"I couldn't confirm {c.title()}'s chat.")
        try:
            out = self.run_cmd("open discord and open anogashi's chat and play lofi")
        finally:
            type(self).chat_reply = staticmethod(lambda c, a: f"Opened {c.title()}'s chat on {a.title()}.")
        self.assertNotIn(("action", "play lofi"), out)
        final = out[-1][1]
        self.assertEqual(out[-1][0], "lyra")
        self.assertIn("Stopped at step 2 of 3", final)
        self.assertIn("Finished: open discord.", final)
        self.assertIn("Not run: play lofi.", final)

    def test_failure_of_the_last_step_is_summed_up(self):
        self.handled["open discord"] = "Opening Discord, sir."
        type(self).chat_reply = staticmethod(lambda c, a: "I couldn't open Discord.")
        try:
            out = self.run_cmd("open discord and open anogashi's chat")
        finally:
            type(self).chat_reply = staticmethod(lambda c, a: f"Opened {c.title()}'s chat on {a.title()}.")
        self.assertEqual(out[-2], ("lyra", "I couldn't open Discord."))
        self.assertIn("Stopped at step 2 of 2", out[-1][1])
        self.assertIn("Finished: open discord.", out[-1][1])

    def test_plain_command_is_untouched(self):
        self.handled["open discord"] = "Opening Discord, sir."
        out = self.run_cmd("open discord")
        self.assertEqual(out, [("user", "open discord"), ("action", "open discord"), ("lyra", "Opening Discord, sir.")])

    def test_is_open_visual_command(self):
        f = sys.modules["engine.core.command_helpers"]._is_open_visual_command
        self.assertTrue(f("open discord"))
        self.assertFalse(f("open discord and open anogashi's chat"))
        self.assertFalse(f("play lofi"))


if __name__ == "__main__":
    unittest.main()
