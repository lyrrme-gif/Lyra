"""Per-game volume presets.

  "set spotify to 20 when I play games"   -> saves a preset
  "list my game volumes" / "clear my game volumes"

When a game starts (proactive.py notices it), Lyra applies the presets to the apps that are making
sound at that moment, and tries again for a minute so a program that starts playing late (Spotify,
Discord) is caught too. Presets live in Lyra's data folder (game_volumes.json).
"""
from __future__ import annotations

import json
import re
import threading
import time


def _file():
    import paths
    return paths.data_path("game_volumes.json")


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    _file().write_text(json.dumps(d, indent=2), encoding="utf-8")


_SET = re.compile(r"^(?:set|lower|turn|put|make)\s+(?P<app>[a-z][\w .\-]{0,30}?)\s+(?:down\s+)?(?:to\s+|at\s+)?(?P<pct>\d{1,3})\s*(?:%|percent)?\s+"
                  r"(?:when|while|whenever)\s+(?:i\s+)?(?:play(?:ing)?|game|gaming|start\s+a\s+game)(?:\s+games?)?$"
                  r"|^when\s+(?:i\s+)?(?:play|game|gaming)(?:\s+games?)?\s*,?\s*(?:set|lower|turn|put)\s+(?P<app2>[a-z][\w .\-]{0,30}?)\s+(?:down\s+)?(?:to\s+|at\s+)?(?P<pct2>\d{1,3})\s*(?:%|percent)?$")
_LIST = re.compile(r"^(?:list|show|what\s+are)\s+(?:me\s+)?(?:my\s+)?game\s+volumes?(?:\s+presets?)?$")
_CLEAR = re.compile(r"^(?:clear|reset|delete)\s+(?:my\s+)?game\s+volumes?(?:\s+presets?)?$")


def try_game_volume(command: str) -> str | None:
    low = command.strip().lower().rstrip("?.! ")
    low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", low).strip()
    m = _SET.match(low)
    if m:
        app = (m.group("app") or m.group("app2")).strip()
        pct = max(0, min(100, int(m.group("pct") or m.group("pct2"))))
        d = _load()
        d[app] = pct
        _save(d)
        return f"Okay - whenever a game starts I'll set {app} to {pct} percent."
    if _LIST.match(low):
        d = _load()
        if not d:
            return "You have no game volume presets. Say 'set spotify to 20 when I play games'."
        return "When you play games: " + ", ".join(f"{a} {p} percent" for a, p in d.items()) + "."
    if _CLEAR.match(low):
        n = len(_load())
        _save({})
        return f"Cleared {n} game volume preset{'s' if n != 1 else ''}."
    return None


def apply_presets(game: str = "") -> str:
    """Apply the presets to every preset app that is making sound now; retry for ~60 s."""
    presets = _load()
    if not presets:
        return ""
    import app_volume
    done: dict[str, int] = {}
    end = time.time() + 60
    while time.time() < end:
        for app, pct in presets.items():
            if app in done:
                continue
            try:
                app_volume.set_app_by_name(app, pct / 100.0)
                done[app] = pct
            except Exception:
                continue
        if len(done) == len(presets):
            break
        time.sleep(10)
    if done:
        return "Game volumes: " + ", ".join(f"{a} {p}%" for a, p in done.items())
    return ""


def on_game_start(game: str, notify) -> None:
    def run():
        msg = apply_presets(game)
        if msg:
            notify(f"{game} started", msg)
    threading.Thread(target=run, name="lyra-game-volume", daemon=True).start()
