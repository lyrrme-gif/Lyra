"""Lyra - fast local path for plain commands: no LLM, no planner round trip.

With LLM_FIRST on, a request such as "volume up" or "pause" used to be understood by the LLM first and only
then reach the regex handlers (a network round trip for a media key). classify() recognises the plain
spellings of these commands and run() does them straight away:

    volume up / down, set volume N, mute / unmute      (system volume)
    play, pause, resume, next, previous                (media keys)

The action runs FIRST and returns what Lyra should say; main speaks it afterwards. Anything that is not
an exact plain spelling ("play lofi on spotify", "mute chrome", "turn it up a bit and open discord") is NOT
claimed: classify() returns None and the normal chain handles it as before.

Open app, tab and timer commands already skip the LLM (open-app block, quick abilities, timers) and are
only timed by perf.py; see main.process_command_chain.

Switch off with FAST_PATH = False in config.py.
"""

import re
import time

_FILLER_LEAD = re.compile(r"^(?:(?:please|kindly|can you|could you|would you|will you)\s+)+")
_FILLER_TAIL = re.compile(r"(?:\s+(?:please|now|for me|thanks|thank you))+$")

_VOL_UP = re.compile(
    r"(?:turn\s+it\s+up|(?:volume|sound)\s+up|louder|(?:turn\s+up|increase|raise)\s+(?:the\s+)?(?:volume|sound)"
    r"|turn\s+(?:the\s+)?(?:volume|sound)\s+up)")
_VOL_DOWN = re.compile(
    r"(?:turn\s+it\s+down|(?:volume|sound)\s+down|quieter|softer|(?:turn\s+down|decrease|lower|reduce)\s+(?:the\s+)?(?:volume|sound)"
    r"|turn\s+(?:the\s+)?(?:volume|sound)\s+down)")
_VOL_SET = re.compile(r"(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+|at\s+)?(\d{1,3})\s*(?:%|percent)?")
_MUTE = re.compile(r"(?:mute|unmute)(?:\s+(?:the\s+)?(?:sound|volume|audio|speakers|computer|pc|it))?")
_PAUSE = re.compile(r"pause(?:\s+(?:the\s+)?(?:music|song|video|playback|track|it|that))?")
# Only unmistakable "start it again" wording: "play lofi" or "play music" can mean a search, so they are not here.
_PLAY = re.compile(r"(?:play(?:\s+(?:the\s+video|it|that))?|(?:resume|unpause)(?:\s+(?:the\s+)?(?:music|song|video|playback|track|it|that))?"
                   r"|continue\s+(?:the\s+)?(?:video|playback)|play\s+(?:it|that|the\s+video)\s+again)")
_REFOCUS = re.compile(r"\b(?:it|that|the\s+video)\b")     # "pause it", "play the video": the media window may have lost focus
_NEXT = re.compile(r"(?:next\s+(?:track|song)|skip(?:\s+(?:this|the)(?:\s+(?:track|song))?)?|skip\s+(?:track|song))")
_PREV = re.compile(r"(?:previous\s+(?:track|song)|go\s+back\s+(?:a\s+)?(?:track|song)|last\s+(?:track|song))")

MAX_WORDS = 6


def _clean(command: str) -> str:
    t = (command or "").lower().strip().rstrip(".!?, ")
    t = re.sub(r"\s+", " ", t)
    t = _FILLER_LEAD.sub("", t)
    t = _FILLER_TAIL.sub("", t).strip()
    return t


def classify(command: str):
    """('volume_up' | 'volume_down' | 'volume_set' | 'mute' | 'pause' | 'play' | 'next' | 'previous', arg, cleaned text)
    for an exact plain command, else None. Never touches the system."""
    t = _clean(command)
    if not t or len(t.split()) > MAX_WORDS:
        return None
    m = _VOL_SET.fullmatch(t)
    if m:
        return "volume_set", max(0, min(100, int(m.group(1)))), t
    for kind, rx in (("volume_up", _VOL_UP), ("volume_down", _VOL_DOWN), ("mute", _MUTE), ("pause", _PAUSE),
                     ("play", _PLAY), ("next", _NEXT), ("previous", _PREV)):
        if rx.fullmatch(t):
            return kind, None, t
    return None


def _system():
    from actions import system
    return system


def _session():
    import session_context
    return session_context


def run(command: str, engine_context=None):
    """Do the command now and return what Lyra should say, or None if it is not a plain fast-path command
    (or the action failed to start: the normal chain then takes over). Never raises."""
    hit = classify(command)
    if hit is None:
        return None
    kind, arg, text = hit
    try:
        import perf
        perf.set_path("fast")
        with perf.span("action"):
            return _do(kind, arg, text)
    except Exception:
        try:
            import ui
            ui.log.warning("fast path %s failed; using the normal chain", kind, exc_info=True)
        except Exception:
            pass
        return None


def _do(kind: str, arg, text: str):
    S = _system()
    if kind == "volume_up":
        S.adjust_volume("up", steps=5)
        return "Volume up."
    if kind == "volume_down":
        S.adjust_volume("down", steps=5)
        return "Volume down."
    if kind == "volume_set":
        ok, msg = S.set_volume_level(arg)
        return msg if ok else f"Couldn't set volume - {msg}"
    if kind == "mute":
        S._send_media_key(S.VK_VOLUME_MUTE)
        return "Toggling mute."

    sc = _session()
    ctx = sc.get()
    win = sc.current_window_title()
    if kind == "pause":
        if _REFOCUS.search(text):
            sc.refocus_media_window()
            time.sleep(0.25)
            win = sc.current_window_title() or ctx.last_media_window or win
        S._send_media_key(S.VK_MEDIA_PLAY_PAUSE)
        ctx.note_media("pause", window_title=win, state="paused")
        where = f" on {ctx.last_media_window}" if ctx.last_media_window else ""
        return f"Paused{where}."
    if kind == "play":
        again = bool(re.search(r"\b(?:again|resume|unpause|continue)\b", text))
        if again or _REFOCUS.search(text):
            sc.refocus_media_window()      # "play it again": bring the media window back first
            time.sleep(0.25)
            win = sc.current_window_title() or ctx.last_media_window or win
        S._send_media_key(S.VK_MEDIA_PLAY_PAUSE)
        ctx.note_media("play", window_title=win, state="playing")
        where = f" on {ctx.last_media_window}" if ctx.last_media_window else ""
        return f"Playing again{where}." if again else f"Playing{where}."
    if kind == "next":
        S._send_media_key(S.VK_MEDIA_NEXT)
        ctx.note_media("next", window_title=win)
        return "Skipping to the next track."
    if kind == "previous":
        S._send_media_key(S.VK_MEDIA_PREV)
        ctx.note_media("prev", window_title=win)
        return "Going back a track."
    return None


# >>> phase-L begin
# CHAT_PREGATE. With config.LLM_FIRST every request first costs a planner call whose only job, for pure
# conversation, is to answer "chat". These phrases can only ever be conversation, so they go straight to
# the chat model. Deliberately tiny and fully anchored (the WHOLE utterance must match): anything with an
# app, a verb like open/play/set, a name or a time word is left to the planner as before. Greetings like
# "good morning" and "what can you do" are NOT here: users bind shortcuts to them.
_CHAT_ONLY = re.compile(
    r"^(?:(?:hey|hi|ok|okay)\s+)?(?:lyra\s*[,!]?\s*)?(?:"
    r"hi|hello|hey|hey there|how are you(?: today| doing)?|how'?s it going|what'?s up|how'?s your day|"
    r"who are you|what'?s your name|nice to meet you|"
    r"thank(?:s| you)(?: (?:so much|a lot|lyra))?|thanks lyra|"
    r"tell me (?:a|another|one more) (?:joke|riddle|fun fact|story)|make me laugh|cheer me up|"
    r"say something (?:funny|nice)|write (?:me )?(?:a|an) (?:poem|haiku|limerick|joke)|"
    r"i(?:'m| am) (?:bored|sad|tired|stressed|happy|excited)"
    r")\s*[.!?]*$",
    re.IGNORECASE,
)


def is_plain_chat(command: str) -> bool:
    """True only for exact small-talk / joke / thanks phrases (see _CHAT_ONLY). Never raises."""
    try:
        text = (command or "").strip()
        return 0 < len(text) <= 60 and bool(_CHAT_ONLY.match(text))
    except Exception:
        return False

# QUESTION_PREGATE. A plain factual question ("who's Tom Holland", "how old is he", "what's One Piece") can never be a
# desktop action, yet LLM_FIRST sent it through the planner first: one extra LLM call with the whole tool list (the
# source of the Groq 429s and of 15-40 s answers). Anything touching the PC, music, timers, the screen or "my/this/that"
# is left to the planner exactly as before.
_Q_START = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:who|whos|who's|whose|what|whats|what's|when|where|which|why|how|"
                      r"is|are|was|were|does|did|do|has|have|will)\b", re.IGNORECASE)
_Q_PC = re.compile(
    r"\b(my|me|mine|this|that|these|those|here|screen|window|windows|tab|tabs|app|apps|file|files|folder|clipboard|"
    r"battery|disk|cpu|ram|memory|volume|brightness|wifi|bluetooth|computer|pc|laptop|playing|song|music|spotify|"
    r"track|lyrics|timer|alarm|reminder|remind|note|notes|list|todo|shortcut|notification|notifications|"
    r"calendar|schedule|mail|email|message|messages|whatsapp|discord|telegram|time|date|today|tonight|weather|"
    r"temperature|open|close|play|pause|stop|start|set|turn|click|type|search|find|show|take|call|send|"
    r"you|your|lyra|mochi|settings)\b", re.IGNORECASE)
_Q_RESEARCH = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:who|whos|who's|whose|what|whats|what's|when|where|which)\b",
                         re.IGNORECASE)


def question_route(command: str) -> str | None:
    """"research" / "chat" when `command` is a plain factual question that the planner can skip, else None. Never raises."""
    try:
        text = (command or "").strip()
        if not (3 <= len(text) <= 120) or "," in text and len(text) > 80:
            return None
        if not _Q_START.match(text) or _Q_PC.search(text):
            return None
        return "research" if _Q_RESEARCH.match(text) else "chat"
    except Exception:
        return None
# <<< phase-L end
