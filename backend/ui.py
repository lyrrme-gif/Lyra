"""Lyra — logging setup + chat transcript UI + mode-toggle state.

IMPORTANT: other modules that need to read/write INPUT_MODE or
VERBOSE_DISPLAY should `import ui` and use `ui.INPUT_MODE` /
`ui.VERBOSE_DISPLAY` rather than `from ui import INPUT_MODE` — the
latter copies the value at import time and will never see updates made
by switch_to_text_mode()/switch_to_voice_mode() below."""

import os
import sys
import time
import logging
try:
    import msvcrt
except ImportError:  # not Windows (never the case for the real app)
    msvcrt = None
import threading
from datetime import datetime
from pathlib import Path

import paths

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Full detail (every internal step) always goes to the .log file. The console
# only shows WARNING+ from the logger — the actual chat transcript you see is
# drawn separately by print_chat() below, so the console stays a clean chat
# view instead of a raw log dump.
LOG_PATH = paths.data_path("ui.log")


def _has_console() -> bool:
    """False in the windowed (no-console) installed build, or when output is piped."""
    try:
        return sys.stdin is not None and sys.stdout is not None and sys.stdout.isatty()
    except Exception:
        return False


HAS_CONSOLE = _has_console()

_handlers = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
if os.environ.get("LYRA_QUIET"):
    _console_handler = logging.StreamHandler(open(os.devnull, "w"))
elif HAS_CONSOLE:
    _console_handler = logging.StreamHandler(sys.stdout)
else:
    _console_handler = None
if _console_handler is not None:
    _console_handler.setLevel(logging.WARNING if os.environ.get("LYRA_TRACE", "1").strip().lower() in ("0", "false", "off", "no") else logging.INFO)  # terminal shows everything unless LYRA_TRACE=0
    _handlers.append(_console_handler)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("lyra")
for _note in paths.NOTES:
    log.info(_note)
paths.NOTES.clear()

# ---------------------------------------------------------------------------
# Chat UI (colored transcript + Ctrl+O detail toggle + Ctrl+T mode toggle)
# ---------------------------------------------------------------------------
if os.name == "nt" and HAS_CONSOLE:
    os.system("")  # enables ANSI escape processing in cmd.exe on Windows 10+

_C_RESET = "\x1b[0m"
_C_DIM = "\x1b[2m"
_C_USER = "\x1b[36m"     # cyan
_C_LYRA = "\x1b[33m"   # amber
_C_DETAIL = "\x1b[90m"   # gray
_C_SYSTEM = "\x1b[35m"   # magenta

VERBOSE_DISPLAY = False           # Ctrl+O (voice mode) / "/details" (text mode) toggles this
INPUT_MODE = "voice"              # "voice" or "text" — Ctrl+T / "/voice" toggles this
_trace_log = []                   # accumulates "what Lyra is doing" notes for the current turn

# ---------------------------------------------------------------------------
# UI event bus — lets a front-end (the Lyra island, via ui_bridge.py) mirror everything the console shows/does, without this module
# needing to import or know anything about Qt. Console printing below is
# completely unchanged; this is purely additive. If nothing calls
# add_listener(), _notify() is a no-op loop over an empty list.
# ---------------------------------------------------------------------------
_listeners = []


def add_listener(fn) -> None:
    """Register fn(event: str, **kwargs) to be called on every UI event.
    Events currently emitted:
      'chat'  -> role, text, detail   (same args as print_chat)
      'state' -> state                (one of idle/listening/thinking/speaking)
      'wave'  -> levels, hz, start_ms (loudness envelope of the speech that just
                                       started playing; drives the UI voice bars)
    A broken listener is logged and skipped rather than crashing the engine —
    a bug in the GUI should never take down voice/text processing."""
    _listeners.append(fn)


def _notify(event: str, **kwargs) -> None:
    for fn in _listeners:
        try:
            fn(event, **kwargs)
        except Exception:
            log.exception("UI listener failed for event '%s'", event)


_last_state = "idle"
_speech_end = 0.0


def speaking_recently(window: float = 0.8) -> bool:
    """True while Lyra is talking and for `window` seconds after (her voice reaches the microphone)."""
    return _last_state == "speaking" or (time.monotonic() - _speech_end) < window


def set_state(state: str) -> None:
    """Broadcast Lyra's current activity state. The console UI ignores
    this entirely (it has no concept of 'state') — it exists purely for
    listeners like the Qt bubble, which uses it to pick a pulse animation."""
    global _last_state, _speech_end
    if state == _last_state and state in ("standby", "idle"):
        return                       # resting states carry no news; never flood the UI feed
    if _last_state == "speaking" and state != "speaking":
        _speech_end = time.monotonic()
    _last_state = state
    _notify("state", state=state)


def emit_wave(levels: list, hz: int, start_ms: int) -> None:
    """Tell listeners how loud the speech that started playing at `start_ms`
    (epoch milliseconds) is over time: `levels` is 0..1, one value per 1/hz s.
    An empty list means "unknown" and the UI simulates a wave instead."""
    _notify("wave", levels=levels, hz=hz, start_ms=start_ms)


def trace(msg: str) -> None:
    """Record a 'what's happening under the hood' note for the current turn.
    Shown under the reply only when VERBOSE_DISPLAY is on."""
    _trace_log.append(msg)
    log.info("[trace] %s", msg)


def _pop_trace() -> str:
    global _trace_log
    joined = " | ".join(_trace_log)
    _trace_log = []
    return joined


_last_lyra_chat: tuple[str, float] = ("", 0.0)
_last_user_at = 0.0


def ensure_spoken_chat(text: str) -> None:
    """Make sure what Lyra is about to SAY is also on screen (caption / chat).

    Most replies are printed with print_chat() right before they are spoken. A few
    paths speak without printing, which left the subtitle window on "Thinking" while
    the answer was audible. Called from voice.speak(); does nothing when the same
    text was just printed, and skips long read-aloud text."""
    t = (text or "").strip()
    if not t or len(t) > 600:
        return
    last, at = _last_lyra_chat
    if last == t and (time.monotonic() - at) < 120:
        return
    print_chat("lyra", t)


def print_chat(role: str, text: str, detail: str = "", info: bool = False) -> None:
    """Print one line of the chat transcript: '[time] Role: text', plus an
    optional dim detail line underneath when VERBOSE_DISPLAY is on. Also
    notifies any registered UI listeners (e.g. the Qt bubble's caption)."""
    ts = datetime.now().strftime("%I:%M %p")
    color, label = {
        "user": (_C_USER, "You"),
        "lyra": (_C_LYRA, "Lyra"),
    }.get(role, (_C_SYSTEM, "System"))
    print(f"{_C_DIM}[{ts}]{_C_RESET} {color}{label}:{_C_RESET} {text}")
    global _last_lyra_chat, _last_user_at
    if role == "user":
        _last_user_at = time.monotonic()
    if role == "lyra":
        _last_lyra_chat = ((text or "").strip(), time.monotonic())
        if _last_user_at:
            log.info("timing: reply text ready %.1fs after the question", time.monotonic() - _last_user_at)
    if VERBOSE_DISPLAY and detail:
        print(f"{_C_DETAIL}   -> {detail}{_C_RESET}")
    _notify("chat", role=role, text=text, detail=detail, info=info)


def print_terminal(role: str, text: str) -> None:
    """Like print_chat, but terminal only: nothing is sent to the UI / chat
    window. For startup banners and other messages meant for the console."""
    ts = datetime.now().strftime("%I:%M %p")
    color, label = {
        "user": (_C_USER, "You"),
        "lyra": (_C_LYRA, "Lyra"),
    }.get(role, (_C_SYSTEM, "System"))
    print(f"{_C_DIM}[{ts}]{_C_RESET} {color}{label}:{_C_RESET} {text}")


def _hotkey_listener_loop(stop_event: threading.Event) -> None:
    """Background thread, ACTIVE ONLY IN VOICE MODE: polls for Ctrl+O (toggle
    detail view) and Ctrl+T (switch to typing mode). Not run during text mode
    — input() would fight it for the same console input, swallowing keys
    unpredictably. Text mode uses '/details' and '/voice' typed commands for
    the same effect instead."""
    if not HAS_CONSOLE or msvcrt is None:
        return  # installed/windowed build: no keyboard console to poll
    while not stop_event.is_set():
        try:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch == b"\x0f":  # Ctrl+O
                    toggle_verbose()
                elif ch == b"\x14":  # Ctrl+T
                    switch_to_text_mode()
        except Exception:
            pass
        time.sleep(0.05)


def switch_to_text_mode() -> None:
    global INPUT_MODE
    if not HAS_CONSOLE:
        print_chat("system", "Typing mode needs a console window, so it isn't available in the installed app. Type in the island instead.")
        return
    INPUT_MODE = "text"
    print_chat("system", "Switching to typing mode (this takes effect within a couple seconds). Type '/voice' to go back.")


def switch_to_voice_mode() -> None:
    global INPUT_MODE
    INPUT_MODE = "voice"
    print_chat("system", "Switching to voice mode. Ctrl+T to type, Ctrl+O to toggle detail view.")


def toggle_verbose() -> None:
    global VERBOSE_DISPLAY
    VERBOSE_DISPLAY = not VERBOSE_DISPLAY
    print_chat("system", f"Detail view {'ON' if VERBOSE_DISPLAY else 'OFF'}.")
