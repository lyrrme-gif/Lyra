"""Screen recording sound: WAV tracks kept in step with the clock, the ffmpeg mux, and the recorder's sound path.
The mux tests use a real ffmpeg (skipped when there is none)."""
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import types
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("ui", types.SimpleNamespace(log=__import__("logging").getLogger("t")))
import record_audio as ra  # noqa: E402
import screen_record as sr  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="no ffmpeg here")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def tone(seconds, rate=48000, ch=2, freq=440, amp=12000):
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", v) * ch
    return bytes(frames)


def wav_info(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate(), w.getnchannels(), w.getframerate()


def probe(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True)
    return r.stderr.decode("utf-8", "replace")


def audio_peak_db(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True)
    for line in r.stderr.decode("utf-8", "replace").splitlines():
        if "max_volume" in line:
            return float(line.split("max_volume:")[1].split("dB")[0])
    return None


# ── WavTrack ────────────────────────────────────────────────────────────────────────────────

def test_silence_gap_is_filled_so_sound_stays_in_step(tmp_path):
    clk = Clock()
    t = ra.WavTrack(tmp_path / "a.wav", 8000, 2, clk, t0=100.0)
    clk.t = 100.1
    t.push(tone(0.1, 8000))            # arrives at 0.1 s, covers 0.0-0.1 s
    clk.t = 103.1                      # the PC was silent for 3 s, then sound again
    t.push(tone(0.1, 8000))
    t.finish(5.0)
    secs, ch, rate = wav_info(tmp_path / "a.wav")
    assert (ch, rate) == (2, 8000)
    assert abs(secs - 5.0) < 0.01
    assert t.peak > 10000


def test_only_silence_means_peak_zero(tmp_path):
    clk = Clock()
    t = ra.WavTrack(tmp_path / "s.wav", 8000, 1, clk, t0=100.0)
    t.finish(2.0)
    assert wav_info(tmp_path / "s.wav")[0] == pytest.approx(2.0, abs=0.01) and t.peak == 0


def test_surround_is_folded_to_stereo_keeping_the_centre():
    # 6 channels: only the centre plays -> both stereo sides get it
    frame = struct.pack("<6h", 0, 0, 10000, 0, 0, 0)
    out = struct.unpack("<2h", ra.to_stereo(frame, 6))
    assert out[0] == out[1] and out[0] > 3000
    assert ra.to_stereo(b"\x01\x00\x02\x00", 2) == b"\x01\x00\x02\x00"


def test_modes():
    assert ra.wants("off") == (False, False)
    assert ra.wants("system") == (True, False)
    assert ra.wants("mic") == (False, True)
    assert ra.wants("both") == (True, True)
    assert ra.wants("nonsense") == (True, False)      # unknown saved value -> the default


def test_missing_library_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", None)       # import raises ImportError
    cap = ra.AudioCapture(True, False, tmp_path, "x")
    assert cap.start() is False and "PyAudioWPatch" in " ".join(cap.warnings)


# ── the mux, with a real ffmpeg ───────────────────────────────────────────────────────────

@pytest.fixture
def video(tmp_path):
    out = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                    "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


def make_wav(path, seconds, rate=48000, ch=2, freq=440, amp=12000):
    clk = Clock()
    t = ra.WavTrack(path, rate, ch, clk, t0=100.0)
    clk.t = 100.0 + seconds
    t.push(tone(seconds, rate, ch, freq, amp))
    t.finish(seconds)
    return path


@needs_ffmpeg
def test_mux_one_track_gives_video_plus_aac_and_ends_with_the_video(tmp_path, video):
    w = make_wav(tmp_path / "sys.wav", 3.0)                         # sound is shorter than the picture
    out = tmp_path / "out.mp4"
    subprocess.run(ra.build_mux_cmd(FFMPEG, str(video), [str(w)], str(out), 0.0), check=True, capture_output=True)
    info = probe(out)
    assert "Video: h264" in info and "Audio: aac" in info
    assert "Duration: 00:00:04" in info                             # not cut to 3 s
    assert audio_peak_db(out) > -30                                 # real sound, not silence


@needs_ffmpeg
def test_mux_two_tracks_are_mixed_and_different_rates_are_fine(tmp_path, video):
    a = make_wav(tmp_path / "sys.wav", 4.0, rate=48000, ch=2, freq=440)
    b = make_wav(tmp_path / "mic.wav", 4.0, rate=44100, ch=1, freq=880)
    out = tmp_path / "out.mp4"
    subprocess.run(ra.build_mux_cmd(FFMPEG, str(video), [str(a), str(b)], str(out), 0.3), check=True, capture_output=True)
    assert "Audio: aac" in probe(out) and audio_peak_db(out) > -30


# ── the recorder: sound path ───────────────────────────────────────────────────────────────

REAL_POPEN = subprocess.Popen


def popen_for(video_src):
    """Fake only the screen-grabbing ffmpeg (its command has gdigrab); the mux ffmpeg really runs."""
    def fake(cmd, **kw):
        return VideoProc(cmd[-1], str(video_src)) if "gdigrab" in cmd else REAL_POPEN(cmd, **kw)
    return fake


class FakeCap:
    def __init__(self, tracks, t0=0.0):
        self.tracks, self.t0, self.stopped = tracks, t0, False
        self.labels = ["PC sound"]
        self.warnings = []

    def stop(self):
        self.stopped = True
        return self.tracks


class VideoProc:
    """Stands in for the capturing ffmpeg: on stop it leaves a real tiny video at the output path."""
    def __init__(self, out, src):
        self.out, self.src, self.stdin, self.stderr, self.returncode = out, src, io.BytesIO(), io.BytesIO(), None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        shutil.copy(self.src, self.out)

    def terminate(self):
        self.returncode = 0


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(sr.sys, "platform", "win32")
    monkeypatch.setattr(sr, "out_dir", lambda: tmp_path / "rec")
    (tmp_path / "rec").mkdir()
    monkeypatch.setattr(sr, "_ffmpeg", lambda: FFMPEG or "ffmpeg")
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (1920, 1080))
    monkeypatch.setattr(sr, "_probe_ddagrab", lambda exe, idx=0: False)      # never try the Desktop Duplication path here
    monkeypatch.setattr(sr, "recover_async", lambda force=False: None)
    monkeypatch.setattr(sr, "_pref", lambda k, d: d)
    sr._plan_cache.clear()
    sr._plan_info = ""
    sr._proc, sr._path, sr._last, sr._error, sr._started = None, "", "", "", 0.0
    sr._audio, sr._audio_info, sr._audio_warn, sr._saving, sr._mux_thread = None, "", "", 0, None
    sr._segments, sr._paused, sr._paused_total, sr._stopping = [], False, 0.0, False
    sr._stopped.set()
    yield


@needs_ffmpeg
def test_recording_with_sound_ends_as_one_mp4_with_audio(tmp_path, monkeypatch, video):
    wav = make_wav(tmp_path / "sys.wav", 4.0)
    tracks = [{"role": "system", "path": str(wav), "peak": 12000, "seconds": 4.0}]
    monkeypatch.setattr(sr, "_open_sound", lambda mode, stem: (FakeCap(tracks), "PC sound", ""))
    monkeypatch.setattr(sr.subprocess, "Popen", popen_for(video))
    s = sr.start()
    assert s["recording"] and s["audio"] == "PC sound"
    # the picture is written to a work file, never to the final name, until the sound is in
    assert sr._vpath != sr._path and ".tmp" in sr._vpath
    s = sr.stop()
    assert s["saved"] and not s["saving"] and not s["error"]
    assert "Audio: aac" in probe(s["path"])
    names = os.listdir(tmp_path / "rec")
    assert [n for n in names if n.endswith(".mp4")] == [os.path.basename(s["path"])]
    assert os.listdir(tmp_path / "rec" / ".tmp") == []                # work files cleaned up
    assert not os.path.exists(wav)


@needs_ffmpeg
def test_if_the_mux_fails_the_picture_is_kept_and_it_says_so(tmp_path, monkeypatch, video):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    tracks = [{"role": "system", "path": str(bad), "peak": 0, "seconds": 4.0}]
    monkeypatch.setattr(sr, "_open_sound", lambda mode, stem: (FakeCap(tracks), "PC sound", ""))
    monkeypatch.setattr(sr.subprocess, "Popen", popen_for(video))
    sr.start()
    s = sr.stop()
    assert s["saved"] and os.path.getsize(s["path"]) > 0
    assert s["error"].startswith("Saved without sound")
    assert "Audio:" not in probe(s["path"])


def test_no_sound_source_still_records_the_picture_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_run_low", lambda cmd, timeout: (shutil.copy(cmd[cmd.index("-i") + 1], cmd[-1]), (True, ""))[1])
    monkeypatch.setattr(sr, "_open_sound", lambda mode, stem: (None, "", "sound needs the PyAudioWPatch package"))
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: types.SimpleNamespace(
        poll=lambda: None, stdin=io.BytesIO(), stderr=io.BytesIO(), returncode=None,
        wait=lambda timeout=None: open(cmd[-1], "wb").write(b"mp4data"), terminate=lambda: None))
    s = sr.start()
    assert s["recording"] and s["audio"] == "" and "PyAudioWPatch" in s["audio_warning"]
    assert sr._vpath != sr._path and ".tmp" in sr._vpath              # the picture goes to a crash-safe work file; the MP4 is made at the end
    reply = sr.try_command("stop recording")
    assert "saved" in reply.lower()


def test_voice_start_reply_tells_the_truth_about_sound(monkeypatch):
    monkeypatch.setattr(sr, "start", lambda: {"error": "", "audio": "PC sound + microphone", "audio_warning": ""})
    assert "PC sound + microphone" in sr.try_command("start recording")
    monkeypatch.setattr(sr, "start", lambda: {"error": "", "audio": "", "audio_warning": "sound needs the PyAudioWPatch package"})
    assert "without sound" in sr.try_command("start recording")
