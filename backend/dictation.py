"""Lyra — dictation.

Hold (or toggle) the dictation shortcut (dictation_hotkey.py) while any window is focused
and Lyra types what you say into it, WORD BY WORD, as real Unicode characters (no clipboard).

How it works
  * The microphone feeds the streaming recognizer (streaming_stt.py). Its partial text grows
    while you talk; every word except the last 1-2 (they can still change) is typed.
    A pause, or the end of a segment, types the held-back words too.
  * Spoken lists become real lists: "note one open the door next point close the door"
    types "1. Open the door" / "2. Close the door" (dictation_text.py).
  * When you stop, an LLM polish pass fixes punctuation, capitals and spoken self-corrections.
    The typed text is replaced ONLY if the focused window, control and caret are unchanged and
    you pressed no key / clicked nothing since. Otherwise the island shows an
    "Apply polished version" button (apply_pending(), called by the bridge).
  * Replacing is verified first: the typed text is selected, copied back and compared with what
    Lyra typed. If it is not the same text, nothing is changed.

Phase 2 additions
  * Output: "live" (default) types word by word as above; "paste" collects the words and pastes them
    once when you stop, then puts your old clipboard text back. A hands-free dictation (double-tap the
    key, tap again to finish) always pastes.
  * Esc cancels: nothing is pasted; live-typed text is removed again if the caret has not moved.
  * Your dictionary / replacements and voice snippets are applied to the words before they are typed.
  * Optional AI clean-up (Settings > Dictation styles, OFF by default; TEXT only) with a Natural or
    Polished style chosen from the app you dictate into.
  * Finished dictations go to the local history (history_store) and statistics (dictation_stats).
  * If the Phase 1 engines (stt_engines) are ready, a pasted dictation is re-transcribed from the
    recorded audio for accuracy. The audio lives in memory only and is dropped right after.

There is no voice command for this: the old "dictate" / "write ..." phrases are gone.
"""

from __future__ import annotations

import re
import sys
import threading
import time

import dbg
import ui
from config import (
    DICTATION_MAX_POLISH_CHARS, DICTATION_PAUSE_FLUSH_S, DICTATION_PENDING_WORDS,
    WAKE_CHUNK_SIZE, WAKE_SAMPLE_RATE,
)
import dictation_clean
import dictation_text

log = ui.log

_lock = threading.Lock()
_session: "_Session | None" = None
_pending: dict | None = None          # a polished version waiting for the "Apply" button
_before_record = None                 # microphone hand-over with the wake-word listener (see ptt.py)
_after_record = None
_last_text = ""                       # the last finished dictation (memory only; "paste last again")
IDLE_STOP_S = 8.0                    # any dictation with no new words for this long ends by itself (stuck key, forgotten mic)
HANDSFREE_IDLE_S = 8.0                # hands-free listening stops by itself after this much silence
MAX_AUDIO_S = 600                     # a pasted dictation keeps at most this much audio in memory

# Never auto-replace in these: Shift+Left does not select in a console, and Ctrl+C there would
# interrupt a running program instead of copying.
_NO_REPLACE_APPS = frozenset({
    "windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
    "mintty.exe", "putty.exe", "alacritty.exe", "wezterm-gui.exe",
})

def set_mic_hooks(before_record=None, after_record=None) -> None:
    """Coordinate with the wake-word listener, which must give the microphone up while we dictate."""
    global _before_record, _after_record
    _before_record, _after_record = before_record, after_record


def is_active() -> bool:
    """True while Lyra is listening for dictation (not while the polish pass runs)."""
    return _session is not None


def start() -> bool:
    """Begin dictating into the focused window. False if it cannot start right now."""
    global _session, _pending
    problem = _why_not()
    dbg.ev("dictation", f"start requested -> {'cannot start: ' + problem if problem else 'ok'}")
    if problem:
        _notify(problem, "fail", 2600)
        return False
    with _lock:
        if _session is not None:
            return False
        sess = _Session()
        _session = sess
        _pending = None               # a new dictation replaces any offer from the last one
    sess.thread = threading.Thread(target=sess.run, daemon=True, name="lyra-dictation")
    sess.thread.start()
    return True


def stop() -> None:
    """Stop dictating. The words still in the recognizer are typed first, then the polish pass runs."""
    sess = _session
    dbg.ev("dictation", f"stop requested (session active: {sess is not None})")
    if sess is not None:
        sess.stop_evt.set()


def apply_pending() -> tuple[bool, str]:
    """The "Apply polished version" button: go back to the window and replace the dictated text.
    Returns (ok, message for the island). The text is verified before anything changes."""
    global _pending
    with _lock:
        offer, _pending = _pending, None
    if not offer or time.time() - offer["ts"] > 900:
        return False, "Nothing to apply"
    import computer.keyboard as kb
    if not kb.focus_window(offer["hwnd"]):
        return False, "That window is gone"
    time.sleep(0.2)
    if _replace(offer["raw"], offer["polished"]):
        return True, "Polished version applied"
    with _lock:
        _pending = offer              # keep it: the user can move the cursor and try again
    return False, "Put the cursor at the end of the dictated text, then try again"


def dismiss_pending() -> None:
    """The X on the island's Apply toast: forget the waiting offer (nothing is changed in any window)."""
    global _pending
    with _lock:
        _pending = None


def cancel() -> None:
    """Esc: stop listening and throw the dictation away (nothing is pasted or saved)."""
    sess = _session
    dbg.ev("dictation", f"cancel requested (session active: {sess is not None})")
    if sess is not None:
        sess.cancelled = True
        sess.stop_evt.set()


def set_handsfree() -> None:
    """The key was double-tapped: keep listening after it is released; the next tap pastes.
    Words typed so far stay where they are; from now on the rest is pasted at the end."""
    sess = _session
    if sess is not None:
        sess.handsfree = True
        if not sess.typed:
            sess.output = "paste"
        _pill("handsfree")


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _pill(state: str) -> None:
    """Tell the island/pill what dictation is doing and where the pill should sit (Settings > Dictation)."""
    try:
        import ui_bridge
        ui_bridge._push_event({"type": "dictation_pill", "state": state,
                               "position": dictation_clean.pref("p2_pill_position"),
                               "ts": int(time.time() * 1000)})
    except Exception:
        pass


def _app_of(snap: dict | None) -> str:
    """"winword" for the window in `snap` (focus_snapshot), "" if unknown."""
    try:
        import computer.keyboard as kb
        return dictation_clean.app_key(kb.process_name(snap["pid"])) if snap else ""
    except Exception:
        return ""


def _phrase_map() -> "dictation_text.PhraseMap":
    """Dictionary replacements + voice snippets (a snippet wins over a replacement of the same words)."""
    entries: dict[str, str] = {}
    try:
        import dictionary_store
        entries.update(dictionary_store.phrase_entries())
    except Exception as e:
        log.debug("dictation: dictionary unavailable: %s", e)
    if dictation_clean.pref("p2_snippets_in_dictation"):
        try:
            import dictionary_store
            entries.update(dictionary_store.snippets_module().trigger_map())
        except Exception as e:
            log.debug("dictation: snippets unavailable: %s", e)
    return dictation_text.PhraseMap(entries)


def _hints() -> list[str]:
    try:
        import dictionary_store
        return dictionary_store.hints()
    except Exception:
        return []


def _record(text: str, app: str, engine: str, language: str, seconds: float) -> None:
    """A dictation is finished: remember it (memory, local history) and count it (statistics)."""
    global _last_text
    text = (text or "").strip()
    if not text:
        return
    _last_text = text
    try:
        import history_store
        history_store.add(text, app, engine, language, seconds)
    except Exception as e:
        log.debug("dictation: history not saved: %s", e)
    try:
        import dictation_stats                     # Phase 3 (C3); absent until merged
        dictation_stats.record(len(text.split()), seconds, app, engine, language)
    except ImportError:
        pass
    except Exception as e:
        log.debug("dictation: stats not recorded: %s", e)
def _notify(title: str, phase: str, ms: int) -> None:
    try:
        import ui_bridge
        ui_bridge.push_activity("dictation", title, phase=phase, duration_ms=ms)
    except Exception:
        pass


def _why_not() -> str:
    """Why dictation cannot start now ("" = it can)."""
    if sys.platform != "win32":
        return "Dictation needs Windows"
    try:
        import ptt
        if getattr(ptt, "_holding", False):
            return "Finish the voice command first"
    except Exception:
        pass
    import streaming_stt
    if not streaming_stt.loaded():
        if not streaming_stt.available():
            return "Dictation needs sherpa-onnx (pip install sherpa-onnx)"
        return "Dictation model is still loading"
    return ""


def _early_on() -> bool:
    """Decode each finished sentence while you are still talking (faster end, but more CPU while speaking)."""
    try:
        import settings_store
        return bool(settings_store.get("dictation_refine_early", False))
    except Exception:
        return False


def _refine_on() -> bool:
    """Live typing is re-checked with the accurate engine (Parakeet / Whisper) when you stop."""
    try:
        import settings_store
        return bool(settings_store.get("dictation_refine", True))
    except Exception:
        return True


def _llm_polish(raw: str, style: str = "natural") -> str | None:
    """AI clean-up of typed text. Only when the user switched AI clean-up on (Phase 2); text only."""
    return dictation_clean.ai_cleanup(raw, style)


def _replace(raw: str, new: str) -> bool:
    """Replace the `raw` text just before the caret with `new`. Checks first that the text
    before the caret really is `raw`; if not, the caret is put back and nothing is changed."""
    import computer.keyboard as kb
    kb.release_modifiers()
    if not kb.select_back(len(raw)):
        dbg.ev("dictation", "replace: could not select the typed text")
        return False
    time.sleep(0.05)
    got = kb.read_selection()
    if got is None or re.sub(r"\r\n|\r", "\n", got) != raw:
        dbg.ev("dictation", f"replace: selected text did not match what Lyra typed (got {None if got is None else got[:40]!r})")
        kb.collapse_selection_right()
        return False
    ok = kb.send_unicode(new)
    dbg.ev("dictation", f"replace: typed the corrected text -> {ok}")
    return ok       # typing over the selection replaces it


def _erase(raw: str) -> bool:
    """Delete the `raw` text just before the caret, after checking it really is `raw`."""
    import computer.keyboard as kb
    kb.release_modifiers()
    if not kb.select_back(len(raw)):
        return False
    time.sleep(0.05)
    got = kb.read_selection()
    if got is None or re.sub(r"\r\n|\r", "\n", got) != raw:
        kb.collapse_selection_right()
        return False
    return kb._send(kb._tap(0x08))                  # Backspace removes the selection


def _offer(raw: str, polished: str, hwnd: int) -> None:
    """Show the "Apply polished version" button on the island."""
    global _pending
    with _lock:
        _pending = {"raw": raw, "polished": polished, "hwnd": hwnd, "ts": time.time()}
    try:
        import ui_bridge
        ui_bridge.push_dictation_polish()
    except Exception as e:
        log.warning("dictation: could not show the apply button: %s", e)


class _Session:
    def __init__(self) -> None:
        self.output = "paste"             # always: the words are pasted once, when you let go
        self.handsfree = False
        self.cancelled = False
        self.buffer: list[str] = []        # paste mode: the words collected so far
        self.audio: list[bytes] = []       # paste mode: raw audio, memory only, for the accurate engine pass
        self.audio_bytes = 0
        self.seg_chunks: list[bytes] = []  # audio of the sentence being spoken now (for the early engine pass)
        self.seg_jobs: list = []           # futures: each finished sentence is decoded while you keep talking
        self._pool = None
        self.t0 = time.monotonic()
        self.final_text = ""
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.typed: list[str] = []         # everything typed, in order
        self.target: dict | None = None    # window that got the first word
        self.polish_ok = True              # False once the text may be split across windows / carets
        self.input_mark = 0                # user input count right after the last typing
        self.end_snap: dict | None = None  # focus state right after the last word
        self.end_input = 0

    # -- one dictation: listen + type, then polish ------------------------------------------
    def run(self) -> None:
        global _session
        hook_paused = False
        try:
            ui.set_state("listening")
            _pill("listening")
            try:
                import status_watch
                status_watch.set_mic_active(True)
            except Exception:
                pass
            if _before_record:
                try:
                    _before_record()
                    hook_paused = True
                except Exception as e:
                    log.warning("Could not pause the wake-word microphone: %s", e)
            self._silence_lyra()
            _notify("Dictating\u2026", "start", 1800)
            self._listen_and_type()
        except Exception as e:
            log.error("Dictation failed: %s", e)
            _notify("Dictation stopped", "fail", 2200)
        finally:
            with _lock:
                _session = None
            try:
                import status_watch
                status_watch.set_mic_active(False)
            except Exception:
                pass
            if hook_paused and _after_record:
                try:
                    _after_record()
                except Exception as e:
                    log.warning("Could not resume the wake-word microphone: %s", e)
            if getattr(ui, "_last_state", "idle") in ("listening", "thinking"):
                ui.set_state("idle")
        try:
            self._finish()
        except Exception as e:
            log.error("Dictation finish failed: %s", e)
        finally:
            self.audio.clear()                              # audio never outlives the dictation
            self.seg_chunks.clear()
            self.seg_jobs.clear()
            self.buffer.clear()
            if self._pool is not None:
                self._pool.shutdown(wait=False)
            _pill("idle")

    @staticmethod
    def _silence_lyra() -> None:
        try:
            import voice
            voice.pygame.mixer.music.stop()      # do not transcribe her own voice
        except Exception:
            pass

    def _listen_and_type(self) -> None:
        import pyaudio
        import streaming_stt
        rec = streaming_stt.new_session()
        if rec is None:
            raise RuntimeError("streaming model not ready")
        words = dictation_text.WordStream(DICTATION_PENDING_WORDS, _phrase_map())
        pa = pyaudio.PyAudio()
        stream = None
        try:
            kwargs = {}
            try:
                import mic_devices
                idx = mic_devices.preferred_input_index(pa)      # Settings > Voice > microphone
                if idx is not None:
                    kwargs["input_device_index"] = idx
            except Exception:
                pass
            stream = pa.open(format=pyaudio.paInt16, channels=1, rate=WAKE_SAMPLE_RATE, input=True,
                             frames_per_buffer=WAKE_CHUNK_SIZE, **kwargs)
            last_text, changed, flushed = "", time.monotonic(), False
            dbg_t0 = dbg_t = time.monotonic()
            dbg.ev("dictation", f"microphone open; output={self.output}, refine={_refine_on()}")
            while not self.stop_evt.is_set():
                try:
                    data = stream.read(WAKE_CHUNK_SIZE, exception_on_overflow=False)
                except Exception:
                    break
                if (self.output == "paste" or _refine_on()) and self.audio_bytes < MAX_AUDIO_S * WAKE_SAMPLE_RATE * 2:
                    self.audio.append(data)                 # RAM only; dropped when the dictation ends
                    self.audio_bytes += len(data)
                    self.seg_chunks.append(data)
                text, endpoint = rec.feed(data)
                now = time.monotonic()
                if now - dbg_t >= 1.0:
                    dbg_t = now
                    dbg.ev("dictation", f"listening {now - dbg_t0:.0f}s: audio={self.audio_bytes / (WAKE_SAMPLE_RATE * 2):.1f}s, "
                                        f"live text={len(text)} chars, no new words for {now - changed:.1f}s")
                if text != last_text:
                    last_text, changed, flushed = text, now, False
                if now - changed > (HANDSFREE_IDLE_S if self.handsfree else IDLE_STOP_S):
                    dbg.ev("dictation", "idle timeout: no new words, stopping by itself")
                    self.stop_evt.set()                     # never listen forever, even if a key release was missed
                if endpoint:                                # a pause long enough to close the segment
                    dbg.ev("dictation", f"pause detected: sentence closed ({len(text)} chars)")
                    self._submit_segment(bool(text))
                    self._emit(words.update(text, flush=True))
                    words.end_segment()
                    rec.reset()
                    last_text, flushed = "", False
                elif text and not flushed and now - changed >= DICTATION_PAUSE_FLUSH_S:
                    self._emit(words.update(text, flush=True))   # paused: nothing more is coming soon
                    flushed = True
                else:
                    self._emit(words.update(text))
            dbg.ev("dictation", f"stop signal received after {time.monotonic() - dbg_t0:.1f}s (cancelled={self.cancelled})")
            # Stopped: type what is still in the recognizer, including the held-back words.
            if not self.cancelled:
                tail = rec.finish()
                self._submit_segment(bool(tail) or bool(last_text))
                self._emit(words.update(tail, flush=True))
                self._emit(words.finish())
        finally:
            if stream is not None:
                for fn in ("stop_stream", "close"):
                    try:
                        getattr(stream, fn)()
                    except Exception:
                        pass
            pa.terminate()
        time.sleep(0.15)                                    # let the app draw its caret
        import computer.keyboard as kb
        import dictation_hotkey
        self.end_snap, self.end_input = kb.focus_snapshot(), dictation_hotkey.input_count()

    def _emit(self, text: str) -> None:
        """Live mode types the words now; paste mode collects them for the end."""
        if not text:
            return
        if self.cancelled and self.output == "paste":
            return
        if self.output == "paste":
            self.buffer.append(text)
        else:
            self._type(text)

    def _type(self, text: str) -> None:
        if not text:
            return
        import computer.keyboard as kb
        import dictation_hotkey
        snap = kb.focus_snapshot()
        if self.target is None:
            self.target = snap
        elif snap and self.target and snap["hwnd"] != self.target["hwnd"]:
            self.polish_ok = False                          # the words are now split across windows
        if self.typed and dictation_hotkey.input_count() != self.input_mark:
            self.polish_ok = False                          # the user typed / clicked in between
        kb.release_modifiers()                              # a held Ctrl / Alt must not turn text into shortcuts
        if not kb.send_unicode(text):
            raise RuntimeError("could not type into the window")
        self.typed.append(text)
        self.input_mark = dictation_hotkey.input_count()

    # -- after the last word -------------------------------------------------------------
    def _finish(self) -> None:
        """Cancel / paste / polish, then remember the dictation."""
        seconds = max(0.0, time.monotonic() - self.t0)
        if self.cancelled:
            self._cancel_cleanup()
            _notify("Dictation cancelled", "fail", 1400)
            _pill("cancelled")
            return
        _pill("processing")
        if self.output == "paste":
            self._finish_paste(seconds)
            return
        raw = "".join(self.typed)
        refined = self._refine_live(raw)
        if refined:
            raw = refined                                   # this is now the text in the window
        self.final_text = self._polish(raw) or raw
        if self.final_text.strip():
            _record(self.final_text, _app_of(self.target), "refined" if refined else "streaming", "en", seconds)
        _pill("done")

    @staticmethod
    def _decode(pcm_bytes: bytes) -> dict | None:
        """One accurate-engine decode of int16 16 kHz audio; None when the engine is not available."""
        try:
            import numpy as np
            import stt_engines                              # Phase 1 (C1); absent until merged
            if not stt_engines.status().get("ready"):
                return None
            pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            t0 = time.monotonic()
            res = stt_engines.transcribe(pcm, language=None, hints=_hints())
            dbg.ev("stt", f"decode {len(pcm) / 16000:.1f}s of audio in {time.monotonic() - t0:.1f}s -> engine={res.get('engine')}, "
                          f"language={res.get('language')!r}, route={res.get('route')!r}, "
                          f"fallback={res.get('fallback')!r}, text={str(res.get('text'))[:80]!r}")
            return res
        except ImportError:
            return None
        except Exception as e:
            log.info("Dictation: accurate engine pass skipped (%s) - using the live text", e)
            return None

    def _submit_segment(self, spoke: bool) -> None:
        """A sentence just ended: decode it in the background so little is left to do at the end."""
        chunks, self.seg_chunks = self.seg_chunks, []
        if (not spoke or not chunks or self.cancelled or not _early_on()
                or not (self.output == "paste" or _refine_on())):
            return
        pcm = b"".join(chunks)
        if len(pcm) < 0.2 * WAKE_SAMPLE_RATE * 2:           # under 0.2 s: nothing worth decoding
            return
        try:
            if self._pool is None:
                from concurrent.futures import ThreadPoolExecutor
                self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lyra-dict")
            self.seg_jobs.append(self._pool.submit(self._decode, pcm))
        except Exception as e:
            log.info("Dictation: could not start the early engine pass (%s)", e)

    def _engine_text(self) -> tuple[str, str, str] | None:
        """(text, engine, language) from the accurate engines when they are ready, else None.
        Sentences already decoded during the dictation are reused; if any of them failed, the whole
        audio is decoded once instead."""
        if not self.audio:
            return None
        if self.seg_jobs:
            parts, eng, lang, ok = [], "", "", True
            for f in self.seg_jobs:
                try:
                    r = f.result(timeout=60)
                except Exception:
                    r = None
                if not r:
                    ok = False
                    break
                t = str(r.get("text") or "").strip()
                if t:
                    parts.append(t)
                    eng, lang = eng or str(r.get("engine") or "engine"), lang or str(r.get("language") or "")
            if ok and parts:
                return " ".join(parts), eng, lang
        res = self._decode(b"".join(self.audio))
        text = str((res or {}).get("text") or "").strip()
        if not text:
            return None
        return text, str(res.get("engine") or "engine"), str(res.get("language") or "")

    def _finish_paste(self, seconds: float) -> None:
        import computer.keyboard as kb
        text, engine, language = "".join(self.buffer).strip(), "streaming", "en"
        better = self._engine_text()
        dbg.ev("dictation", f"paste mode: live text={text[:60]!r} | accurate engine={(better[0][:60] if better else None)!r}")
        if better:
            text, engine, language = better
            text = dictation_text.apply_phrases(text, _phrase_map())
        if not text:
            _notify("Nothing heard", "fail", 1600)
            return
        app = _app_of(kb.focus_snapshot())                  # style follows the window you paste into
        text, how = dictation_clean.cleanup(text, app)
        if not text:
            return
        kb.release_modifiers()
        time.sleep(0.05)
        dbg.ev("dictation", f"pasting {len(text)} chars into {app!r}")
        if kb.paste_text(text, restore=True):               # your clipboard text is put back afterwards
            _notify("Dictation pasted", "success", 1600)
            self.final_text = text
            _record(text, app, engine, language, seconds)
            _pill("done")
        else:
            _notify("Could not paste the dictation", "fail", 2200)

    def _cancel_cleanup(self) -> None:
        """Esc in live mode: take the typed words out again, but only if that is provably safe."""
        raw = "".join(self.typed)
        if not raw or not self.polish_ok or not self.target:
            return
        import computer.keyboard as kb
        import dictation_hotkey
        if kb.process_name(self.target["pid"]) in _NO_REPLACE_APPS or not self._unchanged(kb, dictation_hotkey):
            return
        if _erase(raw):
            log.info("Dictation cancelled: the typed text was removed")
        else:
            log.info("Dictation cancelled: the typed text stays (could not verify it)")

    def _refine_live(self, raw: str) -> str | None:
        """After the last word: re-listen to the audio with the accurate engine and swap the fast
        live guess for it. Same safety rules as polish: only if the caret and window are untouched
        and the text before the caret is exactly what Lyra typed; otherwise offer the button."""
        import computer.keyboard as kb
        import dictation_hotkey
        if (not _refine_on() or not self.polish_ok or not self.target or not self.audio
                or not raw.strip()):
            return None
        if kb.process_name(self.target["pid"]) in _NO_REPLACE_APPS:
            return None
        better = self._engine_text()
        dbg.ev("dictation", f"refine: live text={raw.strip()[:60]!r} | accurate engine text={(better[0][:60] if better else None)!r}")
        if not better:
            return None
        style = dictation_clean.style_for_app(_app_of(self.target))
        text = dictation_text.apply_phrases(better[0], _phrase_map())
        try:
            text = dictation_clean.rule_cleanup(text, style) or text
        except Exception:
            pass
        text = text.strip()
        if not text or text == raw.strip() or len(text) > len(raw) * 3 + 40:
            return None
        text += raw[len(raw.rstrip()):]                     # keep the trailing space Lyra typed
        same = self._unchanged(kb, dictation_hotkey)
        dbg.ev("dictation", f"refine: window/keys unchanged since last word = {same}")
        if same:
            if _replace(raw, text):
                time.sleep(0.1)
                self.end_snap, self.end_input = kb.focus_snapshot(), dictation_hotkey.input_count()
                _notify("Dictation corrected", "success", 1600)
                return text
            log.info("Dictation: the text before the caret was not what Lyra typed - correction skipped")
        else:
            log.info("Dictation: you moved on - correction skipped")
        return None                                         # never nag: no button for the automatic correction

    def _polish(self, raw: str) -> str | None:
        """Optional AI clean-up of the live-typed text. Returns the polished text if it replaced the typed
        text, else None. Off by default (Settings > Dictation styles); sends TEXT only."""
        import computer.keyboard as kb
        import dictation_hotkey
        if (not dictation_clean.ai_enabled() or not self.polish_ok or not self.target
                or len(raw.split()) < 2 or len(raw) > DICTATION_MAX_POLISH_CHARS):
            return None
        if kb.process_name(self.target["pid"]) in _NO_REPLACE_APPS:
            return None
        style = dictation_clean.style_for_app(_app_of(self.target))
        polished = dictation_text.accept_polish(raw, _llm_polish(raw, style))
        if not polished:
            return None
        if self._unchanged(kb, dictation_hotkey):
            if _replace(raw, polished):
                _notify(f"Dictation polished ({style})", "success", 1800)
                return polished
            log.info("Dictation: the text before the caret was not what Lyra typed - offering the button instead")
        _offer(raw, polished, self.target["hwnd"])
        return None

    def _unchanged(self, kb, hotkey) -> bool:
        """Same window, same control, same caret, and no key or click since the last word."""
        now = kb.focus_snapshot()
        before = self.end_snap
        if not (now and before):
            log.info("Dictation: correction check - no focus snapshot")
        else:
            why = [n for n, bad in (
                ("mouse hook off", not hotkey.mouse_hook_ok()),
                ("window changed", now["hwnd"] != before["hwnd"]),
                ("focused control changed", now["focus"] != before["focus"]),
                ("key/click since last word", hotkey.input_count() != self.end_input)) if bad]
            if why:
                log.info("Dictation: correction check failed: %s", ", ".join(why))
        return bool(
            now and before and hotkey.mouse_hook_ok()
            and now["hwnd"] == before["hwnd"] and now["focus"] == before["focus"]
            # caret position is NOT compared: apps move/blink the caret rectangle by themselves, and
            # _replace() verifies the text before the caret anyway before changing anything.
            and hotkey.input_count() == self.end_input
        )
