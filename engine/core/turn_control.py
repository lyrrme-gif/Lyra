"""Lyra - stop what she is saying, or drop the answer to a turn that is still being worked on.

Two counters, nothing else:
  * speech generation: stop_speech() bumps it. Every piece of speech remembers the number it started with and quietly
    skips itself when the number has moved (a streamed answer's later sentences, a long reply read in chunks, the
    Windows fallback voice). A NEW message uses this: she stops reading the old answer and moves on to the new one.
  * turn generation: cancel_turn() bumps it (and also stops the speech). A turn remembers the number it began with
    (begin_turn(), per thread) and then stays silent: no reply on screen, no voice, no state changes. The Esc key uses
    this while she is thinking or talking. The model request itself cannot be torn out of the network call; the
    streamed answer is dropped as soon as it notices, a non-streamed one when it returns.

Pure Python, no imports at load time (ui / voice are imported inside the functions, so ui.py may import this file).
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_speech_gen = 0
_turn_gen = 0
_local = threading.local()
_offline_proc = None          # the Windows fallback voice process, so it can be killed (tts._speak_offline)


def speech_gen() -> int:
    return _speech_gen


def speech_stopped_since(gen: int) -> bool:
    """True when stop_speech() / cancel_turn() ran after `gen` was read: that speech must not go on."""
    return gen != _speech_gen


def begin_turn() -> None:
    """Called by the thread that runs a turn, first thing: remembers which turn generation it belongs to."""
    _local.gen = _turn_gen


def turn_cancelled() -> bool:
    """True on a thread whose turn was cancelled with cancel_turn() after begin_turn(). Other threads: False."""
    gen = getattr(_local, "gen", None)
    return gen is not None and gen != _turn_gen


def set_offline_proc(proc) -> None:
    global _offline_proc
    _offline_proc = proc


def _halt_playback() -> None:
    try:
        from engine.voice import voice
        voice.pygame.mixer.music.stop()
    except Exception:
        pass
    proc = _offline_proc
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass


def stop_speech() -> None:
    """Stop talking now and skip whatever was still queued to be said."""
    global _speech_gen
    with _lock:
        _speech_gen += 1
    _halt_playback()


def cancel_turn() -> bool:
    """Esc: stop talking and drop the answer of the turn in progress. Returns True when there was something to stop."""
    global _speech_gen, _turn_gen
    try:
        from engine.bridge import ui
        busy = getattr(ui, "_last_state", "idle") in ("speaking", "thinking")
    except Exception:
        ui = None
        busy = True
    with _lock:
        _speech_gen += 1
        _turn_gen += 1
    _halt_playback()
    if ui is not None:
        try:
            ui.set_state("idle", _force=True)
        except Exception:
            pass
    return busy


def is_talking() -> bool:
    try:
        from engine.bridge import ui
        return getattr(ui, "_last_state", "idle") == "speaking"
    except Exception:
        return False
