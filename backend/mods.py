"""Lyra — mods (modes).

A mod is a mode Lyra can be in: Gaming, Chill, Study, Angry... Each one has

  * a colour        (tints Mochi and the pill in the island),
  * actions         (apps / sites / music opened when the mod is switched on),
  * a personality   (extra instructions added to her system prompt while it's on),
  * a spoken line   (what she says when it switches on; generated in character
                     by the LLM, with a fixed fallback if the LLM is unavailable).

Only one mod is active at a time; with none active she is her normal self.

The slots (up to 4) and their names live in llm_store (llm_keys.json), so the
Settings window can rename them. What each slot DOES lives in mods.json next to
this file. It is created on first run with the defaults below; edit it freely
(apps, sites, music, personality text). Behaviour is tied to the slot id
(mod_1..mod_4), so renaming a slot doesn't change what it does.

Action steps use the same tool vocabulary as the agent (agent.py):
  {"tool": "open_app",     "args": {"app_name": "discord"}}
  {"tool": "open_site",    "args": {"site_name": "google"}}
  {"tool": "open_spotify", "args": {"query": "chill lofi beats"}}   # searches + plays
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import ui

log = ui.log

SPEC_PATH = Path(__file__).resolve().parent / "mods.json"

# Names the slots had before personalities existed; migrated to the new defaults.
_LEGACY_NAMES = ["gaming", "focus", "work", "chill"]

DEFAULT_SPECS: dict[str, dict[str, Any]] = {
    "mod_1": {
        "name": "Gaming",
        "color": "#22C55E",
        "actions": [
            {"tool": "open_app", "args": {"app_name": "spotify"}},
            {"tool": "open_app", "args": {"app_name": "discord"}},
        ],
        "persona": (
            "You are the user's hyped gaming buddy. Upbeat, quick and a little cocky-playful; "
            "light gamer slang, never cringe. Proactively bring up gaming stuff when it fits: "
            "game suggestions for their mood, co-op or multiplayer ideas, tips and builds, news or "
            "patches worth checking, and a quick reminder to hydrate or stretch if they've been "
            "playing a while. Keep replies short and energetic."
        ),
        "announce": (
            "Spotify and Discord are opening. Hype them up and include ONE specific, fun gaming "
            "suggestion (a game, a co-op idea, or a quick tip)."
        ),
        "greeting": "Gaming mode on. Spotify and Discord are up. What are we playing?",
        "off": "Gaming mode off. Back to normal.",
    },
    "mod_2": {
        "name": "Chill",
        "color": "#3B82F6",
        "actions": [
            {"tool": "open_spotify", "args": {"query": "chill lofi beats"}},
        ],
        "persona": (
            "You are calm, warm and unhurried, like a friend on a couch. Soft, relaxed phrasing, "
            "short sentences, no pressure and no urgency. Gently suggest taking it easy: a break, "
            "some music, a walk, a snack. Never lecture, never rush."
        ),
        "announce": "You're putting on some chill music. Say it in a calm, laid-back way.",
        "greeting": "Chill mode. Putting on some easy music. Take it slow.",
        "off": "Chill mode off. Back to normal.",
    },
    "mod_3": {
        "name": "Study",
        "color": "#F59E0B",
        "actions": [
            {"tool": "open_site", "args": {"site_name": "google"}},
            {"tool": "open_app", "args": {"app_name": "spotify"}},
        ],
        "persona": (
            "You are serious, focused and efficient. No jokes, no small talk, no tangents. Be "
            "concise and clear: explain things step by step, offer a study plan or a timer "
            "(for example 25-minute focus blocks) when useful, and gently steer the user back to "
            "the task if they drift. Calm authority."
        ),
        "announce": (
            "Google and Spotify are open. Be crisp and businesslike, and ask what they're studying."
        ),
        "greeting": "Study mode. Google and Spotify are open. What are we working on?",
        "off": "Study mode off. Back to normal.",
    },
    "mod_4": {
        "name": "Angry",
        "color": "#EF4444",
        "actions": [],
        "persona": (
            "You are stern, irritated and blunt. Short, sharp sentences. No pleasantries, no "
            "emojis, no softening. Sound like a strict, fed-up drill instructor who is annoyed at "
            "wasted time, procrastination and sloppiness. You still do exactly what is asked and "
            "give correct, genuinely useful answers: the anger is attitude, not sabotage. Never "
            "insult the user personally, no slurs, no profanity."
        ),
        "announce": (
            "You're now in a bad mood. Be curt and irritated, tell them to stop wasting time and "
            "say what they need."
        ),
        "greeting": "Angry mode. Stop wasting time. What do you need?",
        "off": "Fine. Back to normal.",
    },
}

FALLBACK_COLORS = ["#22C55E", "#3B82F6", "#F59E0B", "#EF4444"]

_lock = threading.RLock()
_active_id: str | None = None


# ── Specs (mods.json) ─────────────────────────────────────────────────────────

def _ensure_spec_file() -> None:
    if SPEC_PATH.exists():
        return
    try:
        SPEC_PATH.write_text(json.dumps(DEFAULT_SPECS, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning("mods: could not write %s: %s", SPEC_PATH.name, e)


def _specs() -> dict[str, dict[str, Any]]:
    """Defaults merged with the user's mods.json (user values win)."""
    _ensure_spec_file()
    user: dict[str, Any] = {}
    try:
        raw = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            user = raw
    except Exception as e:
        log.warning("mods: bad %s, using defaults: %s", SPEC_PATH.name, e)
    out: dict[str, dict[str, Any]] = {}
    for mid, base in DEFAULT_SPECS.items():
        merged = dict(base)
        if isinstance(user.get(mid), dict):
            merged.update(user[mid])
        out[mid] = merged
    for mid, extra in user.items():  # any extra slot ids the user added
        if mid not in out and isinstance(extra, dict):
            out[mid] = extra
    return out


def _spec(mod_id: str) -> dict[str, Any]:
    return _specs().get(mod_id, {})


# ── Slots (names, from llm_store) ─────────────────────────────────────────────

def _stored_slots() -> list[dict[str, Any]]:
    """Slot list from llm_keys.json, migrating the old Gaming/Focus/Work/Chill
    defaults to the new Gaming/Chill/Study/Angry ones."""
    import llm_store
    mods = [dict(m) for m in (llm_store.get().get("mods") or [])][:4]
    names = [str(m.get("name", "")).strip().lower() for m in mods]
    if not mods or names == _LEGACY_NAMES:
        mods = [
            {"id": mid, "name": spec["name"], "enabled": False, "color": spec["color"]}
            for mid, spec in DEFAULT_SPECS.items()
        ]
        try:
            llm_store.save({"mods": mods})
            log.info("mods: migrated mod slots to Gaming / Chill / Study / Angry")
        except Exception as e:
            log.warning("mods: could not save migrated slots: %s", e)
    return mods


def slots() -> list[dict[str, Any]]:
    """Slots as the UI sees them: `enabled` means "is the active mod"."""
    specs = _specs()
    out = []
    for i, m in enumerate(_stored_slots()):
        mid = str(m.get("id") or f"mod_{i + 1}")
        color = specs.get(mid, {}).get("color") or FALLBACK_COLORS[i % 4]
        out.append({
            "id": mid,
            "name": str(m.get("name") or specs.get(mid, {}).get("name") or f"Mod {i + 1}"),
            "enabled": mid == _active_id,
            "color": color,
        })
    return out


def _slot(mod_id: str | None) -> dict[str, Any] | None:
    return next((s for s in slots() if s["id"] == mod_id), None)


def public_state() -> dict[str, Any]:
    with _lock:
        return {"mods": slots(), "active": _active_id}


def save_names(mods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist renamed slots (from the Settings window). Only names are taken."""
    import llm_store
    specs = _specs()
    cleaned = []
    for i, m in enumerate(mods[:4]):
        mid = str(m.get("id") or f"mod_{i + 1}")
        cleaned.append({
            "id": mid,
            "name": str(m.get("name") or specs.get(mid, {}).get("name") or f"Mod {i + 1}")[:32],
            "enabled": False,
            "color": specs.get(mid, {}).get("color") or FALLBACK_COLORS[i % 4],
        })
    llm_store.save({"mods": cleaned})
    return slots()


# ── Personality ───────────────────────────────────────────────────────────────

def active_id() -> str | None:
    return _active_id


def persona_overlay() -> str:
    """Text appended to Lyra's system prompt while a mod is active ("" = normal)."""
    with _lock:
        mid = _active_id
    if not mid:
        return ""
    slot, spec = _slot(mid), _spec(mid)
    if not slot or not spec.get("persona"):
        return ""
    return (
        f"CURRENT MODE: {slot['name'].upper()}. This overrides your default tone; "
        f"every rule above still applies.\n{spec['persona']}"
    )


# ── Switching ─────────────────────────────────────────────────────────────────

def _is_failure(msg: str) -> bool:
    low = (msg or "").lower()
    return low.startswith(("failed", "step failed", "unknown tool", "could not", "couldn't"))


def _run_actions(steps: list[dict], results: list[tuple[dict, str]]) -> None:
    import agent
    for step in steps:
        try:
            msg = agent._execute_step(step)
        except Exception as e:
            msg = f"Step failed: {e}"
        results.append((step, msg))
        time.sleep(0.4)  # let each app start before the next


def _announce(slot: dict[str, Any], spec: dict[str, Any]) -> str:
    """The line she says when a mod switches on: in character via the LLM, else fixed."""
    fallback = str(spec.get("greeting") or "{name} mode on.").replace("{name}", slot["name"])
    try:
        import agent
        from persona import SYSTEM_INSTRUCTIONS
        client, model = agent._llm()
        if not client:
            return fallback
        system = SYSTEM_INSTRUCTIONS + "\n\n" + persona_overlay()
        ask = (
            f"You just switched into {slot['name']} mode. {spec.get('announce', '')} "
            "Say it out loud in one or two short sentences, in this mode's voice. "
            "No lists, no markdown, no quotation marks."
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": ask}],
            max_tokens=120,
            temperature=0.8,
        )
        text = " ".join((resp.choices[0].message.content or "").split()).strip('"')
        if 3 <= len(text) <= 300:
            return text
    except Exception as e:
        log.warning("mods: in-character greeting failed, using fixed line: %s", e)
    return fallback


def activate(mod_id: str) -> str:
    """Switch to a mod: run its actions, change her personality, and return what
    she should say. Safe to call from any thread."""
    global _active_id
    slot, spec = _slot(mod_id), _spec(mod_id)
    if not slot:
        return "I don't have a mode like that."
    with _lock:
        if _active_id == mod_id:
            return f"Already in {slot['name']} mode."
        _active_id = mod_id
    log.info("mods: %s mode ON", slot["name"])
    ui.trace(f"mode: {slot['name']}")

    results: list[tuple[dict, str]] = []
    worker = threading.Thread(
        target=_run_actions, args=(list(spec.get("actions") or []), results), daemon=True, name="lyra-mod-actions"
    )
    worker.start()
    line = _announce(slot, spec)          # runs while the apps are launching
    worker.join(timeout=1.5)

    failed = []
    for step, msg in list(results):
        if _is_failure(msg):
            args = step.get("args") or {}
            failed.append(str(args.get("app_name") or args.get("site_name") or step.get("tool", "that")))
    if failed:
        line += " I couldn't open " + " or ".join(failed) + "."
    return line


def deactivate() -> str:
    """Back to normal Lyra."""
    global _active_id
    with _lock:
        mid, _active_id = _active_id, None
    if not mid:
        return "I'm already in normal mode."
    slot, spec = _slot(mid), _spec(mid)
    log.info("mods: %s mode OFF", slot["name"] if slot else mid)
    return str(spec.get("off") or "Back to normal.")


# ── Voice / text commands ─────────────────────────────────────────────────────

_VERB = r"(?:(?:switch|change|go)\s+to|activate|enable|start|turn\s+on|enter|put\s+me\s+in|set)"
_OFF = re.compile(
    r"^(?:please\s+)?(?:"
    r"(?:go|switch|get|come)\s+back\s+to\s+normal|back\s+to\s+normal|normal\s+mode|default\s+mode|"
    r"(?:turn\s+off|exit|leave|disable|stop|end|cancel)\s+(?:the\s+)?(?:\w+\s+)?mode|calm\s+down"
    r")(?:\s+please)?$"
)


def _normalise(text: str) -> str:
    t = re.sub(r"[^\w\s']", " ", (text or "").lower())
    t = " ".join(t.split())
    return re.sub(r"^(?:hey|ok|okay)?\s*lyra\s+", "", t).strip()


def match_command(text: str) -> tuple[str, dict[str, Any] | None] | None:
    """('on', slot) / ('off', None) / None. Anchored on purpose: "play some gaming
    music" must never switch modes."""
    t = _normalise(text)
    if not t:
        return None
    if _OFF.match(t):
        return ("off", None)
    for slot in slots():
        name = re.escape(slot["name"].lower())
        with_mode = rf"^(?:please\s+)?(?:{_VERB}\s+)?(?:the\s+)?{name}\s+mode(?:\s+please)?$"
        with_verb = rf"^(?:please\s+)?{_VERB}\s+(?:the\s+)?{name}(?:\s+mode)?(?:\s+please)?$"
        if re.match(with_mode, t) or re.match(with_verb, t):
            return ("on", slot)
    return None


def handle_command(text: str) -> str | None:
    """If `text` is a mode command, do it and return her reply; else None."""
    m = match_command(text)
    if not m:
        return None
    action, slot = m
    return activate(slot["id"]) if action == "on" and slot else deactivate()
