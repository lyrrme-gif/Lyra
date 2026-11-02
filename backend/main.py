"""Lyra — top-level loops: voice mode, text mode, and the shared
command-processing chain that both feed into."""

import os
import re
import sys
import time
import threading

# Windowed (no-console) build: PyInstaller leaves stdin/stdout/stderr as None, and
# libraries that write progress bars or warnings would crash on that.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r")

# Hide the console window of every child process (powershell, shutdown, ffmpeg...). Must stay first.
import no_console  # noqa: F401

# pydub is imported by the audio/wake-word stack on some installations.
# Give it a bundled ffmpeg binary when available so startup does not emit the
# misleading "Couldn't find ffmpeg" warning. The recognition path itself uses
# WAV bytes and does not require ffmpeg.
try:
    import imageio_ffmpeg
    os.environ.setdefault("FFMPEG_BINARY", imageio_ffmpeg.get_ffmpeg_exe())
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        from pydub import AudioSegment as _AS
    _AS.converter = os.environ["FFMPEG_BINARY"]
except Exception:
    pass

import speech_recognition as sr

import ui
import voice
import actions
import brain
import router
import shortcuts
import paths
import compound
import perf
import fastpath
import convo_idle
from config import LLM_FIRST

# Explicit definition / meaning questions — these ARE allowed to go to the LLM.
_DEFINE_PATTERN = re.compile(
    r"\b("
    r"what\s+does\s+.+\s+mean"
    r"|what(?:'s|\s+is)\s+the\s+meaning\s+of\b"
    r"|what(?:'s|\s+is)\s+(?:a|an|the)\s+\w+"
    r"|define\s+\w+"
    r"|explain\s+(?:what\s+)?\w+"
    r"|what\s+does\s+\w+\s+stand\s+for"
    r")\b",
    re.IGNORECASE,
)

# Greetings that should get a short friendly reply, not a clarification.
_GREETING_PATTERN = re.compile(
    r"^(hi|hello|hey|good\s+(morning|afternoon|evening)|howdy|yo|sup)[\s.!]*$",
    re.IGNORECASE,
)


# --- YouTube "play X" detection (runs before the LLM planner) -------------------------
_YT_STATE = {"q": ""}  # last query we opened, so "play the first video" can follow up
_YT_PLAY_PATTERNS = (
    # "open/play/watch X on YouTube" and the common shorthand "on YT".
    r"^(?:please\s+)?(?:open\s+)?youtube\s+(?:and\s+)?(?:play|watch)\s+(.+)$",
    r"^(?:please\s+)?(?:play|watch|open)\s+(.+?)\s+(?:on|in|from|using|with)\s+(?:the\s+)?(?:youtube|yt)$",
    r"^(?:please\s+)?(?:open)\s+(.+?)\s+(?:on|in|from|using|with)\s+(?:the\s+)?(?:youtube|yt)$",
    r"^(?:please\s+)?(?:search|find)\s+(.+?)\s+on\s+(?:youtube|yt)\s+and\s+(?:play|watch)(?:\s+it)?$",
)
_YT_FOLLOWUP = r"^(?:please\s+)?(?:play|open|click|start|watch)\s+(?:the\s+)?(?:first|top)\s+(?:video|result)$"


def _youtube_play_query(command: str) -> str:
    """Return the search query if the command asks to play something on YouTube, else ''."""
    c = (command or "").strip().rstrip(".!?").strip()
    if not c:
        return ""
    for pat in _YT_PLAY_PATTERNS:
        m = re.match(pat, c, re.I)
        if m and m.group(1).strip():
            return m.group(1).strip()
    if _YT_STATE["q"] and re.match(_YT_FOLLOWUP, c, re.I):
        return _YT_STATE["q"]
    return ""


def _maybe_ask_for_clarification(command: str) -> str:
    """If the utterance is too short / vague to be a real request, return a
    short clarifying reply instead of letting the LLM define the word.
    Returns '' when the command should proceed normally."""
    text = (command or "").strip()
    if not text:
        return ""

    # User explicitly asked what something means → let the brain handle it.
    if _DEFINE_PATTERN.search(text):
        return ""

    # Simple greeting → brief response without going full dictionary mode.
    if _GREETING_PATTERN.match(text):
        return "Hello, sir. What can I do for you?"

    # If we're waiting on a follow-up answer, never treat short replies as "huh?"
    try:
        import session_context
        if session_context.get().has_pending():
            return ""
    except Exception:
        pass

    # Word count heuristic: 1–2 tokens with no action-y verbs → clarify.
    words = re.findall(r"[a-zA-Z0-9']+", text)
    if len(words) <= 2:
        low = text.lower()
        actiony = (
            "open", "close", "play", "pause", "stop", "volume", "mute",
            "lock", "screenshot", "shutdown", "restart", "search", "go",
            "launch", "start", "stop", "next", "previous", "skip",
            "watch", "screen", "click", "describe",
            "spotify", "youtube", "chrome", "discord", "whatsapp",
            "yes", "no", "yeah", "yep", "nope", "ok", "okay",
        )
        if any(a in low for a in actiony):
            return ""
        return "What would you like me to do with that, sir?"

    return ""


_AFFIRM_RE = re.compile(r"(yes|yeah|yep|yup|sure|ok|okay|please|go ahead|do it|yes please|please do|sure thing)[\s.!,]*", re.I)
_NEGATE_RE = re.compile(r"(no|nope|nah|no thanks|no thank you|don't|never mind|nevermind)[\s.!,]*", re.I)
# "Should I start playback?" -> "start playback"
_OFFER_RE = re.compile(
    r"\b(?:should i|shall i|want me to|would you like me to|do you want me to)\s+(.+?)[\s?.!]*$", re.I)


def _offer_to_command(question: str) -> str:
    """Turn Lyra's own offer into the command a bare 'yes' means."""
    q = (question or "").strip()
    if not q:
        return ""
    last = re.split(r"(?<=[.!?])\s+", q)[-1]
    m = _OFFER_RE.search(last)
    if m:
        cmd = m.group(1).strip()
        return cmd[:1].upper() + cmd[1:]
    return f"Yes, go ahead. (You asked: {q})"


# Spoken filler / greeting at the start of an utterance: "hello uhm who is tom holland" -> "who is tom holland".
_LEADING_FILLER_RE = re.compile(r"^(?:(?:hello|hi|hey|uh+|um+|uhm+|hm+|erm|well|so|okay|ok)[\s,.!?\u2026-]+)+", re.I)
# A greeting / filler and nothing else: never a request worth remembering.
_GREETING_ONLY_RE = re.compile(r"^(?:(?:hello|hi|hey|hiya|yo|good\s+(?:morning|afternoon|evening)|uh+|um+|uhm+|hm+|erm|lyra|again|there)[\s,.!?\u2026-]*)+$", re.I)
# What a person says when it is a whole new request, not the missing piece of the last one.
_FRESH_REQUEST_RE = re.compile(
    r"^(?:who|what|when|where|why|how|which|whose|who's|what's|how's|search|google|look|find|open|close|launch|start|"
    r"play|pause|stop|resume|skip|tell|show|set|turn|take|send|call|text|remind|can you|could you|would you|please)\b", re.I)
# Lyra's open invitations are not clarifying questions: nothing is waiting to be completed.
_OPEN_OFFER_RE = re.compile(
    r"\b(?:how (?:can|may|could) i (?:help|assist)|what can i (?:do|help)|anything (?:else )?(?:i can|you)|"
    r"what(?:'s| is) on your mind|how(?:'s| is) it going|how are you)\b", re.I)


def _strip_leading_fillers(text: str) -> str:
    stripped = _LEADING_FILLER_RE.sub("", (text or "").strip(), count=1).strip()
    return stripped or (text or "").strip()


def _combine_pending(pending: str, answer: str, kind: str | None, question: str = "") -> str:
    """Turn a short follow-up into a full command using the pending intent.

    `question` is what Lyra actually asked. A bare yes/no answers THAT question,
    not the user's earlier message (which may just be a remark like "its not").
    """
    pend = (pending or "").strip()
    ans = (answer or "").strip()
    low_ans = ans.lower()
    if question and _AFFIRM_RE.fullmatch(low_ans):
        cmd = _offer_to_command(question)
        if cmd:
            # "Want me to look it up?" -> re-run the ORIGINAL request so brain.py searches it
            if pend and re.search(r"\b(look|search|google|find|check)\b", cmd, re.I):
                return pend
            return cmd
    if question and _NEGATE_RE.fullmatch(low_ans):
        return ans
    if not pend:
        return ans
    # "hello" -> "How can I help?" -> "uhm" -> "who is tom holland": the last part is a NEW request. It must not be
    # glued onto the old words ("hello uhm who is tom holland" ended up as the search query).
    cleaned = _strip_leading_fillers(ans)
    if _GREETING_ONLY_RE.fullmatch(pend) or _FRESH_REQUEST_RE.match(cleaned):
        return cleaned
    # A plain yes/no to "want me to look it up?" etc.: "yes" re-runs the original
    # request unchanged (brain.py then knows to search); "no" stays "no".
    if not re.search(r"\b(play|listen|song|track|music)\b", pend, re.I):
        if re.fullmatch(r"(yes|yeah|yep|yup|sure|ok|okay|please|go ahead|do it|yes please|please do|sure thing)[\s.!,]*", low_ans):
            return pend
        if re.fullmatch(r"(no|nope|nah|no thanks|no thank you|don't|never mind|nevermind)[\s.!,]*", low_ans):
            return ans
    # "spotify" / "youtube" after a play request
    if kind == "play_where" or re.search(r"\b(play|listen|song|track|music)\b", pend, re.I):
        if re.search(r"\b(on|in|with|using)\b", pend, re.I):
            return f"{pend} {ans}"
        return f"{pend} on {ans}"
    # Generic: append the answer
    return f"{pend} {ans}"


def _looks_like_question(reply: str) -> bool:
    r = (reply or "").strip()
    if not r:
        return False
    if _OPEN_OFFER_RE.search(r):
        return False
    if "?" in r:
        return True
    return bool(re.search(
        r"\b(which|where|what (?:app|site)|spotify or|youtube or|let me know)\b",
        r, re.I,
    ))


def _infer_pending_kind(user_cmd: str, reply: str) -> str:
    blob = f"{user_cmd} {reply}".lower()
    if re.search(r"\b(spotify|youtube|music|play|track|song)\b", blob):
        return "play_where"
    return "general"


def _remember_followup(request: str, reply: str) -> None:
    """If Lyra's reply is a question, remember the original request so a
    short answer can complete it; otherwise clear any pending request."""
    try:
        import session_context
        if _looks_like_question(reply) and not _GREETING_ONLY_RE.fullmatch((request or "").strip()):
            session_context.get().set_pending(request, _infer_pending_kind(request, reply), question=reply)
        else:
            session_context.get().clear_pending()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Command processing (shared by voice mode and text mode)
# ---------------------------------------------------------------------------
def _is_music_recognition_command(command: str) -> bool:
    try:
        from actions.system import RECOGNIZE_MUSIC_PATTERN
        return bool(RECOGNIZE_MUSIC_PATTERN.search(command or ""))
    except Exception:
        return False


def _is_open_visual_command(command: str) -> bool:
    """Return True for explicit open/launch commands that should be surfaced
    by the Island instead of becoming a spoken LLM response.

    The action router remains authoritative: this only decides whether a
    successful open action should be silent in chat/TTS. Keeping the check
    broad also covers installed apps whose names are not in the built-in list.
    """
    if not re.match(r"^\s*(?:please\s+)?(?:open|launch|start)\s+.+$", command or "", re.I):
        return False
    # "open discord and open Sara's chat" is two steps, not one open command.
    return len(compound.split_compound(command)) < 2

# >>> phase-L begin
def _chat_pregate_on() -> bool:
    """config.CHAT_PREGATE (default on): exact small talk skips the planner round trip."""
    try:
        import config
        return bool(getattr(config, "CHAT_PREGATE", True))
    except Exception:
        return True
# <<< phase-L end


def _fast_path_on() -> bool:
    """config.FAST_PATH (default on): plain volume / media commands skip the LLM."""
    try:
        import config
        return bool(getattr(config, "FAST_PATH", True))
    except Exception:
        return True


def _speech_stream(recognizer, source):
    """A voice.SpeechStream for the next chat answer, or None (STREAM_TTS off, barge-in on, or not available):
    the answer is then spoken whole, after the model has finished, as before."""
    opener = getattr(voice, "open_speech_stream", None)
    try:
        return opener(recognizer, source) if opener else None
    except Exception as e:
        ui.log.warning("speech stream: %s", e)
        return None


def _ask_brain(prompt: str, history: list, intent: str, stream) -> str:
    """brain.ask_brain; with a stream, the model's text is spoken sentence by sentence while it is written."""
    if stream is None:
        return brain.ask_brain(prompt, history, intent=intent)
    return brain.ask_brain(prompt, history, intent=intent, on_text=stream.feed)


def _say(reply: str, stream, recognizer, source):
    """Speak a chat answer: finish the stream (it says what is left and waits), or speak the whole reply."""
    if stream is None:
        return voice.speak(reply, recognizer, source)
    return stream.finish(reply, recognizer, source)


def _results_on_island(reply: str) -> bool:
    """The island is showing search results: no caption and no speech for the announcement."""
    try:
        import yt_island
        return yt_island.is_shown_reply(reply)
    except Exception:
        return False

def _confirmed_text_action(cmd: str):
    """Re-run a previously confirmed text command with confirmation bypassed once."""
    ctx={"confirmed":True}
    try:
        return actions.try_handle_action(cmd,ctx)
    except Exception as e:
        return f"Confirmed action failed: {e}"

def _chain_split_allowed(command: str) -> bool:
    """True when `command` may be split into steps. Not while something else owns the next
    utterance (dictation, a shortcut wizard, a clarifying question, translation, circle-to-ask),
    and not for sentences another handler parses as a whole: a saved shortcut's trigger,
    a reminder or routine, or a plan the planner claims."""
    t = (command or "").strip().lower().rstrip(".!? ")
    try:
        import dictation
        if dictation.is_active():
            return False
    except Exception:
        pass
    try:
        if shortcuts.wizard_active():
            return False
        for sc in shortcuts.list_shortcuts():
            if t in [str(x).strip().lower() for x in sc.get("triggers", [])]:
                return False
    except Exception:
        pass
    try:
        import translate_mode
        if translate_mode.is_active():
            return False
    except Exception:
        pass
    try:
        import circle_ask
        if circle_ask.has_pending():
            return False
    except Exception:
        pass
    try:
        import session_context
        if session_context.get().has_pending() and len(re.findall(r"[a-zA-Z0-9']+", command)) <= 6:
            return False
    except Exception:
        pass
    try:
        import schedule_parse
        if schedule_parse.parse_oneshot(command) or schedule_parse.parse_routine(command) or schedule_parse.parse_alarm(command):
            return False
    except Exception:
        pass
    try:
        import planner
        if planner.should_plan(command):
            return False
    except Exception:
        pass
    return True


def _chain_failure(chain, mark, include_reply: bool = True) -> str:
    """The message to give when the chain step that just ran did not finish, else "".
    Handlers return text, not a status, so the step's reply (the last Lyra chat line printed
    since `mark`) is what tells us."""
    cur = getattr(ui, "_last_lyra_chat", None)
    reply = cur[0] if cur and cur is not mark else ""
    if not compound.looks_failed(reply):
        return ""
    return chain.failure_message(reply, include_reply)


def _mark_info(text: str) -> None:
    """A real answer (not a task result): the Mochi sounds only replace the short stock acknowledgements for it."""
    try:
        import mochi_sounds
        mochi_sounds.mark_info(text)
    except Exception:
        pass


def process_command_chain(command: str, conversation_history: list, recognizer=None, source=None) -> bool:
    """Run one command through the chain (see _process_command_chain) and close its timing line."""
    if not perf.active():
        perf.begin("text", command)     # typed in the console: no speech, the clock starts now
    perf.set_text(command)
    convo_idle.reset_if_idle(conversation_history)   # a long quiet spell since the last turn: start a fresh chat
    try:
        return _process_command_chain(command, conversation_history, recognizer, source)
    finally:
        convo_idle.touch()              # the quiet spell is counted from the END of this turn
        perf.finish(spoken=False)       # no-op when speak() already closed the turn


def _process_command_chain(command: str, conversation_history: list, recognizer=None, source=None) -> bool:
    """Process `command`, and in voice mode keep chaining directly into any
    barge-in interruption without waiting for a fresh voice.listen(). Prints each
    turn to the chat transcript. Returns True if we should go back to
    standby (voice mode only — text mode ignores the return value)."""

    # Build engine context for permission confirmations. Typed UI confirmations
    # are resolved by the same PermissionManager instead of blocking on stdin.
    engine_context = {"recognizer": recognizer, "source": source} if recognizer and source else {}
    running = {"cmd": command}   # the step being run: a confirmation re-runs this, not the whole sentence
    if ui.INPUT_MODE == "text" or not (recognizer and source):
        engine_context["confirm_callback"] = lambda cmd=None: _confirmed_text_action(cmd or running["cmd"])
    try:
        from permissions import PermissionManager
        pm = PermissionManager()
        if pm.pending_text_confirmation():
            answer = command.strip()
            if re.fullmatch(r"(yes|y|yeah|yep|sure|ok|okay|confirm|go ahead|do it|no|n|nope|nah|cancel|stop)", answer, re.I):
                ui.print_chat("user", command)
                result = pm.resolve_text_confirmation(answer)
                if result:
                    ui.print_chat("lyra", result)
                    return True
    except Exception as e:
        ui.log.warning("text confirmation resolution: %s", e)

    # A compound command ("open discord and open anogashi's chat") is split into steps by
    # compound.split_compound and run one at a time through the handlers below. Every branch
    # below ends in `continue` once it has shown and spoken its reply, so the next pass through
    # this loop starts only after the previous step has finished.
    chain = None              # compound.Chain of the compound command being run, if any
    pending_steps: list = []  # its steps that have not started yet (the same list as chain.pending)
    chain_mark = None         # last Lyra chat line before the running step; a newer one is its reply

    while command or pending_steps:
        command = (command or "").strip()
        if command and chain is not None:
            # A new command while a chain exists: the user spoke over it. Drop what was left.
            if pending_steps:
                ui.trace(f"chain: interrupted, dropped {len(pending_steps)} step(s)")
            chain, pending_steps = None, []
        if not command:
            if not pending_steps:
                break
            # The previous step is over. Go on only if it worked; otherwise say where it stopped.
            stop_msg = _chain_failure(chain, chain_mark)
            if stop_msg:
                chain, pending_steps = None, []
                ui.print_chat("lyra", stop_msg)
                command = voice.speak(stop_msg, recognizer, source) or ""
                continue
            command = chain.next_step()
            ui.trace(f"chain: step {len(chain.ran)}/{chain.total}: {command!r}")
        else:
            ui.print_chat("user", command)
            if _chain_split_allowed(command):
                steps = compound.split_compound(command)
                if len(steps) >= 2:
                    chain = compound.Chain(steps)
                    pending_steps = chain.pending
                    command = chain.next_step()
                    ui.trace(f"chain: {len(steps)} steps; step 1/{chain.total}: {command!r}")
        running["cmd"] = command
        chain_mark = getattr(ui, "_last_lyra_chat", None)
        # Voice turns reach this shared command path without going through the
        # UI chat handler, so publish the processing state here as well. This
        # drives the island's thinking Mochi and the caption window indicator.
        ui.set_state("thinking")

        # Follow-up answers to Lyra's last clarifying question
        # e.g. user: "play X" → Lyra: "Spotify or YouTube?" → user: "spotify"
        try:
            import session_context
            sctx = session_context.get()
            if sctx.has_pending() and len(re.findall(r"[a-zA-Z0-9']+", command)) <= 6:
                question = getattr(sctx, "pending_question", "") or ""
                pending, kind = sctx.take_pending()
                if pending:
                    combined = _combine_pending(pending, command, kind, question)
                    ui.trace(f"follow-up: {command!r} + pending {pending!r} -> {combined!r}")
                    command = combined
        except Exception as e:
            ui.log.warning("pending follow-up: %s", e)

        # Circle to ask: a circled part of the screen is waiting for its question (the default
        # "What is this?" after a circle, or whatever you say or type next).
        try:
            import circle_ask
            if circle_ask.has_pending():
                circle_reply = circle_ask.answer(command)
                if circle_reply:
                    # A real ANSWER about the picture: it is spoken, shown as a subtitle and the island shows her talking.
                    # Without this a short explanation counted as a "task result" and became a silent Mochi sound.
                    _mark_info(circle_reply)
                    ui.print_chat("lyra", circle_reply)
                    command = voice.speak(circle_reply, recognizer, source) or ""
                    continue
        except Exception as e:
            ui.log.warning("circle to ask: %s", e)

        # Screen guide: Lyra is acting on the screen and asked "yes or no?" about the next step.
        try:
            import guide
            guide_reply = guide.answer_pending(command)
            if guide_reply:
                ui.print_chat("lyra", guide_reply)
                command = voice.speak(guide_reply, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("screen guide answer: %s", e)

        # Music recognition is an explicit, high-priority command. Handle it
        # before LLM-first routing so phrases like "what's the playing song"
        # can never turn into a normal chat answer. The Island owns the visual
        # result; do not speak a duplicate assistant response.
        if _is_music_recognition_command(command):
            try:
                action_reply = actions.try_handle_action(command, engine_context)
                if action_reply:
                    try:
                        conversation_history.append({"user": command, "lyra": "[music recognition shown on Island]"})
                    except Exception:
                        pass
                    command = ""
                    continue
            except Exception as e:
                ui.log.warning("music recognition fast path: %s", e)

        # Priority 0.65: scheduled actions and routines ("remind me to call Mom tomorrow at 9",
        # "every morning at 9:00 open Unity and Spotify", "what's scheduled"). Relative times
        # ("in 20 minutes") are left to the timers just below.
        try:
            import scheduler
            try:
                import tab_sync                  # "add dentist to my calendar tomorrow at 9" -> a scheduled reminder
                _sched_cmd = tab_sync.rewrite(command)
            except Exception:
                _sched_cmd = command
            sched_reply = scheduler.handle_command(_sched_cmd)
            try:
                sched_reply = tab_sync.calendar_reply(command, _sched_cmd, sched_reply)
            except Exception:
                pass
        except Exception as e:
            ui.log.warning("scheduler: %s", e)
            sched_reply = None
        if sched_reply:
            try:
                import tab_sync                  # added -> Calendar tab opens with the "added" animation
                tab_sync.open_for(command, sched_reply, "schedule")
            except Exception:
                pass
            ui.print_chat("lyra", sched_reply)
            command = voice.speak(sched_reply, recognizer, source) or ""
            continue

        # Wallpaper tab by voice: "change my wallpaper", "open the wallpapers".
        try:
            import wallpaper
            wp_reply = wallpaper.handle_command(command)
        except Exception as e:
            ui.log.warning("wallpaper: %s", e)
            wp_reply = None
        if wp_reply:
            ui.print_chat("lyra", wp_reply)
            command = voice.speak(wp_reply, recognizer, source) or ""
            continue

        # Appearance + behaviour settings by voice: "set dark mode", "set accent to blue", "speak faster".
        try:
            import settings_voice
            sv_reply = settings_voice.handle_command(command)
        except Exception as e:
            ui.log.warning("settings voice: %s", e)
            sv_reply = None
        if sv_reply:
            ui.print_chat("lyra", sv_reply)
            command = voice.speak(sv_reply, recognizer, source) or ""
            continue

        # Controls tab by voice: "turn off bluetooth", "dark mode on", "open the controls".
        try:
            import controls
            ctl_reply = controls.handle_command(command)
        except Exception as e:
            ui.log.warning("controls: %s", e)
            ctl_reply = None
        if ctl_reply:
            ui.print_chat("lyra", ctl_reply)
            command = voice.speak(ctl_reply, recognizer, source) or ""
            continue

        # Priority 0.7: timers — keep timer commands deterministic and instant.
        # The timer backend owns the notification when a timer finishes; no Timer tab
        # or countdown UI is required here.
        try:
            import timers
            with perf.span("action"):
                timer_reply = timers.handle_command(command)
        except Exception as e:
            ui.log.warning("timers: %s", e)
            timer_reply = None
        if timer_reply:
            try:
                import tab_sync                  # "how much time is left" -> answer + the Timer tab
                tab_sync.open_for(command, timer_reply, "timer")
            except Exception:
                pass
            ui.print_chat("lyra", timer_reply)
            command = voice.speak(timer_reply, recognizer, source) or ""
            continue

        # Check for mode switching commands
        if ui.HAS_CONSOLE and voice.SWITCH_TO_TEXT_PATTERN.search(command):
            ui.print_chat("lyra", "Switching to text mode, sir.")
            voice.speak("Switching to text mode, sir.")
            ui.switch_to_text_mode()
            return True  # Exit voice mode

        if voice.STANDBY_PATTERN.search(command) and not voice.NEGATIVE_STANDBY_PATTERN.search(command):
            ui.print_chat("lyra", "Going back to standby, sir.")
            voice.speak("Going back to standby, sir.")
            return True

        if voice.EXIT_PATTERN.search(command):
            ui.print_chat("lyra", "Powering down systems. Goodbye, sir.")
            voice.speak("Powering down systems. Goodbye, sir.")
            sys.exit()

        # Shortcut create/edit/delete wizard — must be checked before
        # shortcut/command matching, otherwise a wizard reply like "yes" or
        # "open chrome" (an action step, not a command to run right now)
        # would get swallowed by the normal matching below instead of
        # advancing the wizard.
        if shortcuts.wizard_active():
            wiz_reply = shortcuts.handle_wizard_turn(command)
            detail = ui._pop_trace()
            ui.print_chat("lyra", wiz_reply, detail)
            command = voice.speak(wiz_reply, recognizer, source) or ""
            continue

        wiz_reply = shortcuts.try_start_wizard(command)
        if wiz_reply:
            detail = ui._pop_trace()
            ui.print_chat("lyra", wiz_reply, detail)
            command = voice.speak(wiz_reply, recognizer, source) or ""
            continue
            continue


        # Open-chat commands must be handled before shortcuts and the planner;
        # otherwise a loose shortcut/app match can turn "open X chat" into
        # simply opening Discord (or another unrelated app).
        try:
            import messaging
            chat_open_reply = messaging.try_open_chat(command, engine_context)
            if chat_open_reply:
                ui.print_chat("lyra", chat_open_reply)
                command = voice.speak(chat_open_reply, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("messaging open-chat: %s", e)

        # Priority 1: Check shortcuts (exact match)
        shortcut_reply = shortcuts.try_handle_shortcut(command, engine_context)
        if shortcut_reply:
            detail = ui._pop_trace()
            ui.print_chat("lyra", shortcut_reply, detail)
            command = voice.speak(shortcut_reply, recognizer, source) or ""
            continue

        # Deterministic send-message and media paths run before the planner.
        try:
            import messaging
            send_reply=messaging.try_handle(command, engine_context)
            if send_reply:
                ui.print_chat("lyra",send_reply)
                conversation_history.append({"user":command,"lyra":send_reply})
                command=voice.speak(send_reply,recognizer,source) or ""
                continue
        except Exception as e:
            ui.log.warning("messaging: %s",e)
        # Spotify: "search X on Spotify" shows song cards on the island; "play X on Spotify" plays the
        # best match straight away (no search, same "Playing ..." banner). Typed and spoken alike.
        # If nothing is found, fall through to the old direct-play path below.
        _sp_mode, _sp_q = "", ""
        try:
            import spotify_island as _spotify_island
            _sp_mode, _sp_q = _spotify_island.parse_intent(command)
        except Exception as e:
            ui.log.warning("spotify parse: %s", e)
        if _sp_q:
            if _sp_mode == "play":
                reply = _spotify_island.play_query(_sp_q)
                ui.trace(f"spotify island: playing {_sp_q!r}" if reply else f"spotify island: nothing to play for {_sp_q!r}")
            else:
                reply = _spotify_island.show(_sp_q)
                ui.trace(f"spotify island: cards for {_sp_q!r}" if reply else f"spotify island: no cards for {_sp_q!r}")
            if reply:
                conversation_history.append({"user": command, "lyra": reply})
                ui.print_chat("lyra", reply)
                command = voice.speak(reply, recognizer, source) or ""
                continue
            ui.trace(f"spotify island: falling back to direct play for {_sp_q!r}")
        # YouTube lookup/search is deliberately separate from playback.
        # "look for/search/find X on YouTube" should show real result cards in
        # Chat without opening a video; "play X on YouTube" remains the direct
        # playback path below.
        _yt_search_q = ""
        try:
            import yt_island as _yt_island
            _yt_search_q = _yt_island.parse_query(command)
        except Exception as e:
            ui.log.warning("youtube parse: %s", e)
        if _yt_search_q:
            q = _yt_search_q
            reply = _yt_island.show(q) or f"I couldn't find any YouTube results for {q}."
            conversation_history.append({"user": command, "lyra": reply})
            if _results_on_island(reply):
                command = ""  # results are on the island: say nothing
                continue
            ui.print_chat("lyra", reply)
            command = voice.speak(reply, recognizer, source) or ""
            continue

        # "search the web / on Google for X": result cards on the island, and Lyra says a few words.
        _web_q = ""
        try:
            import web_island as _web_island
            _web_q = _web_island.parse_query(command)
        except Exception as e:
            ui.log.warning("web search parse: %s", e)
        if _web_q:
            # The results go to the island first, then Lyra talks over them (caption + voice wave).
            reply = _web_island.show(_web_q) or f"I couldn't find any results for {_web_q}."
            conversation_history.append({"user": command, "lyra": reply})
            ui.print_chat("lyra", reply)
            command = voice.speak(reply, recognizer, source) or ""
            continue

        # "play the first result" right after a YouTube search on the island
        try:
            import yt_island as _yt_island2
            _pick = _yt_island2.parse_pick(command)
            _pick_reply = _yt_island2.play_pick(_pick) if _pick is not None else ""
        except Exception as e:
            ui.log.warning("youtube pick: %s", e)
            _pick_reply = ""
        if _pick_reply:
            ui.print_chat("lyra", _pick_reply)
            conversation_history.append({"user": command, "lyra": _pick_reply})
            command = voice.speak(_pick_reply, recognizer, source) or ""
            continue

        # "open all those searches": every result now on the island, not a guess by the planner.
        try:
            import yt_island as _yt_island3
            _all_reply = _yt_island3.open_all() if _yt_island3.parse_open_all(command) else ""
        except Exception as e:
            ui.log.warning("open all results: %s", e)
            _all_reply = ""
        if _all_reply:
            ui.print_chat("lyra", _all_reply)
            conversation_history.append({"user": command, "lyra": _all_reply})
            command = voice.speak(_all_reply, recognizer, source) or ""
            continue

        _yt_q = _youtube_play_query(command)
        if _yt_q:
            q = _yt_q
            try:
                from actions.apps import play_youtube
                ok,detail=play_youtube(q)
                if ok:
                    _YT_STATE["q"] = q
                reply=f"Opened the first YouTube result for {q}." if ok else detail
            except Exception as e:
                reply=f"I couldn't open that YouTube video: {e}"
            ui.print_chat("lyra",reply); conversation_history.append({"user":command,"lyra":reply})
            command=voice.speak(reply,recognizer,source) or ""
            continue

        # Priority 1.25: explicit app/site opens must reach the deterministic
        # action router before LLM-first routing. The activity HUD still shows
        # the polished Opening… animation, but the command ALSO completes a
        # normal chat turn so the composer is unlocked and the user gets an
        # honest answer (e.g. “Opening Spotify, sir.”).
        if _is_open_visual_command(command):
            try:
                open_reply = actions.try_handle_action(command, engine_context)
                if open_reply and _results_on_island(open_reply):
                    command = ""
                    continue
                if open_reply:
                    detail = ui._pop_trace()
                    ui.print_chat("lyra", open_reply, detail)
                    try:
                        conversation_history.append({"user": command, "lyra": open_reply})
                    except Exception:
                        pass
                    try:
                        ui.set_state("idle")
                    except Exception:
                        pass
                    command = voice.speak(open_reply, recognizer, source) or ""
                    continue
            except Exception as e:
                ui.log.warning("explicit app/site open: %s", e)

        # Priority 1.4: quick local abilities - exact answers (battery, disk, time left, maths,
        # conversions, notes, lists, clipboard AI) must beat the chat model, which only guesses.
        # Loose phrasings ("lock my pc") are first rewritten into the wording the matchers know.
        try:
            command = actions.normalize_command(command)
            quick = actions.quick_reply(command)
            if not quick:
                try:
                    # `shortcuts` is imported at the top of the file: never re-import it here -
                    # a local import would make the name local to this whole function.
                    quick = shortcuts.try_handle_shortcut_exact(command, engine_context)
                except Exception as e:
                    ui.log.warning("routine: %s", e)
            if quick:
                try:
                    import activity_log
                    activity_log.record("quick", command, quick)
                except Exception:
                    pass
                detail = ui._pop_trace()
                ui.print_chat("lyra", quick, detail)
                try:
                    conversation_history.append({"user": command, "lyra": quick})
                except Exception:
                    pass
                command = voice.speak(quick, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("quick abilities: %s", e)

        # Feature commands must reach their deterministic handlers before the general LLM router.
        try:
            _feature_reply = actions.try_optional_action(command, engine_context)
            if _feature_reply:
                if _results_on_island(_feature_reply):
                    command = ""
                    continue
                detail = ui._pop_trace()
                ui.print_chat("lyra", _feature_reply, detail)
                conversation_history.append({"user": command, "lyra": _feature_reply})
                command = voice.speak(_feature_reply, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("feature command: %s", e)

        # An active translation session consumes each utterance before the chat model.
        try:
            import translate_mode
            _translation = translate_mode.handle_turn(command)
            if _translation is not None:
                if _translation:
                    ui.print_chat("lyra", _translation)
                    conversation_history.append({"user": command, "lyra": _translation})
                    command = voice.speak(_translation, recognizer, source) or ""
                else:
                    command = ""
                continue
        except Exception as e:
            ui.log.warning("translation mode: %s", e)

        # Explicit screen clicks are deterministic actions. Handle them before
        # LLM-first planning so a spoken "click <target>" cannot be rewritten
        # as opening a site or choosing a different visible result.
        if re.match(r"^\s*(?:click|tap|press|hit)\b", command, re.I):
            try:
                _click_reply = actions.try_handle_action(command, engine_context)
            except Exception as e:
                ui.log.warning("direct click: %s", e)
                _click_reply = None
            if _click_reply:
                detail = ui._pop_trace()
                ui.print_chat("lyra", _click_reply, detail)
                conversation_history.append({"user": command, "lyra": _click_reply})
                command = voice.speak(_click_reply, recognizer, source) or ""
                continue

        # Planner owns explicit multi-step requests and its confirm/stop replies.
        try:
            import planner
            _plan_reply = planner.handle_turn(command)
            if _plan_reply is not None:
                if _plan_reply:
                    ui.print_chat("lyra", _plan_reply)
                    conversation_history.append({"user": command, "lyra": _plan_reply})
                    command = voice.speak(_plan_reply, recognizer, source) or ""
                else:
                    command = ""
                continue
        except Exception as e:
            ui.log.warning("planner: %s", e)

        # Priority 1.45: fast local path (config.FAST_PATH). Plain "volume up", "pause", "next song", "mute"...
        # are done at once, with no LLM round trip: the ACTION runs first, Lyra speaks AFTER it.
        # Open-app, tab and timer commands already skip the LLM (open-app block, quick abilities, timers).
        if _fast_path_on():
            fast_reply = fastpath.run(command, engine_context)
            if fast_reply:
                detail = ui._pop_trace()
                ui.print_chat("lyra", fast_reply, detail)
                try:
                    conversation_history.append({"user": command, "lyra": fast_reply})
                except Exception:
                    pass
                command = voice.speak(fast_reply, recognizer, source) or ""
                continue

        # Priority 1.4: "remember ..." is saved to Lyra's memory directly. With LLM-first routing the planner
        # used to claim it and file it as a note, so it never reached Settings > Privacy & Memory.
        try:
            from actions.memory_commands import try_remember
            _mem_reply = try_remember(command)
            if _mem_reply:
                ui.trace("action: remember (memory)")
                detail = ui._pop_trace()
                ui.print_chat("lyra", _mem_reply, detail)
                try:
                    conversation_history.append({"user": command, "lyra": _mem_reply})
                except Exception:
                    pass
                command = voice.speak(_mem_reply, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("memory: %s", e)

        # Priority 1.5: LLM-first routing (config.LLM_FIRST) — or, when off,
        # just the multi-step agent (open X then Y then Z).
        try:
            import agent
            if LLM_FIRST or agent.looks_multi_step(command):
                # >>> phase-L begin
                if LLM_FIRST and _chat_pregate_on() and fastpath.is_plain_chat(command):
                    agent_reply, agent_route = None, "chat"       # pure small talk: no planner call
                    ui.trace("path: chat pre-gate (planner skipped)")
                elif LLM_FIRST and _chat_pregate_on() and fastpath.question_route(command):
                    agent_reply, agent_route = None, fastpath.question_route(command)
                    ui.trace(f"path: question pre-gate (planner skipped, routed as {agent_route})")
                else:
                    agent_reply, agent_route = agent.route_and_run(command, engine_context, force=LLM_FIRST)
                # <<< phase-L end
                if agent_reply:
                    detail = ui._pop_trace()
                    ui.print_chat("lyra", agent_reply, detail)
                    try:
                        conversation_history.append({"user": command, "lyra": agent_reply})
                    except Exception:
                        pass
                    command = voice.speak(agent_reply, recognizer, source) or ""
                    continue
                if LLM_FIRST and agent_route in ("chat", "research"):
                    # Not an action: straight to the chat brain, no regex/router detour.
                    stream = _speech_stream(recognizer, source)
                    reply = _ask_brain(command, conversation_history, agent_route, stream)
                    detail = ui._pop_trace()
                    _mark_info(reply)
                    ui.print_chat("lyra", reply, detail)
                    _remember_followup(command, reply)
                    command = _say(reply, stream, recognizer, source) or ""
                    continue
        except Exception as e:
            ui.log.warning("agent: %s", e)

        # Priority 2: Check direct commands
        action_reply = actions.try_handle_action(command, engine_context)
        if action_reply and _results_on_island(action_reply):
            command = ""
            continue
        if action_reply:
            if _is_music_recognition_command(command):
                # Recognition owns the Island UI. Do not duplicate the result in
                # the chat transcript or speak it as a normal assistant answer.
                try:
                    conversation_history.append({"user": command, "lyra": "[music recognition shown on Island]"})
                except Exception:
                    pass
                command = ""
                continue
            detail = ui._pop_trace()
            ui.print_chat("lyra", action_reply, detail)
            # Keep action turns in history so "that" / "again" have context
            try:
                conversation_history.append({"user": command, "lyra": action_reply})
            except Exception:
                pass
            command = voice.speak(action_reply, recognizer, source) or ""
            continue

        # Short / unclear utterances (e.g. a single word) should NOT fall
        # through to the LLM dictionary mode. Only define when the user
        # explicitly asks what something means.
        clarify = _maybe_ask_for_clarification(command)
        if clarify:
            ui.print_chat("lyra", clarify)
            try:
                import session_context
                # Keep original so a one-word answer can complete it
                session_context.get().set_pending(command, "general")
            except Exception:
                pass
            command = voice.speak(clarify, recognizer, source) or ""
            continue

        # Fast/free local matching (regex + fuzzy) already missed — take one
        # LLM-assisted pass to normalize STT noise and classify the intent,
        # then give the action router a second shot with the cleaned-up
        # text before falling through to plain chat. Only commands that
        # would otherwise have gone nowhere pay this extra latency.
        route = router.classify(command)
        working_command, intent, guessed_url = route["corrected"], route["intent"], route.get("guessed_url")

        if working_command != command:
            if _is_music_recognition_command(working_command):
                action_reply = actions.try_handle_action(working_command, engine_context)
                if action_reply:
                    try:
                        conversation_history.append({"user": command, "lyra": "[music recognition shown on Island]"})
                    except Exception:
                        pass
                    command = ""
                    continue
            action_reply = actions.try_handle_action(working_command, engine_context)
            if action_reply:
                detail = ui._pop_trace()
                ui.print_chat("lyra", action_reply, detail)
                command = voice.speak(action_reply, recognizer, source) or ""
                continue

        # Still nothing — if the router was confident enough to guess a URL
        # for this (a website not in any known list), try it and remember
        # it for next time. Scoped to websites only; see try_learned_open()
        # for why local apps aren't auto-learned the same way.
        if intent == "action" and guessed_url:
            action_reply = actions.try_learned_open(working_command, guessed_url)
            if action_reply:
                detail = ui._pop_trace()
                ui.print_chat("lyra", action_reply, detail)
                command = voice.speak(action_reply, recognizer, source) or ""
                continue

        stream = _speech_stream(recognizer, source)
        reply = _ask_brain(working_command, conversation_history, intent, stream)
        detail = ui._pop_trace()
        _mark_info(reply)
        ui.print_chat("lyra", reply, detail)
        # If Lyra asked a follow-up, remember the original request
        _remember_followup(working_command, reply)
        command = _say(reply, stream, recognizer, source) or ""

    if chain is not None and not pending_steps:
        # The last step has no next pass to check it. Its own reply already said what went
        # wrong; add which steps finished.
        stop_msg = _chain_failure(chain, chain_mark, include_reply=False)
        if stop_msg:
            ui.print_chat("lyra", stop_msg)
            voice.speak(stop_msg, recognizer, source)
    return False


def run_text_mode(conversation_history: list) -> None:
    """Typed-input loop. Returns as soon as ui.INPUT_MODE flips away from
    'text', e.g. via the '/voice' command."""
    ui.print_chat("system", "Typing mode. Type a message, '/voice' to switch back, '/details' to toggle detail view, '/exit' to quit.")
    while ui.INPUT_MODE == "text":
        try:
            raw = input("> ")
        except (EOFError, KeyboardInterrupt):
            raise KeyboardInterrupt
        command = raw.strip()
        if not command:
            continue

        low = command.lower()
        if low == "/voice":
            ui.print_chat("user", command)
            ui.switch_to_voice_mode()
            return
        if low in ("/details", "/detail"):
            ui.print_chat("user", command)
            ui.toggle_verbose()
            continue
        if low in ("/exit", "/quit"):
            ui.print_chat("user", command)
            ui.print_chat("lyra", "Powering down systems. Goodbye, sir.")
            voice.speak("Powering down systems. Goodbye, sir.")
            sys.exit()

        process_command_chain(command, conversation_history)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def _shortcut_summary() -> str:
    """The startup line about keys, built from the real settings so it can't drift from what is armed."""
    def pretty(combo: str) -> str:
        names = {"meta": "Win", "win": "Win", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift"}
        return "+".join(names.get(p.strip().lower(), p.strip().upper()) for p in str(combo).split("+") if p.strip())
    parts = []
    try:
        import settings_store
        if settings_store.get("shortcut_typing_enabled") is not False:
            combo = settings_store.get("shortcut_typing_combo") or "ctrl+meta"
            parts.append(f"{pretty(combo)} = typing mode")
    except Exception:
        pass
    parts.append("tap Ctrl then hold Ctrl = circle to ask")
    for key, default, label in (("shortcut_wheel_combo", "ctrl+capslock", "tab wheel"),):
        try:
            combo = settings_store.get(key)
            combo = default if combo is None else str(combo)
            if combo:
                parts.append(f"{pretty(combo)} = {label}")
        except Exception:
            pass
    return ", ".join(parts) + "."


def main():
    ui.log.info("Lyra starting up.")
    try:
        import lowcpu
        lowcpu.apply_process_priority()               # below normal: games and apps always come first
    except Exception:
        pass
    try:
        from ui_bridge import start_bridge
        if start_bridge():
            ui.log.info("UI bridge started (http://127.0.0.1:8765)")
        else:
            # Voice replies can still run without the bridge, but the island,
            # captions, and UI controls would be disconnected. Do not leave a
            # healthy-looking source process running in that broken state.
            ui.log.error("Exiting: the UI bridge could not start (see the line above).")
            sys.exit(1)
    except Exception as e:
        ui.log.exception("UI bridge failed to start; exiting: %s", e)
        sys.exit(1)

    # Speech models. They used to load one after another on the main thread (Whisper), then two more
    # threads at once, so the first ~25 s after launch were a pile-up of CPU and RAM that stuttered
    # every window. Now ONE low-priority thread loads them in turn, and the hotkeys arm straight away:
    # a press that arrives early simply waits for the model (SpeechRecognizer.transcribe loads lazily).
    #   1. Whisper (first run downloads it; the island shows "Downloading voice models...")
    #   2. the small streaming model (partial text, early turn end, dictation)
    #   3. Parakeet, only once the PC is quiet, and only when the user can use it
    def _speech_warmup():
        try:
            if os.name == "nt":
                import ctypes
                _k = ctypes.windll.kernel32
                _k.SetThreadPriority(_k.GetCurrentThread(), -1)     # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass
        try:
            voice._stt_engine.load()
        except Exception as e:
            ui.log.error("Speech model unavailable (%s) - voice commands will not work.", e)
        try:
            import streaming_stt
            streaming_stt.load()
        except Exception as e:
            ui.log.warning("Streaming speech not started: %s", e)
        try:
            time.sleep(4)    # let the UI and the first hotkey presses settle
            import psutil
            for _ in range(20):   # ...then wait (up to ~20 s) for a quiet PC so the UI never stutters under the load
                if psutil.cpu_percent(interval=1.0) < 50:
                    break
        except Exception:
            pass
        try:
            import stt_engines
            stt_engines.preload()
        except Exception as e:
            ui.log.warning("Parakeet preload not started: %s", e)
    threading.Thread(target=_speech_warmup, name="lyra-speech-warmup", daemon=True).start()
    try:    # recording keyboard shortcuts (Settings -> Shortcuts): Ctrl+Alt+R / Ctrl+Alt+P unless changed or removed
        import record_hotkey
        record_hotkey.apply()
    except Exception as e:
        ui.log.debug("record hotkeys not started: %s", e)
    try:    # the other keyboard shortcuts (play / pause, next, open a tab ...): each has its own key in Settings -> Shortcuts
        import hotkeys
        hotkeys.apply()
    except Exception as e:
        ui.log.debug("keyboard shortcuts not started: %s", e)
    recognizer = sr.Recognizer()

    # Build the Windows app index once at startup. This lets Lyra launch
    # applications that are not hard-coded in actions/apps.py.
    try:
        app_count = actions.scan_installed_apps_startup()   # saved index at once, refreshed in the background when old
        ui.log.info("Indexed %d installed applications.", app_count)
    except Exception as e:
        ui.log.warning("Installed-app scan failed during startup: %s", e)

    # Voice: hold Right Ctrl and talk (ptt.py). There is no always-listening wake word any more:
    # the microphone is only open while a key is held.
    conversation_history = []

    # Global Right Ctrl push-to-talk → process_command_chain
    def _ptt_command(text: str) -> None:
        try:
            import dictation as _dictation
            if _dictation.is_active():         # words spoken into the dictation key are text, never a command
                return
            process_command_chain(text, conversation_history)
        except Exception as e:
            ui.log.error("PTT command error: %s", e)
            ui.set_state("idle")

    try:
        import ptt
        ptt.start(on_command=_ptt_command)
        ui.log.info("Right Ctrl push-to-talk enabled")
    except Exception as e:
        ui.log.warning("PTT not available: %s", e)

    # Dictation: hold (or toggle) a shortcut and Lyra types what you say into the focused window.
    try:
        import dictation
        import dictation_hotkey
        if dictation_hotkey.start():
            ui.log.info("Dictation shortcut enabled")
    except Exception as e:
        ui.log.warning("Dictation shortcut not available: %s", e)

    # Restore the user's privacy-aware screen-watch mode. "ask" never starts
    # a background capture loop; "always" starts the lightweight local watcher.
    try:
        import screen_watch, settings_store
        saved_screen_mode = str(settings_store.get("screen_watch_mode") or "off")
        if saved_screen_mode in {"off", "ask", "always"}:
            screen_watch.set_mode(saved_screen_mode)
            ui.log.info("Screen watch mode restored: %s", saved_screen_mode)
    except Exception as e:
        ui.log.debug("Screen watch restore skipped: %s", e)


    ui.print_terminal(  # terminal only — not shown in the chat window
        "system",
        f"Lyra is online. Hold Right Ctrl to talk. {_shortcut_summary()}",
    )
    ui.print_terminal("system", f"Log file: {ui.LOG_PATH}")
    # Silent startup — no spoken greeting until the user wakes Lyra.

    hotkey_stop_event = threading.Event()

    try:
        # PTT owns the microphone. The worker publishes thinking/speaking/idle
        # itself. Do NOT publish "idle" from this heartbeat: speak() runs in
        # the PTT worker while this loop is still alive, so doing that here
        # would overwrite the speaking state every 120 ms and make the island
        # voice wave disappear almost immediately.
        ui.set_state("idle")
        while True:
            if ui.INPUT_MODE == "voice":
                time.sleep(0.12)
            else:
                run_text_mode(conversation_history)

    except KeyboardInterrupt:
        ui.print_chat("system", "Manual interrupt received. Shutting down, sir.")
        voice.speak("Manual interrupt received. Shutting down, sir.")
        sys.exit(0)
    finally:
        pass



if __name__ == "__main__":
    main()
