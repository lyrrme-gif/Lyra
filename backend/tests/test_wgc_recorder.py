"""The built-in recorder (wgc_recorder.py): a fake `windows_capture` package delivers moving frames, the REAL ffmpeg encodes them.
Skipped without ffmpeg. What cannot be tested here is the real Windows Graphics Capture package itself."""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import types
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("ui", types.SimpleNamespace(log=__import__("logging").getLogger("t")))
import numpy as np  # noqa: E402
import screen_record as sr  # noqa: E402
import wgc_recorder as wgc  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
SHAPE = (108, 192, 4)


class _Frame:
    def __init__(self, arr): self.frame_buffer, self.height, self.width = arr, arr.shape[0], arr.shape[1]


class _Control:
    def __init__(self): self.stopped = threading.Event()
    def stop(self): self.stopped.set()


class FakeWindowsCapture:
    """Stands in for windows_capture.WindowsCapture: calls on_frame_arrived from its own thread, like the real one."""
    rate, deliver, made = 60.0, True, []

    def __init__(self, cursor_capture=None, draw_border=None, monitor_index=None):
        self.handlers, self.kw = {}, (cursor_capture, draw_border, monitor_index)
        FakeWindowsCapture.made.append(self)

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    def start_free_threaded(self):
        ctl = _Control()

        def run():
            n = 0
            while not ctl.stopped.is_set() and FakeWindowsCapture.deliver:
                arr = np.zeros(SHAPE, dtype=np.uint8)
                x = (n * 3) % (SHAPE[1] - 20)
                arr[40:60, x:x + 20] = (255, 128, 0, 255)                 # a square that moves: every picture is different
                self.handlers["on_frame_arrived"](_Frame(arr), ctl)
                n += 1
                ctl.stopped.wait(1.0 / FakeWindowsCapture.rate)

        threading.Thread(target=run, daemon=True).start()
        return ctl


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    fake = types.ModuleType("windows_capture")
    fake.WindowsCapture = FakeWindowsCapture
    monkeypatch.setitem(sys.modules, "windows_capture", fake)
    FakeWindowsCapture.rate, FakeWindowsCapture.deliver, FakeWindowsCapture.made = 60.0, True, []
    vals = {"record_backend": "lyra", "record_fps": 30, "record_audio": "off"}
    monkeypatch.setattr(sr, "_pref", lambda k, d=None: vals.get(k, d))
    monkeypatch.setattr(sr, "out_dir", lambda: tmp_path)
    monkeypatch.setattr(sr.sys, "platform", "win32")
    monkeypatch.setattr(sr, "_ffmpeg", lambda: FFMPEG)
    monkeypatch.setattr(wgc, "_nvenc_preset", lambda exe: None)
    monkeypatch.setattr(wgc, "_open_sound", lambda mode, stem: (None, "", ""))
    monkeypatch.setattr(wgc, "FIRST_FRAME_TIMEOUT", 1.0)
    sr._live = sr._paused = False
    sr._saving, sr._secs, sr._size = 0, 0, 0
    sr._last = sr._error = ""
    wgc._S = None
    yield vals
    if wgc._S is not None:
        wgc._S.stop_evt.set()


def _info(path):
    t = subprocess.run([FFMPEG, "-hide_banner", "-i", path, "-map", "0:v", "-f", "null", "-"], capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", t)
    frames = re.findall(r"frame=\s*(\d+)", t)
    size = re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", t)
    return (int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3]) if dur else 0.0), (int(frames[-1]) if frames else 0), (size.groups() if size else None), t


@needs_ffmpeg
def test_a_recording_is_an_mp4_at_a_constant_frame_rate_of_the_right_length():
    st = sr.start()
    assert st["recording"] and st["capture"] == wgc.LABEL and not st["error"]
    time.sleep(2.5)
    mid = sr.status()
    assert mid["recording"] and 1 <= mid["seconds"] <= 3 and mid["size"] > 0 and mid["health"]["written"] > 30
    done = sr.stop()
    assert not done["recording"] and done["saved"] and done["path"].endswith(".mp4") and not done["error"]
    dur, frames, size, text = _info(done["path"])
    assert 2.0 < dur < 3.1, dur
    assert 60 <= frames <= 90, frames                                     # 30 fps x ~2.5 s
    assert size == ("192", "108")
    assert "built-in WGC" in text and "Lyra screen recording" in text     # the note about how it was made
    assert not [f for f in os.listdir(os.path.dirname(done["path"]) + "/.tmp")]    # no work files left


@needs_ffmpeg
def test_a_slow_screen_still_gives_a_constant_frame_rate_by_repeating_pictures():
    FakeWindowsCapture.rate = 8.0                                         # Windows delivers only 8 new pictures a second
    sr.start()
    time.sleep(2.0)
    h = sr.status()["health"]
    done = sr.stop()
    dur, frames, _size, _t = _info(done["path"])
    assert 45 <= frames <= 75 and h["dup"] > h["written"] // 2            # ~30 fps out, most of them repeats
    assert h["captured"] < h["written"]


@needs_ffmpeg
def test_pause_leaves_no_gap_in_the_video_and_stops_the_clock():
    sr.start()
    time.sleep(1.0)
    p = sr.pause()
    assert p["paused"] and p["recording"]
    s1 = sr.status()["seconds"]
    time.sleep(1.6)
    assert sr.status()["paused"] and sr.status()["seconds"] == s1         # the clock stands still while paused
    r = sr.resume()
    assert r["recording"] and not r["paused"]
    time.sleep(1.0)
    done = sr.stop()
    dur, frames, _s, _t = _info(done["path"])
    assert 1.5 < dur < 2.7, dur                                            # ~2 s recorded, the 1.6 s pause is not in it
    assert 45 <= frames <= 80


@needs_ffmpeg
def test_sound_is_mixed_in(tmp_path, monkeypatch):
    wav = tmp_path / "x-system.wav"
    t = np.arange(48000 * 4) / 48000
    pcm = np.repeat((np.sin(2 * np.pi * 440 * t) * 9000).astype("<i2")[:, None], 2, axis=1)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000); w.writeframes(pcm.tobytes())

    class Cap:
        t0, labels, warnings, calls = time.monotonic(), ["PC sound"], [], []
        def pause(self): self.calls.append("pause")
        def resume(self, d=0.0): self.calls.append("resume")
        def stop(self): return [{"role": "system", "path": str(wav), "peak": 9000, "seconds": 4.0, "track": None}]

    cap = Cap()
    monkeypatch.setattr(wgc, "_open_sound", lambda mode, stem: (cap, "PC sound", ""))
    st = sr.start()
    assert st["audio"] == "PC sound"
    time.sleep(1.5)
    sr.pause(); sr.resume()
    assert cap.calls == ["pause", "resume"]
    done = sr.stop()
    assert done["saved"] and not done["error"], done
    _d, _f, _s, text = _info(done["path"])
    assert "Audio: aac" in text and "Video: h264" in text
    assert not wav.exists()


def test_a_screen_that_never_delivers_a_picture_is_reported_and_cleaned_up():
    FakeWindowsCapture.deliver = False
    st = sr.start()
    assert not st["recording"] and "no picture arrived" in st["error"].lower()
    assert wgc._S is None and not sr._live


def test_the_missing_package_says_what_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "windows_capture", None)              # import fails
    st = sr.start()
    assert not st["recording"] and "pip install windows-capture" in st["error"]


def test_off_windows_it_says_so(monkeypatch):
    monkeypatch.setattr(sr.sys, "platform", "linux")
    assert "only works on Windows" in sr.start()["error"]


@needs_ffmpeg
def test_the_setting_switches_between_the_built_in_recorder_and_obs(fresh):
    assert sr._builtin()
    sr.start()
    time.sleep(0.6)
    assert sr.status()["capture"] == wgc.LABEL
    sr.stop()
    fresh["record_backend"] = "obs"
    assert not sr._builtin()
    assert sr.status()["capture"] == "OBS"                                 # the OBS path answers (OBS is not running here: that is fine)


@needs_ffmpeg
def test_the_self_test_reports_and_leaves_nothing_behind(tmp_path):
    r = wgc.selftest(seconds=1.5)
    assert r["ok"], r
    assert "pictures per second" in r["message"] and "ffmpeg" in r["message"]
    assert not [f for f in os.listdir(tmp_path) if f.endswith((".mp4", ".mkv"))]
    assert not sr._live and sr._pref("record_audio", "x") == "off"


def test_the_second_screen_shape_change_is_skipped_not_fatal():
    s = wgc._Session(30, "off")
    s.on_frame(_Frame(np.zeros(SHAPE, dtype=np.uint8)), _Control())
    s.on_frame(_Frame(np.zeros((50, 60, 4), dtype=np.uint8)), _Control())    # the screen changed size
    assert s.captured == 1 and s.warned_size


def test_keyword_arguments_the_package_does_not_know_are_dropped():
    class Old:                                                             # an older windows-capture without draw_border
        def __init__(self, cursor_capture=None, monitor_index=None): self.kw = (cursor_capture, monitor_index)
    m = types.SimpleNamespace(WindowsCapture=Old)
    assert wgc._make_capture(m, 1, True).kw == (True, 1)
