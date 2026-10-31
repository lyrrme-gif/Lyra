"""Lyra — the sound of a screen recording.

ffmpeg's gdigrab only grabs pictures, so the sound is captured here, next to it:
  * PC sound  = WASAPI loopback (what the speakers/headphones play), through PyAudioWPatch;
  * microphone = the saved input device (Settings -> audio_input) or the Windows default.
Each source is written to its own temporary WAV (native rate, mono or stereo). WASAPI loopback delivers nothing
while the PC is silent, so gaps are filled with silence to keep the sound in step with the clock. When the
recording stops, screen_record muxes the WAVs into the MP4 with ffmpeg (video is copied, sound becomes AAC).

Pause / resume: while paused the streams stay open (re-opening a WASAPI device can take a second) but their data
is dropped, and the clock the gap-filling uses is moved forward by the length of the pause, so the sound of a
paused recording is one continuous track with no hole and no silence where the pause was.

Nothing here touches the video part; a missing library / device only means "no sound", with the reason returned
so Lyra can say it instead of pretending.
"""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np

MODES = ("off", "system", "mic", "both")
DEFAULT_MODE = "system"
# ffmpeg needs a moment to open the screen grabber after it is launched; the sound started just before it,
# so the first part of the sound is cut by (launch gap + this guess) to line up with the first video frame.
VIDEO_START_GUESS = 0.35
# Audio buffer size in frames. 4096 frames (~85 ms) wakes Python about 12 times a second per source instead of
# 47 times with the old 1024: the sound is the same, the recorder just sleeps more.
BUFFER_FRAMES = 4096


def wants(mode: str) -> tuple[bool, bool]:
    """(PC sound, microphone) for a saved mode."""
    mode = mode if mode in MODES else DEFAULT_MODE
    return mode in ("system", "both"), mode in ("mic", "both")


def to_stereo(raw: bytes, channels: int) -> bytes:
    """int16 interleaved audio with any channel count -> mono / stereo int16 bytes (surround is folded down)."""
    if channels <= 2:
        return raw
    a = np.frombuffer(raw, dtype=np.int16)
    a = a[: len(a) // channels * channels].reshape(-1, channels).astype(np.float32)
    if channels >= 6:                                   # FL FR FC LFE BL BR (SL SR): keep the dialogue (centre) and the sides
        side_l = a[:, 4] * 0.707 if channels > 4 else 0
        side_r = a[:, 5] * 0.707 if channels > 5 else 0
        left = a[:, 0] + a[:, 2] * 0.707 + side_l
        right = a[:, 1] + a[:, 2] * 0.707 + side_r
        out = np.stack([left, right], axis=1) * 0.6
    else:
        out = a[:, :2]
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


class WavTrack:
    """One source written to a WAV, kept in step with the wall clock (gaps become silence)."""

    def __init__(self, path: Path, rate: int, channels: int, clock=time.monotonic, t0: float | None = None):
        self.path, self.rate, self.ch = Path(path), int(rate), max(1, int(channels))
        self._clock = clock
        self._t0 = clock() if t0 is None else t0
        self._w = wave.open(str(self.path), "wb")
        self._w.setnchannels(self.ch)
        self._w.setsampwidth(2)
        self._w.setframerate(self.rate)
        self._n = 0                                     # frames written so far
        self._lock = threading.Lock()
        self.peak = 0                                   # loudest sample seen (0 = only silence arrived)
        self._paused_at: float | None = None            # clock value when the current pause began
        self._hold_until = 0.0                          # data arriving before this clock value is dropped (resume delay)

    @property
    def seconds(self) -> float:
        return self._n / self.rate

    def _silence(self, frames: int) -> None:
        block = bytes(self.rate * self.ch * 2)          # one second per write: a long gap never builds a huge buffer
        while frames > 0:
            n = min(frames, self.rate)
            self._w.writeframes(block[: n * self.ch * 2])
            self._n += n
            frames -= n

    def pause(self) -> None:
        """Stop writing. The data keeps arriving but is dropped; the pause is cut out of the timeline."""
        with self._lock:
            if self._w is not None and self._paused_at is None:
                self._paused_at = self._clock()

    def resume(self, delay: float = 0.0) -> None:
        """Carry on after pause(). The clock origin moves forward by the pause, so no silence is inserted for it.
        `delay` also drops the first part of what arrives (the screen grabber needs a moment to deliver its first
        picture; this keeps the sound lined up with it, like the lead trim at the start of a recording)."""
        with self._lock:
            if self._paused_at is None:
                return
            now = self._clock()
            self._t0 += (now - self._paused_at) + max(0.0, delay)
            self._hold_until = now + max(0.0, delay)
            self._paused_at = None

    def push(self, raw: bytes) -> None:
        frames = len(raw) // (2 * self.ch)
        if not frames:
            return
        raw = raw[: frames * 2 * self.ch]
        with self._lock:
            if self._w is None or self._paused_at is not None:
                return
            if self._hold_until and self._clock() < self._hold_until:
                return
            gap = int((self._clock() - self._t0) * self.rate) - frames - self._n
            if gap > self.rate // 20:                   # more than 50 ms missing: the PC was silent, fill it
                self._silence(gap)
            self._w.writeframes(raw)
            self._n += frames
            a = np.frombuffer(raw, dtype=np.int16)
            if len(a):                                  # two reductions, no copy (this runs for every audio buffer)
                self.peak = max(self.peak, int(a.max()), -int(a.min()))

    def finish(self, total_seconds: float) -> None:
        """Pad with silence to `total_seconds` (all tracks end together) and close."""
        with self._lock:
            if self._w is None:
                return
            self._paused_at = None
            missing = int(total_seconds * self.rate) - self._n
            if missing > 0:
                self._silence(missing)
            self._w.close()
            self._w = None


def _pick_mic(pa, name: str):
    """The microphone device info: the saved name (part of it is enough), else the Windows default."""
    want = (name or "").strip().lower()
    if want and not want.startswith("default"):
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d.get("maxInputChannels", 0) > 0 and not d.get("isLoopbackDevice") and want in str(d.get("name", "")).lower():
                return d
    return pa.get_default_input_device_info()


class AudioCapture:
    """Opens the wanted sources, writes them to WAVs in `folder`, and hands the finished tracks back on stop()."""

    def __init__(self, system: bool, mic: bool, folder: Path, stem: str, mic_name: str = "", clock=time.monotonic):
        self.system, self.mic, self.folder, self.stem, self.mic_name = system, mic, Path(folder), stem, mic_name
        self._clock = clock
        self.t0 = clock()
        self._pa = None
        self._streams: list = []
        self.tracks: list[dict] = []                    # [{role, path, track}]
        self.warnings: list[str] = []
        self._paused_total = 0.0                        # seconds spent paused (finished pauses)
        self._pause_began: float | None = None

    @property
    def paused(self) -> bool:
        return self._pause_began is not None

    def active_seconds(self) -> float:
        """Recorded time: the wall clock since start, minus every pause."""
        now = self._clock()
        open_pause = (now - self._pause_began) if self._pause_began is not None else 0.0
        return max(0.0, (now - self.t0) - self._paused_total - open_pause)

    def pause(self) -> None:
        if self._pause_began is None:
            self._pause_began = self._clock()
            for t in self.tracks:
                t["track"].pause()

    def resume(self, delay: float = 0.0) -> None:
        """Start listening again. `delay` = how long the screen grabber needs for its first picture."""
        if self._pause_began is None:
            return
        self._paused_total += (self._clock() - self._pause_began) + max(0.0, delay)
        self._pause_began = None
        for t in self.tracks:
            t["track"].resume(delay)

    @property
    def labels(self) -> list[str]:
        return ["PC sound" if t["role"] == "system" else "microphone" for t in self.tracks]

    def start(self) -> bool:
        """True when at least one source is recording. Reasons for the others go to self.warnings."""
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.warnings.append("sound needs the PyAudioWPatch package (pip install PyAudioWPatch)")
            return False
        try:
            self._pa = pyaudio.PyAudio()
        except Exception as e:  # noqa: BLE001
            self.warnings.append(f"the audio system could not be opened ({str(e)[:80]})")
            return False
        self.folder.mkdir(parents=True, exist_ok=True)
        self.t0 = self._clock()
        if self.system:
            self._open("system", pyaudio, self._loopback_device)
        if self.mic:
            self._open("mic", pyaudio, lambda pa: _pick_mic(pa, self.mic_name))
        if not self.tracks:
            self._close_pa()
            return False
        return True

    def _loopback_device(self, pa):
        try:
            return pa.get_default_wasapi_loopback()
        except Exception:  # noqa: BLE001
            dev = next(iter(pa.get_loopback_device_info_generator()), None)
            if not dev:
                raise RuntimeError("no playback device to capture")
            return dev

    def _open(self, role: str, pyaudio, pick) -> None:
        label = "PC sound" if role == "system" else "microphone"
        track = None
        try:
            dev = pick(self._pa)
            rate, ch_in = int(dev["defaultSampleRate"]), max(1, int(dev["maxInputChannels"]))
            ch_out = min(ch_in, 2)
            track = WavTrack(self.folder / f"{self.stem}-{role}.wav", rate, ch_out, self._clock, self.t0)

            def cb(in_data, frame_count, time_info, status, _t=track, _c=ch_in):
                try:
                    _t.push(to_stereo(in_data, _c))
                except Exception:  # noqa: BLE001 - never raise inside the audio thread
                    pass
                return (None, pyaudio.paContinue)

            stream = self._pa.open(format=pyaudio.paInt16, channels=ch_in, rate=rate, input=True,
                                   input_device_index=int(dev["index"]), frames_per_buffer=BUFFER_FRAMES, stream_callback=cb)
            self._streams.append(stream)
            self.tracks.append({"role": role, "path": str(track.path), "track": track})
        except Exception as e:  # noqa: BLE001
            if track is not None:
                track.finish(0)
                try:
                    track.path.unlink()
                except OSError:
                    pass
            self.warnings.append(f"the {label} could not be opened ({str(e)[:80]})")

    def _close_pa(self) -> None:
        for s in self._streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:  # noqa: BLE001
                pass
        self._streams = []
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._pa = None

    def stop(self) -> list[dict]:
        """Stop listening, pad every track to the same length, return [{role, path, peak, seconds}]."""
        self._close_pa()
        total = self.active_seconds()
        out = []
        for t in self.tracks:
            t["track"].finish(total)
            out.append({"role": t["role"], "path": t["path"], "peak": t["track"].peak, "seconds": round(total, 1)})
        return out


def write_concat_list(list_file: str | Path, segments: list[str]) -> None:
    """The text file ffmpeg's concat demuxer reads: one `file '...'` line per picture segment (forward slashes, quotes escaped)."""
    lines = []
    for seg in segments:
        p = Path(seg).as_posix().replace("'", "'\\''")
        lines.append(f"file '{p}'")
    Path(list_file).write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_final_cmd(ffmpeg: str, segments: list[str], tracks: list[str], out: str, trim: float = 0.0, duration: float = 0.0,
                    list_file: str | Path | None = None, shorten: bool = False, comment: str = "") -> list[str]:
    """ffmpeg command that makes the finished file: the picture segments are joined and COPIED (never re-encoded), the
    WAV tracks (if any) are mixed into one AAC stereo track, and an .mp4 gets `+faststart`.

    segments   one file normally; after a pause there is one per stretch of recording (joined with the concat demuxer,
               which needs `list_file`, see write_concat_list()).
    trim       seconds cut off the start of every sound track (the sound began before the first video frame).
    duration   length of the picture in seconds: the output is cut there with -t. 0 = unknown (a recording that was
               cut off by a crash has no length written): the file then ends with the longest input, unless
               `shorten=True`, which ends it with the shortest (`-shortest`; right for a recovered recording, whose
               sound tracks are padded to the full length, so the file ends with the picture).
               (apad + -shortest was used once and never ends on ffmpeg 7.x: the mix ran forever at 12 % CPU and the
               recording never reached the folder. The tracks are already padded to the recording length, so nothing
               infinite is generated here.)
    """
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if len(segments) > 1:
        if list_file is None:
            raise ValueError("several picture segments need a concat list file")
        cmd += ["-f", "concat", "-safe", "0", "-i", str(list_file)]
    else:
        cmd += ["-i", str(segments[0])]
    for t in tracks:
        cmd += ["-ss", f"{max(0.0, trim):.3f}", "-i", str(t)]
    if not tracks:
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    else:
        conv = "aresample=48000,aformat=channel_layouts=stereo"
        pad = f",apad=whole_dur={duration:.3f}" if duration and duration > 0 else ""     # finite padding: ends at the video length
        if len(tracks) == 1:
            graph = f"[1:a]{conv}{pad}[a]"
        else:
            graph = (f"[1:a]{conv}[a1];[2:a]{conv}[a2];"
                     f"[a1][a2]amix=inputs=2:duration=longest,volume=2,alimiter=limit=0.95{pad}[a]")
        cmd += ["-filter_complex", graph, "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    if duration and duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    elif tracks and shorten:
        cmd += ["-shortest"]
    if comment:
        cmd += ["-metadata", f"comment={comment}"]                    # how it was recorded (shows in the file's Details)
    if str(out).lower().endswith((".mp4", ".mov", ".m4v")):
        cmd += ["-movflags", "+faststart"]
    cmd += [str(out)]
    return cmd


def build_mux_cmd(ffmpeg: str, video: str, tracks: list[str], out: str, trim: float = 0.0, duration: float = 0.0) -> list[str]:
    """One picture file + sound tracks -> one file (the single-segment case of build_final_cmd)."""
    return build_final_cmd(ffmpeg, [video], tracks, out, trim, duration)
