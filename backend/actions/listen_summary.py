"""'Summarize what I'm hearing': listen to the PC's sound for a while, transcribe it with the
local Whisper model, and summarize it with the chat brain.

It runs in the background (a minute of audio plus transcription takes a while), so Lyra answers
at once and posts the summary in the chat and as an island notification when it is ready.
Only your PC's playback is captured (never the microphone), nothing is stored, and the audio file
is deleted right after transcription.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import time

_busy = threading.Lock()

_LISTEN = re.compile(
    r"^(?:summari[sz]e|recap|tldr|tl;dr|take\s+notes\s+(?:on|of|from))\s+"
    r"(?:what\s+(?:i'?m|you'?re|we'?re|is\s+being)\s+(?:hearing|listening\s+to|watching|said|playing)"
    r"|what(?:'s|\s+is)\s+(?:playing|being\s+said)"
    r"|(?:this|the)\s+(?:video|call|meeting|lecture|podcast|stream|audio|clip|song))"
    r"(?:\s+for\s+(?P<n>\d+)\s*(?P<unit>seconds?|secs?|minutes?|mins?))?$"
    r"|^listen\s+(?:to\s+(?:this|my\s+pc)\s*)?(?:and\s+)?(?:summari[sz]e|take\s+notes)(?:\s+for\s+(?P<n2>\d+)\s*(?P<unit2>seconds?|secs?|minutes?|mins?))?$"
)
_DEFAULT_S, _MIN_S, _MAX_S = 60, 15, 180


def _seconds(m: re.Match) -> int:
    n = m.group("n") or m.group("n2")
    unit = (m.group("unit") or m.group("unit2") or "seconds").lower()
    if not n:
        return _DEFAULT_S
    secs = int(n) * (60 if unit.startswith("min") else 1)
    return max(_MIN_S, min(_MAX_S, secs))


def _post(text: str, title: str = "Summary") -> None:
    try:
        import ui
        ui.print_chat("lyra", text, "")
    except Exception:
        pass
    try:
        import ui_bridge
        ui_bridge.push_notification("reminder", "reminder", title, text[:160])
    except Exception:
        pass


def _transcribe(wav_path: str) -> str:
    from faster_whisper import WhisperModel
    import config
    import model_setup
    path = model_setup.ensure_whisper(config.WHISPER_MODEL)
    model = WhisperModel(path, device=config.WHISPER_DEVICE, compute_type=config.WHISPER_COMPUTE_TYPE)
    segments, _info = model.transcribe(wav_path, language=None, vad_filter=True, beam_size=1,
                                       condition_on_previous_text=False)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _job(seconds: int) -> None:
    wav_path = ""
    try:
        import music_recognition
        wav = music_recognition.capture_system_audio(seconds, max_seconds=_MAX_S)
        if not wav:
            _post("I couldn't capture your PC's audio. Is PyAudioWPatch installed?", "Summary failed")
            return
        try:
            peak = music_recognition._wav_peak(wav)
        except Exception:
            peak = 1000
        if peak < 300:
            _post("Your PC was silent while I listened, so there is nothing to summarize.", "Summary")
            return
        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with open(wav_path, "wb") as f:
            f.write(wav)
        text = _transcribe(wav_path)
        if len(text) < 40:
            _post("I didn't hear enough speech to summarize - it may be music or silence.", "Summary")
            return
        import brain
        prompt = ("Below is an automatic transcript of about " + f"{seconds} seconds of audio from the user's PC. "
                  "Summarize what it is about in three to five short sentences, in the language of the transcript. "
                  "Mention names and numbers if there are any. The transcript may contain mistakes.\n\n"
                  f"Transcript:\n{text[:12000]}")
        reply = (brain.ask_brain(prompt, [], intent="chat") or "").strip()
        _post(reply or "I couldn't summarize that.", "Summary ready")
    except Exception as e:  # noqa: BLE001
        _post(f"I couldn't summarize the audio - {type(e).__name__}: {str(e)[:120]}", "Summary failed")
    finally:
        if wav_path:
            try:
                os.remove(wav_path)
            except OSError:
                pass
        try:
            _busy.release()
        except RuntimeError:
            pass


def try_listen(command: str) -> str | None:
    low = command.strip().lower().rstrip("?.! ")
    low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", low).strip()
    m = _LISTEN.match(low)
    if not m:
        return None
    if not _busy.acquire(blocking=False):
        return "I'm already listening - give me a moment."
    secs = _seconds(m)
    threading.Thread(target=_job, args=(secs,), name="lyra-listen-summary", daemon=True).start()
    mins = f"{secs // 60} minute{'s' if secs // 60 != 1 else ''}" if secs % 60 == 0 else f"{secs} seconds"
    return f"Listening to your PC's audio for {mins}. I'll post the summary here when it's ready."
