"""Tests for Phase 3 (speed): perf.py, fastpath.py, voice.SpeechStream, brain streaming and their use in
main.process_command_chain. From the backend folder:

    python -m unittest tests.test_phase3 -v

Nothing here needs a microphone, a speaker or an API key: the audio engine, the LLM client and the
handlers are fakes, so these check the logic (what is claimed, what runs first, what is streamed, what is
timed), not how fast a real Whisper / TTS / provider is.
"""
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from engine.brain import fastpath
from engine.core import perf

_REAL_LOG = perf.LOG_PATH


def setUpModule():
    """Every test that runs a turn writes its timing line: keep them out of the real %TEMP%\\lyra-timing.jsonl."""
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="lyra-test-timing-")
    os.close(fd)
    perf.LOG_PATH = path


def tearDownModule():
    try:
        os.remove(perf.LOG_PATH)
    except OSError:
        pass
    perf.LOG_PATH = _REAL_LOG


# ---------------------------------------------------------------------------------------------
# fastpath.classify / run
# ---------------------------------------------------------------------------------------------
class ClassifyTests(unittest.TestCase):
    def kind(self, text):
        hit = fastpath.classify(text)
        return hit[0] if hit else None

    def test_plain_commands_are_claimed(self):
        for text, kind in [
            ("volume up", "volume_up"), ("turn it up", "volume_up"), ("Louder.", "volume_up"),
            ("please turn the volume down", "volume_down"), ("quieter", "volume_down"),
            ("set volume to 30", "volume_set"), ("volume 45 percent", "volume_set"), ("set the volume to 80%", "volume_set"),
            ("mute", "mute"), ("unmute the sound", "mute"),
            ("pause", "pause"), ("pause the music", "pause"), ("could you pause it please", "pause"),
            ("play", "play"), ("resume", "play"), ("play it again", "play"), ("unpause", "play"),
            ("next song", "next"), ("skip", "next"), ("skip this song", "next"),
            ("previous track", "previous"), ("go back a song", "previous"),
        ]:
            self.assertEqual(self.kind(text), kind, text)

    def test_volume_is_clamped(self):
        self.assertEqual(fastpath.classify("set volume to 250")[1], 100)

    def test_everything_else_goes_to_the_normal_chain(self):
        for text in [
            "play lofi on spotify", "play some music", "play despacito", "mute chrome", "set chrome volume to 20",
            "pause and open discord", "turn it up a bit and open discord", "open spotify", "next slide", "next",
            "continue", "what is the volume", "pause the timer for ten minutes", "resume my resume writing",
            "", "   ", "volume", "skip to the end of the video and tell me how it ends",
        ]:
            self.assertIsNone(fastpath.classify(text), text)

    def test_long_sentences_are_never_claimed(self):
        self.assertIsNone(fastpath.classify("pause the music now please for me thanks a lot"))


class FakeSystem:
    VK_VOLUME_MUTE, VK_MEDIA_PLAY_PAUSE, VK_MEDIA_NEXT, VK_MEDIA_PREV = 0xAD, 0xB3, 0xB0, 0xB1

    def __init__(self):
        self.calls = []

    def adjust_volume(self, direction, steps=5):
        self.calls.append(("volume", direction, steps))

    def set_volume_level(self, pct):
        self.calls.append(("set", pct))
        return True, f"Volume set to {pct}%."

    def _send_media_key(self, vk):
        self.calls.append(("key", vk))


class FakeCtx:
    last_media_window = "Spotify"

    def __init__(self):
        self.noted = []

    def note_media(self, action, window_title=None, state=None):
        self.noted.append((action, state))


class FakeSession:
    def __init__(self):
        self.ctx = FakeCtx()
        self.refocused = 0

    def get(self):
        return self.ctx

    def current_window_title(self):
        return "Spotify Premium"

    def refocus_media_window(self):
        self.refocused += 1
        return True


class RunTests(unittest.TestCase):
    def setUp(self):
        self.sys = FakeSystem()
        self.sess = FakeSession()
        p1 = mock.patch.object(fastpath, "_system", lambda: self.sys)
        p2 = mock.patch.object(fastpath, "_session", lambda: self.sess)
        p3 = mock.patch.object(fastpath.time, "sleep", lambda s: None)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        perf.finish()

    def test_volume(self):
        self.assertEqual(fastpath.run("volume up"), "Volume up.")
        self.assertEqual(fastpath.run("quieter"), "Volume down.")
        self.assertEqual(fastpath.run("set volume to 30"), "Volume set to 30%.")
        self.assertEqual(self.sys.calls, [("volume", "up", 5), ("volume", "down", 5), ("set", 30)])

    def test_media_keys_and_session_context(self):
        self.assertEqual(fastpath.run("pause"), "Paused on Spotify.")
        self.assertEqual(fastpath.run("next song"), "Skipping to the next track.")
        self.assertEqual(fastpath.run("previous track"), "Going back a track.")
        self.assertEqual(fastpath.run("play"), "Playing on Spotify.")
        self.assertEqual(fastpath.run("play it again"), "Playing again on Spotify.")
        self.assertEqual([c[1] for c in self.sys.calls], [0xB3, 0xB0, 0xB1, 0xB3, 0xB3])
        self.assertEqual(self.sess.ctx.noted, [("pause", "paused"), ("next", None), ("prev", None),
                                               ("play", "playing"), ("play", "playing")])
        self.assertEqual(self.sess.refocused, 1)      # only "play it again" brings the media window back

    def test_not_claimed_means_nothing_runs(self):
        self.assertIsNone(fastpath.run("play lofi on spotify"))
        self.assertEqual(self.sys.calls, [])

    def test_a_failing_action_falls_back_to_the_normal_chain(self):
        def boom(*a, **k):
            raise OSError("no audio device")
        self.sys.adjust_volume = boom
        self.assertIsNone(fastpath.run("volume up"))

    def test_the_turn_is_labelled_fast_and_the_action_is_timed(self):
        perf.begin("test")
        fastpath.run("volume up")
        turn = perf._turn
        self.assertEqual(turn.path, "fast")
        self.assertIn("action", turn.stages)
        perf.finish()


# ---------------------------------------------------------------------------------------------
# perf
# ---------------------------------------------------------------------------------------------
class PerfTests(unittest.TestCase):
    def setUp(self):
        perf.finish()
        fd, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        p = mock.patch.object(perf, "LOG_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def rows(self):
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_no_turn_means_everything_is_a_no_op(self):
        perf.add("stt", 1.0)
        perf.mark("tts_first_audio")
        with perf.span("action"):
            pass
        perf.finish()
        self.assertFalse(os.path.getsize(self.path))

    def test_a_turn_is_logged_once_with_every_stage(self):
        perf.begin("ptt", "volume up")
        perf.add("stt", 0.4)
        with perf.span("llm_route"):
            time.sleep(0.02)
        with perf.span("action"):
            time.sleep(0.01)
        perf.mark("tts_request")
        time.sleep(0.01)
        perf.mark("tts_first_audio")
        perf.finish()
        perf.finish()       # a second finish must not write a second line
        (row,) = self.rows()
        self.assertEqual(row["source"], "ptt")
        self.assertEqual(row["path"], "llm")
        self.assertTrue(row["spoken"])
        self.assertEqual(row["stt_ms"], 400)
        self.assertGreaterEqual(row["llm_route_ms"], 20)
        self.assertGreaterEqual(row["action_ms"], 10)
        self.assertGreaterEqual(row["tts_wait_ms"], 10)
        self.assertGreaterEqual(row["to_audio_ms"], row["llm_route_ms"] + row["action_ms"])
        self.assertGreaterEqual(row["total_ms"], row["to_audio_ms"])

    def test_nested_spans_of_the_same_stage_count_once(self):
        perf.begin("t")
        with perf.span("action"):
            with perf.span("action"):
                time.sleep(0.03)
        self.assertLess(perf._turn.stages["action"], 0.06)
        perf.finish(spoken=False)

    def test_a_marker_keeps_its_first_time(self):
        perf.begin("t")
        perf.mark("tts_first_audio")
        first = perf._turn.points["tts_first_audio"]
        time.sleep(0.02)
        perf.mark("tts_first_audio")
        self.assertEqual(perf._turn.points["tts_first_audio"], first)
        perf.finish()

    def test_path_defaults(self):
        perf.begin("t")
        perf.add("stt", 0.1)
        self.assertEqual(perf.summary(perf._turn)["path"], "local")
        perf.add("llm_chat", 0.1)
        self.assertEqual(perf.summary(perf._turn)["path"], "llm")
        perf.set_path("fast")
        self.assertEqual(perf.summary(perf._turn)["path"], "fast")
        perf.finish(spoken=False)

    def test_an_ignored_utterance_writes_nothing(self):
        perf.begin("wake")
        perf.finish(spoken=False)
        self.assertFalse(os.path.getsize(self.path))

    def test_line_and_report(self):
        for ms in (1000, 3000):
            perf.begin("typed", "hello")
            perf.add("llm_chat", ms / 1000)
            perf.mark("llm_first_token")
            perf.mark("tts_request")
            perf.mark("tts_first_audio")
            perf.finish()
        line = perf.format_line(self.rows()[0])
        self.assertIn("TIMING [typed] 'hello' path=llm", line)
        self.assertIn("llm chat 1000ms (first word", line)
        text = perf.report(self.path)
        self.assertIn("2 turns", text)
        self.assertIn("[llm] 2 turns", text)
        self.assertIn("llm_chat avg 2000", text)

    def test_switched_off_by_config(self):
        cfg = types.ModuleType("engine.core.config")
        cfg.TIMING_LOGS = False
        with mock.patch.dict(sys.modules, {"engine.core.config": cfg}):
            perf.begin("t")
        self.assertFalse(perf.active())


# ---------------------------------------------------------------------------------------------
# voice.SpeechStream, with a fake audio engine
# ---------------------------------------------------------------------------------------------
VOICE_STUBS = ("edge_tts", "pygame", "speech_recognition", "engine.voice.stt", "engine.bridge.ui")


class FakeMusic:
    def __init__(self, played):
        self.played = played
        self.busy = 0

    def load(self, path):
        self.path = path

    def play(self):
        self.played.append(self.path)
        self.busy = 2

    def get_busy(self):
        self.busy -= 1
        return self.busy > 0

    def unload(self):
        pass

    def stop(self):
        pass


class StreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {n: sys.modules.get(n) for n in VOICE_STUBS + ("voice", "tts")}
        cls.addClassCleanup(cls._restore)
        for n in VOICE_STUBS + ("voice", "tts"):
            sys.modules.pop(n, None)
        cls.states = []

        def mod(name, **attrs):
            m = types.ModuleType(name)
            m.__dict__.update(attrs)
            sys.modules[name] = m
            return m

        mod("edge_tts")
        cls.played = []
        mixer = types.SimpleNamespace(music=FakeMusic(cls.played), init=lambda *a, **k: None,
                                      get_init=lambda: True, quit=lambda: None)
        mod("pygame", mixer=mixer, error=Exception)
        mod("speech_recognition", WaitTimeoutError=TimeoutError, Recognizer=object, Microphone=object)
        mod("engine.voice.stt", SpeechRecognizer=lambda: types.SimpleNamespace())
        mod("engine.bridge.ui", log=logging.getLogger("t"), set_state=cls.states.append, emit_wave=lambda *a, **k: None,
            ensure_spoken_chat=lambda t: None, trace=lambda *a, **k: None, INPUT_MODE="text")
        from engine.voice import voice  # noqa: F401  (imports tts, where speak / SpeechStream live)
        cls.voice = sys.modules["engine.voice.tts"]

    @classmethod
    def _restore(cls):
        for n, m in cls.saved.items():
            sys.modules.pop(n, None)
            if m is not None:
                sys.modules[n] = m

    def setUp(self):
        self.synthesized = []
        self.spoken_whole = []
        self.played.clear()
        self.states.clear()
        v = self.voice

        def synth(text):
            self.synthesized.append(text)
            return f"/tmp/fake-{len(self.synthesized)}.mp3"

        for name, fn in {"_synth_chunk": synth, "_ensure_mixer": lambda: None, "_duck_spotify": lambda: [],
                         "_restore_spotify": lambda d: None, "_start_wave": lambda p: None,
                         "_speak_offline": lambda t: self.synthesized.append("OFFLINE " + t),
                         "speak": lambda t, r=None, s=None: self.spoken_whole.append(t)}.items():
            p = mock.patch.object(v, name, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(v.os, "remove", lambda path: None)
        p.start()
        self.addCleanup(p.stop)
        perf.finish()

    def stream(self, pieces, reply=None):
        st = self.voice.SpeechStream()
        for p in pieces:
            st.feed(p)
        return st, st.finish(reply if reply is not None else "".join(pieces))

    def test_sentences_are_spoken_in_order_as_they_arrive(self):
        text = "Sure, here is the answer to your question. It is forty two. And that is all I know about it."
        pieces = [text[i:i + 7] for i in range(0, len(text), 7)]       # arrives in small bits
        st, res = self.stream(pieces)
        self.assertIsNone(res)
        self.assertEqual(self.synthesized, ["Sure, here is the answer to your question.",
                                            "It is forty two. And that is all I know about it."][:len(self.synthesized)])
        self.assertEqual(" ".join(self.synthesized), text)
        self.assertEqual(len(self.played), len(self.synthesized))
        self.assertEqual(self.spoken_whole, [])       # nothing was spoken the whole-reply way
        self.assertEqual(self.states[-1], "idle")
        self.assertIn("speaking", self.states)

    def test_first_sentence_is_synthesised_before_the_model_is_done(self):
        st = self.voice.SpeechStream()
        st.feed("Here is the first sentence of my answer. And the second one is still be")
        deadline = time.time() + 2
        while not self.synthesized and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.synthesized, ["Here is the first sentence of my answer."])
        st.feed("ing written.")
        st.finish("")
        self.assertEqual(self.synthesized[-1], "And the second one is still being written.")

    def test_short_sentences_are_glued_together(self):
        st, _ = self.stream(["Yes. ", "Okay. ", "I can do that for you right now. ", "Done."])
        self.assertEqual(" ".join(self.synthesized), "Yes. Okay. I can do that for you right now. Done.".replace(". ", ". "))
        self.assertTrue(all(len(s) >= 18 for s in self.synthesized[:-1]))      # only the last piece may be short

    def test_a_long_run_without_punctuation_is_cut_at_a_comma(self):
        run = "first this and then that, and after that some more words go here without any full stop, " \
              "and then even more words follow after the second comma so it keeps going and going"
        st, _ = self.stream([run])
        self.assertGreaterEqual(len(self.synthesized), 2)
        self.assertEqual(" ".join(self.synthesized).replace(" ,", ","), run)

    def test_nothing_streamed_speaks_the_whole_reply_the_normal_way(self):
        st = self.voice.SpeechStream()
        st.finish("It is 3 o'clock.")
        self.assertEqual(self.spoken_whole, ["It is 3 o'clock."])
        self.assertEqual(self.synthesized, [])

    def test_unspeakable_text_falls_back_to_the_normal_speak(self):
        st, _ = self.stream(["1. first thing\n", "2. second thing\n"], reply="1. first thing\n2. second thing")
        self.assertEqual(self.synthesized, [])
        self.assertEqual(len(self.spoken_whole), 1)

    def test_a_voice_failure_uses_the_windows_voice(self):
        with mock.patch.object(self.voice, "_synth_chunk", lambda t: None):
            st, _ = self.stream(["This one cannot be synthesised at all."])
        self.assertEqual(self.synthesized, ["OFFLINE This one cannot be synthesised at all."])

    def test_the_turn_is_timed(self):
        perf.begin("test")
        self.stream(["A reasonably long sentence to say out loud."])
        # speak() closes the turn for whole replies; the stream closes it itself
        self.assertFalse(perf.active())

    def test_open_speech_stream_respects_config_and_barge_in(self):
        v = self.voice
        self.assertIsInstance(v.open_speech_stream(None, None), v.SpeechStream)
        cfg = types.ModuleType("engine.core.config")
        cfg.STREAM_TTS = False
        with mock.patch.dict(sys.modules, {"engine.core.config": cfg}):
            self.assertIsNone(v.open_speech_stream(None, None))
        with mock.patch.object(v, "BARGE_IN_ENABLED", True):
            self.assertIsNone(v.open_speech_stream(object(), object()))     # the mic must stay open
            self.assertIsInstance(v.open_speech_stream(None, None), v.SpeechStream)


# ---------------------------------------------------------------------------------------------
# brain.ask_brain streaming, with a fake client
# ---------------------------------------------------------------------------------------------
BRAIN_STUBS = ("openai", "openai.types", "openai.types.chat", "engine.brain.memory", "engine.brain.knowledge", "engine.actions", "engine.voice.voice", "engine.bridge.ui", "engine.brain.brain")


class FakeChunk:
    def __init__(self, text):
        self.choices = [types.SimpleNamespace(delta=types.SimpleNamespace(content=text))]


class FakeClient:
    def __init__(self, pieces, fail_stream_first=False, break_after=None):
        self.pieces, self.fail_stream_first, self.break_after = pieces, fail_stream_first, break_after
        self.calls = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(bool(kw.get("stream")))
        if kw.get("stream"):
            if self.fail_stream_first:
                raise RuntimeError("stream not supported")
            return self._gen()
        msg = types.SimpleNamespace(content="".join(self.pieces))
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=None)

    def _gen(self):
        for i, p in enumerate(self.pieces):
            if self.break_after is not None and i == self.break_after:
                raise ConnectionError("dropped")
            yield FakeChunk(p)


class BrainStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {n: sys.modules.get(n) for n in BRAIN_STUBS}
        cls.addClassCleanup(cls._restore)
        for n in BRAIN_STUBS:
            sys.modules.pop(n, None)

        def mod(name, **attrs):
            m = types.ModuleType(name)
            m.__dict__.update(attrs)
            sys.modules[name] = m
            return m

        class _OpenAI:
            def __init__(self, *a, **k):
                pass

        mod("openai", OpenAI=_OpenAI)
        mod("openai.types")
        mod("openai.types.chat", ChatCompletionMessageParam=dict)
        mod("engine.brain.memory", all_facts=lambda: [])
        mod("engine.brain.knowledge", try_knowledge_shortcut=lambda p: None)
        mod("engine.actions", get_system_status=lambda: "", get_current_window=lambda: "")
        import re as _re
        mod("engine.voice.voice", SMALL_TALK_PATTERN=_re.compile(r"^(?:hi|hello|hey)$", _re.I))
        mod("engine.bridge.ui", log=logging.getLogger("t"), set_state=lambda s: None, trace=lambda *a, **k: None)
        from engine.brain import brain
        cls.brain = brain

    @classmethod
    def _restore(cls):
        for n, m in cls.saved.items():
            sys.modules.pop(n, None)
            if m is not None:
                sys.modules[n] = m

    def ask(self, client, with_stream=True):
        got = []
        b = self.brain
        with mock.patch.object(b, "_get_client", lambda provider=None: (client, "test-model")), \
                mock.patch.object(b, "_style_hint", lambda: ""):
            reply = b.ask_brain("tell me a story about a fox", [], intent="chat", on_text=got.append if with_stream else None)
        return reply, got

    def test_text_is_streamed_and_the_full_reply_returned(self):
        client = FakeClient(["Once ", "upon ", "a time."])
        reply, got = self.ask(client)
        self.assertEqual(got, ["Once ", "upon ", "a time."])
        self.assertEqual(reply, "Once upon a time.")
        self.assertEqual(client.calls, [True])

    def test_without_a_callback_nothing_changes(self):
        client = FakeClient(["Hello ", "there."])
        reply, got = self.ask(client, with_stream=False)
        self.assertEqual((reply, got, client.calls), ("Hello there.", [], [False]))

    def test_a_failing_stream_is_retried_normally_with_nothing_said(self):
        client = FakeClient(["All ", "good."], fail_stream_first=True)
        reply, got = self.ask(client)
        self.assertEqual((reply, got, client.calls), ("All good.", [], [True, False]))

    def test_a_stream_that_breaks_half_way_keeps_what_was_said(self):
        client = FakeClient(["One. ", "Two. ", "Three."], break_after=2)
        reply, got = self.ask(client)
        self.assertEqual(got, ["One. ", "Two. "])
        self.assertEqual(reply, "One. Two.")
        self.assertEqual(client.calls, [True])      # no second request: it would repeat what was said

    def test_the_chat_call_is_timed_and_the_first_word_marked(self):
        perf.finish()
        perf.begin("test")
        self.ask(FakeClient(["Hi ", "you."]))
        self.assertIn("llm_chat", perf._turn.stages)
        self.assertIn("llm_first_token", perf._turn.points)
        perf.finish(spoken=False)


# ---------------------------------------------------------------------------------------------
# The real loop, with fake handlers: what runs first, what skips the LLM
# ---------------------------------------------------------------------------------------------
LOOP_STUBS = ("speech_recognition", "engine.bridge.ui", "engine.voice.voice", "engine.actions", "engine.brain.brain", "engine.brain.router", "engine.shortcuts", "wake", "engine.core.paths",
              "engine.core.config", "engine.features.messaging", "engine.brain.agent", "engine.core.main", "engine.core.command_pipeline", "engine.core.command_helpers")


class FakeStream:
    def __init__(self, log):
        self.log = log

    def feed(self, text):
        self.log.append(("stream", text))

    def finish(self, reply="", recognizer=None, source=None):
        self.log.append(("stream-finish", reply))
        return None


class LoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {n: sys.modules.get(n) for n in LOOP_STUBS}
        cls.addClassCleanup(cls._restore)
        for n in LOOP_STUBS:
            sys.modules.pop(n, None)

        def mod(name, **attrs):
            m = types.ModuleType(name)
            m.__dict__.update(attrs)
            sys.modules[name] = m
            return m

        cls.log = log = []

        def print_chat(role, text, detail=""):
            log.append((role, text))
            if role == "lyra":
                ui._last_lyra_chat = ((text or "").strip(), time.monotonic())

        ui = mod("engine.bridge.ui", INPUT_MODE="text", HAS_CONSOLE=False, log=logging.getLogger("t"), print_chat=print_chat,
                 set_state=lambda *a, **k: None, trace=lambda *a, **k: None, _pop_trace=lambda: "", _last_lyra_chat=None)
        never = re.compile(r"(?!x)x")

        def speak(text, r=None, s=None):
            log.append(("spoke", text))
            perf.finish()
            return ""

        cls.stream_on = False
        mod("engine.voice.voice", speak=speak, SWITCH_TO_TEXT_PATTERN=never, STANDBY_PATTERN=never,
            NEGATIVE_STANDBY_PATTERN=never, EXIT_PATTERN=never,
            open_speech_stream=lambda r=None, s=None: FakeStream(log) if cls.stream_on else None)
        mod("engine.actions", try_handle_action=lambda c, ctx=None: log.append(("action", c)), normalize_command=lambda c: c,
            quick_reply=lambda c: None, try_optional_action=lambda c, ctx=None: None, try_learned_open=lambda c, u: None)

        def ask_brain(c, h, intent=None, on_text=None):
            log.append(("llm-chat", c))
            if on_text:
                for piece in ("It is a fox. ", "It is red."):
                    on_text(piece)
            return "It is a fox. It is red."

        mod("engine.brain.brain", ask_brain=ask_brain)
        mod("engine.brain.router", classify=lambda c: {"corrected": c, "intent": "chat"})
        def route_and_run(c, ctx=None, force=False):
            log.append(("llm-route", c))
            return None, "chat"

        mod("engine.brain.agent", looks_multi_step=lambda c: False, route_and_run=route_and_run)
        mod("engine.shortcuts", wizard_active=lambda: False, try_start_wizard=lambda c: None,
            try_handle_shortcut=lambda c, e=None: None, try_handle_shortcut_exact=lambda c, e=None: None,
            list_shortcuts=lambda: [])
        mod("wake")
        mod("engine.core.paths", data_path=lambda *parts: "/nonexistent/" + "/".join(parts))
        cls.cfg = mod("engine.core.config", CONVERSATION_TIMEOUT=1, PAUSE_THRESHOLD=1, LLM_FIRST=True, WAKE_FOLLOWUP_GRACE=1)
        mod("speech_recognition")
        from engine.features import messaging
        messaging.open_chat = lambda contact, app, ctx=None: "opened"
        from engine.core import main
        cls.main = main

    @classmethod
    def _restore(cls):
        for n, m in cls.saved.items():
            sys.modules.pop(n, None)
            if m is not None:
                sys.modules[n] = m

    def setUp(self):
        self.log.clear()
        self.sys = FakeSystem()
        self.sess = FakeSession()
        for p in (mock.patch.object(fastpath, "_system", lambda: self.sys),
                  mock.patch.object(fastpath, "_session", lambda: self.sess),
                  mock.patch.object(fastpath.time, "sleep", lambda s: None),
                  mock.patch.object(perf, "LOG_PATH", perf.LOG_PATH)):
            p.start()
            self.addCleanup(p.stop)
        type(self).stream_on = False
        self.cfg.__dict__.pop("FAST_PATH", None)
        perf.finish()

    def run_cmd(self, text):
        self.main.process_command_chain(text, [])
        return list(self.log)

    def test_volume_up_skips_the_llm_and_the_action_runs_before_the_voice(self):
        out = self.run_cmd("volume up")
        self.assertEqual(out, [("user", "volume up"), ("lyra", "Volume up."), ("spoke", "Volume up.")])
        self.assertEqual(self.sys.calls, [("volume", "up", 5)])        # done once, before speaking
        self.assertFalse([x for x in out if x[0].startswith("llm")])

    def test_pause_and_next_skip_the_llm(self):
        for text, reply in (("pause", "Paused on Spotify."), ("next song", "Skipping to the next track.")):
            self.log.clear()
            out = self.run_cmd(text)
            self.assertEqual(out[-1], ("spoke", reply))
            self.assertFalse([x for x in out if x[0].startswith("llm")])

    def test_anything_else_still_goes_through_the_llm(self):
        out = self.run_cmd("play lofi on spotify")
        self.assertIn(("llm-route", "play lofi on spotify"), out)
        self.assertEqual(self.sys.calls, [])

    def test_fast_path_can_be_switched_off(self):
        self.cfg.FAST_PATH = False
        out = self.run_cmd("volume up")
        self.assertIn(("llm-route", "volume up"), out)
        self.assertEqual(self.sys.calls, [])

    def test_chat_answer_is_streamed_into_the_voice(self):
        type(self).stream_on = True
        out = self.run_cmd("why is the sky blue")
        kinds = [x[0] for x in out]
        self.assertEqual(kinds, ["user", "llm-route", "llm-chat", "stream", "stream", "lyra", "stream-finish"])
        self.assertNotIn("spoke", kinds)                         # not spoken the whole-reply way
        self.assertEqual(out[-1], ("stream-finish", "It is a fox. It is red."))

    def test_without_a_stream_the_answer_is_spoken_whole_afterwards(self):
        out = self.run_cmd("why is the sky blue")
        self.assertEqual([x[0] for x in out], ["user", "llm-route", "llm-chat", "lyra", "spoke"])

    def test_a_turn_is_timed_end_to_end(self):
        perf.begin("test")
        self.run_cmd("volume up")
        with open(perf.LOG_PATH, encoding="utf-8") as fh:
            row = json.loads(fh.readlines()[-1])
        self.assertEqual((row["path"], row["text"]), ("fast", "volume up"))
        self.assertIsNotNone(row["action_ms"])


if __name__ == "__main__":
    unittest.main()
