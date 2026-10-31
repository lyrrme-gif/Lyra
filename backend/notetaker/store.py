"""Lyra notetaker - local transcript store: <data>/notetaker/sessions/<id>.json. TEXT ONLY, never audio.

Atomic writes (temp file + rename), retention purge, export. Everything stays on this computer."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


def root() -> Path:
    import paths
    d = paths.data_dir() / "notetaker" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def valid_id(sid: str) -> bool:
    return bool(isinstance(sid, str) and _ID_RE.match(sid))


def _path(sid: str) -> Path:
    if not valid_id(sid):
        raise ValueError("bad session id")
    return root() / f"{sid}.json"


def save(doc: dict) -> None:
    p = _path(doc["id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def get(sid: str) -> dict | None:
    try:
        return json.loads(_path(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def exists(sid: str) -> bool:
    try:
        return _path(sid).is_file()
    except ValueError:
        return False


def list_sessions() -> list[dict]:
    """Newest first, without the lines (a short preview instead)."""
    out = []
    for p in root().glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        lines = d.get("lines") or []
        out.append({
            "id": d.get("id"), "title": d.get("title", ""), "started": d.get("started", 0),
            "duration": d.get("duration", 0), "speakers": d.get("speakers", []),
            "has_summary": bool(d.get("summary")), "diarized": bool(d.get("diarized")),
            "preview": " ".join(l.get("text", "") for l in lines[:3])[:160], "lines": len(lines),
        })
    out.sort(key=lambda d: d["started"], reverse=True)
    return out


def update(sid: str, **fields) -> dict | None:
    d = get(sid)
    if d is None:
        return None
    d.update(fields)
    save(d)
    return d


def rename_speaker(sid: str, old: str, new: str) -> dict | None:
    new = (new or "").strip()[:40]
    d = get(sid)
    if d is None or not new or old not in (d.get("speakers") or []):
        return None
    for l in d["lines"]:
        if l.get("speaker") == old:
            l["speaker"] = new
    d["speakers"] = list(dict.fromkeys(new if s == old else s for s in d["speakers"]))
    save(d)
    return d


def delete(sid: str) -> bool:
    try:
        _path(sid).unlink()
        return True
    except (OSError, ValueError):
        return False


def delete_all() -> int:
    n = 0
    for p in list(root().glob("*.json")):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def purge_expired(days: int, now: float | None = None) -> int:
    """Delete transcripts that ended more than `days` days ago. days <= 0 keeps everything."""
    if not days or days <= 0:
        return 0
    cutoff = (now or time.time()) - days * 86400
    n = 0
    for p in list(root().glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if float(d.get("ended") or d.get("started") or 0) < cutoff:
                p.unlink()
                n += 1
        except (OSError, ValueError):
            continue
    return n


def mmss(t: float) -> str:
    t = int(max(0, t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def export_markdown(doc: dict) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(doc.get("started", 0)))
    out = [f"# {doc.get('title') or 'Meeting'}", f"*{when} · {mmss(doc.get('duration', 0))}*", ""]
    s = doc.get("summary")
    if s:
        out += ["## Summary", s.get("text", ""), ""]
        if s.get("todos"):
            out.append("## To-dos")
            out += [f"- [ ] {t.get('text', '')}" + (f" ({t['owner']})" if t.get("owner") else "") for t in s["todos"]]
            out.append("")
    out.append("## Transcript")
    out += [f"**{l.get('speaker', '')}** [{mmss(l.get('start', 0))}] {l.get('text', '')}" for l in doc.get("lines", [])]
    return "\n".join(out) + "\n"
