"""Lyra — TTS (speak), mixer, offline voices, SpeechStream.

Split from voice.py (listen + wake patterns stay there).

TTS uses edge-tts — Microsoft Edge's free neural voices (Aria/Jenny/etc.),
not the built-in Windows SAPI5 voices pyttsx3 used before. Trade-off: it
needs an internet connection and synthesizes to a temp mp3 before playback
starts (a few hundred ms of latency you didn't have with pyttsx3's instant
local synthesis), in exchange for actually sounding smooth instead of
robotic. Playback goes through pygame.mixer so barge-in (stopping mid-
sentence when the user talks over Lyra) still works the same way it did
with pyttsx3's tts.stop().
"""

import os
import re
import time
import asyncio
import tempfile
import threading
import queue

import pygame
import speech_recognition as sr

from engine.bridge import ui
from engine.core import perf
from engine.core.config import (
    TTS_VOICE, TTS_RATE_PCT,
    BARGE_IN_START_DELAY, ECHO_THRESHOLD_MULTIPLIER, BARGE_IN_CHECK_TIMEOUT,
    PHRASE_TIME_LIMIT, BARGE_IN_ENABLED,
)
from engine.voice.stt import SpeechRecognizer

log = ui.log

# Initialize the local speech recognizer (faster-whisper)
_stt_engine = SpeechRecognizer()


def _fix_heard(text):
    """Repair misheard names / dictionary words in a voice command before it is acted on (stt_fix.py)."""
    try:
        from engine.voice import stt_fix
        return stt_fix.fix(text)
    except Exception:
        return text

_mixer_ready = False

# When Lyra speaks over Spotify, duck only Spotify's audio session instead of
# changing the master Windows volume.  This preserves the user's system volume
# and makes the music return exactly to its previous level afterwards.
_SPOTIFY_DUCK_FACTOR = 0.35

def _duck_spotify(factor: float = _SPOTIFY_DUCK_FACTOR):
    """Lower active Spotify audio sessions and return restore callbacks.

    Uses Windows Core Audio through pycaw when available.  If pycaw is not
    installed or Spotify is not using a separate audio session, this is a
    harmless no-op.
    """
    restored = []
    _quiet_volume_hud()
    try:
        from pycaw.pycaw import AudioUtilities
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            try:
                process = session.Process
                if process is None or process.name().lower() != "spotify.exe":
                    continue
                volume = session.SimpleAudioVolume
                original = float(volume.GetMasterVolume())
                target = max(0.0, min(1.0, original * factor))
                if abs(target - original) > 0.001:
                    volume.SetMasterVolume(target, None)
                    restored.append((volume, original))
            except Exception:
                continue
    except Exception as e:
        log.debug("Spotify ducking unavailable: %s", e)
    return restored

def _restore_spotify(volumes) -> None:
    _quiet_volume_hud()
    for volume, original in volumes or []:
        try:
            volume.SetMasterVolume(original, None)
        except Exception:
            pass


def _quiet_volume_hud() -> None:
    """Ducking is Lyra's own doing: keep the island's volume HUD from popping up for it."""
    try:
        from engine.features import app_volume
        app_volume.quiet()
    except Exception:
        pass


_mixer_device = None  # speaker name the mixer was opened with (None = system default)


def _wanted_output():
    """Speaker chosen in Settings > Voice, or None for the system default."""
    try:
        from engine.core import settings_store
        name = str(settings_store.get("audio_output") or "").strip()
    except Exception:
        name = ""
    return None if name in ("", "Default Speaker") else name


def _ensure_mixer() -> None:
    """Open the mixer on the speaker picked in Settings; re-open it when that changes
    (takes effect from the next spoken reply, no restart)."""
    global _mixer_ready, _mixer_device
    want = _wanted_output()
    if _mixer_ready and want == _mixer_device:
        return
    if _mixer_ready:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        _mixer_ready = False
    try:
        if want:
            pygame.mixer.init(devicename=want)
        else:
            pygame.mixer.init()
    except Exception as e:
        log.warning("Speaker %r unavailable (%s) - using the default one.", want, e)
        pygame.mixer.init()
    _mixer_device = want
    _mixer_ready = True


def list_output_devices() -> list:
    """Names of the playback devices SDL can see (empty if unavailable)."""
    try:
        _ensure_mixer()
        from pygame._sdl2.audio import get_audio_device_names
        return [str(n) for n in get_audio_device_names(False)]
    except Exception:
        return []


_test_lock = threading.Lock()


def test_voice(text: str = "This is how I sound right now.") -> bool:
    """Speak a sample line with the current voice settings. False if already busy."""
    if not _test_lock.acquire(blocking=False):
        return False
    try:
        speak(text, force=True)      # force: the sample must be heard even when Mochi sounds replace short replies
    finally:
        _test_lock.release()
    return True


_edge_patched = False


def _latest_edge_version() -> str:
    """Current stable Microsoft Edge (what the speech service expects to see), best effort."""
    try:
        import json
        import urllib.request
        req = urllib.request.Request("https://edgeupdates.microsoft.com/api/products?view=enterprise",
                                     headers={"User-Agent": "Lyra/1.0"})
        with urllib.request.urlopen(req, timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        for prod in data:
            if str(prod.get("Product")) != "Stable":
                continue
            for rel in prod.get("Releases", []):
                if rel.get("Platform") == "Windows" and rel.get("Architecture") == "x64" and rel.get("ProductVersion"):
                    return str(rel["ProductVersion"])
    except Exception as e:
        _vlog(f"edge version lookup failed: {e!r}")
    # Offline lookup failed: estimate (Edge ships roughly one major release a month; 143 = Dec 2025).
    months = (time.gmtime().tm_year - 2025) * 12 + (time.gmtime().tm_mon - 12)
    return f"{143 + max(months, 0)}.0.0.0"


def _patch_edge_tts_version() -> bool:
    """edge-tts hard-codes a Chromium version; when Microsoft starts rejecting it (HTTP 403)
    the library breaks until it is released again. Present the current Edge version instead."""
    global _edge_patched
    try:
        import edge_tts.constants as C
        import edge_tts.communicate as M
        ver = _latest_edge_version()
        if ver == C.CHROMIUM_FULL_VERSION:
            return False
        major = ver.split(".")[0]
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              f"Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0")
        C.CHROMIUM_FULL_VERSION, C.CHROMIUM_MAJOR_VERSION = ver, major
        C.SEC_MS_GEC_VERSION = f"1-{ver}"
        M.SEC_MS_GEC_VERSION = C.SEC_MS_GEC_VERSION
        for hdrs in (C.BASE_HEADERS, C.WSS_HEADERS, C.VOICE_HEADERS):
            hdrs["User-Agent"] = ua
        _edge_patched = True
        _vlog(f"edge-tts now presents Edge {ver}")
        log.info("edge-tts: presenting Edge %s to the speech service", ver)
        return True
    except Exception as e:
        _vlog(f"edge-tts patch failed: {e!r}")
        return False


class _CachedResolver:
    """DNS cache that survives between replies. Every reply used to run in a fresh event loop with a fresh
    resolver, so each sentence paid a DNS lookup for Microsoft's speech host (seconds on a flaky resolver, the same
    one that made Serper fail). Answers are kept 30 min, and a stale answer is used when a new lookup fails.
    Not tied to any event loop, so it can be shared by the loops asyncio.run() creates."""
    _cache: dict = {}
    last_lookup_ms = 0.0

    async def resolve(self, host, port=0, family=0):
        import socket
        key = (host, port, family)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < 1800:
            self.last_lookup_ms = 0.0
            return hit[1]
        t0 = time.monotonic()
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM, family=family, flags=socket.AI_ADDRCONFIG)
        except Exception:
            if hit:
                _vlog(f"dns: lookup for {host} failed, using the cached address")
                return hit[1]
            raise
        out = []
        for fam, _t, proto, _cn, addr in infos:
            out.append({"hostname": host, "host": addr[0], "port": addr[1], "family": fam, "proto": proto,
                        "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV})
        self.last_lookup_ms = (time.monotonic() - t0) * 1000
        self._cache[key] = (time.monotonic(), out)
        return out

    async def close(self):
        pass


_edge_resolver = _CachedResolver()


def _edge_connector():
    """None = edge-tts uses its own default aiohttp connection (the safe default).

    The shared DNS cache above used to be handed to edge-tts through a custom aiohttp connector. On some
    aiohttp / edge-tts version combinations that makes EVERY request fail with "Session is closed", and Lyra then
    speaks with the Windows fallback voice (which ignores the voice chosen in Settings). The DNS cache is only a small
    speed-up, so it is now opt-in: set the hidden setting `edge_dns_cache` to true to use it again."""
    try:
        from engine.core import settings_store
        if settings_store.get("edge_dns_cache") is not True:
            return None
        import aiohttp
        return aiohttp.TCPConnector(resolver=_edge_resolver)
    except Exception:
        return None


def _synthesize_to_file(text: str) -> str:
    """Calls the edge-tts service and saves the result to a temp mp3.
    Raises on failure (e.g. no internet) — caller decides how to handle it."""
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)

    async def _run():
        voice_id = TTS_VOICE
        rate = TTS_RATE_PCT
        try:
            from engine.core import settings_store
            v = str(settings_store.get("tts_voice") or "").strip()
            if v:
                voice_id = v
        except Exception:
            pass
        try:
            import engine.core.settings_store as _ss
            if _ss.get("tts_auto_language") is not False:  # on by default
                from engine.voice.tts_lang import pick_voice
                voice_id = pick_voice(text, voice_id)
        except Exception:
            pass
        import edge_tts           # first spoken sentence only: edge_tts + aiohttp cost ~0.14 s at launch otherwise
        try:
            await edge_tts.Communicate(text, voice=voice_id, rate=rate, connector=_edge_connector()).save(path)
        except Exception as e:
            if getattr(e, "status", None) != 403 and "403" not in str(e):
                raise
            # Microsoft rejected the client version: present a current one and retry once.
            if _edge_patched or not _patch_edge_tts_version():
                raise
            await edge_tts.Communicate(text, voice=voice_id, rate=rate, connector=_edge_connector()).save(path)

    t0 = time.monotonic()
    asyncio.run(_run())
    # Visible in ui.log: how long the online voice really takes per piece, and whether DNS was the slow part.
    try:
        ui.trace(f"tts: edge-tts {len(text)} chars in {(time.monotonic() - t0) * 1000:.0f} ms "
                 f"(dns {'cached' if not _edge_resolver.last_lookup_ms else f'{_edge_resolver.last_lookup_ms:.0f} ms'})")
    except Exception:
        pass
    return path


def _voices_dir() -> str:
    """Folder holding the offline voice files. Next to the code in dev; for the packaged
    backend also next to the .exe or in %APPDATA%\\Lyra\\voices."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # the engine/ folder (this file is in engine/voice/)
    cands = [os.path.join(here, "voices")]
    try:
        import sys
        cands.append(os.path.join(os.path.dirname(sys.executable), "voices"))
    except Exception:
        pass
    cands.append(os.path.join(os.environ.get("APPDATA", ""), "Lyra", "voices"))
    try:
        from engine.core import paths
        cands.append(str(paths.data_dir() / "voices"))   # where Settings -> Voice -> "Download" puts the offline voice
    except Exception:
        pass
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


_edge_down_until = 0.0  # monotonic time before which edge-tts is not retried (it just failed)
_edge_fails = 0         # consecutive edge-tts failures; one blip must not switch the voice for long
_EDGE_DEADLINE = 8.0    # seconds edge-tts gets per reply (3.5 was too tight while the PC is busy)


def _piper_model():
    """Path of a Piper .onnx voice: Settings 'piper_model', env LYRA_PIPER_MODEL, or any
    .onnx in backend/voices/. None when none is installed."""
    cand = ""
    try:
        from engine.core import settings_store
        cand = str(settings_store.get("piper_model") or "").strip()
    except Exception:
        pass
    cand = cand or os.environ.get("LYRA_PIPER_MODEL", "").strip()
    if cand and os.path.isfile(cand):
        return cand
    d = _voices_dir()
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".onnx") and not f.lower().startswith("kokoro"):
                return os.path.join(d, f)
    return None


def _light_mode() -> bool:
    """Settings -> Features -> "Light mode" (on by default): heavy models are loaded only when they are
    needed. For the voice that means Microsoft's online voice (edge-tts) is the main one and the offline
    Kokoro model is NOT loaded at start-up, only if Microsoft's voice is down or not working."""
    try:
        from engine.core import settings_store
        return settings_store.feature_on("light_mode")
    except Exception:
        return True


_kokoro = None
_kokoro_lock = threading.Lock()
_kokoro_slow = 0  # consecutive replies generated slower than they play: too heavy for this PC


def _offline_chosen() -> bool:
    """True only when the user picked the offline voice (Settings -> Voice -> Voice engine). The default is
    Microsoft's Edge voice, and then the offline models are never loaded, never used and never downloaded."""
    try:
        from engine.core import settings_store
        return str(settings_store.get("tts_engine") or "edge").strip().lower() == "offline"
    except Exception:
        return False


def preload_offline() -> None:
    """Called when the offline voice has just been downloaded (or chosen): warm it up in the background."""
    threading.Thread(target=_preload_kokoro, name="lyra-kokoro-preload", daemon=True).start()


def _kokoro_allowed() -> bool:
    """Kokoro only when it was chosen, unless the user chose Piper (offline_voice=piper) or this PC is too slow."""
    if not _offline_chosen():
        return False
    if _kokoro_slow >= 2:
        return False
    try:
        from engine.core import settings_store
        if str(settings_store.get("offline_voice") or "").strip().lower() == "piper":
            return False
    except Exception:
        pass
    return True


_PUNCT = {"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": ", ",
          "\u2011": "-", "\u2010": "-", "\u2026": "...", "\u00a0": " ", "\u202f": " ", "\u2022": ","}
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]")


def _tts_clean(text: str) -> str:
    """Curly quotes, long dashes, emoji etc. make a voice stumble (and made English text
    look non-ASCII, which sent it to the wrong voice). Turn them into plain speech."""
    for k, v in _PUNCT.items():
        text = text.replace(k, v)
    text = _EMOJI.sub(" ", text)
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _kokoro_model_paths():
    d = _voices_dir()
    bank = os.path.join(d, "voices-v1.0.bin")
    for name in ("kokoro-v1.0.onnx", "kokoro-v1.0.int8.onnx"):  # full first: int8 is SLOWER on CPUs without VNNI
        m = os.path.join(d, name)
        if os.path.isfile(m) and os.path.isfile(bank):
            return m, bank
    return None


def _get_kokoro():
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            paths = _kokoro_model_paths()
            if not paths:
                return None
            from kokoro_onnx import Kokoro
            t0 = time.time()
            k, how = None, "default"
            try:
                # Use the graphics card when this PC's onnxruntime offers one (onnxruntime-directml
                # or -gpu); otherwise the CPU. Setting kokoro_provider = "cpu" forces the CPU.
                import onnxruntime as ort
                want = ""
                try:
                    from engine.core import settings_store
                    want = str(settings_store.get("kokoro_provider") or "").strip().lower()
                except Exception:
                    pass
                avail = ort.get_available_providers()
                prov = []
                if want in ("gpu", "cuda", "dml"):  # opt-in only: DirectML fails on some Kokoro ops
                    prov = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in avail]
                if prov:
                    sess = ort.InferenceSession(paths[0], providers=prov + ["CPUExecutionProvider"])
                    k, how = Kokoro.from_session(sess, paths[1]), prov[0].replace("ExecutionProvider", "")
            except Exception as e:
                _vlog(f"kokoro gpu setup failed {e!r}; using CPU")
                k = None
            if k is None:
                # Our own CPU session: ONNX Runtime's idle threads sleep instead of spinning on a core (that, together with
                # loading right after the download, froze the mouse), and in light mode fewer threads: the default (every
                # core) makes a short reply spike the whole CPU.
                try:
                    import onnxruntime as ort
                    from engine.voice import polite_load
                    light = _light_mode()
                    so = polite_load.session_options(max(1, min(4, (os.cpu_count() or 4) // 2)) if light else None)
                    sess = ort.InferenceSession(paths[0], sess_options=so, providers=["CPUExecutionProvider"])
                    k, how = Kokoro.from_session(sess, paths[1]), ("CPU, %d threads" % so.intra_op_num_threads if light else "CPU")
                except Exception as e:
                    _vlog(f"kokoro limited-thread setup failed {e!r}; default CPU session")
                    k = None
            if k is None:
                k = Kokoro(*paths)
            try:
                k.create("Hello.", voice="af_heart", lang="en-us")  # warm-up: the first run is slow
            except Exception as e:
                if how != "default":  # the GPU backend cannot run the model: back to the CPU
                    _vlog(f"kokoro {how} failed warm-up ({str(e)[:80]!r}); using CPU")
                    k, how = Kokoro(*paths), "CPU"
            _kokoro = k
            _vlog(f"kokoro loaded in {time.time() - t0:.1f}s ({os.path.basename(paths[0])}, {how})")
        return _kokoro


_KOKORO_LANGS = {"fr": ("ff_siwis", "fr-fr"), "es": ("ef_dora", "es"), "it": ("if_sara", "it"), "pt": ("pf_dora", "pt-br")}


def _kokoro_default_voice() -> str:
    try:
        from engine.core import settings_store
        return str(settings_store.get("kokoro_voice") or "").strip() or "af_heart"
    except Exception:
        return "af_heart"


def _kokoro_voice_lang(text: str):
    """(voice, language) Kokoro should use for this text, or None when it has no good voice
    (Arabic, German, unknown scripts...) and the next voice in the chain should take over."""
    try:
        from engine.voice import tts_lang
        lang = tts_lang.detect(text)
    except Exception:
        lang = ""
    if re.search(r"\b(?:ich|nicht|und|der|die|das|ist|wie|geht|dir|mir|wir|auch|nein|danke)\b", text.lower()) and \
            len(re.findall(r"\b(?:ich|nicht|und|der|die|das|ist|wie|geht|dir|mir|wir|auch|nein|danke)\b", text.lower())) >= 2:
        return None  # German: no Kokoro voice (and tts_lang can mistake it for Spanish)
    if lang in _KOKORO_LANGS:
        return _KOKORO_LANGS[lang]
    if lang == "en" or (lang == "" and text.isascii()):
        return (_kokoro_default_voice(), "en-us")
    return None


def _synthesize_kokoro(text: str, force=None):
    """Offline neural voice (Kokoro, much closer to Aria than Piper). English text only.
    Needs: pip install kokoro-onnx, plus backend/voices/kokoro-v1.0.onnx and voices-v1.0.bin.
    Returns a wav path, or None if unavailable."""
    try:
        text = _tts_clean(text)
        if not text or not _kokoro_allowed():  # too slow on this PC: next voice
            return None
        vl = force or _kokoro_voice_lang(text)
        if vl is None:  # a language Kokoro has no voice for
            return None
        name, klang = vl
        k = _get_kokoro()
        if k is None:
            return None
        speed = 1.0   # speech speed is always Normal
        t0 = time.time()
        samples, rate = k.create(text, voice=name, speed=speed, lang=klang)
        import soundfile as sf
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        sf.write(path, samples, rate)
        global _kokoro_slow
        took, audio_s = time.time() - t0, len(samples) / float(rate)
        _kokoro_slow = _kokoro_slow + 1 if took > audio_s * 1.0 and audio_s > 1.5 else 0
        _vlog(f"kokoro ok ({name}) {len(text)} chars in {took:.1f}s for {audio_s:.1f}s of audio"
              + (" - TOO SLOW, switching to Piper" if _kokoro_slow >= 2 else ""))
        return path
    except Exception as e:
        _vlog(f"kokoro FAILED {e!r}")
        return None


def _preload_kokoro() -> None:
    """Load the offline model in the background so the first offline reply is not slow. Not done at
    start-up in light mode (see _light_mode); only when Microsoft's voice turns out to be unavailable."""
    try:
        if _kokoro_model_paths() and _kokoro_allowed():
            from engine.voice import polite_load
            with polite_load.gentle():        # background: one model load at a time, quiet PC, below-normal priority
                _get_kokoro()
    except Exception as e:
        _vlog(f"kokoro preload failed {e!r}")


_END = object()


def _split_chunks(text: str, min_len: int = 28, max_len: int = 70) -> list:
    """Cut a reply into pieces that are quick to generate, so the first one starts playing
    within ~2 s: sentences, then ; : and commas for long ones. Tiny pieces are glued on."""
    pieces = []
    for sent in re.split(r"(?<=[.!?;:])\s+", text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) > max_len:
            parts = re.split(r"(?<=,)\s+", sent)
            cur = ""
            for part in parts:
                cand = (cur + " " + part).strip() if cur else part
                if cur and len(cand) > max_len:
                    pieces.append(cur)
                    cur = part
                else:
                    cur = cand
            if cur:
                pieces.append(cur)
        else:
            pieces.append(sent)
    out, cur = [], ""
    for p in pieces:
        cur = (cur + " " + p).strip() if cur else p
        if len(cur) >= min_len:
            out.append(cur)
            cur = ""
    if cur:
        if out and len(cur) < min_len:
            out[-1] += " " + cur
        else:
            out.append(cur)
    return out


def _speak_kokoro_stream(text: str) -> bool:
    """Speak a long reply sentence by sentence: the first sentence starts playing as soon as it
    is ready while the rest is still being generated. True if it handled the whole reply."""
    text = _tts_clean(text)
    if not text or not _kokoro_allowed() or _get_kokoro() is None:
        return False
    vl = _kokoro_voice_lang(text)
    if vl is None:
        return False
    chunks = _split_chunks(text)
    if len(chunks) < 2:
        return False
    _vlog(f"kokoro stream: {len(chunks)} chunks")
    from engine.core import turn_control
    _gen = turn_control.speech_gen()
    q: "queue.Queue" = queue.Queue()

    def worker():
        for c in chunks:
            q.put(_synthesize_kokoro(c, vl))
        q.put(_END)

    threading.Thread(target=worker, name="lyra-kokoro-stream", daemon=True).start()
    ducked = []
    started = False
    try:
        _ensure_mixer()
        ducked = _duck_spotify()
        while True:
            path = q.get()
            if path is _END:
                break
            if path is None:
                continue
            if turn_control.speech_stopped_since(_gen) or turn_control.turn_cancelled():
                try:
                    os.remove(path)          # stopped: the rest of the reply is not said
                except OSError:
                    pass
                continue
            try:
                pygame.mixer.music.load(path)
                if not started:
                    ui.set_state("speaking")
                    perf.mark("tts_first_audio")
                    started = True
                pygame.mixer.music.play()
                _start_wave(path)
                while pygame.mixer.music.get_busy():
                    time.sleep(0.03)
            finally:
                try:
                    pygame.mixer.music.unload()
                except Exception:
                    pass
                try:
                    os.remove(path)
                except OSError:
                    pass
        return True
    except Exception as e:
        log.error("streamed speech failed: %s", e)
        return True  # already partly spoken: do not repeat it
    finally:
        _restore_spotify(ducked)


def _synthesize_piper(text: str):
    """Offline neural voice (Piper). Returns a wav path, or None if Piper/model is missing or the offline
    voice was not chosen."""
    if not _offline_chosen():
        return None
    model = _piper_model()
    if not model:
        return None
    text = _tts_clean(text)
    import subprocess
    import sys
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        base = [sys.executable, "-m", "piper", "-m", model, "-f", path]
        # More varied rhythm than Piper's defaults (0.667 / 0.8), which sound like reading
        # word by word. Tunable in settings: piper_noise, piper_noise_w, piper_length.
        def _num(key, default):
            try:
                from engine.core import settings_store
                v = settings_store.get(key)
                return float(v) if v not in (None, "") else default
            except Exception:
                return default
        tuned = base + ["--noise-scale", str(_num("piper_noise", 0.85)),
                        "--noise-w-scale", str(_num("piper_noise_w", 1.0)),
                        "--length-scale", str(_num("piper_length", 1.0)),
                        "--sentence-silence", "0.15"]
        r = subprocess.run(tuned, input=text, capture_output=True, text=True, encoding="utf-8",
                           timeout=60, creationflags=0x08000000)
        if r.returncode != 0:  # this Piper build does not know those options: plain call
            _vlog(f"piper tuned call failed rc={r.returncode} err={r.stderr.strip()[:160]!r}; retrying plain")
            r = subprocess.run(base, input=text, capture_output=True, text=True, encoding="utf-8",
                               timeout=60, creationflags=0x08000000)
        if r.returncode == 0 and os.path.getsize(path) > 1000:
            _vlog(f"piper voice ok ({os.path.basename(model)})")
            return path
        _vlog(f"piper failed rc={r.returncode} err={r.stderr.strip()[:200]!r}")
    except Exception as e:
        _vlog(f"piper FAILED {e!r}")
    try:
        os.remove(path)
    except OSError:
        pass
    return None


def _edge_with_deadline(text: str, secs: float) -> str:
    """edge-tts, but never longer than `secs`: while Microsoft refuses the connection a failing
    attempt (handshake, version lookup, retry) used to hold every first reply for ~8 s."""
    box: dict = {}

    def run():
        try:
            box["path"] = _synthesize_to_file(text)
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=run, name="lyra-edge-tts", daemon=True)
    t.start()
    t.join(secs)
    if "path" in box:
        return box["path"]
    if "err" in box:
        raise box["err"]
    raise TimeoutError("edge-tts did not answer in time")


def _edge_probe() -> None:
    """At startup: find out once, in the background, whether the online voice works, so the first
    reply does not pay for the answer."""
    global _edge_down_until
    try:
        path = _edge_with_deadline("Hi.", 5.0)
        try:
            os.remove(path)
        except OSError:
            pass
        _vlog("edge-tts probe ok - online voice available")
    except Exception as e:  # noqa: BLE001
        _edge_down_until = time.monotonic() + 60
        _vlog(f"edge-tts probe failed ({str(e)[:90]!r}); the Windows voice will be used until it is back")


# Microsoft's voice (edge-tts) is the main one. The offline Kokoro model (hundreds of MB, heavy on CPU)
# is loaded only when that voice fails, unless light mode is switched off (then it is preloaded as before).
if _offline_chosen():
    threading.Thread(target=_preload_kokoro, name="lyra-kokoro-preload", daemon=True).start()
threading.Thread(target=_edge_probe, name="lyra-edge-probe", daemon=True).start()


def _start_wave(audio_path: str):
    """Compute the speech loudness envelope in the background and hand it to the
    UI voice bars. Runs in a thread so it never delays the voice; the UI plays the
    envelope against `start_ms`, so arriving a little late is fine. Never raises."""
    start_ms = int(time.time() * 1000)

    def _run():
        try:
            import engine.voice.speech_wave as lyra_wave
            levels = lyra_wave.compute_envelope(audio_path)
        except Exception:
            levels = []
        try:
            ui.emit_wave(levels, 25, start_ms)
        except Exception:
            pass

    try:
        t = threading.Thread(target=_run, name="lyra-wave", daemon=True)
        t.start()
        return t
    except Exception:
        return None


_TRACE_HEAD = re.compile(r"^\s*[a-z]+(?:_[a-z]+)+:\s*\[(?:verified|executed|failed|error|ok|skipped)[^\]]*\]", re.I)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


def _speakable(text: str) -> str:
    """What is worth saying aloud: Lyra's own words, not tool traces, result listings or URLs.
    Keeps a natural-language answer that follows a search listing."""
    out: list[str] = []
    skipping = False  # inside a tool trace / numbered result listing
    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            skipping = False
            continue
        trace_head = _TRACE_HEAD.match(line)
        if trace_head:
            # Tool status prefixes belong to the UI, but the tool's natural
            # language result after the prefix is still meant to be spoken.
            line = line[trace_head.end():].lstrip()
            if not line:
                skipping = True
                continue
        if re.match(r"^\s*\d+\.\s", line):
            skipping = True
            continue
        if skipping and (raw.startswith("  ") or raw.startswith("\t") or _URL_RE.fullmatch(line.strip() or "")):
            continue
        skipping = False
        out.append(line)
    t = " ".join(out)
    t = _URL_RE.sub(" ", t)
    t = re.sub(r"\b[a-z]+(?:_[a-z]+)+:\s*\[(?:verified|executed|failed|error|ok|skipped)[^\]]*\]\s*", " ", t, flags=re.I)
    t = re.sub(r"[`*_#>|]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _vlog(msg: str) -> None:
    """Voice diagnostics: %TEMP%\\lyra-voice.log (the backend console is not always visible)."""
    try:
        with open(os.path.join(tempfile.gettempdir(), "lyra-voice.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def _speak_offline(text: str) -> None:
    """Fallback voice: Windows System.Speech via PowerShell (no extra packages)."""
    path = None
    try:
        import subprocess
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female) } catch {}; "
              f"$s.Speak((Get-Content -Raw -Encoding UTF8 -LiteralPath '{path}'))")
        from engine.core import turn_control
        p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
        turn_control.set_offline_proc(p)         # so Esc / a new message can kill it
        try:
            _out, _err = p.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill()
            _out, _err = p.communicate()
        finally:
            turn_control.set_offline_proc(None)
        _vlog(f"offline voice done rc={p.returncode} err={(_err or '').strip()[:200]!r}")
    except Exception as e:
        log.error("offline speech failed: %r", e)
        _vlog(f"offline voice FAILED {e!r}")
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def _speak_impl(text: str, recognizer=None, source=None):
    """Speak `text`. If `recognizer`/`source` are given, listen concurrently
    so the user can interrupt — the barged-in text is returned so it can be
    processed as the next command. Returns the barge-in text, or None/\"\" if
    Lyra finished without being interrupted."""
    try:
        ui.ensure_spoken_chat(text)      # subtitle for replies nobody printed
    except Exception:
        pass
    _speak_t0 = time.monotonic()
    from engine.core import turn_control
    _gen = turn_control.speech_gen()    # stop_speech() / Esc after this point: do not start playing
    text = _speakable(text) or "I don't have a response to read aloud."
    log.info("Lyra: %s", text)
    _vlog(f"speak {len(text)} chars: {text[:80]!r}")
    # NOT "speaking" yet: the island's wave must start with the sound, not with the synthesis.

    global _edge_down_until, _edge_fails
    audio_path = None
    # The offline voice is used first only when it was chosen AND its files are there; otherwise Microsoft's.
    prefer_offline = _offline_chosen() and bool(_kokoro_model_paths() or _piper_model())
    if time.monotonic() >= _edge_down_until and not prefer_offline:
        try:
            audio_path = _edge_with_deadline(text, _EDGE_DEADLINE)
            _edge_fails = 0
        except Exception as e:
            # Usually a network blip, an outdated edge-tts (pip install -U edge-tts) or a blocked
            # connection. First failure: only 20 s pause (this reply uses the offline voice, the
            # next one retries online). Repeated failures: back off up to 5 min.
            _edge_fails += 1
            _edge_down_until = time.monotonic() + min(300, 20 * (2 ** (_edge_fails - 1)))
            log.error("edge-tts synthesis failed (%d in a row, try: pip install -U edge-tts): %s",
                      _edge_fails, str(e)[:160])
            _vlog(f"edge-tts FAILED: {str(e)[:200]!r}")
    if audio_path is None and (not BARGE_IN_ENABLED or recognizer is None or source is None):
        if _speak_kokoro_stream(text):  # first sentence plays while the rest is generated
            ui.set_state("idle")
            return None
    if audio_path is None:
        audio_path = _synthesize_kokoro(text)  # best offline voice
    if audio_path is None:
        audio_path = _synthesize_piper(text)  # offline neural voice, if installed
    if audio_path is None:
        if turn_control.speech_stopped_since(_gen) or turn_control.turn_cancelled():
            ui.set_state("idle")
            return None
        ui.set_state("speaking")
        perf.mark("tts_first_audio")
        _speak_offline(text)  # last resort: the Windows built-in voice
        ui.set_state("idle")
        return None

    wave_thread = None
    spotify_ducked = []
    if turn_control.speech_stopped_since(_gen) or turn_control.turn_cancelled():
        try:
            os.remove(audio_path)
        except OSError:
            pass
        ui.set_state("idle")
        return None
    try:
        _ensure_mixer()
        pygame.mixer.music.load(audio_path)
        # Duck Spotify immediately before Lyra actually starts speaking.
        spotify_ducked = _duck_spotify()
        log.info("timing: voice audio ready after %.1fs", time.monotonic() - _speak_t0)
        ui.set_state("speaking")
        perf.mark("tts_first_audio")
        pygame.mixer.music.play()
        wave_thread = _start_wave(audio_path)

        # Do not listen to the microphone while Lyra is speaking unless the
        # optional explicit barge-in switch is enabled.  Without this guard,
        # the microphone can hear Lyra's own speakers and feed her sentence
        # back through STT as a fake user command.
        if not BARGE_IN_ENABLED or recognizer is None or source is None:
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
            return None

        original_threshold = recognizer.energy_threshold
        recognizer.energy_threshold = original_threshold * ECHO_THRESHOLD_MULTIPLIER
        time.sleep(BARGE_IN_START_DELAY)

        barge_in_text = None
        try:
            while pygame.mixer.music.get_busy():
                try:
                    audio = recognizer.listen(source, timeout=BARGE_IN_CHECK_TIMEOUT, phrase_time_limit=PHRASE_TIME_LIMIT)
                except sr.WaitTimeoutError:
                    continue
                pygame.mixer.music.stop()
                # Use faster-whisper for barge-in transcription
                status, barge_in_text = _stt_engine.transcribe_audio_data(audio)
                if status == "ok":
                    barge_in_text = _fix_heard(barge_in_text)
                    log.info("Barge-in heard: '%s'", barge_in_text)
                else:
                    barge_in_text = ""
                break
        finally:
            recognizer.energy_threshold = original_threshold

        return barge_in_text
    except Exception as e:
        log.error("TTS playback failed: %s", e)
        return None
    finally:
        _restore_spotify(spotify_ducked)
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass  # unload() needs pygame>=2.4 — harmless if it's not available
        if wave_thread is not None:
            wave_thread.join(timeout=1.0)  # let it release the file before we delete it
        try:
            os.remove(audio_path)
        except OSError:
            pass
        ui.set_state("idle")


def speak(text: str, recognizer=None, source=None, force: bool = False):
    """Speak `text` (see _speak_impl) and close the turn's timing line when the speech is over.
    `force=True` always uses the voice (Settings -> Voice -> Test voice), never a Mochi sound."""
    try:
        from engine.voice import mochi_sounds
        if not force and mochi_sounds.silences(text):
            # A short acknowledgement ("Opening Spotify, sir.") is a Mochi sound now (Settings -> Voice).
            # The reply still goes on screen: the subtitle and the Mochi sound come from that chat event.
            ui.ensure_spoken_chat(text)
            ui.set_state("idle")
            perf.finish(spoken=False)
            return None
    except Exception as e:
        log.warning("mochi sound check failed, speaking instead: %s", e)
    from engine.core import turn_control
    if turn_control.turn_cancelled():
        return None                      # Esc: this turn's answer is dropped
    perf.mark("tts_request")
    try:
        return _speak_impl(text, recognizer, source)
    finally:
        perf.finish()


# ---------------------------------------------------------------------------
# Streamed speech: say a chat answer while the model is still writing it
# ---------------------------------------------------------------------------
_SENT_END = re.compile(r"(?<=[.!?\u2026])\s+|\n+")
_LIST_MARK = re.compile(r"(?:\d{1,2}[.)]|[-*\u2022])")     # "1." or "-" alone: the start of a list line, not a sentence


def _next_cut(buf: str):
    """The first sentence end in `buf` (a regex match), ignoring the dot after a list number."""
    for m in _SENT_END.finditer(buf):
        if _LIST_MARK.fullmatch(buf[:m.start()].split("\n")[-1].strip()):
            continue
        return m
    return None


def _synth_chunk(text: str):
    """Audio file for one piece of a streamed reply: the online voice, else Kokoro, else Piper. None -> the
    caller falls back to the Windows voice. Same fallbacks and back-off as speak()."""
    global _edge_down_until, _edge_fails
    if _offline_chosen() and (_kokoro_model_paths() or _piper_model()):
        return _synthesize_kokoro(text) or _synthesize_piper(text)
    if time.monotonic() >= _edge_down_until:
        try:
            path = _edge_with_deadline(text, _EDGE_DEADLINE)
            _edge_fails = 0
            return path
        except Exception as e:  # noqa: BLE001
            _edge_fails += 1
            _edge_down_until = time.monotonic() + min(300, 20 * (2 ** (_edge_fails - 1)))
            log.error("edge-tts synthesis failed (%d in a row): %s", _edge_fails, str(e)[:160])
            _vlog(f"edge-tts FAILED (stream): {str(e)[:200]!r}")
    return _synthesize_kokoro(text) or _synthesize_piper(text)


class SpeechStream:
    """Speaks text that arrives in small pieces. feed() each piece from the model; the first finished
    sentence is synthesised and played while the rest is still being written. finish() flushes the last
    words, waits until Lyra has stopped talking and closes the turn's timing.

    Not used while barge-in listening is on (that needs the microphone during speech): see open_speech_stream."""

    FIRST_MIN = 18     # characters: the first piece is short so the voice starts early
    NEXT_MIN = 32      # later pieces are a little longer so the voice does not stutter
    MAX_RUN = 110      # a run this long without . ! ? is cut at its last comma

    def __init__(self):
        self._buf = ""
        self._held = ""                 # a finished sentence too short to say alone
        self._fed = False
        self._chunks = 0
        self._to_synth: "queue.Queue" = queue.Queue()
        self._to_play: "queue.Queue" = queue.Queue()
        self._threads: list = []
        self._closed = False
        from engine.core import turn_control
        self._gen = turn_control.speech_gen()      # stop_speech() / Esc later: nothing more is said

    # -- text in ------------------------------------------------------------
    def _stopped(self) -> bool:
        from engine.core import turn_control
        return turn_control.speech_stopped_since(self._gen) or turn_control.turn_cancelled()

    def feed(self, delta: str) -> None:
        if not delta or self._closed or self._stopped():
            return
        self._fed = True
        self._buf += delta
        while True:
            m = _next_cut(self._buf)
            if m:
                piece, self._buf = self._buf[:m.start()], self._buf[m.end():]
                self._add(piece)
                continue
            if len(self._buf) > self.MAX_RUN:
                cut = self._buf.rfind(", ", 0, self.MAX_RUN)
                if cut > 20:
                    piece, self._buf = self._buf[:cut + 1], self._buf[cut + 2:]
                    self._add(piece)
                    continue
            break

    def _add(self, piece: str, last: bool = False) -> None:
        clean = _speakable(piece)
        if not clean:
            return
        clean = (self._held + " " + clean).strip() if self._held else clean
        need = self.FIRST_MIN if self._chunks == 0 else self.NEXT_MIN
        if len(clean) < need and not last:
            self._held = clean
            return
        self._held = ""
        self._chunks += 1
        if self._chunks == 1:
            perf.mark("tts_request")
            self._start()
        self._to_synth.put(clean)

    # -- audio out ----------------------------------------------------------
    def _start(self) -> None:
        for fn, name in ((self._synth_loop, "lyra-stream-synth"), (self._play_loop, "lyra-stream-play")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def _synth_loop(self) -> None:
        while True:
            text = self._to_synth.get()
            if text is _END:
                self._to_play.put(_END)
                return
            try:
                self._to_play.put((text, _synth_chunk(text)))
            except Exception as e:  # noqa: BLE001
                log.error("stream synthesis failed: %s", e)
                self._to_play.put((text, None))

    def _play_loop(self) -> None:
        ducked = []
        first = True
        try:
            _ensure_mixer()
            while True:
                item = self._to_play.get()
                if item is _END:
                    return
                text, path = item
                if self._stopped():
                    if path:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                    continue                      # stopped: drain the queue without saying it
                if first:
                    ducked = _duck_spotify()
                    ui.set_state("speaking")
                    perf.mark("tts_first_audio")
                    first = False
                log.info("Lyra: %s", text)
                if path is None:
                    _speak_offline(text)          # last resort: the Windows built-in voice
                    continue
                try:
                    pygame.mixer.music.load(path)
                    pygame.mixer.music.play()
                    _start_wave(path)
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.03)
                finally:
                    try:
                        pygame.mixer.music.unload()
                    except Exception:
                        pass
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        except Exception as e:  # noqa: BLE001
            log.error("streamed speech failed: %s", e)
        finally:
            _restore_spotify(ducked)

    # -- end of the answer --------------------------------------------------
    def finish(self, reply: str = "", recognizer=None, source=None):
        """The model is done. Says what is left and waits for the end of the speech. If nothing was streamed
        (a local answer, an error message), the whole `reply` is spoken the normal way. Returns what speak()
        returns (barge-in text, normally nothing)."""
        if self._closed:
            return None
        self._closed = True
        if self._fed:
            self._add(self._buf, last=True)
            self._buf = ""
            if self._held:
                held, self._held = self._held, ""
                self._chunks += 1
                if self._chunks == 1:
                    perf.mark("tts_request")
                    self._start()
                self._to_synth.put(held)
        if self._chunks == 0:
            return speak(reply, recognizer, source)
        if reply:
            ui.ensure_spoken_chat(reply)
        self._to_synth.put(_END)
        try:
            for t in self._threads:
                t.join(timeout=120)
        finally:
            ui.set_state("idle")
            perf.finish()
        return None


def open_speech_stream(recognizer=None, source=None):
    """A SpeechStream for the next chat answer, or None when streaming is off (STREAM_TTS = False) or the
    microphone must stay open during speech (barge-in): the caller then speaks the finished answer."""
    try:
        from engine.core import config
        if not getattr(config, "STREAM_TTS", True):
            return None
    except Exception:
        pass
    if BARGE_IN_ENABLED and recognizer is not None and source is not None:
        return None
    return SpeechStream()


