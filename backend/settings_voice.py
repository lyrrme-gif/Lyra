"""Lyra — island theme + behaviour settings by voice or typed chat ("set dark mode", ...).

Rule-based (no LLM). Every behaviour change goes through settings_store.update_group(), the same
validated path the Settings window saves with, so a voice change and a click are identical.
The island theme lives in the UI's localStorage, so it is pushed to the island as an "appearance"
event; the island writes it and every Lyra window follows. (The Settings window's own look is fixed:
white, blue accent, cozy spacing, so there is nothing to change by voice there.)

  parse(text)  -> plan dict | None      (pure, no side effects: easy to test)
  handle_command(text) -> reply | None  (applies the plan)
"""

from __future__ import annotations

import re

import settings_store
import ui

log = ui.log

ISLAND_THEMES = {"black": "pure-black", "pure black": "pure-black", "pure-black": "pure-black",
                 "midnight": "midnight", "aurora": "aurora", "dynamic": "dynamic",
                 "frost": "frost", "glass": "frost"}
STYLES = {"short": "short", "brief": "short", "concise": "short", "balanced": "balanced",
          "normal": "balanced", "default": "balanced", "detailed": "detailed", "long": "detailed",
          "detail": "detailed"}
NOTIFY = {"discord": "notify_discord", "whatsapp": "notify_whatsapp", "telegram": "notify_telegram",
          "reminder": "notify_reminders", "reminders": "notify_reminders"}

_ON = r"(?:turn\s+on|switch\s+on|enable|activate|show)"
_OFF = r"(?:turn\s+off|switch\s+off|disable|deactivate|hide)"
_THE = r"(?:the\s+|my\s+|your\s+|lyra'?s?\s+)?"


def _norm(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"^(?:hey\s+|ok\s+|okay\s+)?(?:lyra[,!]?\s+)?", "", t)
    t = re.sub(r"^(?:please\s+|can\s+you\s+|could\s+you\s+|i\s+want\s+you\s+to\s+|i\s+want\s+)+", "", t)
    t = re.sub(r"(?:\s+(?:please|now|for\s+me|back|again|instead|too|then|thanks|thank\s+you))+$", "", t)
    t = re.sub(r"^(?:go\s+back\s+to|back\s+to|put\s+it\s+back\s+(?:to|in))\s+", "set ", t)
    return re.sub(r"[\s.!?]+$", "", t).strip()


def _num(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _time(h: str, m: str | None, ap: str | None) -> str | None:
    hh, mm = int(h), int(m or 0)
    if ap:
        if not 1 <= hh <= 12:
            return None
        hh = hh % 12 + (12 if ap.lower().startswith("p") else 0)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        return None
    return f"{hh:02d}:{mm:02d}"


def _switch(t: str, noun: str) -> bool | None:
    """True/False when `t` turns `noun` (a regex) on/off, else None."""
    if re.fullmatch(rf"{_ON}\s+{_THE}{noun}", t) or re.fullmatch(rf"{noun}\s+on", t):
        return True
    if re.fullmatch(rf"{_OFF}\s+{_THE}{noun}", t) or re.fullmatch(rf"{noun}\s+off", t):
        return False
    return None


def parse(text: str) -> dict | None:
    t = _norm(text)
    if not t:
        return None

    # ── Dark / light mode (Windows only: Lyra's own windows have a fixed look) ──────────
    m = re.fullmatch(rf"(?:(?:set|switch|change|turn|go|put\s+it|use|enable|activate)\s+(?:on\s+|off\s+|to\s+|into\s+)?)?"
                     rf"{_THE}(dark|light)\s+(?:mode|theme)(?:\s+(on|off))?", t)
    if m:
        dark = m.group(1) == "dark"
        if m.group(2) == "off" or re.match(rf"{_OFF}\b", t):
            dark = not dark
        return {"kind": "darkmode", "dark": dark}
    if re.fullmatch(rf"(?:toggle|flip)\s+{_THE}(?:dark|light)\s+(?:mode|theme)", t):
        return {"kind": "darkmode", "dark": None}

    # ── Island theme ─────────────────────────────────────────────────────────────────────
    m = re.fullmatch(r"(?:set|change|switch|use|make)\s+(?:the\s+|my\s+)?(?P<isl>island\s+)?theme\s+(?:to\s+)?(?P<v>[a-z -]+)", t)
    if m:
        v = m.group("v").strip()
        if v in ISLAND_THEMES:
            name = ISLAND_THEMES[v]
            return {"kind": "ui", "ui": {"island_theme": name}, "say": f"Island theme: {v.replace('-', ' ')}."}
    m = re.fullmatch(r"(?:set|change|switch|use|make)\s+(?:the\s+|my\s+)?(?:island\s+)?(?:to\s+)?(?P<v>black|pure black|midnight|aurora|dynamic|frost|glass)(?:\s+theme)?", t)
    if m:
        return {"kind": "ui", "ui": {"island_theme": ISLAND_THEMES[m.group("v")]}, "say": f"Island theme: {m.group('v')}."}

    if re.fullmatch(r"reset\s+(?:the\s+|my\s+)?(?:appearance|look)(?:\s+settings)?", t):
        return {"kind": "ui", "ui": {"island_theme": "midnight"}, "say": "Island theme reset."}

    # ── Reply style ──────────────────────────────────────────────────────────────────────
    m = re.fullmatch(r"(?:set|change|make|switch|use)\s+(?:the\s+|my\s+)?(?:reply|answer|response)s?(?:\s+style)?\s*(?:to\s+|be\s+)?(?P<s>[a-z]+)", t) \
        or re.fullmatch(r"(?:answer|reply|respond)\s+(?:in\s+)?(?P<s>shorter|briefly|short|in\s+detail|detailed)", t) \
        or re.fullmatch(r"be\s+(?:more\s+)?(?P<s>brief|concise|detailed|short)", t)
    if m:
        s = m.group("s").replace("shorter", "short").replace("briefly", "short").replace("in detail", "detailed")
        if s in STYLES:
            return {"kind": "set", "group": "ai", "key": "reply_style", "value": STYLES[s],
                    "say": f"Reply style: {STYLES[s]}."}

    # ── Short acknowledgements: voice / mochi sounds / both ──────────────────────────────
    m = re.fullmatch(r"(?:set|change|use|switch)\s+(?:the\s+)?(?:acknowledg(?:e)?ments?|confirmations?|short\s+replies)\s+(?:to\s+)?(?P<v>voice|speech|speak|sounds?|mochi(?:\s+sounds?)?|both)", t)
    if m:
        v = m.group("v")
        mode = "both" if v == "both" else "speak" if v in ("voice", "speech", "speak") else "mochi"
        return {"kind": "set", "group": "voice", "key": "mochi_sound_mode", "value": mode,
                "say": {"speak": "Short replies will be spoken.", "mochi": "Short replies will be Mochi sounds.",
                        "both": "Short replies: voice and Mochi sounds."}[mode]}

    # ── Notifications ────────────────────────────────────────────────────────────────────
    for word, key in NOTIFY.items():
        for on, rx in ((True, _ON), (False, _OFF)):
            if re.fullmatch(rf"{rx}\s+{_THE}{word}\s+notifications?", t) or re.fullmatch(rf"{rx}\s+notifications?\s+(?:for|from)\s+{word}", t):
                return {"kind": "set", "group": "notifications", "key": key, "value": on,
                        "say": f"{word.capitalize()} notifications are {'on' if on else 'off'}."}
    m = re.fullmatch(r"(?:set|change)\s+(?:the\s+)?notification\s+(?:time|duration|length)\s+(?:to\s+)?(\d+)\s*(?:s|sec|secs|seconds?)?", t)
    if m:
        return {"kind": "set", "group": "notifications", "key": "notify_toast_ms", "value": int(m.group(1)) * 1000,
                "say": None, "fmt": "Notifications stay for {s} seconds.", "scale": 1000}
    on = _switch(t, r"quiet\s+hours")
    if on is not None:
        return {"kind": "set", "group": "notifications", "key": "notify_quiet_enabled", "value": on,
                "say": f"Quiet hours are {'on' if on else 'off'}."}
    m = re.fullmatch(r"(?:set|change)\s+(?:the\s+)?quiet\s+hours\s+(?:from\s+|to\s+)?(\d{1,2})(?::(\d\d))?\s*(am|pm)?\s*(?:to|until|till|-)\s*(\d{1,2})(?::(\d\d))?\s*(am|pm)?", t)
    if m:
        a, b = _time(m.group(1), m.group(2), m.group(3)), _time(m.group(4), m.group(5), m.group(6))
        if a and b:
            return {"kind": "quiet", "start": a, "end": b}
    on = _switch(t, r"calls?\s+only(?:\s+notifications?)?")
    if on is not None:
        return {"kind": "set", "group": "notifications", "key": "notify_calls_only", "value": on,
                "say": f"Chat apps will {'only notify for calls' if on else 'notify for everything'}."}

    # ── Simple on/off switches ───────────────────────────────────────────────────────────
    for noun, group, key, label, note in (
        (r"(?:proactive\s+)?(?:tips|suggestions|heads-?ups)", "features", "proactive_enabled", "Proactive tips", ""),
        (r"(?:the\s+)?(?:mochi|mascot)", "features", "feature_mascot", "Mochi", ""),
        (r"dictation\s+(?:clean-?up|polish)", "voice", "dictation_polish", "Dictation clean-up", ""),
        (r"(?:the\s+)?tab\s+wheel", "features", "feature_tab_wheel", "Tab wheel", ""),
        (r"circle\s+to\s+ask", "features", "feature_circle_ask", "Circle to ask", ""),
    ):
        on = _switch(t, noun)
        if on is not None:
            return {"kind": "set", "group": group, "key": key, "value": on,
                    "say": f"{label} is {'on' if on else 'off'}{note}."}
    on = _switch(t, r"(?:the\s+)?(?:timer|alarm)\s+sounds?")
    if on is not None:
        return {"kind": "raw", "key": "timer_alarm_sound", "value": on,
                "say": f"Timer sound is {'on' if on else 'off'}."}
    m = re.fullmatch(r"(?:set|change|make)\s+(?:the\s+)?(?:mochi|mascot)\s+(?:size\s+)?(?:to\s+)?(small|medium|large)(?:\s+size)?", t)
    if m:
        return {"kind": "set", "group": "features", "key": "mascot_size", "value": m.group(1), "say": f"Mochi size: {m.group(1)}."}
    m = re.fullmatch(r"(?:set|change)\s+(?:the\s+)?music\s+recognition\s+(?:to\s+)?(off|manual|automatic|auto)", t)
    if m:
        v = "automatic" if m.group(1) == "auto" else m.group(1)
        return {"kind": "set", "group": "music", "key": "music_recognition_mode", "value": v, "say": f"Music recognition: {v}."}
    return None


def _push_ui(fields: dict) -> None:
    try:
        import ui_bridge
        ui_bridge.push_appearance(fields)
    except Exception as e:
        log.warning("appearance push: %s", e)


def _save(group: str, key: str, value) -> bool:
    try:
        settings_store.update_group(group, {key: value})
        return True
    except ValueError as e:
        log.warning("settings voice: %s", e)
        return False


def handle_command(text: str) -> str | None:
    plan = parse(text)
    if not plan:
        return None
    kind = plan["kind"]
    try:
        if kind == "darkmode":
            import controls
            dark = plan["dark"]
            if dark is None:
                cur = controls.get_dark()
                dark = not cur if cur is not None else True
            res = controls.toggle("dark", dark)
            word = "Dark" if dark else "Light"
            if res.get("ok") is False:
                return f"{word} mode is on for Lyra (Windows wouldn't switch)."
            return f"{word} mode is on."
        if kind == "ui":
            _push_ui(plan["ui"])
            return plan["say"]
        if kind == "set":
            if not _save(plan["group"], plan["key"], plan["value"]):
                return "I couldn't change that setting."
            if plan.get("fmt"):
                v = plan["value"] / plan["scale"] if plan.get("scale") else plan["value"]
                return plan["fmt"].format(v=plan["value"], s=int(v) if float(v).is_integer() else v)
            return plan["say"]
        if kind == "raw":
            settings_store.set_many({plan["key"]: plan["value"]})
            return plan["say"]
        if kind == "quiet":
            ok = _save("notifications", "notify_quiet_start", plan["start"]) and _save("notifications", "notify_quiet_end", plan["end"])
            if not ok:
                return "I couldn't change that setting."
            _save("notifications", "notify_quiet_enabled", True)
            return f"Quiet hours: {plan['start']} to {plan['end']}."
    except Exception as e:
        log.warning("settings voice: %s", e)
    return None
