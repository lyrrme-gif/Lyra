"""Lyra - benchmark Whisper vs Parakeet: latency and WER on short clips. Run it ON A REAL MACHINE:

    cd backend
    python -m stt_engines.bench --synth            # makes clips with Windows SAPI (pip install pyttsx3)
    python -m stt_engines.bench --dir my_clips     # my_clips/a.wav + a.txt (reference text), 16 kHz mono wav
Options: --engines whisper,parakeet  --language en  --runs 3

Needs the Parakeet model downloaded (Settings > Voice) and the Whisper model present. Clips are
synthesised/read in memory and temp files are deleted; nothing is uploaded. Synthetic speech is easy
for ASR, so treat WER as a sanity check and use --dir with real recordings for a fair comparison.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

SENTENCES = [
    "open spotify and play some music",
    "set a timer for ten minutes",
    "what is the weather like tomorrow in london",
    "turn the volume down a little please",
    "send a message to anna saying I will be late",
    "take a screenshot and save it to the desktop",
]


def _norm(text: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:'[^\W_]+)?", text.lower())


def wer(ref: str, hyp: str) -> float:
    """Word error rate (punctuation and case ignored). 0.0 = perfect; can exceed 1.0."""
    r, h = _norm(ref), _norm(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: need 16 kHz mono 16-bit wav")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def synth_clips() -> list[tuple[str, np.ndarray, str]]:
    import pyttsx3                                           # optional; Windows SAPI5
    out = []
    eng = pyttsx3.init()
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(SENTENCES):
            p = Path(tmp) / f"{i}.wav"
            eng.save_to_file(s, str(p))
            eng.runAndWait()
            raw = wave.open(str(p), "rb")
            sr, n = raw.getframerate(), raw.getnframes()
            a = np.frombuffer(raw.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
            raw.close()
            if sr != 16000:                                  # plain linear resample, enough for a benchmark
                a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * 16000 / sr)), np.arange(len(a)), a).astype(np.float32)
            out.append((f"synth{i}", a, s))
    return out


def dir_clips(folder: Path) -> list[tuple[str, np.ndarray, str]]:
    out = []
    for w in sorted(folder.glob("*.wav")):
        t = w.with_suffix(".txt")
        if t.is_file():
            out.append((w.stem, read_wav(w), t.read_text(encoding="utf-8").strip()))
    return out


def run(clips, engines, language, runs, transcribe=None) -> dict:
    """-> {engine: {median_ms, p95_ms, wer, rtf, errors}}. `transcribe` is injectable for tests."""
    if transcribe is None:
        import stt_engines
        transcribe = stt_engines.transcribe
    report = {}
    for eng in engines:
        transcribe(clips[0][1], language=language, engine=eng)                 # warm-up, not timed
        times, wers, errors, audio_s = [], [], 0, 0.0
        for _name, audio, ref in clips:
            for _ in range(runs):
                t0 = time.perf_counter()
                r = transcribe(audio, language=language, engine=eng)
                times.append((time.perf_counter() - t0) * 1000)
                if r.get("error") or r.get("engine") != eng:
                    errors += 1
            wers.append(wer(ref, r.get("text", "")))
            audio_s += len(audio) / 16000
        times.sort()
        report[eng] = {
            "median_ms": round(statistics.median(times), 1),
            "p95_ms": round(times[min(len(times) - 1, int(len(times) * 0.95))], 1),
            "wer": round(sum(wers) / len(wers), 4),
            "rtf": round((sum(times) / 1000 / runs) / audio_s, 3),             # <1 = faster than real time
            "errors": errors,                                                  # engine fell back / failed
        }
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synth", action="store_true")
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--engines", default="whisper,parakeet")
    ap.add_argument("--language", default="en")
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args(argv)
    clips = dir_clips(a.dir) if a.dir else synth_clips() if a.synth else []
    if not clips:
        print("No clips. Use --synth (pyttsx3) or --dir <folder with .wav + .txt>.")
        return 2
    rep = run(clips, [e.strip() for e in a.engines.split(",")], a.language, a.runs)
    print(f"{'engine':10} {'median ms':>10} {'p95 ms':>8} {'WER':>7} {'RTF':>6} {'fallbacks':>9}")
    for e, r in rep.items():
        print(f"{e:10} {r['median_ms']:>10} {r['p95_ms']:>8} {r['wer']:>7.2%} {r['rtf']:>6} {r['errors']:>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
