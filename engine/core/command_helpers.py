"""Lyra — helpers used by the command pipeline.

Extracted from main.py (Phase 2) so the top-level loop stays readable.
Behaviour is unchanged: these are the same functions, just in their own module.
"""
from __future__ import annotations

import re

from engine import actions
from engine.bridge import ui
from engine.voice import voice
from engine.brain import brain
from engine.core import compound
from engine import shortcuts

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
        from engine.brain import session_context
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
        from engine.brain import session_context
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
        from engine.actions.system import RECOGNIZE_MUSIC_PATTERN
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
        from engine.core import config
        return bool(getattr(config, "CHAT_PREGATE", True))
    except Exception:
        return True
# <<< phase-L end


def _fast_path_on() -> bool:
    """config.FAST_PATH (default on): plain volume / media commands skip the LLM."""
    try:
        from engine.core import config
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
        from engine.features import yt_island
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
        from engine.voice import dictation
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
        from engine.features import translate_mode
        if translate_mode.is_active():
            return False
    except Exception:
        pass
    try:
        from engine.features import circle_ask
        if circle_ask.has_pending():
            return False
    except Exception:
        pass
    try:
        from engine.brain import session_context
        if session_context.get().has_pending() and len(re.findall(r"[a-zA-Z0-9']+", command)) <= 6:
            return False
    except Exception:
        pass
    try:
        from engine.features import schedule_parse
        if schedule_parse.parse_oneshot(command) or schedule_parse.parse_routine(command) or schedule_parse.parse_alarm(command):
            return False
    except Exception:
        pass
    try:
        from engine.brain import planner
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
        from engine.voice import mochi_sounds
        mochi_sounds.mark_info(text)
    except Exception:
        pass


