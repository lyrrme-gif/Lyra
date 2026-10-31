"""Screen recording v2: pause / resume, crash-safe work files, a stop that never blocks status(), the watchdog,
stray cleanup that leaves other people's ffmpeg alone, recovery, the Desktop Duplication fallback, the keyboard shortcuts.
Tests that need a real ffmpeg are skipped without one."""
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("ui", types.SimpleNamespace(log=__import__("logging").getLogger("t")))
try:
    import psutil  # noqa: F401  (imported before the tests pretend to be on Windows: psutil refuses to load there)
except ImportError:
    psutil = None
import record_audio as ra  # noqa: E402
import screen_record as sr  # noqa: E402

_REAL_SEG_DURATION = sr._segments_duration
_REAL_VERSION = sr._ffmpeg_version
_REAL_PROBE_DD = sr._probe_ddagrab

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")


class Proc:
    """A fake ffmpeg: alive until wait()/terminate(); wait() writes something into its output file."""
    def __init__(self, out, die_at_once=False, block=None, stderr=b""):
        self.out, self.stdin, self.stderr = out, io.BytesIO(), io.BytesIO(stderr)
        self.returncode = 1 if die_at_once else None
        self.block, self.pid = block, 4242

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.block is not None:
            self.block.wait(5)
        self.returncode = 0
        with open(self.out, "wb") as f:
            f.write(b"mkvdata" * 10)

    def terminate(self):
        self.returncode = 0


class FakeCap:
    def __init__(self):
        self.t0, self.labels, self.warnings, self.calls = time.monotonic(), ["PC sound"], [], []

    def pause(self):
        self.calls.append("pause")

    def resume(self, delay=0.0):
        self.calls.append(("resume", delay))

    def stop(self):
        self.calls.append("stop")
        return []


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(sr.sys, "platform", "win32")
    monkeypatch.setattr(sr, "out_dir", lambda: tmp_path)
    monkeypatch.setattr(sr, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (1920, 1080))
    monkeypatch.setattr(sr, "_probe_ddagrab", lambda exe, idx=0: False)
    monkeypatch.setattr(sr, "_probe_nvenc_bgra", lambda *a: False)
    monkeypatch.setattr(sr, "recover_async", lambda force=False: None)
    monkeypatch.setattr(sr, "_pref", lambda k, d: d)
    monkeypatch.setattr(sr, "_open_sound", lambda mode, stem: (None, "", ""))
    monkeypatch.setattr(sr, "_ffmpeg_version", lambda exe: (7, 1))           # no ffmpeg runs: Popen is faked in these tests
    monkeypatch.setattr(sr, "_segments_duration", lambda exe, segs: 2.0)
    sr._plan_cache.clear()
    sr._proc, sr._path, sr._last, sr._error, sr._started = None, "", "", "", 0.0
    sr._segments, sr._paused, sr._paused_total, sr._stopping, sr._saving = [], False, 0.0, False, 0
    sr._audio, sr._notice = None, ""
    sr._dd_bad = sr._gpu_bad = False
    sr._stopped.set()
    yield
    sr._watch_evt.set()


def _final_ok(calls):
    def run(cmd, timeout):
        calls.append(cmd)
        shutil.copy(cmd[-1 - 0] if False else cmd[cmd.index("-i") + 1], cmd[-1]) if "concat" not in cmd else open(cmd[-1], "wb").write(b"mp4")
        return True, ""
    return run


# ── the work file is crash-safe, the command is right ─────────────────────────────────────────────

def test_the_picture_goes_to_a_matroska_work_file_without_mp4_options():
    cmd = sr.build_cmd("ffmpeg", "seg.mkv")
    assert cmd[-1] == "seg.mkv" and "-movflags" not in cmd
    assert "+faststart" in sr.build_cmd("ffmpeg", "x.mp4")


def test_cpu_encoder_gets_a_thread_limit_and_progress_output():
    cmd = sr.build_cmd("ffmpeg", "s.mkv", prof={**sr.profile("medium", region=False), "threads": 3, "progress": ["-progress", "pipe:2", "-stats_period", "2"]})
    assert cmd[cmd.index("-threads") + 1] == "3" and "pipe:2" in cmd and cmd[-1] == "s.mkv"
    gpu = sr.build_cmd("ffmpeg", "s.mkv", prof={**sr.profile("medium", region=False), "gpu": "p4", "threads": 3})
    assert "h264_nvenc" in gpu and "-threads" not in gpu


def test_ddagrab_plan_builds_a_desktop_duplication_command():
    prof = {**sr.profile("high", region=False)}
    cmd = sr.build_cmd("ffmpeg", "s.mkv", prof=prof, plan={"grab": "ddagrab", "verified": True, "env": {}, "input": [], "crop": None})
    src = cmd[cmd.index("-i") + 1]
    assert "gdigrab" not in cmd and src.startswith("ddagrab=output_idx=0:framerate=60") and "hwdownload" in src


def test_priority_and_threads_follow_the_cpu_setting(monkeypatch):
    monkeypatch.setattr(sr, "_ffmpeg_version", lambda exe: (7, 1))
    monkeypatch.setattr(sr.os, "cpu_count", lambda: 16)
    assert sr._tuning("ffmpeg", {"cpu": "balanced"})["threads"] == 8
    assert sr._tuning("ffmpeg", {"cpu": "light"})["threads"] == 2
    assert "threads" not in sr._tuning("ffmpeg", {"gpu": "p4"})
    monkeypatch.setattr(sr.subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0x8000, raising=False)
    monkeypatch.setattr(sr.subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000, raising=False)
    assert sr._proc_flags({"gpu": "p4"}) & 0x4000 and not sr._proc_flags({"gpu": "p4"}) & 0x8000   # gives way to a game
    assert not sr._proc_flags({}) & 0xC000                                                          # the CPU encoder needs its cores
    assert sr._proc_flags({"cpu": "light"}) & 0x4000


# ── audio timeline across a pause ─────────────────────────────────────────────────────────────────

def test_a_pause_leaves_no_hole_and_no_silence_in_the_sound(tmp_path):
    import wave
    now = [0.0]
    t = ra.WavTrack(str(tmp_path / "a.wav"), 48000, 2, clock=lambda: now[0], t0=0.0)
    chunk = b"\x10\x00" * 2 * 4800                     # 0.1 s
    for _ in range(10):                                  # 1 s recorded
        now[0] += 0.1
        t.push(chunk)
    t.pause()
    for _ in range(50):                                  # 5 s paused: data keeps arriving and is dropped
        now[0] += 0.1
        t.push(chunk)
    t.resume(delay=0.0)
    for _ in range(10):                                  # 1 s more
        now[0] += 0.1
        t.push(chunk)
    t.finish(2.0)
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert abs(w.getnframes() / 48000 - 2.0) < 0.05


def test_capture_active_seconds_excludes_pauses(tmp_path):
    now = [100.0]
    cap = ra.AudioCapture(True, False, tmp_path, "x", clock=lambda: now[0]) if "clock" in ra.AudioCapture.__init__.__code__.co_varnames else None
    if cap is None:
        pytest.skip("AudioCapture has no injectable clock")
    cap.t0 = 100.0
    now[0] = 110.0
    assert abs(cap.active_seconds() - 10) < 1e-6
    cap.pause()
    now[0] = 140.0
    assert abs(cap.active_seconds() - 10) < 1e-6          # frozen while paused
    cap.resume(delay=0.5)
    now[0] = 145.0
    assert abs(cap.active_seconds() - (15 - 0.5)) < 1e-6


# ── pause / resume / stop with a fake ffmpeg ──────────────────────────────────────────────────────

def test_pause_and_resume_make_segments_that_are_joined_at_the_end(monkeypatch):
    launched, calls = [], []
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd) or Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok(calls))
    s = sr.start()
    assert s["recording"] and not s["paused"]
    s = sr.pause()
    assert s["recording"] and s["paused"] and len(sr._segments) == 1 and sr._proc is None
    s = sr.resume()
    assert s["recording"] and not s["paused"] and len(launched) == 2 and launched[1][-1].endswith("-seg02.mkv")
    s = sr.stop()
    assert not s["recording"] and s["saved"] and os.path.exists(s["path"]) and s["path"].endswith(".mp4")
    assert "concat" in calls[-1]                              # both segments were joined, not re-encoded
    assert "-c:v" in calls[-1] and calls[-1][calls[-1].index("-c:v") + 1] == "copy"
    assert not [n for n in os.listdir(os.path.join(os.path.dirname(s["path"]), ".tmp"))]   # nothing left behind


def test_recorded_time_does_not_count_the_pause():
    sr._started, sr._paused_total, sr._paused, sr._paused_at = time.time() - 100, 30.0, False, 0.0
    sr._proc = Proc("x")
    assert 69 < sr._active_seconds() < 71
    sr._proc, sr._paused, sr._paused_at = None, True, time.time() - 20
    assert 49 < sr._active_seconds() < 51                     # 100 - 30 - 20 so far


def test_pause_and_resume_talk_to_the_sound(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    cap = FakeCap()
    monkeypatch.setattr(sr, "_open_sound", lambda mode, stem: (cap, "PC sound", ""))
    sr.start()
    sr.pause()
    sr.resume()
    assert cap.calls[0] == "pause" and cap.calls[1][0] == "resume" and cap.calls[1][1] == ra.VIDEO_START_GUESS


def test_pause_when_not_recording_does_nothing():
    assert not sr.pause()["recording"] and not sr.resume()["recording"]


def test_a_recording_paused_for_ever_is_saved(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    sr.pause()
    sr._paused_at -= sr.MAX_PAUSE_SECONDS + 5
    s = sr.status()
    assert not s["recording"] and s["saved"] and "paused for too long" in s["error"]


def test_voice_pause_and_resume(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    assert "not recording" in sr.try_command("pause the recording")
    sr.start()
    assert "paused" in sr.try_command("pause recording").lower()
    assert "again" in sr.try_command("resume the recording").lower()
    assert "isn't paused" in sr.try_command("resume recording")


def test_toggle_helpers_are_what_the_shortcuts_call(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    assert sr.toggle()["recording"]
    assert sr.toggle_pause()["paused"]
    assert not sr.toggle_pause()["paused"]
    assert not sr.toggle()["recording"]
    assert not sr.toggle_pause()["recording"]                  # nothing running: no-op


# ── stopping never blocks the island ──────────────────────────────────────────────────────────────

def test_status_answers_at_once_while_a_stop_is_still_closing_ffmpeg(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1], block=gate))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    done = []
    t = threading.Thread(target=lambda: done.append(sr.stop()))
    t.start()
    for _ in range(200):                                       # wait until stop() is inside ffmpeg's wait()
        if sr._stopping:
            break
        time.sleep.__wrapped__(0.01) if hasattr(time.sleep, "__wrapped__") else threading.Event().wait(0.01)
    t0 = time.monotonic()
    s = sr.status()                                            # the lock must be free
    assert time.monotonic() - t0 < 0.5
    assert s["stopping"] and s["saving"] and not s["recording"]
    gate.set()
    t.join(10)
    assert done and done[0]["saved"]


def test_start_waits_for_the_previous_recording_to_finish_closing(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    sr.stop()
    assert sr.start()["recording"]                              # no "still saving" refusal


def test_the_island_gets_paused_state_and_normal_calls_keep_their_shape(monkeypatch):
    import ui_bridge
    seen = []
    monkeypatch.setattr(ui_bridge, "push_record", lambda rec, at=0, **kw: seen.append((rec, at, kw)))
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    assert seen[-1][0] is True and seen[-1][1] > 0 and seen[-1][2] == {}
    sr.pause()
    assert seen[-1][2].get("paused") is True and "elapsed_ms" in seen[-1][2]
    sr.stop()
    assert seen[-1][:2] == (False, 0)


# ── the watchdog (nobody has to ask for the status) ───────────────────────────────────────────────

def test_the_watchdog_saves_a_recording_whose_ffmpeg_died(monkeypatch):
    monkeypatch.setattr(sr, "WATCH_SECONDS", 0.05)
    box = {}
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: box.setdefault("p", Proc(cmd[-1], stderr=b"Access denied\n")))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    with open(box["p"].out, "wb") as f:
        f.write(b"data")
    box["p"].returncode = 1                                     # ffmpeg crashed
    for _ in range(100):
        if not sr._active() and not sr._stopping and sr._last:
            break
        threading.Event().wait(0.05)
    assert not sr._active() and sr._last and "Access denied" in sr._error


def test_the_watchdog_enforces_the_time_limit_without_a_status_call(monkeypatch):
    monkeypatch.setattr(sr, "WATCH_SECONDS", 0.05)
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    sr._started -= sr._limit + 10
    for _ in range(100):
        if not sr._active() and not sr._stopping:
            break
        threading.Event().wait(0.05)
    assert not sr._active()


def test_a_nearly_full_drive_stops_and_saves(monkeypatch):
    monkeypatch.setattr(sr, "WATCH_SECONDS", 0.05)
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    monkeypatch.setattr(sr, "_free_bytes", lambda folder: 100 << 20)
    for _ in range(100):
        if not sr._active() and not sr._stopping:
            break
        threading.Event().wait(0.05)
    assert not sr._active() and "almost full" in sr._error


def test_recording_does_not_start_on_a_full_drive(monkeypatch):
    monkeypatch.setattr(sr, "_free_bytes", lambda folder: 10 << 20)
    called = []
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: called.append(1))
    s = sr.start()
    assert not s["recording"] and "free space" in s["error"] and not called


def test_an_ffmpeg_that_is_not_keeping_up_gets_a_notice(monkeypatch):
    monkeypatch.setattr(sr, "WATCH_SECONDS", 0.05)
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    sr._health.update({"speed": 0.6, "frames": 100})
    for _ in range(100):
        if sr._notice:
            break
        threading.Event().wait(0.05)
    assert "keep up" in sr.status()["notice"]
    sr.stop()


def test_ffmpeg_progress_lines_become_health_numbers_and_errors_are_kept():
    sr._health.clear()
    sr._tail.clear()
    p = types.SimpleNamespace(stderr=io.BytesIO(b"frame=300\nfps=29.9\ndup_frames=4\ndrop_frames=1\nspeed=0.98x\ntotal_size=123456\n"
                                                b"stream_0_0_q=-1.0\nprogress=continue\nsomething went wrong\n"))
    sr._drain(p)
    assert sr._health == {"frames": 300, "fps": 29.9, "dup": 4, "drop": 1, "speed": 0.98, "size": 123456}
    assert list(sr._tail) == ["something went wrong"]


# ── the Desktop Duplication fallback ──────────────────────────────────────────────────────────────

def test_if_desktop_duplication_will_not_start_the_old_grabber_takes_over(monkeypatch):
    monkeypatch.setattr(sr, "_probe_ddagrab", lambda exe, idx=0: True)      # this PC "passes" the test encode ...
    monkeypatch.setattr(sr, "_monitor_count", lambda: 1)
    cmds = []

    def popen(cmd, **kw):
        cmds.append(cmd)
        return Proc(cmd[-1], die_at_once=("ddagrab" in " ".join(cmd)), stderr=b"No such filter: 'ddagrab'\n")

    monkeypatch.setattr(sr.subprocess, "Popen", popen)
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    s = sr.start()
    assert s["recording"] and not s["error"]
    assert "ddagrab" in " ".join(cmds[0]) and "gdigrab" in cmds[1] and sr._dd_bad


def test_ddagrab_is_only_for_one_screen_and_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(sr, "_probe_ddagrab", lambda exe, idx=0: True)
    monkeypatch.setattr(sr, "_monitor_count", lambda: 2)
    assert not sr._dd_possible("ffmpeg")
    monkeypatch.setattr(sr, "_monitor_count", lambda: 1)
    assert sr._dd_possible("ffmpeg")
    monkeypatch.setattr(sr, "_pref", lambda k, d: "compat" if k == "record_capture" else d)
    assert not sr._dd_possible("ffmpeg")


# ── no wasted work ────────────────────────────────────────────────────────────────────────────────

def test_listing_recordings_runs_no_process_and_no_screen_lookup_while_recording(tmp_path, monkeypatch):
    (tmp_path / "old.mp4").write_bytes(b"x" * 10)
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    sr.start()
    ran = []
    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(sr, "region_for", lambda *a, **k: ran.append("region"))
    r = sr.list_recordings()
    assert not ran
    assert [i["name"] for i in r["items"] if not i["current"]] == ["old.mp4"]


def test_the_list_never_runs_powershell_or_looks_for_strays(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "recover_async", sr.__dict__["recover_async"])        # the real one
    seen = []
    monkeypatch.setattr(sr, "kill_strays", lambda: seen.append("strays") or 0)
    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: seen.append(a))
    monkeypatch.setattr(sr, "_recover_last", -1e9)
    monkeypatch.setattr(sr, "_strays_done", False)
    for _ in range(5):
        sr.list_recordings()
    threading.Event().wait(0.3)
    assert not [x for x in seen if x != "strays"]               # nothing spawned
    assert seen.count("strays") <= 1                            # and the stray scan ran at most once


def test_the_folder_answer_is_remembered_for_a_few_seconds(tmp_path, monkeypatch):
    monkeypatch.undo() if False else None
    real = types.SimpleNamespace()
    import importlib
    mod = importlib.reload(sr)                                   # the real out_dir
    n = []
    monkeypatch.setattr(mod, "_custom_dir", lambda: n.append(1) or tmp_path)
    mod._dir_cache = ("", 0.0, None)
    mod.out_dir(); mod.out_dir(); mod.out_dir()
    assert len(n) == 3                                           # the setting is read each time (cheap) ...
    calls = []
    orig = type(tmp_path).mkdir
    monkeypatch.setattr(type(tmp_path), "mkdir", lambda self, *a, **k: calls.append(1) or orig(self, *a, **k))
    mod.out_dir(); mod.out_dir()
    assert not calls                                             # ... but the disk is not touched again


# ── strays: only our own screen grabber, never anybody else's ffmpeg ──────────────────────────────

def _fake_ffmpeg(tmp_path, name, *args):
    bindir = tmp_path.parent / ("bin_" + tmp_path.name)            # the program itself lives outside the recordings folder, like ffmpeg.exe does
    bindir.mkdir(exist_ok=True)
    exe = bindir / name
    exe.write_text("#!/bin/sh\nsleep 30\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return subprocess.Popen([str(exe), *args])


def test_kill_strays_ends_our_leftover_grabber_and_leaves_other_ffmpeg_alone(tmp_path):
    if psutil is None:
        pytest.skip("needs psutil")
    mine = _fake_ffmpeg(tmp_path, "ffmpeg-a", "-f", "gdigrab", "-i", "desktop", str(tmp_path / ".tmp" / "x-seg01.mkv"))
    other = _fake_ffmpeg(tmp_path, "ffmpeg-b", "-f", "gdigrab", "-i", "desktop", "/somewhere/else/mine.mp4")
    convert = _fake_ffmpeg(tmp_path, "ffmpeg-c", "-i", str(tmp_path / "a.mkv"), str(tmp_path / "b.mp4"))
    try:
        time.sleep(0.2) if False else threading.Event().wait(0.3)
        assert sr.kill_strays() == 1
        threading.Event().wait(0.3)
        assert mine.poll() is not None and other.poll() is None and convert.poll() is None
    finally:
        for p in (mine, other, convert):
            p.kill()


# ── recovery and real ffmpeg ──────────────────────────────────────────────────────────────────────

def _make_segment(path, seconds, fmt_args=()):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=15",
                    "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", *fmt_args, str(path)],
                   check=True, capture_output=True)


def _duration(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    import re
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def _wav(path, seconds, freq=440):
    import numpy as np
    t = np.arange(int(48000 * seconds)) / 48000
    a = (np.sin(2 * np.pi * freq * t) * 12000).astype("<i2")
    st = np.repeat(a[:, None], 2, axis=1)
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
        w.writeframes(st.tobytes())


@needs_ffmpeg
def test_three_segments_and_two_sound_tracks_become_one_mp4_of_the_right_length(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_segments_duration", _REAL_SEG_DURATION)
    segs = []
    for i, sec in enumerate((2, 1, 2), 1):
        p = tmp_path / f"s{i}.mkv"; _make_segment(p, sec); segs.append(str(p))
    a, b = tmp_path / "sys.wav", tmp_path / "mic.wav"
    _wav(a, 5.2); _wav(b, 5.2, 660)
    lst = tmp_path / "l.txt"
    ra.write_concat_list(lst, segs)
    out = tmp_path / "o.mp4"
    dur = sr._segments_duration(FFMPEG, segs)
    assert 4.7 < dur < 5.3
    ok, why = sr._run_low(ra.build_final_cmd(FFMPEG, segs, [str(a), str(b)], str(out), 0.2, dur, lst), 60)
    assert ok, why
    info = subprocess.run([FFMPEG, "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
    assert "Video: h264" in info and "Audio: aac" in info
    assert abs(_duration(out) - dur) < 0.3


@needs_ffmpeg
def test_paths_with_spaces_and_quotes_survive_the_concat_list(tmp_path):
    d = tmp_path / "my rec's folder"; d.mkdir()
    segs = []
    for i in (1, 2):
        p = d / f"a b{i}.mkv"; _make_segment(p, 1); segs.append(str(p))
    lst = tmp_path / "l.txt"; ra.write_concat_list(lst, segs)
    out = tmp_path / "o.mp4"
    ok, why = sr._run_low(ra.build_final_cmd(FFMPEG, segs, [], str(out), 0, 0, lst), 60)
    assert ok, why
    assert 1.7 < _duration(out) < 2.3


@needs_ffmpeg
def test_a_recording_killed_mid_write_is_still_recoverable(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_segments_duration", _REAL_SEG_DURATION)
    """The point of the Matroska work file: ffmpeg is killed hard (power cut / crash) and the picture is not lost."""
    seg = tmp_path / "k.mkv"
    p = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-re", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15",
                          "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", *sr.MKV_CRASH_SAFE, str(seg)], stdin=subprocess.PIPE)
    threading.Event().wait(2.5)
    p.send_signal(signal.SIGKILL)
    p.wait()
    assert seg.stat().st_size > 0
    final = tmp_path / "final.mp4"
    wav = tmp_path / "s.wav"; _wav(wav, 6.0)
    out, note = sr._assemble(FFMPEG, [str(seg)], [str(wav)], str(final), 0.3, "k")
    assert out == str(final) and os.path.exists(final), note
    assert _duration(final) > 1.0                               # what was recorded before the kill, with its sound
    info = subprocess.run([FFMPEG, "-hide_banner", "-i", str(final)], capture_output=True, text=True).stderr
    assert "Audio: aac" in info


@needs_ffmpeg
def test_recover_orphans_finishes_an_interrupted_recording_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_ffmpeg", lambda: FFMPEG)
    monkeypatch.setattr(sr, "_segments_duration", _REAL_SEG_DURATION)
    monkeypatch.setattr(sr, "kill_strays", lambda: 0)
    tmp = tmp_path / ".tmp"; tmp.mkdir()
    seg1, seg2 = tmp / "Lyra-x-seg01.mkv", tmp / "Lyra-x-seg02.mkv"
    _make_segment(seg1, 1); _make_segment(seg2, 1)
    _wav(tmp / "Lyra-x-system.wav", 2.4)
    man = tmp / "Lyra-x.json"
    man.write_text(json.dumps({"final": str(tmp_path / "Lyra-x.mp4"), "stem": "Lyra-x", "segments": [str(seg1), str(seg2)], "lead": 0.3}))
    old = time.time() - 120
    for f in tmp.iterdir():
        os.utime(f, (old, old))
    monkeypatch.setattr(sr, "_tmp_dir", lambda: tmp)
    assert sr.recover_orphans() == 1
    assert (tmp_path / "Lyra-x.mp4").exists() and 1.6 < _duration(tmp_path / "Lyra-x.mp4") < 2.4
    assert os.listdir(tmp) == []


def test_recover_leaves_a_recording_that_is_being_made_right_now_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "kill_strays", lambda: 0)
    tmp = tmp_path / ".tmp"; tmp.mkdir()
    (tmp / "Lyra-y.json").write_text(json.dumps({"final": str(tmp_path / "Lyra-y.mp4"), "stem": "Lyra-y", "segments": []}))
    monkeypatch.setattr(sr, "_tmp_dir", lambda: tmp)
    assert sr.recover_orphans() == 0 and (tmp / "Lyra-y.json").exists()      # too new: still being written


def test_a_failed_mix_keeps_the_picture_as_a_playable_mkv(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_tmp_dir", lambda: tmp_path)
    monkeypatch.setattr(sr, "_run_low", lambda cmd, timeout: (False, "disk full"))
    seg = tmp_path / "s.mkv"; seg.write_bytes(b"picture")
    out, note = sr._assemble("ffmpeg", [str(seg)], [], str(tmp_path / "f.mp4"), 0, "s")
    assert out.endswith("f.mkv") and os.path.exists(out) and "disk full" in note and note.startswith("Saved")


def test_mkv_files_are_listed_opened_and_deleted_like_mp4s(tmp_path):
    (tmp_path / "a.mkv").write_bytes(b"x")
    assert [i["name"] for i in sr.list_recordings()["items"]] == ["a.mkv"]
    assert sr._resolve("a.mkv") is not None and sr._resolve("a.txt") is None
    assert sr.delete_recording("a.mkv")["ok"] and not (tmp_path / "a.mkv").exists()


# ── settings and shortcuts ────────────────────────────────────────────────────────────────────────

def test_new_settings_have_defaults_and_are_validated():
    import settings_store as ss
    assert "record_hotkeys" not in ss._DEFAULTS and ss._DEFAULTS["shortcut_record_combo"] == "ctrl+alt+r" and ss._DEFAULTS["shortcut_pause_combo"] == "ctrl+alt+p"
    assert ss._DEFAULTS["record_cpu"] == "balanced" and ss._DEFAULTS["record_capture"] == "auto"
    group = ss.PREF_GROUPS["recordings"] if hasattr(ss, "PREF_GROUPS") else None
    if group is not None:
        assert {"record_cpu", "record_capture"} <= set(group) and "record_hotkeys" not in group


def test_hotkeys_do_nothing_off_windows_and_report_their_state(monkeypatch):
    import record_hotkey as rh
    monkeypatch.setattr(rh.sys, "platform", "linux")
    rh.start(); rh.stop()
    assert rh._thread is None
    st = rh.status()
    assert st["defaults"] == {"toggle": "ctrl+alt+r", "pause": "ctrl+alt+p"} and st["taken"] == [] and st["unusable"] == []
    monkeypatch.setattr(rh, "combos", lambda: {"toggle": "", "pause": ""})      # both removed in Settings: no thread at all
    rh.apply()
    assert rh._thread is None


def test_the_shortcut_handlers_call_the_recorder(monkeypatch):
    import record_hotkey as rh
    seen = []
    monkeypatch.setattr(sr, "toggle", lambda: seen.append("toggle") or {})
    monkeypatch.setattr(sr, "toggle_pause", lambda: seen.append("pause") or {})
    rh._pressed("toggle"); rh._pressed("pause")
    assert seen == ["toggle", "pause"]


def test_the_work_file_is_flushed_packet_by_packet_so_a_crash_loses_one_frame():
    cmd = sr.build_cmd("ffmpeg", "s.mkv")
    assert cmd[-5:-1] == ["-flush_packets", "1", "-cluster_time_limit", "1000"] and cmd[-1] == "s.mkv"
    assert "-flush_packets" not in sr.build_cmd("ffmpeg", "s.mp4")


# ── Desktop Duplication on several screens, zero copy, and the note inside the file ──────────────

TWO = [(0, 0, 1920, 1080), (1920, 0, 3840, 1080)]


def test_the_chosen_screen_is_matched_to_its_ddagrab_output_by_rectangle():
    assert sr._pick_output(TWO, 0, 0, 1920, 1080) == 0
    assert sr._pick_output(TWO, 1920, 0, 1920, 1080) == 1
    assert sr._pick_output(TWO, 3840, 0, 1920, 1080) is None            # a screen on another graphics card: not in the list
    assert sr._pick_output([], 0, 0, 1920, 1080) is None


def test_dxgi_outputs_is_empty_off_windows(monkeypatch):
    monkeypatch.setattr(sr.sys, "platform", "linux")
    assert sr._dxgi_outputs() == []


def _screens(monkeypatch, n, rects, probe=True):
    import screen_capture
    monkeypatch.setattr(sr, "_monitor_count", lambda: n)
    monkeypatch.setattr(sr, "_dxgi_outputs", lambda: rects)
    monkeypatch.setattr(sr, "_probe_ddagrab", lambda exe, idx=0: probe)
    from contextlib import nullcontext
    monkeypatch.setattr(screen_capture, "real_pixels", lambda: nullcontext(), raising=False)


def test_several_screens_use_the_fast_grabber_on_the_screen_that_was_picked(monkeypatch):
    _screens(monkeypatch, 2, TWO)
    second = {"l": 1920, "t": 0, "w": 1920, "h": 1080, "vl": 0, "vt": 0, "vw": 3840, "vh": 1080}
    assert sr._dd_output("ffmpeg", second) == 1
    assert sr._dd_output("ffmpeg", {**second, "l": 0}) == 0
    plan = sr.plan_capture("ffmpeg", second)
    assert plan["grab"] == "ddagrab" and plan["output_idx"] == 1
    cmd = sr.build_cmd("ffmpeg", "s.mkv", prof=sr.profile("high", region=False), plan=plan)
    assert cmd[cmd.index("-i") + 1].startswith("ddagrab=output_idx=1:framerate=60")


def test_all_screens_and_other_graphics_cards_keep_the_older_grabber(monkeypatch):
    _screens(monkeypatch, 2, TWO)
    assert sr._dd_output("ffmpeg", None) is None                         # "All screens": only gdigrab can do that
    off_card = {"l": 3840, "t": 0, "w": 1920, "h": 1080, "vl": 0, "vt": 0, "vw": 5760, "vh": 1080}
    assert sr._dd_output("ffmpeg", off_card) is None
    _screens(monkeypatch, 2, TWO, probe=False)
    assert sr._dd_output("ffmpeg", {"l": 0, "t": 0, "w": 1920, "h": 1080, "vl": 0, "vt": 0, "vw": 3840, "vh": 1080}) is None


def test_zero_copy_keeps_frames_on_the_graphics_card():
    prof = {**sr.profile("high", region=False), "max_h": 0, "gpu": "p4"}
    plan = {"grab": "ddagrab", "verified": True, "env": {}, "input": [], "crop": None, "output_idx": 0, "zero_copy": True}
    cmd = sr.build_cmd("ffmpeg", "s.mkv", prof=prof, plan=plan)
    src = cmd[cmd.index("-filter_complex") + 1]                        # not "-f lavfi -i": that input device cannot carry frames on the card
    assert "-i" not in cmd and "-f" not in cmd[:cmd.index("-filter_complex")]
    assert src.startswith("ddagrab=") and "hwdownload" not in src and "-vf" not in cmd and "-pix_fmt" not in cmd and "h264_nvenc" in cmd
    assert cmd[-1] == "s.mkv"
    through_memory = sr.build_cmd("ffmpeg", "s.mkv", prof=prof, plan={**plan, "zero_copy": False})
    assert "hwdownload" in through_memory[through_memory.index("-i") + 1] and "-vf" in through_memory and "-pix_fmt" in through_memory
    cpu = sr.build_cmd("ffmpeg", "s.mkv", prof={**sr.profile("high", region=False), "max_h": 0}, plan=plan)     # no GPU encoder: never zero copy
    assert "hwdownload" in cpu[cpu.index("-i") + 1] and "libx264" in cpu


def test_every_recording_says_how_it_was_made():
    prof = {**sr.profile("high", region=False), "max_h": 0, "gpu": "p4"}
    plan = {"grab": "ddagrab", "verified": True, "env": {}, "input": [], "crop": None, "zero_copy": True}
    cmd = sr.build_cmd("ffmpeg", "s.mkv", prof=prof, plan=plan)
    note = cmd[cmd.index("-metadata") + 1]
    assert note == "comment=Lyra screen recording: ddagrab zero-copy, NVENC p4, 60 fps" and cmd[-1] == "s.mkv"
    assert "gdigrab" in sr._how(sr.profile("medium", region=False), None) and "x264" in sr._how(sr.profile("medium", region=False), None)


def test_start_falls_back_zero_copy_then_memory_then_gdigrab_then_cpu(monkeypatch):
    plan = {"env": {}, "input": [], "crop": None, "verified": True, "grab": "ddagrab", "output_idx": 0, "info": "whole screen (desktop duplication)"}
    monkeypatch.setattr(sr, "plan_capture", lambda exe, region, grab=None: dict(plan) if not sr._dd_bad else
                        {"env": {}, "input": [], "crop": None, "verified": True, "grab": "gdigrab", "info": "whole screen"})
    monkeypatch.setattr(sr, "_gpu_preset", lambda exe: None if sr._gpu_bad else "p4")
    monkeypatch.setattr(sr, "_probe_zero_copy", lambda *a: True)
    monkeypatch.setattr(sr, "_pref", lambda k, d: "auto" if k == "record_encoder" else d)
    cmds = []

    def popen(cmd, **kw):
        cmds.append(cmd)
        line = " ".join(cmd)
        dies = ("ddagrab" in line) or ("h264_nvenc" in line)             # only gdigrab + x264 works on this "PC"
        return Proc(cmd[-1], die_at_once=dies, stderr=b"nope\n")

    monkeypatch.setattr(sr.subprocess, "Popen", popen)
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    s = sr.start()
    assert s["recording"] and not s["error"], s
    kinds = []
    for c in cmds:
        src = c[c.index("-i") + 1] if "-i" in c else (c[c.index("-filter_complex") + 1] if "-filter_complex" in c else "")
        kinds.append(("zero-copy" if "hwdownload" not in src and "ddagrab" in src else "dd-memory" if "ddagrab" in src else "gdigrab") + ("/nvenc" if "h264_nvenc" in c else "/x264"))
    assert kinds == ["zero-copy/nvenc", "dd-memory/nvenc", "gdigrab/nvenc", "gdigrab/x264"], kinds
    assert sr._zc_bad and sr._dd_bad and sr._gpu_bad


@needs_ffmpeg
def test_the_note_survives_into_the_finished_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_segments_duration", _REAL_SEG_DURATION)
    seg = tmp_path / "s.mkv"; _make_segment(seg, 1)
    out, note = sr._assemble(FFMPEG, [str(seg)], [], str(tmp_path / "f.mp4"), 0, "s", comment="Lyra screen recording: ddagrab zero-copy, NVENC p4, 60 fps")
    assert out.endswith("f.mp4"), note
    info = subprocess.run([FFMPEG, "-hide_banner", "-i", out], capture_output=True, text=True).stderr
    assert "ddagrab zero-copy" in info
    segs = []
    for i in (1, 2):
        p = tmp_path / f"m{i}.mkv"; _make_segment(p, 1); segs.append(str(p))
    out2, note2 = sr._assemble(FFMPEG, segs, [], str(tmp_path / "g.mp4"), 0, "g", comment="Lyra screen recording: gdigrab, x264 ultrafast, 30 fps")
    assert "gdigrab" in subprocess.run([FFMPEG, "-hide_banner", "-i", out2], capture_output=True, text=True).stderr


# ── the performance test ──────────────────────────────────────────────────────────────────────────

def test_dwm_intervals_become_a_frame_rate_and_a_share_of_slow_frames():
    import record_diag as rd
    smooth = rd.summarize([16.7] * 60)
    assert smooth["fps"] == 59.9 and smooth["slow_pct"] == 0
    choppy = rd.summarize([16.7, 16.7, 33.4, 33.4, 16.7, 50.0, 16.7, 33.4, 16.7, 16.7])
    assert choppy["fps"] < 40 and choppy["slow_pct"] == 40 and choppy["p95_ms"] >= 33.4
    assert rd.summarize([16.7] * 3) == {}                      # too few to say anything


def test_ffmpeg_progress_text_is_read_and_the_error_kept():
    import record_diag as rd
    r = rd.parse_progress("frame=120\nfps=60.0\ndup_frames=3\ndrop_frames=0\nspeed=1.01x\nprogress=end\nCannot load nvcuda.dll\n")
    assert r == {"frame": 120, "dup_frames": 3, "drop_frames": 0, "speed": 1.01, "error": "Cannot load nvcuda.dll"}


def test_the_report_names_each_method_and_says_why_a_faster_one_was_not_used():
    import record_diag as rd
    rep = rd.format_report({
        "ffmpeg": "7 1", "settings": "60 fps", "screens": 1, "outputs": 1, "baseline": {"fps": 60.0, "p95_ms": 17.0, "slow_pct": 0},
        "trials": [{"name": "A  old", "result": {"frame": 360, "capture_fps": 60.0, "dup_frames": 0, "drop_frames": 0, "speed": 1.0, "dwm": {"fps": 31.0, "p95_ms": 40.0, "slow_pct": 70}}},
                   {"name": "C  zero", "skipped": "the test encode failed"}],
        "why": {"zero_copy": "Impossible to convert between the formats supported by the filter"}})
    assert "NO capture: 60.0 fps" in rep and "[A  old]" in rep and "DURING capture: 31.0 fps" in rep
    assert "not tested: the test encode failed" in rep and "why zero_copy was not usable: Impossible" in rep


def test_the_performance_test_refuses_while_recording_and_off_windows(monkeypatch):
    import record_diag as rd
    monkeypatch.setattr(rd.sys, "platform", "linux")
    assert not rd.run()["ok"]
    monkeypatch.setattr(rd.sys, "platform", "win32")
    monkeypatch.setattr(sr, "_recording_dir_busy", lambda: True)
    assert "Stop the recording" in rd.run()["report"]


def test_a_failed_probe_remembers_what_ffmpeg_said(monkeypatch):
    sr._zc_cache.clear(); sr.probe_why.clear()
    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stderr=b"x\nNo device available for encoder\n"))
    assert sr._probe_zero_copy("ffmpeg", 0, 60, "p3") is False
    assert "No device available for encoder" in sr.probe_why["zero_copy"]
    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stderr=b"No such filter: 'ddagrab'\n"))
    assert _REAL_PROBE_DD("ffmpeg", 0) is False and "ddagrab" in sr.probe_why["ddagrab"]


# ── the PC / graphics card monitor and the extra trials ───────────────────────────────────────────

def test_nvidia_smi_lines_are_read():
    import record_diag as rd
    assert rd.parse_gpu_line("37, 12, 2048\n") == {"gpu": 37.0, "enc": 12.0, "mem": 2048.0}
    assert rd.parse_gpu_line("[N/A], [N/A], 100") == {} and rd.parse_gpu_line("") == {}


def test_samples_become_averages_and_the_busiest_programs():
    import record_diag as rd
    r = rd.summarize_samples([10, 30, 50], [40, 40, 41], [8000, 8000, 8100],
                             [{"gpu": 20, "enc": 5, "mem": 1000}, {"gpu": 60, "enc": 9, "mem": 1100}],
                             {"ffmpeg": [5, 7], "dwm": [2, 2], "idle": [0.0, 0.1], "lyra": [1, 1]})
    assert r["cpu_avg"] == 30.0 and r["cpu_max"] == 50.0 and r["ram_mb"] == 8033
    assert r["gpu_avg"] == 40.0 and r["gpu_max"] == 60 and r["enc_avg"] == 7.0 and r["gpu_mem"] == 1050
    assert [n for n, _ in r["top"]] == ["ffmpeg", "dwm", "lyra"]                  # sorted; the idle one is left out


def test_the_monitor_really_measures_this_machine():
    if psutil is None:
        pytest.skip("needs psutil")
    import record_diag as rd
    m = rd.Monitor().start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3.2:                                        # keep one core busy so there is something to see
        sum(i * i for i in range(20000))
    r = m.stop()
    assert r["cpu_avg"] is not None and r["ram_mb"] > 0 and r["top"], r


def test_the_report_shows_load_gpu_and_the_busiest_programs():
    import record_diag as rd
    pc = {"cpu_avg": 22.5, "cpu_max": 40.0, "ram_pct": 55.0, "ram_mb": 9000, "gpu_avg": 30.0, "gpu_max": 55, "enc_avg": 8.0, "gpu_mem": 1500,
          "top": [("ffmpeg", 6.1), ("dwm", 3.2)]}
    rep = rd.format_report({"ffmpeg": "7 1", "settings": "x", "screens": 1, "outputs": 1, "baseline": {}, "baseline_pc": {"cpu_avg": 3.0, "cpu_max": 5.0, "ram_pct": 50.0, "ram_mb": 8000},
                            "trials": [{"name": "E  real", "result": {"frame": 100, "capture_fps": 30.0, "dwm": {"fps": 59.0, "p95_ms": 18.0, "slow_pct": 1}, "pc": pc, "how": "whole screen (desktop duplication)"}}]})
    assert "CPU 22.5% average (max 40.0%), RAM 55.0% (9000 MB" in rep and "video encoder 8.0%" in rep
    assert "ffmpeg 6.1%, dwm 3.2%" in rep and "Graphics card: not measured (nvidia-smi not found)" in rep and "(Lyra said: whole screen" in rep


def test_a_trial_can_capture_at_60_and_write_30(monkeypatch):
    import record_diag as rd
    seen = []

    class P:
        returncode = 0
        def __init__(self, cmd, **kw): seen.append(cmd)
        def communicate(self, timeout=None): return b"", b"frame=180\nspeed=1.0x\n"

    monkeypatch.setattr(rd.subprocess, "Popen", P)
    monkeypatch.setattr(rd.time, "sleep", lambda s: None)
    monkeypatch.setattr(rd, "measure_dwm", lambda s: {})
    monkeypatch.setattr(rd.Monitor, "start", lambda self: self)
    monkeypatch.setattr(rd.Monitor, "stop", lambda self: {})
    plan = {"grab": "ddagrab", "verified": True, "env": {}, "input": [], "crop": None, "output_idx": 0}
    r = rd._trial("ffmpeg", {**sr.profile("medium", region=False), "fps": 60}, plan, 6, out_fps=30)
    cmd = seen[0]
    assert cmd[cmd.index("-vf") + 1].startswith("fps=30,") and "framerate=60" in cmd[cmd.index("-i") + 1] and cmd[-3:] == ["-f", "null", "-"]
    assert r["frame"] == 180 and r["capture_fps"] == 30.0


def test_the_real_recording_trial_is_deleted_afterwards(tmp_path, monkeypatch):
    import record_diag as rd
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: Proc(cmd[-1]))
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    monkeypatch.setattr(rd.time, "sleep", lambda s: None)
    monkeypatch.setattr(rd, "measure_dwm", lambda s: {"fps": 60.0, "p95_ms": 17.0, "slow_pct": 0})
    monkeypatch.setattr(rd.Monitor, "start", lambda self: self)
    monkeypatch.setattr(rd.Monitor, "stop", lambda self: {"cpu_avg": 5.0, "cpu_max": 9.0, "ram_pct": 40.0, "ram_mb": 7000, "top": []})
    r = rd._real_trial(8)
    assert r["dwm"]["fps"] == 60.0 and r["pc"]["cpu_avg"] == 5.0
    assert not [f for f in os.listdir(tmp_path) if f.endswith((".mp4", ".mkv"))]      # nothing was kept
    assert not sr._active()


# ── the stuck "old grabber" decision (found by the performance test: a REAL recording used gdigrab while the test's own
#    Desktop Duplication trial worked) ─────────────────────────────────────────────────────────────

def test_a_moment_when_the_fast_grabber_was_unavailable_is_not_remembered_for_good(monkeypatch):
    answers = iter([None, None, 0])                                   # unavailable twice, then it works again
    monkeypatch.setattr(sr, "_dd_output", lambda exe, region: next(answers))
    clock = [1000.0]
    monkeypatch.setattr(sr.time, "monotonic", lambda: clock[0])
    first = sr.plan_capture("ffmpeg", None)
    assert first["grab"] == "gdigrab"
    assert sr.plan_capture("ffmpeg", None)["grab"] == "gdigrab"       # within 3 minutes: no new test grab (the 2nd answer is unused)
    clock[0] += 181
    assert sr.plan_capture("ffmpeg", None)["grab"] == "gdigrab"       # asked again: the 2nd answer, still unavailable
    clock[0] += 181
    again = sr.plan_capture("ffmpeg", None)
    assert again["grab"] == "ddagrab" and again["output_idx"] == 0     # and as soon as it works, it is used


def test_the_old_grabbers_own_plan_is_still_kept_for_good(monkeypatch):
    monkeypatch.setattr(sr, "_dd_output", lambda exe, region: None)
    probes = []
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): probes.append(1) or (1920, 1080))
    clock = [0.0]
    monkeypatch.setattr(sr.time, "monotonic", lambda: clock[0])
    sr.plan_capture("ffmpeg", None)
    n_first = len(probes)                                             # (however many probes the old grabber's own plan needs)
    clock[0] += 500
    sr.plan_capture("ffmpeg", None)
    assert n_first >= 1 and len(probes) == n_first, (len(probes), n_first)             # re-deciding every 3 minutes does not repeat the 4-5 ffmpeg probes


def test_a_disk_cache_from_an_older_version_is_ignored(tmp_path, monkeypatch):
    f = tmp_path / "record_cache.json"
    monkeypatch.setattr(sr, "_cache_file", lambda: f)
    monkeypatch.setattr(sr, "_exe_stamp", lambda exe: [exe, 1, 1])
    desk = sr.desktop_rect()
    stale = {"exe": ["ffmpeg", 1, 1], "saved": time.time(), "plans": [{"desk": list(desk), "region": None, "mode": "auto",
             "plan": {"env": {}, "input": [], "crop": None, "verified": True, "grab": "gdigrab", "info": "old"}}]}
    f.write_text(json.dumps(stale))                                   # no "v": what version 1 wrote
    sr._plan_cache.clear()
    assert sr._load_cache("ffmpeg") is False and not sr._plan_cache
    sr._plan_cache[("ffmpeg", desk, None, "auto")] = {"env": {}, "input": [], "crop": None, "verified": True, "grab": "ddagrab", "output_idx": 0, "info": "x"}
    sr._save_cache("ffmpeg")
    assert json.loads(f.read_text())["v"] == sr.CACHE_VERSION
    sr._plan_cache.clear()
    assert sr._load_cache("ffmpeg") is True and sr._plan_cache


def test_the_zero_copy_test_encode_uses_the_same_command_shape_as_the_recording(monkeypatch):
    seen = []
    monkeypatch.setattr(sr.subprocess, "run", lambda cmd, **k: seen.append(cmd) or types.SimpleNamespace(returncode=0, stderr=b""))
    sr._zc_cache.clear()
    assert sr._probe_zero_copy("ffmpeg", 1, 60, "p3") is True
    c = seen[0]
    assert "-filter_complex" in c and "-i" not in c and c[c.index("-filter_complex") + 1].startswith("ddagrab=output_idx=1:framerate=60") and "h264_nvenc" in c


def test_raw_colours_go_to_nvenc_without_a_cpu_conversion():
    prof = {**sr.profile("medium", region=False), "gpu": "p3"}
    plan = {"grab": "ddagrab", "verified": True, "env": {}, "input": [], "crop": None, "output_idx": 0, "raw_rgb": True}
    cmd = sr.build_cmd("ffmpeg", "s.mkv", prof=prof, plan=plan)
    assert "-pix_fmt" not in cmd and "h264_nvenc" in cmd and "hwdownload" in cmd[cmd.index("-i") + 1] and cmd[-1] == "s.mkv"
    assert "ddagrab raw-rgb" in cmd[cmd.index("-metadata") + 1]
    assert "-pix_fmt" in sr.build_cmd("ffmpeg", "s.mkv", prof=prof, plan={**plan, "raw_rgb": False})
    assert "-pix_fmt" in sr.build_cmd("ffmpeg", "s.mkv", prof={**prof, "gpu": None}, plan=plan)    # the CPU encoder always converts


def test_start_uses_raw_colours_and_falls_back_to_the_cpu_conversion(monkeypatch):
    plan = {"env": {}, "input": [], "crop": None, "verified": True, "grab": "ddagrab", "output_idx": 0, "info": "screen 1 (desktop duplication)"}
    monkeypatch.setattr(sr, "plan_capture", lambda exe, region, grab=None: dict(plan))
    monkeypatch.setattr(sr, "_gpu_preset", lambda exe: "p3")
    monkeypatch.setattr(sr, "_probe_zero_copy", lambda *a: False)
    monkeypatch.setattr(sr, "_probe_nvenc_bgra", lambda *a: True)
    monkeypatch.setattr(sr, "_pref", lambda k, d: "auto" if k == "record_encoder" else d)
    cmds = []

    def popen(cmd, **kw):
        cmds.append(cmd)
        return Proc(cmd[-1], die_at_once=("-pix_fmt" not in cmd), stderr=b"Invalid argument\n")      # this "PC" only takes converted frames

    monkeypatch.setattr(sr.subprocess, "Popen", popen)
    monkeypatch.setattr(sr, "_run_low", _final_ok([]))
    s = sr.start()
    assert s["recording"] and not s["error"], s
    assert "-pix_fmt" not in cmds[0] and "-pix_fmt" in cmds[1] and sr._rgb_bad and not sr._gpu_bad     # only the colour step was given up
