"""Lyra — the built-in screen recorder (Settings -> Recordings -> Recorder: "Built-in", experimental).

Screen pictures come from Windows Graphics Capture (the same Windows API OBS uses by default) through the
`windows-capture` package (pip install windows-capture). Frames go to ffmpeg, which encodes them (the NVIDIA encoder when it
works, else x264) into a crash-safe Matroska work file; the sound (record_audio.py: PC sound and/or microphone) is mixed in
and the MP4 is made when the recording stops. Pause and resume are free here: the writer simply stops sending frames.

How a frame travels: Windows Graphics Capture only delivers a picture when something on screen changes, so a pacing thread
sends the latest picture to ffmpeg exactly `fps` times a second (repeating it when nothing changed). That keeps the video
at a constant frame rate that lines up with the sound. Every frame passes through Python, which makes this recorder
heavier than OBS: Settings -> Recordings -> "Test built-in recorder" measures what it costs on this PC.

Nothing runs while nobody records: no thread, no timer, nothing imported.
"""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import engine.features.screen_record as sr


# looked up at call time (not imported by name) so that a changed folder / setting / test stand-in in screen_record is seen
def _ffmpeg(): return sr._ffmpeg()
def _log(): return sr._log()
def _pref(key, default=None): return sr._pref(key, default)
def out_dir(): return sr.out_dir()

LABEL = "Windows Graphics Capture (built-in)"
_CRASH_SAFE = ["-flush_packets", "1", "-cluster_time_limit", "1000"]      # a killed ffmpeg loses at most the last second

_lock = threading.RLock()
_S = None                    # the running (or just stopped) session
_nvenc_cache: dict = {}
FIRST_FRAME_TIMEOUT = 6.0     # seconds Windows may take to deliver the first picture


def _tmp_dir() -> Path:
    d = out_dir() / ".tmp"
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(d), 0x2)        # hidden
        except Exception:  # noqa: BLE001
            pass
    return d


def _open_sound(mode: str, stem: str):
    """Start listening for the saved sound choice. -> (AudioCapture or None, "PC sound + microphone", why-not text)."""
    try:
        from engine.features import record_audio
        want_sys, want_mic = record_audio.wants(mode)
        if mode == "off" or not (want_sys or want_mic):
            return None, "", ""
        mic_name = ""
        try:
            mic_name = str(_pref("audio_input", "") or "")
        except Exception:  # noqa: BLE001
            pass
        cap = record_audio.AudioCapture(want_sys, want_mic, _tmp_dir(), stem, mic_name=mic_name)
        ok = cap.start()
        warn = "; ".join(cap.warnings)
        if warn:
            _log().warning("recording sound: %s", warn)
        return (cap if ok else None), (" + ".join(cap.labels) if ok else ""), warn
    except Exception as e:  # noqa: BLE001 - sound must never stop the picture from being recorded
        _log().warning("recording sound failed: %s", e)
        return None, "", f"sound could not start ({str(e)[:80]})"


def _stop_sound(cap, timeout: float = 8.0) -> list:
    """cap.stop() with a watchdog: a stuck audio driver must never keep the picture from being saved."""
    box: dict = {}

    def run() -> None:
        try:
            box["tracks"] = cap.stop()
        except Exception as e:  # noqa: BLE001
            box["err"] = str(e)[:100]

    th = threading.Thread(target=run, name="lyra-record-sound-stop", daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        _log().warning("recording sound: the audio driver did not stop in %ss, saving the picture without sound", timeout)
        return []
    if "err" in box:
        _log().warning("recording sound: stop failed: %s", box["err"])
        return []
    return box.get("tracks") or []


def _nvenc_preset(exe: str) -> str | None:
    """The NVENC preset that works on this PC (a 5-frame test encode, remembered), None = use the CPU encoder."""
    if exe in _nvenc_cache:
        return _nvenc_cache[exe]
    found = None
    for preset in ("p3", "fast"):
        try:
            r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30",
                                "-frames:v", "5", "-c:v", "h264_nvenc", "-preset", preset, "-f", "null", "-"],
                               capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                found = preset
                break
        except Exception:  # noqa: BLE001
            break
    _nvenc_cache[exe] = found
    return found


def available() -> tuple[bool, str]:
    """Can the built-in recorder run here? -> (yes/no, why not as a sentence)."""
    if sys.platform != "win32":
        return False, "The built-in recorder only works on Windows."
    try:
        import windows_capture  # noqa: F401
    except Exception:  # noqa: BLE001
        return False, "The built-in recorder needs one more package: run  pip install windows-capture  and restart Lyra."
    if not _ffmpeg():
        return False, "ffmpeg wasn't found. Run: pip install imageio-ffmpeg (or install ffmpeg)."
    return True, ""


class _Session:
    def __init__(self, fps: int, sound: str) -> None:
        self.fps, self.sound_mode = fps, sound
        self.stem = f"Lyra-{datetime.now():%Y-%m-%d-%H%M%S}"
        self.final = str(out_dir() / f"{self.stem}.mp4")
        self.seg = str(_tmp_dir() / f"{self.stem}-seg01.mkv")
        self.exe = _ffmpeg() or "ffmpeg"
        self.bufs: list = []
        self.shape = None
        self.front = 0
        self.back = 1
        self.blk = threading.Lock()
        self.first = threading.Event()
        self.stop_evt = threading.Event()
        self.paused = False
        self.paused_at = 0.0
        self.paused_total = 0.0
        self.t0 = 0.0
        self.video_start = 0.0
        self.captured = self.written = self.dupes = self.skipped = 0
        self.proc = None
        self.writer = None
        self.control = None            # what start_free_threaded() returned
        self.inner = None              # the control object handed to the frame callback
        self.cap = None                # record_audio.AudioCapture
        self.cap_info = ""
        self.cap_warn = ""
        self.tail: deque = deque(maxlen=6)
        self.dead = ""
        self.error = ""
        self.warned_size = False
        self.how = ""
        self.lead = 0.0

    # ---- Windows Graphics Capture side ----
    def on_frame(self, frame, ctl) -> None:
        self.inner = ctl
        if self.stop_evt.is_set():
            try:
                ctl.stop()
            except Exception:  # noqa: BLE001
                pass
            return
        import numpy as np
        buf = frame.frame_buffer
        if self.shape is None:
            self.shape = tuple(buf.shape)
            self.bufs = [np.empty(self.shape, dtype=np.uint8) for _ in range(3)]
            self.first.set()
        if tuple(buf.shape) != self.shape:
            if not self.warned_size:                                   # the screen changed size mid-recording: skip such frames
                self.warned_size = True
                _log().warning("built-in recorder: the screen size changed during the recording; those frames are skipped")
            return
        i = self.back
        np.copyto(self.bufs[i], buf)
        with self.blk:
            self.front, self.back = i, (i + 1) % 3
        self.captured += 1

    # ---- the pacing thread: exactly `fps` frames a second into ffmpeg ----
    def pace(self) -> None:
        step = 1.0 / self.fps
        n, last_idx, last_cap = 0, -1, -1
        self.t0 = time.perf_counter()
        while not self.stop_evt.is_set():
            now = time.perf_counter()
            if self.paused:
                self.stop_evt.wait(0.02)
                continue
            elapsed = (now - self.t0) - self.paused_total
            behind = int(elapsed * self.fps) + 1 - n
            if behind <= 0:
                self.stop_evt.wait(max(0.001, n * step - elapsed))
                continue
            if behind > 8:                                             # the PC could not keep up: give up on the rest
                self.skipped += behind - 8
                n += behind - 8
                behind = 8
            for _ in range(behind):
                with self.blk:
                    idx = self.front
                try:
                    self.proc.stdin.write(memoryview(self.bufs[idx]).cast("B"))
                except (OSError, ValueError, AttributeError) as e:
                    self.dead = f"ffmpeg stopped taking frames ({str(e)[:60]})"
                    return
                if idx == last_idx and self.captured == last_cap:
                    self.dupes += 1
                last_idx, last_cap = idx, self.captured
                self.written += 1
                n += 1
                if self.written == 1:
                    self.video_start = time.monotonic()

    def seconds(self) -> float:
        if not self.t0:
            return 0.0
        now = time.perf_counter()
        return max(0.0, (now - self.t0) - self.paused_total - ((now - self.paused_at) if self.paused else 0.0))


def _drain(proc, tail: deque) -> None:
    try:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                tail.append(line[:200])
    except Exception:  # noqa: BLE001
        pass


def _make_capture(wc, monitor_index: int, cursor: bool):
    cls = wc.WindowsCapture
    want = {"cursor_capture": cursor, "draw_border": False, "monitor_index": monitor_index}
    try:
        params = inspect.signature(cls).parameters
        if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
            want = {k: v for k, v in want.items() if k in params}
    except (TypeError, ValueError):
        pass                                                           # a compiled class has no signature: try everything
    for kwargs in (want, {"monitor_index": monitor_index}, {}):
        try:
            return cls(**kwargs)
        except TypeError:
            continue
    raise RuntimeError("the windows-capture package has an interface Lyra doesn't know")


def _set_live(live: bool, paused: bool = False) -> None:
    s = _S
    sr._live, sr._paused = live, paused and live
    sr._secs = int(s.seconds()) if (s and live) else 0
    sr._size = _seg_size(s) if (s and live) else 0


def _seg_size(s) -> int:
    try:
        return os.path.getsize(s.seg)
    except OSError:
        return 0


def busy() -> bool:
    return bool(_S and (not _S.stop_evt.is_set() or sr._saving))


def start() -> dict:
    global _S
    with _lock:
        sr._error = ""
        if _S is not None and not _S.stop_evt.is_set():
            return status()
        ok, why = available()
        if not ok:
            sr._error = why
            return status()
        import windows_capture as wc
        fps = max(10, min(60, int(_pref("record_fps", 30) or 30)))
        s = _Session(fps, str(_pref("record_audio", "system") or "off"))
        try:
            exe = s.exe
            preset = _nvenc_preset(exe)
            # sound first (it must be listening before the first picture)
            s.cap, s.cap_info, s.cap_warn = _open_sound(s.sound_mode, s.stem)
            cap = _make_capture(wc, 1, True)

            @cap.event
            def on_frame_arrived(frame, control):
                s.on_frame(frame, control)

            @cap.event
            def on_closed():
                pass

            try:
                s.control = cap.start_free_threaded()
            except AttributeError:                                     # an older package: run the blocking start() ourselves
                threading.Thread(target=cap.start, name="lyra-wgc", daemon=True).start()
            if not s.first.wait(FIRST_FRAME_TIMEOUT):
                raise RuntimeError("no picture arrived from Windows Graphics Capture (is the screen locked, or another program holding it?)")
            h, w = s.shape[0], s.shape[1]
            s.how = f"Lyra screen recording: built-in WGC, {'NVENC ' + preset if preset else 'x264'}, {fps} fps"
            enc = (["-c:v", "h264_nvenc", "-preset", preset, "-rc", "vbr", "-cq", "23", "-b:v", "0"] if preset else
                   ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-threads", str(max(2, min(8, (os.cpu_count() or 4) // 2)))])
            cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgra", "-video_size", f"{w}x{h}",
                   "-framerate", str(fps), "-i", "pipe:0", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", *enc, *_CRASH_SAFE,
                   "-metadata", "comment=" + s.how, s.seg]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
            s.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, creationflags=flags)
            threading.Thread(target=_drain, args=(s.proc, s.tail), name="lyra-wgc-ffmpeg-log", daemon=True).start()
            s.writer = threading.Thread(target=s.pace, name="lyra-wgc-pacer", daemon=True)
            s.writer.start()
            time.sleep(0.4)
            if s.proc.poll() is not None:
                raise RuntimeError(s.tail[-1] if s.tail else "ffmpeg stopped at once")
        except Exception as e:  # noqa: BLE001
            _abort(s)
            sr._error = f"Couldn't start recording: {str(e)[:200]}"
            return status()
        _S = s
        if s.cap is not None:
            from engine.features import record_audio
            s.lead = max(0.0, s.video_start - s.cap.t0) if s.video_start else record_audio.VIDEO_START_GUESS
        _set_live(True)
    sr._push()
    return status()


def _abort(s) -> None:
    s.stop_evt.set()
    for fn in (lambda: s.control.stop(), lambda: s.inner.stop()):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
    if s.proc is not None:
        try:
            s.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            s.proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    if s.cap is not None:
        _discard(_stop_sound(s.cap))
    try:
        os.remove(s.seg)
    except OSError:
        pass


def _discard(tracks) -> None:
    for t in tracks or []:
        try:
            os.remove(t["path"])
        except OSError:
            pass


def pause() -> dict:
    s = _S
    if s is None or s.stop_evt.is_set() or s.paused:
        return status()
    s.paused_at = time.perf_counter()
    s.paused = True
    if s.cap is not None:
        try:
            s.cap.pause()
        except Exception as e:  # noqa: BLE001
            _log().warning("recording sound: pause failed: %s", e)
    _set_live(True, True)
    sr._push()
    return status()


def resume() -> dict:
    s = _S
    if s is None or s.stop_evt.is_set() or not s.paused:
        return status()
    s.paused_total += time.perf_counter() - s.paused_at
    s.paused = False
    if s.cap is not None:
        try:
            s.cap.resume(0.0)
        except Exception as e:  # noqa: BLE001
            _log().warning("recording sound: resume failed: %s", e)
    _set_live(True, False)
    sr._push()
    return status()


def stop() -> dict:
    global _S
    with _lock:
        s = _S
        if s is None or s.stop_evt.is_set():
            return status()
        secs = s.seconds()
        s.stop_evt.set()
    for fn in (lambda: s.control.stop(), lambda: s.inner.stop()):        # no more pictures
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
    if s.writer is not None:
        s.writer.join(3.0)
    try:
        s.proc.stdin.close()                                            # end of the picture: ffmpeg finishes the file
        s.proc.wait(timeout=12)
    except Exception:  # noqa: BLE001
        try:
            s.proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    tracks = _stop_sound(s.cap) if s.cap is not None else None
    sr._live = sr._paused = False
    sr._secs = sr._size = 0
    sr._saving += 1
    th = threading.Thread(target=_finish, args=(s, tracks, secs), name="lyra-wgc-finish", daemon=True)
    th.start()
    sr._push()
    th.join(timeout=8)
    return status()


def _run_low(cmd: list[str], timeout: float) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    except subprocess.TimeoutExpired:
        return False, "it took too long"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]
    if r.returncode == 0:
        return True, ""
    lines = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return False, (lines[-1][:120] if lines else f"ffmpeg exited with {r.returncode}")


def _finish(s, tracks, secs: float) -> None:
    """Background: picture (+ sound) -> the finished MP4 in Lyra's folder; whatever fails, the picture is kept."""
    from engine.features import record_audio
    result, note = "", ""
    try:
        if not (os.path.exists(s.seg) and os.path.getsize(s.seg) > 0):
            note = "The recording is empty: ffmpeg wrote no picture." + (f" ({s.tail[-1]})" if s.tail else "")
        else:
            wavs = [t["path"] for t in tracks or [] if os.path.exists(t["path"]) and os.path.getsize(t["path"]) > 44]
            dur = float(sr._probe(s.exe, Path(s.seg)).get("duration") or 0)
            out_tmp = str(_tmp_dir() / f"{s.stem}-mixed.mp4")
            tries = ([(wavs, "")] if wavs else []) + [([], "Saved without sound: " if wavs else "")]
            why = ""
            for use, prefix in tries:
                ok, w = _run_low(record_audio.build_final_cmd(s.exe, [s.seg], use, out_tmp, getattr(s, "lead", 0.0), dur,
                                                              shorten=not dur, comment=s.how), max(300.0, dur))
                if ok and os.path.exists(out_tmp) and os.path.getsize(out_tmp) > 0:
                    os.replace(out_tmp, s.final)
                    result, note = s.final, (f"{prefix}{why}"[:160] if prefix else "")
                    break
                why = w or why
            if not result:                                             # last resort: the picture as recorded (a playable .mkv)
                dest = Path(s.final).with_suffix(".mkv")
                shutil.move(s.seg, str(dest))
                result, note = str(dest), f"Saved as a .mkv file (the MP4 could not be made): {why}"[:160]
            else:
                for x in [s.seg, *wavs]:
                    try:
                        os.remove(x)
                    except OSError:
                        pass
    except Exception as e:  # noqa: BLE001
        note = f"Couldn't save the recording: {str(e)[:120]}"
    with _lock:
        if result:
            sr._last = result
        if note:
            sr._error = note
        sr._saving = max(0, sr._saving - 1)
    sr._push()


def status() -> dict:
    s = _S
    live = bool(s and not s.stop_evt.is_set())
    if live and s.dead and not sr._error:
        sr._error = f"Recording stopped: {s.dead}"
    secs = int(s.seconds()) if live else 0
    if live:
        sr._secs = secs
    return {
        "ok": True, "recording": live, "paused": live and s.paused, "stopping": False, "seconds": secs,
        "path": s.final if live else sr._last, "saved": (not live) and bool(sr._last) and os.path.exists(sr._last),
        "error": sr._error, "folder": str(out_dir()), "capture": LABEL, "audio": (s.cap_info if live else ""),
        "audio_warning": (s.cap_warn if live else ""), "saving": sr._saving > 0, "size": _seg_size(s) if live else 0,
        "notice": (f"Built-in recorder: {s.skipped} frames skipped, the PC could not keep up" if live and s.skipped > 30 else ""),
        "health": ({"captured": s.captured, "written": s.written, "dup": s.dupes, "skipped": s.skipped} if live else {}),
        "obs_ok": True, "obs_message": "",
    }


def selftest(seconds: float = 4.0) -> dict:
    """Settings -> "Test built-in recorder": record a few seconds with no sound, delete the clip, and report what it cost.
    Keep something moving on screen meanwhile."""
    ok, why = available()
    if not ok:
        return {"ok": False, "message": why}
    if sr._recording_dir_busy():
        return {"ok": False, "message": "Stop the recording first, then run the test."}
    cpu = {"lyra": 0.0, "ffmpeg": 0.0}
    try:
        import psutil
        me = psutil.Process()
        me.cpu_percent(None)
    except Exception:  # noqa: BLE001
        psutil = None
    saved_audio = sr._pref
    sr._pref = lambda k, d=None: ("off" if k == "record_audio" else saved_audio(k, d))      # no sound in the test
    try:
        st = start()
        if not st.get("recording"):
            return {"ok": False, "message": st.get("error") or "The recording did not start."}
        s = _S
        procs = None
        if psutil is not None:
            try:
                procs = psutil.Process(s.proc.pid)
                procs.cpu_percent(None)
            except Exception:  # noqa: BLE001
                procs = None
        time.sleep(seconds)
        if psutil is not None:
            n = psutil.cpu_count() or 1
            cpu["lyra"] = round(me.cpu_percent(None) / n, 1)
            if procs is not None:
                try:
                    cpu["ffmpeg"] = round(procs.cpu_percent(None) / n, 1)
                except Exception:  # noqa: BLE001
                    pass
        h = status()["health"]
        done = stop()
    finally:
        sr._pref = saved_audio
    path = done.get("path") or ""
    size = os.path.getsize(path) if path and os.path.exists(path) else 0
    try:
        if path:
            sr.delete_recording(os.path.basename(path))
    except Exception:  # noqa: BLE001
        pass
    cap_fps = round(h.get("captured", 0) / seconds, 1)
    msg = (f"Windows delivered {cap_fps} new pictures per second; {h.get('written', 0)} frames were sent to ffmpeg "
           f"({h.get('dup', 0)} repeated, {h.get('skipped', 0)} skipped). CPU used (share of the whole PC): Lyra {cpu['lyra']}%, "
           f"ffmpeg {cpu['ffmpeg']}%. Clip size {size >> 10} KB.")
    return {"ok": size > 0, "message": msg if size > 0 else (done.get("error") or "The test clip was empty.")}
