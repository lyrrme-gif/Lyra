"""Lyra notetaker - cut a track into speech segments by loudness (numpy only)."""
from __future__ import annotations

import numpy as np

from .audio import SR


def segments(audio: np.ndarray, *, frame_s=0.03, min_gap_s=0.7, min_len_s=0.35, max_len_s=28.0,
             pad_s=0.15) -> list[tuple[float, float]]:
    """-> [(start_s, end_s)] of speech. The threshold adapts to the track's own noise floor, so a quiet
    mic and a loud system mix both work. Long stretches are cut at their quietest point."""
    n = int(frame_s * SR)
    if audio.size < n:
        return []
    frames = audio[: audio.size // n * n].reshape(-1, n)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    if rms.max() < 0.003:                                   # digital silence / nothing playing
        return []
    floor = float(np.percentile(rms, 20))
    top = float(rms.max())
    thr = max(0.004, min(floor * 3.0, top * 0.25), top * 0.06)     # cap: continuous speech must not raise the bar
    active = rms > thr
    out: list[list[int]] = []
    start = None
    for i, a in enumerate(active):
        if a and start is None:
            start = i
        elif not a and start is not None:
            out.append([start, i])
            start = None
    if start is not None:
        out.append([start, len(active)])
    gap = int(min_gap_s / frame_s)
    merged: list[list[int]] = []
    for s, e in out:
        if merged and s - merged[-1][1] <= gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    res: list[tuple[float, float]] = []
    total = len(audio) / SR
    maxf = int(max_len_s / frame_s)
    for s, e in merged:
        if (e - s) * frame_s < min_len_s:
            continue
        while e - s > maxf:                                 # cut at the quietest frame in the last 4 s
            lo = s + maxf - int(4.0 / frame_s)
            cut = lo + int(np.argmin(rms[lo:s + maxf]))
            cut = max(cut, s + 1)
            res.append((max(0.0, s * frame_s - pad_s), min(total, cut * frame_s)))
            s = cut
        res.append((max(0.0, s * frame_s - pad_s), min(total, e * frame_s + pad_s)))
    return res
