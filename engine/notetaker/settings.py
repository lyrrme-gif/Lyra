"""Lyra notetaker - settings (namespaced p7_, stored by settings_store; safe defaults if it is missing)."""
from __future__ import annotations

DEFAULTS = {
    "p7_retention_days": 30,        # transcripts older than this are deleted; 0 = keep until deleted by hand
    "p7_diarize": True,             # tell remote speakers apart IF the optional model is downloaded
    "p7_summarize": False,          # OFF by default: sends transcript TEXT to the chosen provider
    "p7_summary_provider": "local", # local (Ollama on this PC) | key (the provider key set under AI)
    "p7_max_hours": 4,              # recording stops by itself after this long
}
PROVIDERS = ("local", "key")


def get(name: str):
    try:
        from engine.core import settings_store
        v = settings_store.get(name)
        return DEFAULTS[name] if v is None else v
    except Exception:
        return DEFAULTS[name]


def snapshot() -> dict:
    return {k: get(k) for k in DEFAULTS}


def update(changes: dict) -> dict:
    """Validate and save the known keys; raises ValueError naming a bad one."""
    clean = {}
    for k, v in (changes or {}).items():
        if k not in DEFAULTS:
            continue
        if k in ("p7_diarize", "p7_summarize"):
            if not isinstance(v, bool):
                raise ValueError(f"{k}: expected true or false")
            clean[k] = v
        elif k == "p7_summary_provider":
            if v not in PROVIDERS:
                raise ValueError(f"{k}: expected one of {', '.join(PROVIDERS)}")
            clean[k] = v
        else:
            lo, hi = (0, 3650) if k == "p7_retention_days" else (1, 12)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= int(v) <= hi:
                raise ValueError(f"{k}: expected a whole number from {lo} to {hi}")
            clean[k] = int(v)
    if clean:
        from engine.core import settings_store
        settings_store.set_many(clean)
    return snapshot()
