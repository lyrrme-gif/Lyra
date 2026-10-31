"""Lyra - "ask about it, hear the answer AND see its tab".

  "whats on my todo list"            -> answer spoken, To-do tab opens
  "whats the weather"                -> answer spoken, Weather tab opens
  "whats on my calendar today"       -> answer spoken, Calendar tab opens
  "how much time is left on timer"   -> answer spoken, Timer tab opens

The answers still come from the same places as before (actions/notes.py, actions/quick.py,
timers.py). This module adds two things:

  * open_for(command, reply)  - called with a command and the reply that was produced for it. If something
    was ADDED (a to-do, a reminder / calendar entry, a timer) the island opens that tab and plays the "added"
    animation (ui_bridge.push_added); else if the command was a question about to-do / weather / calendar /
    timer / PC stats / clipboard, the island just opens that tab (ui_bridge.push_open_view). It never
    changes the reply and never raises.
  * try_calendar(command)     - the calendar answer: Google Calendar (if connected) + the events and
    reminders of the Calendar tab + scheduled reminders. The Calendar tab keeps its own events in the
    browser, so the island mirrors them here (POST /calendar/sync -> set_calendar_items).
  * rewrite(command)          - a few looser to-do phrasings become the one notes.py already answers.

"open the todo tab" style commands are in actions/tabs.py.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta

_LEAD = r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?"
_lead_rx = re.compile(_LEAD)


def _low(command: str) -> str:
    low = re.sub(r"\s+", " ", (command or "").strip().lower()).rstrip("?.! ")
    return _lead_rx.sub("", low).strip()


# ── which question opens which tab ───────────────────────────────────────────────────────────

_EDIT = re.compile(r"^(?:add|remove|delete|clear|empty|wipe|cross|tick|mark|put|set|create|make|start|cancel|stop|"
                   r"pause|resume|schedule|remind|turn|change|update)\b")
_ASK = r"(?:what|whats|what's|what are|what is|read|show|tell|list|give|any|do i|how many|check|is there|are there)"

_TODO_Q = re.compile(r"^" + _ASK + r"\b.*\b(?:to-?do|to do|tasks?|task list|checklist)\b|"
                     r"^what (?:do|should) i (?:still )?(?:need|have|want) to do\b")
_WEATHER_Q = re.compile(r"\b(?:weather|forecast|temperature|how (?:hot|cold|warm) is it|will it rain|"
                        r"is it (?:going to )?(?:rain|raining|snow|sunny))\b")
_CAL_Q = re.compile(r"^" + _ASK + r"\b.*\b(?:calendar|agenda|schedule|events?|meetings?|appointments?)\b|"
                    r"^what do i have (?:on )?(?:today|tomorrow|tonight)\b|"
                    r"^(?:am i|do i have anything|anything) (?:free|on|scheduled|planned)\b")
_TIMER_Q = re.compile(r"\b(?:timers?|stopwatch|pomodoro|countdown|focus session)\b.*\b(?:left|remaining|at|running|active|status|going)\b|"
                      r"\bhow (?:much|long)\b.*\b(?:left|remaining|elapsed|so far)\b|"
                      r"\btime (?:left|remaining)\b|\bdo i have (?:any|a) (?:timers?|stopwatch)\b|"
                      r"\b(?:check|show)\b.*\b(?:timers?|stopwatch|pomodoro)\b")
_STATS_Q = re.compile(r"\b(?:cpu|processor|ram|memory usage|disk space|disk usage|storage left|internet speed|network speed|"
                      r"pc performance|computer performance|why is my (?:pc|computer|laptop) (?:so )?slow|how(?:'s| is) my (?:pc|computer|laptop))\b")
_CLIP_Q = re.compile(r"\b(?:what(?:'s| is|s) (?:in|on) (?:my|the) clipboard|(?:read|show|tell)(?: me)?(?: out)? (?:my|the) clipboard|"
                     r"clipboard (?:history|contents?))\b")
_TIMER_EDIT = re.compile(r"\b(?:set|start|create|cancel|stop|pause|resume|reset|delete|remove|kill|turn off|remind)\b")


def view_for(command: str) -> str | None:
    """The island tab this question belongs to ('todo', 'weather', 'calendar', 'timer'), or None."""
    low = _low(command)
    if not low or len(low.split()) > 18:
        return None
    if _TIMER_Q.search(low) and not _TIMER_EDIT.search(low):
        return "timer"
    if _EDIT.match(low):
        return None
    if re.match(r"^(?:open|launch|close|go to|switch to|bring up|pull up|take me to)\b", low):
        return None           # "open the todo tab" is actions/tabs.py's
    if _STATS_Q.search(low):
        return "stats"
    if _CLIP_Q.search(low):
        return "clipboard"
    if _WEATHER_Q.search(low):
        return "weather"
    if _CAL_Q.search(low) and not re.search(r"\bscheduled\b", low):
        return "calendar"
    if _TODO_Q.search(low):
        return "todo"
    return None


_ADDED_TODO = re.compile(r"^added\s+(?P<item>.+?)\s+to\s+your\s+to-do\s+list\.?$", re.I)
# Replies that mean "nothing was added" (a question like "how long should ..." is not a failure).
_FAIL = ("i couldn't", "i could not", "sorry", "that time has already passed", "that's already", "that's too many",
         "you have too many", "you already have too many", "how long should", "i can set timers up to")
_FAIL_ANIM = tuple(f for f in _FAIL if f != "how long should")

# "set a timer ...", "remind me ...", "add milk to my to-do list", "schedule ..." = a request to ADD something.
_ADD_VERB = re.compile(r"^(?:add|set|start|create|make|put|schedule|book|save|begin|remind|wake)\b")
_ADD_WHAT = re.compile(r"\b(?:timers?|stopwatch|pomodoro|focus session|alarm|reminders?|remind me|calendar|events?|"
                       r"meetings?|appointments?|to-?do|to do|tasks?|wake me)\b")


def _first_sentence(reply: str) -> str:
    m = re.match(r"^(.{3,90}?[.!?])(?:\s|$)", reply.strip())
    return (m.group(1) if m else reply.strip()[:90]).rstrip(".")


def _kind_view(command: str, reply: str = "", hint: str = "") -> tuple[str, str]:
    """(kind, tab) of an add request, from the reply when it says so, else from the command's words."""
    low, rl = _low(command), (reply or "").lower()
    if hint == "timer" or re.search(r"\b(?:timers?|stopwatch|pomodoro|focus session|countdown)\b", low):
        if "stopwatch" in low or rl.startswith("stopwatch"):
            return "stopwatch", "timer"
        if "focus" in low or "pomodoro" in low or rl.startswith("focus session"):
            return "focus", "timer"
        return "timer", "timer"
    if re.search(r"\balarms?\b", low) or rl.startswith("alarm set"):
        return "alarm", "calendar"
    if re.search(r"\b(?:events?|meetings?|appointments?|calendar)\b", low) or "to your calendar" in rl:
        return "event", "calendar"
    if re.search(r"\bremind", low) or rl.startswith(("reminder set", "okay, i'll")) or hint == "schedule":
        return "reminder", "calendar"
    return "todo", "todo"


def is_add_request(command: str) -> bool:
    low = _low(command)
    return bool(low) and len(low.split()) <= 30 and bool(_ADD_VERB.match(low)) and bool(_ADD_WHAT.search(low))


def add_result(command: str, reply: str, hint: str = "") -> dict | None:
    """What the island should play for an add request: {view, kind, status, text}, or None when `reply` is not
    the outcome of an add request. status 'ok' = it was added, 'fail' = it was refused / could not be added.
    hint: 'timer' / 'schedule' = which handler produced the reply (main.py knows)."""
    r = (reply or "").strip()
    if not r:
        return None
    low = r.lower()
    if low.startswith(_FAIL_ANIM):
        if not (hint or is_add_request(command)):
            return None                       # "sorry, ..." to a question is not a failed add
        kind, view = _kind_view(command, r, hint)
        return {"view": view, "kind": kind, "status": "fail", "text": _first_sentence(r) or "Couldn't add that"}
    if low.startswith(_FAIL):
        return None
    m = _ADDED_TODO.match(r)
    if m:
        return {"view": "todo", "kind": "todo", "status": "ok", "text": "Added: " + m.group("item")}
    if hint == "timer" and re.match(r"^(?:.{0,40}timer set for|okay, i'll remind you in|focus session started|stopwatch started)", low):
        kind, view = _kind_view(command, r, hint)
        return {"view": view, "kind": kind, "status": "ok", "text": _first_sentence(r)}
    if hint == "schedule" and re.match(r"^(?:reminder set for|alarm set for|okay, i'll|added .+ to your calendar|saved your|done\. )", low):
        kind, view = _kind_view(command, r, hint)
        return {"view": view, "kind": kind, "status": "ok", "text": _first_sentence(r)}
    return None


def added_for(command: str, reply: str, hint: str = "") -> tuple[str, str] | None:
    """(tab, label) when `reply` says something was just added, else None."""
    res = add_result(command, reply, hint)
    return (res["view"], res["text"]) if res and res["status"] == "ok" else None


def open_for(command: str, reply: str | None, hint: str = "") -> None:
    """Open the tab that goes with `command`, now that `reply` is being given. Plays the matching animation
    when something was added, or the failure animation when an add request was refused. Never raises."""
    try:
        r = (reply or "").strip()
        if not r:
            return
        import ui_bridge
        res = add_result(command, r, hint)
        if res:
            ui_bridge.push_added(res["view"], res["text"], res["kind"], res["status"])
            return
        if r.lower().startswith(_FAIL):
            return
        view = view_for(command)
        if view:
            ui_bridge.push_open_view(view)
    except Exception:
        pass


# ── looser to-do phrasings -> the wording actions/notes.py answers ───────────────────────────

_TODO_READ = re.compile(
    r"^(?:what(?:'s|s|\s+is|\s+are)?\s+(?:on\s+|in\s+)?(?:my\s+|the\s+)?(?:to-?do|to\s+do|tasks?)(?:\s+list)?(?:\s+(?:today|now))?"
    r"|(?:read|show|tell|list|give)(?:\s+me)?\s+(?:out\s+)?(?:my\s+|the\s+)?(?:to-?do|to\s+do|tasks?|task\s+list|checklist)(?:\s+list)?"
    r"|what\s+(?:do|should)\s+i\s+(?:still\s+)?(?:need|have|want)\s+to\s+do(?:\s+today)?"
    r"|(?:do\s+i\s+have|any)\s+(?:any\s+)?(?:to-?dos?|tasks?)(?:\s+(?:left|today))?)$")


_CAL_ADD = re.compile(
    r"^(?:add|put|create|schedule|book|save)\s+(?:a\s+|an\s+|the\s+)?(?:(?:event|reminder)\s+)?(?:called\s+|named\s+)?"
    r"(?P<what>.+?)\s+(?:to|on|in|into)\s+(?:my\s+|the\s+)?(?:google\s+)?calendar(?P<when>\s+(?:today|tomorrow|tonight|on|at|next|this|for)\b.+)?$")
_CAL_ADD2 = re.compile(
    r"^(?:add|create|schedule|book|put)\s+(?:a\s+|an\s+)?(?:event\s+(?:called\s+|named\s+|for\s+)?)?"
    r"(?P<what>(?:meeting|appointment)\b.*?|.+?)\s+(?P<when>(?:today|tomorrow|tonight|on|at|next|this)\b.+)$")


def rewrite(command: str) -> str:
    """Looser phrasings -> the wording the existing handlers answer:
      'whats on my todo' / 'read my tasks' / 'what do I need to do' -> "what's on my to-do list"
      'add dentist to my calendar tomorrow at 9'                   -> 'remind me about dentist tomorrow at 9'
        (the Calendar tab shows Lyra's scheduled reminders, so this one really lands there)"""
    try:
        low = _low(command)
        if _TODO_READ.match(low):
            return "what's on my to-do list"
        m = _CAL_ADD.match(low) or _CAL_ADD2.match(low)
        if m and (m.group("when") or "").strip():
            return f"remind me about {m.group('what').strip()} {m.group('when').strip()}"
    except Exception:
        pass
    return command


_REMINDER_REPLY = re.compile(r"^Okay, I'll remind you to (?P<w>.+?) (?P<when>(?:today|tomorrow|tonight|on|at|next|this)\b.*?)\.(?P<rest>.*)$", re.S)


def calendar_reply(command: str, rewritten: str, reply: str | None) -> str | None:
    """'add dentist to my calendar tomorrow at 9' was run as a reminder; say it the way it was asked
    ("Added dentist to your calendar, tomorrow at 9:00.") instead of "I'll remind you to dentist"."""
    try:
        if reply and rewritten != command:
            m = _REMINDER_REPLY.match(reply)
            if m:
                return f"Added {m.group('w')} to your calendar, {m.group('when')}.{m.group('rest')}"
    except Exception:
        pass
    return reply


# ── calendar: mirror of the Calendar tab's own events ────────────────────────────────────────

_lock = threading.Lock()


def _mirror_file():
    import paths
    return paths.data_path("calendar_items.json")


def set_calendar_items(items) -> int:
    """The island sends the Calendar tab's events/reminders whenever they change."""
    clean = []
    for x in (items if isinstance(items, list) else [])[:500]:
        if not isinstance(x, dict):
            continue
        title = re.sub(r"\s+", " ", str(x.get("title") or "")).strip()[:120]
        day = str(x.get("date") or "")[:10]
        if not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        tm = str(x.get("time") or "")[:5]
        clean.append({"title": title, "date": day, "time": tm if re.fullmatch(r"\d{2}:\d{2}", tm) else "",
                      "kind": "reminder" if x.get("kind") == "reminder" else "event",
                      "repeat": "daily" if x.get("repeat") == "daily" else "none"})
    with _lock:
        f = _mirror_file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": clean, "ts": int(time.time())}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    return len(clean)


def _mirror() -> list[dict] | None:
    """None = the island has never sent its calendar (so we cannot say 'nothing')."""
    try:
        with _lock:
            raw = json.loads(_mirror_file().read_text(encoding="utf-8"))
        items = raw.get("items")
        return items if isinstance(items, list) else None
    except Exception:
        return None


def _say_hhmm(hhmm: str) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    return f"{h % 12 or 12}{'' if m == 0 else f':{m:02d}'} {'AM' if h < 12 else 'PM'}"


_CAL_ASK = re.compile(
    r"^(?:what(?:'s|s|\s+is|\s+are)?\s+(?:on\s+)?(?:my\s+|the\s+)?(?:google\s+)?(?:calendar|agenda|schedule)"
    r"|(?:read|show|tell|give)(?:\s+me)?\s+(?:out\s+)?(?:my\s+|the\s+)?(?:today'?s\s+|tomorrow'?s\s+)?(?:calendar|agenda|schedule)"
    r"|(?:today'?s|tomorrow'?s|my)\s+(?:agenda|calendar|schedule)"
    r"|what\s+do\s+i\s+have\s+(?:on\s+)?(?:today|tomorrow)"
    r"|(?:do\s+i\s+have|any)\s+(?:any\s+)?(?:events?|meetings?|appointments?)"
    r"|what(?:'s|s|\s+is)\s+on\s+(?:today|tomorrow))"
    r"(?:\s+(?:for|on))?(?:\s+(?:today|tomorrow|tonight))?(?:\s+(?:on|in)\s+(?:my\s+)?(?:google\s+)?calendar)?$")


def _google_rows(d: date) -> list[tuple[str, str, bool]]:
    """(title, 'HH:MM' or '', all_day) from Google Calendar; [] when it is not connected."""
    try:
        import google_workspace as g
        import settings_store
        if not settings_store.feature_on("google") or not g.connected():
            return []
        out = []
        for e in g._events_between(g._midnight(d), g._midnight(d + timedelta(days=1))):
            if e.get("all_day"):
                out.append((e["title"], "", True))
            else:
                out.append((e["title"], e["start"].astimezone().strftime("%H:%M"), False))
        return out
    except Exception:
        return []


def _local_rows(d: date, items: list[dict]) -> list[tuple[str, str, bool]]:
    day = d.isoformat()
    return [(x["title"], x.get("time") or "", not x.get("time"))
            for x in items if x["date"] == day or (x.get("repeat") == "daily" and x["date"] <= day)]


def _scheduled_rows(d: date) -> list[tuple[str, str, bool]]:
    try:
        import scheduler
        return [(it["text"], it["time"], False) for it in scheduler.snapshot()["items"] if it["date"] == d.isoformat()]
    except Exception:
        return []


def try_calendar(command: str, engine_context=None) -> str | None:
    """'whats on my calendar today' -> the answer (the tab is opened by open_for). None if not about the calendar."""
    low = _low(command)
    m = _CAL_ASK.match(low)
    if not m:
        return None
    tomorrow = "tomorrow" in low
    d = date.today() + timedelta(days=1 if tomorrow else 0)
    word = "tomorrow" if tomorrow else "today"

    mirror = _mirror()
    rows = _google_rows(d) + _scheduled_rows(d)
    if mirror is not None:
        rows += _local_rows(d, mirror)
    elif not rows:
        return "I can't see your calendar yet. Open the Calendar tab once and then ask me again."

    now_hm = datetime.now().strftime("%H:%M")
    seen, keep = set(), []
    for title, hm, allday in sorted(rows, key=lambda r: r[1] or "00:00"):
        key = (title.lower(), hm)
        if key in seen:
            continue
        seen.add(key)
        if not tomorrow and hm and hm < now_hm:      # already over today
            continue
        keep.append((title, hm, allday))
    if not keep:
        return f"Nothing left on your calendar {word}." if not tomorrow else "Nothing on your calendar tomorrow."
    parts = [f"{t}, all day" if allday else f"{t} at {_say_hhmm(hm)}" for t, hm, allday in keep[:6]]
    more = f", and {len(keep) - 6} more" if len(keep) > 6 else ""
    n = len(keep)
    return f"You have {n} thing{'s' if n != 1 else ''} {word}: " + "; ".join(parts) + more + "."
