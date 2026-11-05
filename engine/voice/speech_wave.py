"""Lyra — loudness envelope of her own voice, for the UI waveform.

While Lyra speaks, the island shows a row of bars that move with her voice.
This module turns the synthesized speech (an mp3 from edge-tts) into a list of
loudness values, one every 1/hz seconds, each 0..1. voice.py sends that list to
the UI once, when playback starts; the UI plays it back on its own clock, so the
bars stay in sync even though the UI only polls for events a few times a second.

Everything here is best-effort: if numpy is missing or the audio can't be
decoded, `compute_envelope` returns [] and the UI falls back to a simulated
speech-like wave. It must never break speaking.
"""

from __future__ import annotations

ENVELOPE_HZ = 25  # loudness samples per second


def levels_from_samples(samples, sample_rate: int, hz: int = ENVELOPE_HZ) -> list[float]:
    """Pure function: raw PCM samples -> list of 0..1 loudness values.

    `samples` is a numpy array, mono (n,) or multi-channel (n, ch)."""
    import numpy as np

    x = np.asarray(samples, dtype=np.float32)
    if x.ndim == 2:
        x = x.mean(axis=1)
    win = max(1, int(sample_rate / hz))
    n = len(x) // win
    if n == 0:
        return []
    rms = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1))
    ref = float(np.percentile(rms, 95))
    if ref <= 1e-6:
        return [0.0] * n  # silence
    lv = np.clip(rms / ref, 0.0, 1.0) ** 0.8          # slightly perceptual
    if n >= 3:                                         # tiny smoothing, keeps syllable shape
        lv = np.convolve(lv, np.array([0.25, 0.5, 0.25], dtype=np.float32), mode="same")
    return [round(float(v), 3) for v in lv]


def compute_envelope(path: str, hz: int = ENVELOPE_HZ) -> list[float]:
    """Decode the audio file at `path` and return its loudness envelope.
    Returns [] on any failure (UI then simulates the wave)."""
    try:
        import pygame
        import pygame.sndarray

        init = pygame.mixer.get_init()
        if not init:
            return []
        sound = pygame.mixer.Sound(path)               # decodes the whole file
        samples = pygame.sndarray.array(sound)         # int16, (n,) or (n, channels)
        return levels_from_samples(samples, init[0], hz)
    except Exception:
        return []
