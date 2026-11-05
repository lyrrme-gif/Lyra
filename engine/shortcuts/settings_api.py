"""Lyra — shortcuts as seen by the Settings window (list / save / delete).

Works on the SAME ShortcutManager the voice matcher uses, so a change made in
Settings is live immediately and shortcuts.json keeps its format.
"""

import re

# action type -> (field, kind, lo, hi); field None = the action takes no value.
ACTION_FIELDS = {
    "open_app": ("target", "text"),
    "open_site": ("target", "text"),
    "open_url": ("url", "text"),
    "open_spotify": ("query", "text"),
    "open_folder": ("path", "text"),
    "set_volume": ("level", "int", 0, 100),
    "volume_up": ("steps", "int", 1, 20),
    "volume_down": ("steps", "int", 1, 20),
    "mute": (None, None),
    "lock_screen": (None, None),
    "take_screenshot": (None, None),
    "wait": ("seconds", "num", 0, 30),
}
MAX_ACTIONS = 20
MAX_TRIGGERS = 10


def _mgr():
    from engine.shortcuts import _manager
    return _manager


def list_all() -> dict:
    m = _mgr()
    items = [
        {"id": sid, "name": sc.get("name", sid), "triggers": list(sc.get("triggers", [])), "actions": list(sc.get("actions", []))}
        for sid, sc in m.shortcuts.items()
    ]
    return {"shortcuts": items, "types": {k: v[0] for k, v in ACTION_FIELDS.items()}}


def _clean_action(a) -> dict:
    if not isinstance(a, dict) or a.get("type") not in ACTION_FIELDS:
        raise ValueError("unknown action type")
    t = a["type"]
    spec = ACTION_FIELDS[t]
    out = {"type": t}
    field = spec[0]
    if field:
        raw = a.get(field)
        if spec[1] == "text":
            text = str(raw if raw is not None else "").strip()
            if not text or len(text) > 300:
                if t == "open_spotify":      # an empty query just opens Spotify
                    text = ""
                else:
                    raise ValueError(f"{t} needs a value (max 300 characters)")
            out[field] = text
        else:
            try:
                num = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{t} needs a number") from None
            num = max(spec[2], min(spec[3], num))
            out[field] = int(round(num)) if spec[1] == "int" else round(num, 1)
    if t == "open_site" and isinstance(a.get("query"), str) and a["query"].strip():
        out["query"] = a["query"].strip()[:80]
    return out


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "shortcut"


def save(data: dict) -> dict:
    """Create (no id) or update (id given) a shortcut. Raises ValueError with a readable message."""
    m = _mgr()
    name = str(data.get("name") or "").strip()
    if not name or len(name) > 60:
        raise ValueError("give the shortcut a name (max 60 characters)")

    triggers, seen = [], set()
    for t in data.get("triggers") or []:
        t = str(t).strip().lower()
        if t and t not in seen:
            seen.add(t)
            triggers.append(t[:80])
    if not triggers or len(triggers) > MAX_TRIGGERS:
        raise ValueError(f"add 1 to {MAX_TRIGGERS} trigger phrases")

    actions = data.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
        raise ValueError(f"add 1 to {MAX_ACTIONS} actions")
    clean = [_clean_action(a) for a in actions]

    sid = str(data.get("id") or "").strip()
    if sid and sid not in m.shortcuts:
        raise ValueError("that shortcut no longer exists")
    if not sid:
        base, sid, n = _slug(name), "", 1
        sid = base
        while sid in m.shortcuts:
            n += 1
            sid = f"{base}_{n}"

    for other_id, other in m.shortcuts.items():  # a phrase can only start one shortcut
        if other_id == sid:
            continue
        clash = {str(t).strip().lower() for t in other.get("triggers", [])} & set(triggers)
        if clash:
            raise ValueError(f"“{sorted(clash)[0]}” already triggers “{other.get('name', other_id)}”")

    m.add_shortcut(sid, name, triggers, clean)
    return {"id": sid, "name": name, "triggers": triggers, "actions": clean}


def delete(sid: str) -> bool:
    return _mgr().remove_shortcut(str(sid))
