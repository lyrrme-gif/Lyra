"""Lyra — Shazam-like music recognition for system audio.

Architecture:
  capture (short sample) → provider.recognize() → structured result

Providers are swappable. Built-in:
  - smtc: Windows media session (instant, no network) when an app reports track metadata
  - shazam: Shazam-compatible fingerprint recognition via ShazamIO (no user API key)
  - audd: optional AudD.io fingerprint fallback (only used when configured)

Never invents results. Failures return clear messages.
Automatic mode only samples when music is actually playing (session active),
never continuously streams audio to an external API.
"""

from __future__ import annotations

import io
import asyncio
import json
import struct
import threading
import time
import wave
from dataclasses import dataclass, asdict
from typing import Protocol

import paths

import ui

log = ui.log

SAMPLE_SECONDS = 10.0
SAMPLE_RATE = 44100
CHANNELS = 1  # mono is enough for fingerprinting


@dataclass
class RecognitionResult:
    ok: bool
    title: str = ""
    artist: str = ""
    album: str = ""
    artwork_url: str = ""
    spotify_url: str = ""
    youtube_url: str = ""
    source: str = ""          # "smtc" | "audd" | ...
    message: str = ""         # human-readable status / error

    def to_dict(self) -> dict:
        return asdict(self)


class RecognitionProvider(Protocol):
    name: str

    def recognize(self, wav_bytes: bytes | None, *, hint: dict | None = None) -> RecognitionResult:
        ...


# ── SMTC (no audio sample needed) ────────────────────────────────────────────

class SmtcProvider:
    name = "smtc"

    def recognize(self, wav_bytes: bytes | None = None, *, hint: dict | None = None) -> RecognitionResult:
        try:
            import media_control
            info = media_control.now_playing()
        except Exception as e:
            return RecognitionResult(False, message=f"Media session unavailable: {e}", source=self.name)
        if not info.get("available"):
            return RecognitionResult(False, message="No media session reporting a track.", source=self.name)
        title = (info.get("title") or "").strip()
        artist = (info.get("artist") or "").strip()
        if not title and not artist:
            return RecognitionResult(False, message="Media session has no title/artist.", source=self.name)
        # Build useful links from the metadata we have
        q = " ".join(x for x in (title, artist) if x).strip()
        from urllib.parse import quote_plus
        return RecognitionResult(
            ok=True,
            title=title or "Unknown title",
            artist=artist or "Unknown artist",
            album="",
            artwork_url="",
            spotify_url=f"https://open.spotify.com/search/{quote_plus(q)}" if q else "",
            youtube_url=f"https://www.youtube.com/results?search_query={quote_plus(q)}" if q else "",
            source=self.name,
            message="Identified from the active media session.",
        )


class ShazamProvider:
    """Shazam-compatible fingerprint recognition through ShazamIO. No user API key required."""
    name = "shazam"

    def recognize(self, wav_bytes: bytes | None, *, hint: dict | None = None) -> RecognitionResult:
        if not wav_bytes:
            return RecognitionResult(False, message="No audio sample to identify.", source=self.name)
        try:
            from shazamio import Serialize, Shazam

            async def run():
                # ShazamIO 0.8.x exposes Shazam as a normal client object, not an
                # async context manager. The old ``async with Shazam(...)`` call
                # therefore fails before a fingerprint is ever sent. Keep the
                # sample as WAV bytes; shazamio-core decodes/down-mixes/resamples
                # it internally.
                shazam = Shazam(segment_duration_seconds=10)
                return await shazam.recognize(wav_bytes)

            raw = asyncio.run(run())
            log.info("Shazam: recognition response received (keys=%s)", list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__)
            matches = raw.get("matches") if isinstance(raw, dict) else None
            if isinstance(matches, list) and not matches:
                return RecognitionResult(False, message="Shazam found no matching track in this sample.", source=self.name)
            track = Serialize.full_track(raw).track
            # ShazamIO's serialized image object differs between releases. Prefer the
            # raw Shazam payload so artwork survives even when ``track.images`` is a
            # plain dict rather than an object with ``coverarthq`` attributes.
            track_data = raw.get("track") if isinstance(raw, dict) else {}
            if not isinstance(track_data, dict):
                track_data = {}

            def _first_url(value, *, wanted=()):
                if isinstance(value, dict):
                    for k, v in value.items():
                        key = str(k).lower()
                        if isinstance(v, str) and v.strip():
                            text = v.strip()
                            low = text.lower()
                            if any(token in low for token in wanted):
                                return text
                        found = _first_url(v, wanted=wanted)
                        if found:
                            return found
                elif isinstance(value, list):
                    for item in value:
                        found = _first_url(item, wanted=wanted)
                        if found:
                            return found
                return ""

            images = track_data.get("images") or {}
            if not isinstance(images, dict):
                images = {}
            title = str(track_data.get("title") or getattr(track, "title", "") or "").strip()
            artist = str(track_data.get("subtitle") or getattr(track, "subtitle", None) or getattr(track, "artist", "") or "").strip()
            album = ""
            for section in (track_data.get("sections") or []):
                for meta in (section.get("metadata") or []) if isinstance(section, dict) else []:
                    if not isinstance(meta, dict):
                        continue
                    label = str(meta.get("title") or meta.get("key") or "").lower()
                    text = str(meta.get("text") or "").strip()
                    if text and ("album" in label or not album):
                        album = text
                    if "album" in label and text:
                        break
                if album and any("album" in str(m.get("title") or m.get("key") or "").lower() for m in (section.get("metadata") or []) if isinstance(m, dict)):
                    break
            artwork = str(images.get("coverarthq") or images.get("coverart") or images.get("coverart300") or "").strip()
            if not artwork:
                artwork = _first_url(track_data, wanted=("coverart", "artwork", "image"))
            spotify_url = _first_url(track_data, wanted=("open.spotify.com", "spotify:track:"))
            if spotify_url.startswith("spotify:track:"):
                spotify_url = "https://open.spotify.com/track/" + spotify_url.split(":")[-1]
            if not title and not artist:
                return RecognitionResult(False, message="Shazam did not find a matching track.", source=self.name)
            from urllib.parse import quote_plus
            q = " ".join(x for x in (title, artist) if x)
            if not spotify_url and q:
                spotify_url = f"https://open.spotify.com/search/{quote_plus(q)}"
            return RecognitionResult(True, title=title, artist=artist, album=album, artwork_url=artwork,
                spotify_url=spotify_url,
                youtube_url=f"https://www.youtube.com/results?search_query={quote_plus(q)}" if q else "",
                source=self.name, message="Identified from a system-audio fingerprint.")
        except Exception as e:
            log.debug("Shazam recognition failed: %s", e)
            return RecognitionResult(False, message=f"Shazam recognition failed: {e}", source=self.name)


HISTORY_PATH = paths.data_path("music_recognition_history.json")
_HISTORY_LOCK = threading.Lock()
_MAX_HISTORY = 50
_HISTORY_CACHE: list[dict] = []

# Previous recognitions only live for one app session: wipe them every time the
# backend starts. SESSION_ID lets the UI drop its own cached copy as well.
SESSION_ID = str(int(time.time() * 1000))


def reset_history() -> None:
    global _HISTORY_CACHE
    with _HISTORY_LOCK:
        _HISTORY_CACHE = []
        try:
            HISTORY_PATH.unlink(missing_ok=True)
        except Exception as e:
            log.warning("Music recognition history reset failed (%s): %s", HISTORY_PATH, e)


reset_history()


def _read_history() -> list[dict]:
    global _HISTORY_CACHE
    try:
        if not HISTORY_PATH.exists():
            return list(_HISTORY_CACHE)
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            _HISTORY_CACHE = data[:_MAX_HISTORY]
            return list(_HISTORY_CACHE)
    except Exception as e:
        log.warning("Music recognition history read failed (%s): %s", HISTORY_PATH, e)
    return list(_HISTORY_CACHE)

def history() -> list[dict]:
    with _HISTORY_LOCK:
        return _read_history()

def save_history(result: RecognitionResult) -> None:
    global _HISTORY_CACHE
    if not result.ok or not (result.title or result.artist):
        return
    item = result.to_dict()
    item["ts"] = int(time.time() * 1000)
    with _HISTORY_LOCK:
        items = _read_history()
        # Every successful recognition is a history event. Do not collapse
        # repeated listens of the same song; users should be able to see that
        # Ctrl+Win/manual recognition actually happened.
        items.insert(0, item)
        items = items[:_MAX_HISTORY]
        # Keep an in-process cache even if a locked/read-only folder prevents the
        # disk write. This makes the History tab reliable for the current session.
        _HISTORY_CACHE = items
        try:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = HISTORY_PATH.with_suffix(HISTORY_PATH.suffix + ".tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(HISTORY_PATH)
            log.info("Music recognition history saved: %s (%d items)", HISTORY_PATH, len(items))
        except Exception as e:
            log.warning("Music recognition history write failed (%s): %s", HISTORY_PATH, e)


# ── AudD fingerprint ─────────────────────────────────────────────────────────

class AuddProvider:
    name = "audd"

    def __init__(self, token: str):
        self.token = (token or "").strip()

    def recognize(self, wav_bytes: bytes | None, *, hint: dict | None = None) -> RecognitionResult:
        if not self.token:
            return RecognitionResult(
                False,
                message="AudD API token not configured. Add one in Settings → Music Recognition.",
                source=self.name,
            )
        if not wav_bytes:
            return RecognitionResult(False, message="No audio sample to identify.", source=self.name)
        try:
            import requests
            files = {"file": ("sample.wav", wav_bytes, "audio/wav")}
            data = {
                "api_token": self.token,
                "return": "spotify,apple_music",
            }
            r = requests.post("https://api.audd.io/", data=data, files=files, timeout=20)
            payload = r.json() if r.content else {}
        except Exception as e:
            return RecognitionResult(False, message=f"Recognition request failed: {e}", source=self.name)

        if payload.get("status") != "success" or not payload.get("result"):
            err = payload.get("error") or {}
            msg = err.get("error_message") if isinstance(err, dict) else None
            return RecognitionResult(
                False,
                message=msg or "Couldn't identify the song.",
                source=self.name,
            )
        res = payload["result"]
        title = (res.get("title") or "").strip()
        artist = (res.get("artist") or "").strip()
        album = (res.get("album") or "").strip()
        if not title and not artist:
            return RecognitionResult(False, message="Couldn't identify the song.", source=self.name)

        spotify_url = ""
        artwork = ""
        try:
            sp = res.get("spotify") or {}
            if isinstance(sp, dict):
                external = sp.get("external_urls") or {}
                spotify_url = external.get("spotify") or ""
                images = (sp.get("album") or {}).get("images") or []
                if images:
                    artwork = images[0].get("url") or ""
        except Exception:
            pass

        from urllib.parse import quote_plus
        q = " ".join(x for x in (title, artist) if x)
        youtube = f"https://www.youtube.com/results?search_query={quote_plus(q)}" if q else ""

        return RecognitionResult(
            ok=True,
            title=title or "Unknown title",
            artist=artist or "Unknown artist",
            album=album,
            artwork_url=artwork,
            spotify_url=spotify_url or (f"https://open.spotify.com/search/{quote_plus(q)}" if q else ""),
            youtube_url=youtube,
            source=self.name,
            message="Identified from system audio.",
        )


# ── System audio capture (WASAPI loopback when possible) ─────────────────────

def _pcm_to_wav(pcm: bytes, rate: int, channels: int, sample_width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def capture_system_audio(seconds: float = SAMPLE_SECONDS, max_seconds: float = 12.0) -> bytes | None:
    """Capture a short WAV of what the PC is playing.

    Tries WASAPI loopback via PyAudioWPatch, then soundcard, then returns None.
    Never falls back to the microphone — that would identify ambient sound, not system audio.
    """
    seconds = max(3.0, min(float(max_seconds), float(seconds)))

    # 1) PyAudioWPatch (WASAPI loopback)
    try:
        import array
        import pyaudiowpatch as pyaudio

        def _peak(pcm16: bytes) -> int:
            a = array.array("h")
            a.frombytes(pcm16[: len(pcm16) // 2 * 2])
            return max((abs(v) for v in a), default=0)

        def _record(p, dev, secs: float):
            """Record `secs` from one loopback device. Never blocks: WASAPI loopback delivers
            nothing while a device is silent, so poll with a deadline and pad with silence."""
            rate = int(dev.get("defaultSampleRate") or SAMPLE_RATE)
            ch = max(1, min(2, int(dev.get("maxInputChannels") or 1)))
            stream = p.open(format=pyaudio.paInt16, channels=ch, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=512)
            need, got, chunks = int(rate * secs), 0, []
            deadline = time.time() + secs + 2.0
            try:
                while got < need and time.time() < deadline:
                    avail = stream.get_read_available()
                    if avail <= 0:
                        time.sleep(0.01)
                        continue
                    data = stream.read(min(avail, need - got), exception_on_overflow=False)
                    chunks.append(data)
                    got += len(data) // (2 * ch)
            finally:
                stream.stop_stream()
                stream.close()
            return b"".join(chunks) + b"\x00" * ((need - got) * 2 * ch), rate, ch

        p = pyaudio.PyAudio()
        try:
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            if not default_speakers.get("isLoopbackDevice"):
                for i in range(p.get_device_count()):
                    dev = p.get_device_info_by_index(i)
                    if dev.get("hostApi") == wasapi_info["index"] and "Loopback" in dev.get("name", ""):
                        if default_speakers.get("name", "").split(" (")[0] in dev.get("name", ""):
                            default_speakers = dev
                            break
            # An app can be routed to a different output than the Windows default (Spotify's
            # per-app output setting, a headset, a game launcher). Listen briefly to every
            # loopback device, default first, and record the one that is actually playing.
            candidates = [default_speakers]
            try:
                for d in p.get_loopback_device_info_generator():
                    if d["index"] != default_speakers["index"]:
                        candidates.append(d)
            except Exception as e:
                log.debug("listing loopback devices failed: %s", e)
            chosen = default_speakers
            if len(candidates) > 1:
                for d in candidates[:5]:
                    try:
                        probe, _, _ = _record(p, d, 0.6)
                        pk = _peak(probe)
                        log.info("Music recognition: loopback probe %r peak=%d", d.get("name"), pk)
                        if pk >= 300:
                            chosen = d
                            break
                    except Exception as e:
                        log.debug("loopback probe failed for %r: %s", d.get("name"), e)
            pcm, rate, channels = _record(p, chosen, seconds)
            # Downmix to mono if stereo (numpy when available: long captures stay fast)
            if channels == 2:
                try:
                    import numpy as np
                    a = np.frombuffer(pcm[: len(pcm) // 4 * 4], dtype="<i2").reshape(-1, 2)
                    pcm = a.astype("int32").sum(axis=1).__floordiv__(2).astype("<i2").tobytes()
                except Exception:
                    mono = bytearray()
                    for i in range(0, len(pcm), 4):
                        if i + 3 >= len(pcm):
                            break
                        l = struct.unpack_from("<h", pcm, i)[0]
                        r = struct.unpack_from("<h", pcm, i + 2)[0]
                        mono.extend(struct.pack("<h", int((l + r) / 2)))
                    pcm = bytes(mono)
                channels = 1
            return _pcm_to_wav(pcm, rate, channels)
        finally:
            p.terminate()
    except Exception as e:
        log.warning("loopback via pyaudiowpatch failed: %s (pip install PyAudioWPatch)", e)

    # 2) soundcard library
    try:
        import soundcard as sc
        import numpy as np
        speaker = sc.default_speaker()
        mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        with mic.recorder(samplerate=SAMPLE_RATE, channels=1) as rec:
            data = rec.record(numframes=int(SAMPLE_RATE * seconds))
        # float32 → int16
        pcm = (np.clip(data.reshape(-1), -1.0, 1.0) * 32767.0).astype("int16").tobytes()
        return _pcm_to_wav(pcm, SAMPLE_RATE, 1)
    except Exception as e:
        log.warning("loopback via soundcard failed: %s", e)

    return None


def music_is_playing() -> bool:
    """Lightweight check: any active (playing) audio session, or SMTC playing."""
    try:
        import media_control
        info = media_control.now_playing()
        if info.get("available") and "playing" in str(info.get("status", "")).lower():
            return True
    except Exception:
        pass
    try:
        import app_volume
        for a in app_volume.list_apps():
            if a.get("active") and not a.get("muted") and a.get("level", 0) > 0.01:
                return True
    except Exception:
        pass
    return False


# ── Public API ───────────────────────────────────────────────────────────────

def _providers() -> list:
    out: list = [SmtcProvider(), ShazamProvider()]
    try:
        import settings_store
        token = str(settings_store.get("audd_api_token") or "")
    except Exception:
        token = ""
    out.append(AuddProvider(token))
    return out


def _wav_peak(wav: bytes) -> int:
    """Loudest sample (0..32768) of a 16-bit WAV: 0 means pure silence."""
    import array
    with wave.open(io.BytesIO(wav), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    a = array.array("h")
    a.frombytes(raw[: len(raw) // 2 * 2])
    return max((abs(v) for v in a), default=0)


def recognize(force_audio: bool = False) -> RecognitionResult:
    """Identify the currently playing track.

    Order:
      1. SMTC media session (fast, local) unless force_audio
      2. Short system-audio sample → fingerprint provider
    """
    providers = _providers()

    if not force_audio:
        smtc = next((p for p in providers if p.name == "smtc"), None)
        if smtc:
            r = smtc.recognize(None)
            if r.ok:
                return r

    if not music_is_playing():
        # Still try capture in case detection missed, but prefer honest message
        pass

    log.info("Music recognition: capturing %.0fs of system audio...", SAMPLE_SECONDS)
    wav = capture_system_audio(SAMPLE_SECONDS)
    if wav is not None:
        try:
            with wave.open(io.BytesIO(wav), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                duration = frames / max(1, rate)
                log.info("Music recognition: captured %.1fs WAV (%d Hz, %d ch, %d bytes)", duration, rate, wf.getnchannels(), len(wav))
        except Exception:
            log.info("Music recognition: captured %d-byte audio sample", len(wav))
    if wav is not None:
        try:
            import os, tempfile
            peak = _wav_peak(wav)
            dump = os.path.join(tempfile.gettempdir(), "lyra-last-capture.wav")
            with open(dump, "wb") as f:
                f.write(wav)
            log.info("Music recognition: capture peak level %d/32768 (saved to %s)", peak, dump)
            if peak < 300:
                return RecognitionResult(
                    False,
                    message=("The capture was silent. Play the song on the default Windows output "
                             "device (the one set in Sound settings) and try again."),
                    source="capture",
                )
        except Exception as e:
            log.debug("capture level check failed: %s", e)
    if wav is None:
        # If SMTC had partial data we already returned; otherwise explain
        return RecognitionResult(
            False,
            message=(
                "Couldn't capture system audio. Install PyAudioWPatch or soundcard for loopback, "
                "or play media that reports track info (Spotify, etc.)."
            ),
            source="capture",
        )

    # Fingerprint providers: Shazam is the primary provider. AudD is optional
    # and must never replace a useful Shazam failure with an API-token error.
    shazam = next((p for p in providers if p.name == "shazam"), None)
    if shazam is not None:
        log.info("Music recognition: sending the sample to Shazam...")
        shazam_result = shazam.recognize(wav)
        if shazam_result.ok:
            return shazam_result
        log.info("Music recognition: Shazam result: %s", shazam_result.message)

    audd = next((p for p in providers if p.name == "audd"), None)
    if audd is not None:
        audd_result = audd.recognize(wav)
        if audd_result.ok:
            return audd_result
        # An unconfigured AudD fallback is not a recognition failure. Return the
        # Shazam result instead, so the UI never tells users to configure AudD
        # when they only asked for the built-in Shazam-compatible recognizer.
        if audd_result.message.startswith("AudD API token not configured"):
            if shazam is not None:
                return shazam_result
        else:
            log.info("Music recognition: AudD fallback: %s", audd_result.message)
            if shazam is None:
                return audd_result

    return shazam_result if shazam is not None else RecognitionResult(
        False, message="Couldn't identify the song.", source="none"
    )


# ── Optional automatic recognition (gated) ───────────────────────────────────

_auto_thread: threading.Thread | None = None
_auto_stop = threading.Event()
_last_auto_ts = 0.0
_last_auto_key = ""


def _auto_mode() -> str:
    try:
        import settings_store
        mode = str(settings_store.get("music_recognition_mode") or "manual").lower()
    except Exception:
        mode = "manual"
    if mode not in ("off", "manual", "automatic"):
        mode = "manual"
    return mode


def start_auto(push) -> None:
    """Background watcher for automatic mode. Only samples when music is playing."""
    global _auto_thread
    if _auto_thread and _auto_thread.is_alive():
        return

    def loop():
        global _last_auto_ts, _last_auto_key
        while not _auto_stop.is_set():
            try:
                if _auto_mode() != "automatic":
                    time.sleep(3.0)
                    continue
                if not music_is_playing():
                    time.sleep(2.0)
                    continue
                # At most once every 90s, and only if track identity likely changed
                now = time.time()
                if now - _last_auto_ts < 90:
                    time.sleep(2.0)
                    continue
                result = recognize(force_audio=False)
                _last_auto_ts = now
                if result.ok:
                    key = f"{result.title}|{result.artist}".lower()
                    if key != _last_auto_key:
                        _last_auto_key = key
                        push({
                            "type": "music_recognition",
                            "ok": True,
                            **{k: v for k, v in result.to_dict().items() if k != "ok"},
                            "ts": int(now * 1000),
                        })
            except Exception as e:
                log.debug("music auto: %s", e)
            time.sleep(2.0)

    _auto_stop.clear()
    _auto_thread = threading.Thread(target=loop, daemon=True, name="lyra-music-recog")
    _auto_thread.start()


def stop_auto() -> None:
    _auto_stop.set()
