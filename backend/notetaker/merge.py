"""Lyra notetaker - turn per-channel segments into one readable transcript."""
from __future__ import annotations

import difflib
import re


def _norm(t: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", t.lower()))


def assign_speakers(segs: list[dict], turns: list[tuple[float, float, int]]) -> list[dict]:
    """Give each system segment the diarization speaker that talks most during it (sets 'spk')."""
    for s in segs:
        best, best_ov = None, 0.0
        for a, b, spk in turns:
            ov = min(s["end"], b) - max(s["start"], a)
            if ov > best_ov:
                best, best_ov = spk, ov
        s["spk"] = best
    return segs


def drop_echo(mic: list[dict], system: list[dict], min_ratio: float = 0.72) -> list[dict]:
    """Without headphones the mic hears the speakers: drop mic lines that repeat an overlapping system line."""
    keep = []
    for m in mic:
        mt = _norm(m["text"])
        echo = False
        for s in system:
            ov = min(m["end"], s["end"]) - max(m["start"], s["start"])
            if ov <= 0.4 * max(0.01, m["end"] - m["start"]):
                continue
            st = _norm(s["text"])
            if mt and st and (difflib.SequenceMatcher(None, mt, st).ratio() >= min_ratio
                              or (len(mt) >= 12 and mt in st)):
                echo = True
                break
        if not echo:
            keep.append(m)
    return keep


def build_lines(mic: list[dict], system: list[dict], *, diarized: bool, you: str = "You") -> tuple[list[dict], list[str]]:
    """-> (lines sorted by time, speaker names in order of appearance). Neighbouring lines by the same
    speaker with a short pause are joined."""
    rows = []
    mic = drop_echo(mic, system)
    order: dict[int, int] = {}
    for s in sorted(system, key=lambda x: x["start"]):
        if diarized and s.get("spk") is not None:
            order.setdefault(s["spk"], len(order) + 1)
    for m in mic:
        rows.append({"start": m["start"], "end": m["end"], "speaker": you, "text": m["text"].strip()})
    for s in system:
        name = (f"Speaker {order[s['spk']]}" if diarized and s.get("spk") is not None else "Remote")
        rows.append({"start": s["start"], "end": s["end"], "speaker": name, "text": s["text"].strip()})
    rows = [r for r in rows if r["text"]]
    rows.sort(key=lambda r: (r["start"], r["end"]))
    out: list[dict] = []
    for r in rows:
        if out and out[-1]["speaker"] == r["speaker"] and r["start"] - out[-1]["end"] < 2.0:
            out[-1]["end"] = max(out[-1]["end"], r["end"])
            out[-1]["text"] += " " + r["text"]
        else:
            out.append(dict(r))
    speakers = list(dict.fromkeys(r["speaker"] for r in out))
    for r in out:
        r["start"], r["end"] = round(r["start"], 2), round(r["end"], 2)
    return out, speakers
