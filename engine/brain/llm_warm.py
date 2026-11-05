"""Lyra — LLM pre-warm.

While you are still speaking, the streaming recognizer (streaming_stt.py) produces partial
text. As soon as it looks like a request for Lyra, we touch the LLM in the background so the
real call, a moment later, does not pay the cold start:
  * Ollama: loads the model into memory (the big one: a cold local model takes seconds)
  * hosted providers (OpenRouter / Gemini / Groq): a cheap request that gets the client's
    first-call setup, DNS and TLS out of the way

Only a warm-up: nothing here ever runs a command or sends the user's words anywhere.
Commands run on the final Whisper transcript, not on partials.
"""

from __future__ import annotations

import threading
import time

from engine.bridge import ui

log = ui.log

_MIN_GAP_S = 20.0          # never warm more often than this
_last = 0.0
_lock = threading.Lock()


def _mentions_wake_name(text: str) -> bool:
    try:
        from engine.voice import stt
        return stt._canonical_wake_name() in stt._normalize_transcript(text.lower())
    except Exception:
        return False


def note_partial(text: str, need_wake: bool = True) -> None:
    """Called with each new partial. Starts a warm-up (at most one per _MIN_GAP_S) once the
    partial has a couple of words and, in standby, contains the wake name."""
    global _last
    if not text or len(text.split()) < 2:
        return
    if need_wake and not _mentions_wake_name(text):
        return
    now = time.monotonic()
    with _lock:
        if now - _last < _MIN_GAP_S:
            return
        _last = now
    threading.Thread(target=_warm, name="lyra-llm-warm", daemon=True).start()


def _warm() -> None:
    try:
        from engine.brain import brain
        client, model = brain._get_client()
        if not client:
            return
        t0 = time.monotonic()
        try:
            from engine.brain.llm_store import get as _llm_get
            provider = str(_llm_get().get("provider") or "").lower()
        except Exception:
            provider = ""
        if provider == "ollama":
            from engine.brain import ollama_support
            ollama_support.preload(model)         # loads the model, generates nothing, expires after the keep-alive
        else:
            client.models.list(timeout=6.0)
        log.debug("LLM pre-warm done in %.0f ms", (time.monotonic() - t0) * 1000)
    except Exception as e:      # a warm-up must never matter
        log.debug("LLM pre-warm skipped: %s", e)
