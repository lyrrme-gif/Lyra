"""'What can you do?', the activity log ('what did I do today') and 'undo that'."""
from __future__ import annotations

import os
import re
import time

_HELP = re.compile(r"^(?:what\s+can\s+you\s+do(?:\s+(?:with|for)\s+(?P<topic>[a-z ]+?))?|what\s+are\s+(?:your|all\s+your)\s+(?:abilities|commands|features|skills)"
                   r"|help(?:\s+me)?(?:\s+with\s+(?P<topic2>[a-z ]+?))?|show\s+(?:me\s+)?(?:your\s+)?(?:commands|abilities|features)|what\s+do\s+you\s+know\s+how\s+to\s+do)$")

_TOPICS = {
    "music": "Music: 'pause', 'next song', 'what song is this', 'mute spotify', 'set chrome volume to 20'.",
    "volume": "Volume: 'turn it up', 'set volume to 30', 'mute', 'set chrome volume to 20 percent'.",
    "files": "Files: 'create a folder called work', 'create a bat file called backup in documents', 'find the file called budget', "
             "'open budget.xlsx', 'open the first one', 'rename old.txt to new.txt', "
             "'what's in my downloads folder', 'remember this folder as projects'.",
    "windows": "Windows: 'minimize', 'maximize', 'snap this to the left', 'switch to chrome', 'list my windows', 'lock my pc'.",
    "timers": "Timers: 'set a timer for 10 minutes', 'remind me in 20 minutes to call mum', 'start a pomodoro', "
              "'how much time is left', 'cancel the timer'. Alarms and reminders: 'set an alarm for 7 am', 'remind me at 4 pm'.",
    "notes": "Notes and lists: 'take a note: ...', 'add milk to my shopping list', 'what's on my to-do list', "
             "'remove eggs from my shopping list'.",
    "clipboard": "Clipboard: 'what's in my clipboard', 'translate what I copied to French', 'summarize my clipboard', "
                 "'fix what I copied', 'read this aloud'.",
    "screen": "Screen: 'what's on my screen', 'explain this error', 'copy the text from my screen', 'click the save button'.",
    "pc": "PC: 'what's my battery', 'disk space', 'why is my pc slow', 'which window is active', 'take a screenshot'.",
    "info": "Info: 'what's the weather', 'give me my morning brief', '5 miles in km', 'convert 50 dollars to euros', "
            "'what is 15 percent of 240'.",
    "routines": "Routines: 'gaming mode', 'focus mode', 'good night'. Edit or add your own in Settings.",
    "listening": "Listening: 'summarize what I'm hearing' (a minute of your PC's sound), 'what song is this'.",
    "downloads": "Downloads: 'sort my downloads' (shows a plan first), then 'yes sort them'. 'undo that' moves everything back.",
    "snippets": "Snippets: 'save snippet email as me@example.com', 'type my email', 'list my snippets', 'delete snippet email'.",
    "gaming": "Gaming: 'set spotify to 20 when I play games', 'list my game volumes', 'clear my game volumes'. Lyra applies them when a game starts.",
    "history": "History: 'what did I do today', 'show my activity', 'undo that', 'clear my activity log'.",
}
_ALIASES = {"audio": "music", "spotify": "music", "sound": "volume", "folders": "files", "file": "files", "window": "windows",
            "timer": "timers", "reminders": "timers", "reminder": "timers", "list": "notes", "lists": "notes", "note": "notes",
            "weather": "info", "conversions": "info", "maths": "info", "math": "info", "battery": "pc", "system": "pc",
            "routine": "routines", "download": "downloads", "snippet": "snippets", "games": "gaming", "game": "gaming", "modes": "routines", "activity": "history", "undo": "history"}


def _help(low: str) -> str | None:
    m = _HELP.match(low)
    if not m:
        return None
    topic = (m.group("topic") or m.group("topic2") or "").strip()
    if topic:
        key = topic if topic in _TOPICS else _ALIASES.get(topic, _ALIASES.get(topic.rstrip("s"), ""))
        if key in _TOPICS:
            return _TOPICS[key]
        return "I don't have a list for that. Try: music, volume, files, windows, timers, notes, clipboard, screen, pc, info, routines, listening or history."
    return ("I can control music and volume, manage windows and files, set timers and reminders, keep notes and lists, "
            "work on your clipboard, read your screen, check battery, disk and weather, run routines like gaming mode, "
            "and summarize what your PC is playing. Ask 'what can you do with' music, files, timers, notes, screen, pc, "
            "info or routines for examples.")


# ── activity log ────────────────────────────────────────────────────────────────────────────

_ACTIVITY = re.compile(r"^(?:what\s+did\s+(?:i|we|you)\s+do\s+(?P<when>today|yesterday)|(?:show|read)(?:\s+me)?\s+(?:my\s+)?(?:activity|history|activity\s+log)"
                       r"|what\s+have\s+you\s+done\s+(?:for\s+me\s+)?(?P<when2>today|yesterday)?)$")
_CLEAR = re.compile(r"^(?:clear|delete|erase|reset)\s+(?:my\s+)?(?:activity|history)(?:\s+log)?$")
_KIND = {"quick": "quick answer", "action": "command", "routine": "routine"}


def _midnight(days_ago: int = 0) -> float:
    t = time.localtime()
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return start - days_ago * 86400


def _activity(low: str) -> str | None:
    if _CLEAR.match(low):
        import activity_log
        n = activity_log.clear()
        return f"Cleared your activity log ({n} entries)."
    m = _ACTIVITY.match(low)
    if not m:
        return None
    import activity_log
    when = m.group("when") or m.group("when2") or "today"
    start, end = (_midnight(1), _midnight(0)) if when == "yesterday" else (_midnight(0), None)
    rows = activity_log.recent(start, end)
    if not rows:
        return f"I have nothing logged for {when}."
    last = rows[-5:]
    bits = []
    for e in reversed(last):
        cmd = e.get("cmd", "").strip()
        bits.append(f"{cmd[:50]}" + (" (undone)" if e.get("undone") else ""))
    return (f"{when.capitalize()} you gave me {len(rows)} command{'s' if len(rows) != 1 else ''}. "
            f"Latest: " + "; ".join(bits) + ".")


# ── undo ────────────────────────────────────────────────────────────────────────────────────

_UNDO = re.compile(r"^(?:undo(?:\s+(?:that|it|this|the\s+last(?:\s+(?:thing|action|one))?))?|revert\s+that|take\s+that\s+back|never\s*mind\s+undo)$")


def _undo(low: str) -> str | None:
    if not _UNDO.match(low):
        return None
    import activity_log
    e = activity_log.last_undoable()
    if not e:
        return "There's nothing I can undo from the last half hour."
    hint = e["undo"]
    kind = hint.get("type")
    try:
        if kind == "delete_path":
            path = hint["path"]
            if not os.path.exists(path):
                activity_log.mark_undone(e["id"])
                return "It's already gone."
            if os.path.isdir(path):
                if os.listdir(path):
                    return "That folder has things in it now, so I left it alone."
                os.rmdir(path)
            else:
                if os.path.getsize(path) > 0:
                    return "That file has content now, so I left it alone."
                os.remove(path)
            activity_log.mark_undone(e["id"])
            return f"Undone - removed {os.path.basename(path)}."
        if kind == "move_back":
            back = 0
            for src, dst in hint.get("moves", []):
                try:
                    if os.path.exists(dst) and not os.path.exists(src):
                        import shutil
                        shutil.move(dst, src)
                        back += 1
                except Exception:
                    continue
            activity_log.mark_undone(e["id"])
            return f"Undone - put {back} file{'s' if back != 1 else ''} back where they were."
        if kind == "note_remove":
            from actions import notes
            with notes._lock:
                d = notes._load()
                for i in range(len(d["notes"]) - 1, -1, -1):
                    if d["notes"][i]["text"] == hint["text"]:
                        d["notes"].pop(i)
                        break
                notes._save(d)
            activity_log.mark_undone(e["id"])
            return "Undone - removed that note."
        if kind == "list_remove":
            from actions import notes
            with notes._lock:
                d = notes._load()
                lst = d["lists"].get(hint["list"], [])
                if hint["item"] in lst:
                    lst.remove(hint["item"])
                notes._save(d)
            activity_log.mark_undone(e["id"])
            return f"Undone - took {hint['item']} off your {hint['list']} list."
    except Exception as ex:  # noqa: BLE001
        return f"I couldn't undo it - {ex}"
    return "I don't know how to undo that one."


def try_help(command: str) -> str | None:
    low = command.strip().lower().rstrip("?.! ")
    low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", low).strip()
    if not low:
        return None
    return _undo(low) or _activity(low) or _help(low)
