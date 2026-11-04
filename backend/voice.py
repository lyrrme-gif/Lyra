"""Lyra — STT listen path, wake/exit patterns, and TTS re-exports.

TTS implementation lives in tts.py. Other modules can keep `import voice`
and call voice.speak / voice.listen as before.
"""
from __future__ import annotations

import re
import logging

import speech_recognition as sr

import ui
import perf
from config import (
    WAKE_WORDS,
    EXIT_PHRASES,
    STANDBY_PHRASES,
    PHRASE_TIME_LIMIT,
)

# Re-export TTS surface so existing `import voice` call sites stay valid.
from tts import (  # noqa: F401
    speak,
    list_output_devices,
    test_voice,
    preload_offline,
    open_speech_stream,
    SpeechStream,
    _stt_engine,
    _edge_probe,
    _synth_chunk,
    _speakable,
    _voices_dir,
)
import tts as _tts
import sys

# Keep edge circuit-breaker state shared with tts (tests assign on voice.*).
class _VoiceModule(sys.modules[__name__].__class__):
    @property
    def _edge_down_until(self):
        return _tts._edge_down_until
    @_edge_down_until.setter
    def _edge_down_until(self, v):
        _tts._edge_down_until = v
    @property
    def _edge_fails(self):
        return _tts._edge_fails
    @_edge_fails.setter
    def _edge_fails(self, v):
        _tts._edge_fails = v

sys.modules[__name__].__class__ = _VoiceModule


log = logging.getLogger("lyra")


def build_phrase_pattern(phrases):
    escaped = [re.escape(p) for p in phrases]
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


WAKE_PATTERN = build_phrase_pattern(WAKE_WORDS)
EXIT_PATTERN = build_phrase_pattern(EXIT_PHRASES)
STANDBY_PATTERN = build_phrase_pattern(STANDBY_PHRASES)


def reload_wake_pattern() -> None:
    """Rebuild WAKE_PATTERN after the user changes wake_name in settings."""
    global WAKE_PATTERN
    try:
        from settings_store import wake_phrases
        phrases = wake_phrases()
    except Exception:
        from config import WAKE_WORDS as phrases
    WAKE_PATTERN = build_phrase_pattern(phrases)
    log.info("Wake pattern reloaded: %s", phrases[:6])


# Mode switching patterns
SWITCH_TO_TEXT_PATTERN = re.compile(
    r"\b(?:switch to|change to|enable|activate|go to|use)\s+(?:text|typing|type)\s+(?:mode)?",
    re.IGNORECASE
)
SWITCH_TO_VOICE_PATTERN = re.compile(
    r"\b(?:switch to|change to|enable|activate|go to|use)\s+(?:voice|speech|speaking|talk)\s+(?:mode)?",
    re.IGNORECASE
)

# NOTE: all patterns below use re.IGNORECASE. Voice-mode input always comes
# through already lowercased (see listen()/speak() using .lower() on the STT
# result), but text-mode input is passed through as typed — without
# IGNORECASE, naturally-capitalized typed commands (e.g. "Create a folder...")
# would silently fail to match and fall through to the LLM as plain chat.
SMALL_TALK_PATTERN = re.compile(
    r"^(hi|hello|hey|how are you|how'?s it going|what'?s up|good morning|good afternoon|"
    r"good evening|who are you|what'?s your name|nice to meet you|what can you do|"
    r"how'?s your day)\b",
    re.IGNORECASE,
)

# Phrases that *contain* "stop" but should NOT trigger standby (Spotify/music).
NEGATIVE_STANDBY_PATTERN = re.compile(
    r"\b(stop (?:the )?(music|track|song|video|playback))\b", re.IGNORECASE
)


def _fix_heard(text):
    """Repair misheard names / dictionary words in a voice command before it is acted on (stt_fix.py)."""
    try:
        import stt_fix
        return stt_fix.fix(text)
    except Exception:
        return text


def listen(recognizer, source, timeout_seconds=5, phrase_time_limit=PHRASE_TIME_LIMIT, standby=False):
    """Listen once. Returns (status, text) where status is 'ok', 'timeout',
    'unrecognized', or 'error'.

    `standby=True` is for the wake-word polling loop specifically — it
    reports state "standby" instead of "listening" so the UI (the bubble)
    can tell "waiting for the wake word" apart from "actively listening in
    a conversation" and only show itself for the latter."""
    log.info("Listening..." if not standby else "Standby: listening for wake word...")
    ui.set_state("standby" if standby else "listening")
    try:
        audio = recognizer.listen(source, timeout=timeout_seconds, phrase_time_limit=phrase_time_limit)
    except sr.WaitTimeoutError:
        return "timeout", ""
    perf.begin("poll")      # the speaker stopped: the clock for this turn starts here

    try:
        status, text = _stt_engine.transcribe_audio_data(audio)
        if status == "ok":
            return "ok", _fix_heard(text)
        elif status == "unrecognized":
            return "unrecognized", ""
        else:
            return "error", ""
    except Exception as e:
        log.error("Speech recognition error: %s", e)
        return "error", ""
