"""Lyra — speech-to-text.

Local, unlimited, accurate. Replaces recognize_google() which needed internet,
had undocumented rate limits, and could throttle without warning.

Engines (Phase 1): NVIDIA Parakeet v3 through stt_engines when the user picked it (or Auto) AND the
voice model is downloaded AND the language is one it supports; otherwise, or on any failure,
the original faster-whisper path below runs exactly as before.
"""

import gc
import os
import re
import threading
import time
import wave
import numpy as np
from faster_whisper import WhisperModel

import ui
import perf
from config import (
    WHISPER_MODEL, WHISPER_DEVICE, WHISPER_LANGUAGE, WHISPER_COMPUTE_TYPE, WHISPER_BEAM_SIZE,
    WHISPER_INITIAL_PROMPT,
)

log = ui.log

# translate_mode.py sets this: "" = let the engine detect the language, None = WHISPER_LANGUAGE (default).
language_override = None

# Words the Parakeet path restores to the right spelling (exact / "spot ify" style joins only; no fuzzy
# matching here, so "discard" is never turned into "discord").
_COMMAND_HINTS = ("Lyra", "Spotify", "Discord", "WhatsApp", "Telegram", "YouTube", "Chrome", "Bluetooth")

_SHARED = {"model": None}
_SHARED_LOCK = threading.Lock()
_preload_started = False
_LAST_USE = [time.monotonic()]       # when the shared model was last handed out
_unloader_started = False
_MIN_IDLE_S = 60.0


def _setting(key: str, default):
    try:
        import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:
        return default


def _whisper_name() -> str:
    """The Whisper size to load: Settings (default "base"), else the one in config.py."""
    v = str(_setting("whisper_model", "") or "").strip().lower()
    return v if v in ("tiny", "base", "small", "medium") else WHISPER_MODEL


def _whisper_threads() -> int:
    """CPU threads for the speech model. Default: half the cores, so a sentence never takes the whole PC."""
    try:
        n = int(_setting("whisper_threads", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else max(2, (os.cpu_count() or 4) // 2)


def _idle_limit_s() -> float:
    try:
        m = float(_setting("whisper_idle_unload_min", 10) or 0)
    except (TypeError, ValueError):
        m = 10.0
    return 0.0 if m <= 0 else max(_MIN_IDLE_S, m * 60.0)


def _unload_if_idle(now: float | None = None) -> bool:
    """Free the shared Whisper model when nobody used it for the idle limit. True when it was unloaded."""
    limit = _idle_limit_s()
    if limit <= 0:
        return False
    now = time.monotonic() if now is None else now
    with _SHARED_LOCK:
        if _SHARED["model"] is None or now - _LAST_USE[0] < limit:
            return False
        _SHARED["model"] = None
    gc.collect()
    log.info("Whisper model unloaded after %d idle minutes (it loads again on the next sentence).", int(limit // 60))
    return True


def _unloader() -> None:
    while True:
        time.sleep(30)
        try:
            _unload_if_idle()
        except Exception:                                  # noqa: BLE001
            pass


def _start_unloader() -> None:
    global _unloader_started
    if not _unloader_started:
        _unloader_started = True
        threading.Thread(target=_unloader, name="lyra-whisper-idle", daemon=True).start()


def _effective_language():
    """Language code for this utterance, or None to auto-detect."""
    ov = language_override
    if ov is None:
        return WHISPER_LANGUAGE or None
    return ov or None


def _load_shared_whisper():
    """ONE Whisper model per process, shared by every SpeechRecognizer and by stt_engines (C1)."""
    with _SHARED_LOCK:
        _LAST_USE[0] = time.monotonic()
        if _SHARED["model"] is None:
            import model_setup
            name = _whisper_name()
            try:
                path = model_setup.ensure_whisper(name)
            except Exception:
                if name == WHISPER_MODEL:
                    raise
                log.warning("Whisper '%s' is not available; using '%s'.", name, WHISPER_MODEL)
                name = WHISPER_MODEL
                path = model_setup.ensure_whisper(name)
            threads = _whisper_threads()
            _SHARED["model"] = WhisperModel(
                path,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                cpu_threads=threads,
            )
            log.info(f"Whisper model loaded: {name} on {WHISPER_DEVICE} ({threads} threads)")
            _start_unloader()
        return _SHARED["model"]


def shared_whisper_model():
    return _load_shared_whisper()


def _preload_parakeet() -> None:
    """Warm Parakeet in the background once (if the user can use it). Never raises, never blocks."""
    global _preload_started
    if _preload_started:
        return
    _preload_started = True
    try:
        import settings_store
        if settings_store.feature_on("light_mode"):
            return            # light mode: Parakeet is loaded by the first utterance that needs it, not at start-up
    except Exception:
        pass
    try:
        import stt_engines
        threading.Thread(target=stt_engines.preload, name="lyra-parakeet-preload", daemon=True).start()
    except Exception as e:
        log.debug("Parakeet preload not started: %s", e)


def _dictionary_hints() -> list:
    try:
        import dictionary_store           # Phase 2 (C4); fine if it is not there yet
        return list(dictionary_store.hints() or [])
    except Exception:
        return []


def _whisper_prompt() -> str:
    """The fixed command vocabulary plus the user's own names (dictionary, contacts, apps), so Whisper
    spells them right instead of guessing. Kept short: a long prompt slows decoding."""
    try:
        import stt_fix
        terms = stt_fix.prompt_terms(24)
    except Exception:
        terms = []
    if not terms:
        return WHISPER_INITIAL_PROMPT
    return WHISPER_INITIAL_PROMPT + " Names: " + ", ".join(terms) + "."


def _parakeet_text(audio_float):
    """Text from stt_engines when this utterance is routed to Parakeet, else None.

    None means "run the original Whisper path": the engine is off, not downloaded, does not support the
    language, or failed. Never raises."""
    try:
        import stt_engines
        lang = _effective_language()
        engine, _why = stt_engines.route(lang)
        if engine != "parakeet":
            return None
        res = stt_engines.transcribe(audio_float, language=lang,
                                     hints=list(_COMMAND_HINTS) + _dictionary_hints())
        if res.get("error"):
            return None
        log.debug("STT engine: %s (%d ms)", res.get("engine"), res.get("ms", 0))
        return str(res.get("text") or "")
    except Exception as e:
        log.warning("stt_engines unavailable, using Whisper: %s", e)
        return None


_WAKE_NAME_RE = re.compile(
    r"\b(hey\s+)?(jarvis|jarviss|jarves|jervis|jar vis|jarv is|lyra|leer|lear|lir|lyra|lira)\b",
    re.IGNORECASE,
)


def _canonical_wake_name() -> str:
    try:
        from settings_store import wake_name
        return wake_name().lower().strip() or "lyra"
    except Exception:
        return "lyra"


def _normalize_transcript(text: str) -> str:
    """Fix systematic STT errors for this project."""
    if not text:
        return text
    name = _canonical_wake_name()
    text = _WAKE_NAME_RE.sub(
        lambda m: (f"hey {name}" if m.group(1) else name), text
    )
    text = text.replace("spot ify", "spotify")
    text = text.replace("spotty fy", "spotify")
    text = text.replace("disc ord", "discord")
    text = text.replace("vs code", "vscode")
    text = text.replace("v s code", "vscode")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _looks_hallucinated(text: str, segments) -> bool:
    words=re.findall(r"[a-z']+", text.lower())
    if len(words) < 4: return False
    # Repeated short loops are common on music/silence.
    n=min(3,len(words)); grams=[tuple(words[i:i+n]) for i in range(len(words)-n+1)]
    if grams and max(grams.count(g) for g in set(grams)) >= 4: return True
    unique=len(set(words))/max(1,len(words))
    if len(words)>=12 and unique < 0.28: return True
    try:
        if getattr(segments,'no_speech_prob',0) > 0.75: return True
        if getattr(segments,'compression_ratio',0) > 2.8: return True
    except Exception: pass
    return False


class SpeechRecognizer:
    """Local speech recognition using faster-whisper (Whisper optimized with CTranslate2)."""

    def __init__(self):
        # The model is loaded by load() - main() calls it after the UI bridge is up,
        # so a first-run download never keeps the UI waiting for the bridge.
        self._load_lock = threading.Lock()

    @property
    def model(self):
        """The one shared model (None while it is unloaded after idling): load() brings it back."""
        return _SHARED["model"]

    @model.setter
    def model(self, _value) -> None:
        pass                                               # there is only the shared copy

    def load(self) -> None:
        """Download (first run) and load the Whisper model. Safe to call repeatedly."""
        with self._load_lock:
            if self.model is not None:
                return
            log.info(f"Initializing speech recognizer (faster-whisper {WHISPER_MODEL})...")
            try:
                self.model = _load_shared_whisper()
            except Exception as e:
                log.error(f"Failed to initialize speech recognizer: {e}")
                raise
        _preload_parakeet()

    def transcribe(self, audio_data: bytes, sample_rate: int = 16000) -> tuple[str, str]:
        """Transcribe audio data to text.

        Returns:
            (status, text) where status is "ok" | "unrecognized" | "error"
        """
        _t_stt = time.monotonic()
        try:
            self.load()
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            audio_float = audio_array.astype(np.float32) / 32768.0
            if audio_float.size < int(0.25 * sample_rate):     # a click or a door, not speech: skip the model entirely
                log.info("STT: audio too short (%.2f s)", audio_float.size / float(sample_rate))
                return "unrecognized", ""

            # Skip genuinely silent buffers, but do not reject a quiet microphone.
            # PTT often starts with a low-level voice and the old 0.01 peak gate
            # made those turns look like "Didn't catch that" before Whisper ran.
            peak = float(np.max(np.abs(audio_float))) if audio_float.size else 0.0
            rms = float(np.sqrt(np.mean(audio_float * audio_float))) if audio_float.size else 0.0
            if peak < 0.004 or rms < 0.0008:
                log.info("STT: audio too quiet (peak=%.4f rms=%.4f)", peak, rms)
                return "unrecognized", ""
            # Gentle gain for quiet speech; never boost already-loud audio.
            if peak < 0.20:
                audio_float = np.clip(audio_float * min(2.5, 0.20 / max(peak, 1e-6)), -1.0, 1.0)

            pk = _parakeet_text(audio_float)
            if pk is not None:
                full_text = _normalize_transcript(pk.lower())
                if not full_text:
                    return "unrecognized", ""
                if _looks_hallucinated(full_text, None):
                    log.info("Parakeet rejected likely hallucination: %r", full_text[:120])
                    return "unrecognized", ""
                log.info(f"Transcribed: '{full_text}'")
                return "ok", full_text

            segments, info = self.model.transcribe(
                audio_float,
                language=_effective_language(),
                beam_size=WHISPER_BEAM_SIZE,    # 1-2 is several times faster than 8 and plenty for short commands
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=400,
                    speech_pad_ms=200,
                ),
                condition_on_previous_text=False,  # better for short independent commands
                initial_prompt=_whisper_prompt(),
                temperature=0.0,                  # deterministic for commands
            )

            text_parts = []
            segs=[]
            for segment in segments:
                t = segment.text.strip()
                if t:
                    text_parts.append(t); segs.append(segment)

            if not text_parts:
                return "unrecognized", ""

            full_text = _normalize_transcript(" ".join(text_parts).lower())
            if _looks_hallucinated(full_text, segs[0] if segs else None):
                log.info("Whisper rejected likely hallucination: %r", full_text[:120])
                return "unrecognized", ""
            log.info(f"Transcribed: '{full_text}'")
            return "ok", full_text

        except Exception as e:
            log.error(f"Transcription error: {e}")
            return "error", ""
        finally:
            perf.add("stt", time.monotonic() - _t_stt)

    def transcribe_audio_data(self, audio_data) -> tuple[str, str]:
        """Transcribe speech_recognition.AudioData."""
        try:
            raw_pcm = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
            return self.transcribe(raw_pcm, sample_rate=16000)
        except Exception as e:
            log.error(f"Failed to convert speech_recognition.AudioData: {e}")
            return "error", ""

    def transcribe_from_wav(self, wav_path: str) -> tuple[str, str]:
        """Transcribe audio from a WAV file."""
        try:
            with wave.open(wav_path, "rb") as wf:
                sample_rate = wf.getframerate()
                audio_data = wf.readframes(wf.getnframes())
            return self.transcribe(audio_data, sample_rate)
        except Exception as e:
            log.error(f"Failed to read WAV file: {e}")
            return "error", ""
