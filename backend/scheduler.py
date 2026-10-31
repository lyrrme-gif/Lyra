"""Lyra - scheduled actions and routines.

Scheduled actions (one-off, saved to disk, survive restarts):
    "search Amazon for gift wraps on 10 November at 11:00"
    "remind me to call Mom tomorrow at 9"
Routines (saved to disk, several actions, a time or a trigger):
    "every morning at 9:00 open Unity and Spotify"      time trigger (days + time of day)
    "when Lyra starts, open Spotify"                    trigger: Lyra starting up
    "when I say work mode, open VS Code and Unity"      trigger: a phrase you say
Managing them:
    "what's scheduled"   "cancel the reminder to call Mom"   "cancel all reminders"
    "list my routines"   "run my morning routine"   "pause my morning routine"
    "resume my morning routine"   "delete my morning routine"

How things run: a due action or routine action is sent through the same path as a command typed
in the island's chat box (ui_bridge._handle_chat_message), so it behaves exactly like you typing
it, one command at a time. A reminder shows a notification on the island and plays the timer chime.
Nothing is spoken from the scheduler thread (the speech mixer has no lock).

Late runs: a reminder always fires, labelled "missed" if Lyra was off. A scheduled action runs if
it is at most 15 minutes late and is otherwise skipped with a notification. A routine runs if it is
at most 10 minutes late.

Data: paths.data_path("schedule.json") - plain JSON.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Callable

import schedule_parse as sp

log = logging.getLogger("lyra")

ACTION_GRACE_S = 15 * 60
ROUTINE_GRACE_S = 10 * 60
TICK_S = 5
STARTUP_DELAY_S = 12
MAX_ITEMS = 50
MAX_ROUTINES = 20

_lock = threading.RLock()
_data: dict | None = None
_thread: threading.Thread | None = None
_run_command: Callable[[str], None] | None = None
_notify: Callable[[str, str, str, str], None] | None = None
_publish_cb: Callable[[bool], None] | None = None


# ── storage ──────────────────────────────────────────────────────────────────────────────────

def _file():
    import paths
    return paths.data_path("schedule.json")


def _load() -> dict:
    global _data
    if _data is not None:
        return _data
    data = {"items": [], "routines": [], "next_id": 1}
    try:
        raw = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data["items"] = [x for x in raw.get("items", []) if isinstance(x, dict) and x.get("text") and x.get("due")]
            data["routines"] = [x for x in raw.get("routines", []) if isinstance(x, dict) and x.get("actions") and x.get("trigger")]
            data["next_id"] = int(raw.get("next_id", 1))
    except FileNotFoundError:
        pass
    except Exception as e:  # a damaged file must never stop Lyra
        log.warning("scheduler: could not read schedule.json: %s", e)
    _data = data
    return data


def _save() -> None:
    try:
        path = _file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_load(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        log.warning("scheduler: could not save schedule.json: %s", e)


def _new_id(prefix: str) -> str:
    d = _load()
    n = int(d["next_id"])
    d["next_id"] = n + 1
    return f"{prefix}{n}"


def _publish(open_view: bool = False) -> None:
    fn = _publish_cb
    if fn is None:
        return
    try:
        fn(open_view)
    except Exception as e:
        log.warning("scheduler: publish failed: %s", e)


def reset_for_tests() -> None:
    global _data
    _data = None


# ── what the island shows ────────────────────────────────────────────────────────────────────

def snapshot() -> dict:
    with _lock:
        d = _load()
        items = []
        for it in sorted(d["items"], key=lambda x: x["due"]):
            dt = datetime.fromtimestamp(it["due"])
            items.append({
                "id": it["id"], "kind": it["kind"], "text": it["text"], "due_ms": int(it["due"] * 1000),
                "date": dt.strftime("%Y-%m-%d"), "time": dt.strftime("%H:%M"),
            })
        routines = [{
            "id": r["id"], "name": r["name"], "enabled": bool(r.get("enabled", True)),
            "trigger": r["trigger"], "actions": list(r["actions"]),
        } for r in d["routines"]]
        return {"items": items, "routines": routines, "now": int(time.time() * 1000)}


def remove_item(item_id: str) -> bool:
    with _lock:
        d = _load()
        n = len(d["items"])
        d["items"] = [x for x in d["items"] if x["id"] != item_id]
        changed = len(d["items"]) != n
        if changed:
            _save()
    if changed:
        _publish()
    return changed


def set_routine_enabled(routine_id: str, enabled: bool) -> bool:
    with _lock:
        r = next((x for x in _load()["routines"] if x["id"] == routine_id), None)
        if not r:
            return False
        r["enabled"] = bool(enabled)
        _save()
    _publish()
    return True


def remove_routine(routine_id: str) -> bool:
    with _lock:
        d = _load()
        n = len(d["routines"])
        d["routines"] = [x for x in d["routines"] if x["id"] != routine_id]
        changed = len(d["routines"]) != n
        if changed:
            _save()
    if changed:
        _publish()
    return changed


# ── running things ───────────────────────────────────────────────────────────────────────────

_REMIND_ACTION = re.compile(r"^remind\s+me\s+(?:to|that|about)\s+(.+)$", re.I)


def _fire_reminder(text: str, late_s: float = 0.0, due: float | None = None, alarm: bool = False) -> None:
    title = "Alarm" if alarm else "Reminder"
    label = text
    if text.strip().lower() == title.lower() and due:
        label = sp.say_clock(datetime.fromtimestamp(due))      # "Reminder" / "Alarm" with no text: show the time
    if late_s > 120 and due:
        label = f"{label} (missed at {datetime.fromtimestamp(due).strftime('%H:%M')})"
    log.info("scheduler: %s: %s", title.lower(), label)
    try:
        if _notify:
            try:
                if alarm:
                    _notify("reminder", "reminder", title, label, force=True)   # quiet hours must not hide an alarm
                else:
                    _notify("reminder", "reminder", title, label)
            except TypeError:
                _notify("reminder", "reminder", title, label)
    except Exception as e:
        log.warning("scheduler: notify failed: %s", e)
    try:
        import timers
        timers._chime("alarm" if alarm else "ding")
    except Exception:
        pass
    try:
        import ui
        ui.print_chat("lyra", f"{title}: {label}")
    except Exception:
        pass


def _run_actions(actions: list[str], label: str) -> None:
    """Run commands one after another in a worker thread (never on the caller's thread: it may
    already hold the chat lock)."""
    def work():
        for cmd in actions:
            try:
                m = _REMIND_ACTION.match(cmd.strip())
                if m:
                    _fire_reminder(m.group(1).strip())
                elif _run_command:
                    log.info("scheduler: running %r (%s)", cmd, label)
                    _run_command(cmd)
                else:
                    log.warning("scheduler: no command runner; skipped %r", cmd)
            except Exception as e:
                log.warning("scheduler: %r failed: %s", cmd, e)
            time.sleep(1.5)
    threading.Thread(target=work, name="lyra-scheduler-run", daemon=True).start()


def _tick(now: float | None = None) -> None:
    now = time.time() if now is None else now
    due_items: list[dict] = []
    due_routines: list[dict] = []
    with _lock:
        d = _load()
        for it in list(d["items"]):
            if it["due"] <= now:
                d["items"].remove(it)
                due_items.append(it)
        dt = datetime.fromtimestamp(now)
        today = dt.strftime("%Y-%m-%d")
        for r in d["routines"]:
            trig = r["trigger"]
            if not r.get("enabled", True) or trig.get("type") != "time" or r.get("last_run") == today:
                continue
            if dt.weekday() not in trig.get("days", []):
                continue
            try:
                hh, mm = (int(x) for x in str(trig["time"]).split(":"))
            except Exception:
                continue
            late = (dt - dt.replace(hour=hh, minute=mm, second=0, microsecond=0)).total_seconds()
            if 0 <= late <= ROUTINE_GRACE_S:
                r["last_run"] = today
                due_routines.append(dict(r))
        if due_items or due_routines:
            _save()
    for it in due_items:
        late = now - it["due"]
        if it["kind"] == "reminder":
            _fire_reminder(it["text"], late, it["due"], bool(it.get("alarm")))
        elif late <= ACTION_GRACE_S:
            _run_actions([it["text"]], "scheduled")
        else:
            _fire_reminder(f"Skipped (missed): {it['text']}")
    for r in due_routines:
        _run_actions(r["actions"], f"routine {r['name']}")
    if due_items or due_routines:
        _publish()


def _loop() -> None:
    time.sleep(STARTUP_DELAY_S)
    with _lock:
        startup = [dict(r) for r in _load()["routines"]
                   if r.get("enabled", True) and r["trigger"].get("type") == "startup"]
    for r in startup:
        _run_actions(r["actions"], f"routine {r['name']}")
    while True:
        try:
            _tick()
        except Exception as e:
            log.warning("scheduler: tick failed: %s", e)
        time.sleep(TICK_S)


def start(run_command: Callable[[str], None], notify: Callable[[str, str, str, str], None],
          publish: Callable[[bool], None] | None = None) -> None:
    """Start the background thread (once)."""
    global _thread, _run_command, _notify, _publish_cb
    _run_command, _notify, _publish_cb = run_command, notify, publish
    if _thread and _thread.is_alive():
        return
    _load()
    _thread = threading.Thread(target=_loop, name="lyra-scheduler", daemon=True)
    _thread.start()


# ── voice / chat commands ────────────────────────────────────────────────────────────────────

_LIST = re.compile(
    r"^(?:what(?:'s|\s+is|\s+do\s+i\s+have|\s+have\s+i\s+got|\s+are\s+my)?|show(?:\s+me)?|list|read(?:\s+me)?)\s+"
    r"(?:my\s+|the\s+|all\s+)?(?:upcoming\s+|scheduled\s+)?"
    r"(?P<what>reminders|alarms|schedule|scheduled(?:\s+(?:actions|tasks|reminders))?|routines)(?:\s+(?:i\s+have|do\s+i\s+have))?$", re.I)
_LIST_B = re.compile(r"^what(?:'s|\s+is)\s+scheduled(?:\s+for\s+(?:today|tomorrow))?$|^what\s+do\s+i\s+have\s+scheduled$", re.I)
_CANCEL_ALL = re.compile(
    r"^(?:cancel|clear|delete|remove)\s+(?:all\s+)?(?:of\s+)?(?:my\s+)?(?:scheduled\s+(?:actions|tasks|reminders)|reminders|schedule)$", re.I)
_CANCEL_ALARM = re.compile(
    r"^(?:cancel|delete|remove|clear)\s+(?:all\s+)?(?:of\s+)?(?:my\s+|the\s+)?(?:all\s+)?alarms?(?:\s+(?:at|for)\s+(?P<t>.+))?$", re.I)
_CANCEL_ONE = re.compile(
    r"^(?:cancel|delete|remove)\s+(?:my\s+|the\s+)?(?:scheduled\s+)?(?:reminder|action|task)\s+(?:to\s+|about\s+|for\s+|that\s+)?(?P<t>.+)$", re.I)
_RT_RUN = re.compile(r"^(?:run|start|do|launch|trigger|activate)\s+(?:my\s+|the\s+)?(?P<n>[\w' -]+?)\s+routine$", re.I)
_RT_DEL = re.compile(r"^(?:delete|remove|cancel)\s+(?:my\s+|the\s+)?(?P<n>[\w' -]+?)\s+routine$|^(?:delete|remove)\s+routine\s+(?P<n2>[\w' -]+)$", re.I)
_RT_OFF = re.compile(r"^(?:pause|disable|turn\s+off|stop)\s+(?:my\s+|the\s+)?(?P<n>[\w' -]+?)\s+routine$", re.I)
_RT_ON = re.compile(r"^(?:resume|enable|turn\s+on|unpause)\s+(?:my\s+|the\s+)?(?P<n>[\w' -]+?)\s+routine$", re.I)


def _find_routine(name: str) -> dict | None:
    key = re.sub(r"\s+", " ", (name or "").strip().lower())
    rs = _load()["routines"]
    return next((r for r in rs if r["name"].lower() == key), None) or next((r for r in rs if key and key in r["name"].lower()), None)


def _describe_trigger(r: dict) -> str:
    t = r["trigger"]
    if t.get("type") == "time":
        return f"{sp.say_days(t.get('days', []))} at {int(t['time'].split(':')[0])}:{t['time'].split(':')[1]}"
    if t.get("type") == "startup":
        return "when I start"
    return f"when you say {t.get('phrase', r['name'])}"


def _say_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _unique_name(name: str) -> str:
    base = (name or "").strip() or f"routine {len(_load()['routines']) + 1}"
    taken = {r["name"].lower() for r in _load()["routines"]}
    n, cand = 2, base
    while cand.lower() in taken:
        cand = f"{base} {n}"
        n += 1
    return cand


def _manage(s: str) -> str | None:
    low = s.lower().strip()
    if _LIST_B.match(low) or _LIST.match(low):
        m = _LIST.match(low)
        what = (m.group("what").lower() if m else "schedule")
        with _lock:
            d = _load()
            items = sorted(d["items"], key=lambda x: x["due"])
            routines = list(d["routines"])
        _publish(True)   # also show it on the island
        parts: list[str] = []
        if what != "routines":
            if items:
                bits = [f"{i['text']} {sp.say_when(datetime.fromtimestamp(i['due']))}" for i in items[:5]]
                more = f" and {len(items) - 5} more" if len(items) > 5 else ""
                parts.append(f"You have {len(items)} scheduled: " + "; ".join(bits) + more + ".")
            elif what in ("reminders", "alarms", "scheduled") or what.startswith("scheduled"):
                parts.append("You have nothing scheduled.")
        if what in ("routines", "schedule"):
            if routines:
                bits = [f"{r['name']} ({_describe_trigger(r)}{'' if r.get('enabled', True) else ', paused'})" for r in routines[:5]]
                parts.append(f"Routines: {'; '.join(bits)}.")
            elif what == "routines":
                parts.append("You have no routines.")
        return " ".join(parts) or "You have nothing scheduled."

    if _CANCEL_ALL.match(low):
        with _lock:
            d = _load()
            n = len(d["items"])
            d["items"] = []
            _save()
        _publish()
        return f"Cancelled {n} scheduled item{'s' if n != 1 else ''}." if n else "You have nothing scheduled."

    m = _CANCEL_ALARM.match(low)
    if m:
        with _lock:
            alarms = [i for i in _load()["items"] if i.get("alarm")]
        if not alarms:
            return "You have no alarms set."
        key = (m.group("t") or "").strip(" .")
        if key:
            t = sp._find_time(key)
            alarms = [i for i in alarms if t and datetime.fromtimestamp(i["due"]).minute == t[1]
                      and (datetime.fromtimestamp(i["due"]).hour == t[0] if t[3]
                           else datetime.fromtimestamp(i["due"]).hour % 12 == t[0] % 12)]
            if not alarms:
                return "I couldn't find an alarm at that time."
        elif len(alarms) > 1 and not re.search(r"\ball\b|alarms$", low):
            return f"You have {len(alarms)} alarms. Say 'cancel the alarm at 7:00', or 'cancel all alarms'."
        for it in alarms:
            remove_item(it["id"])
        return f"Cancelled {len(alarms)} alarm{'s' if len(alarms) != 1 else ''}."

    m = _CANCEL_ONE.match(low)
    if m:
        key = m.group("t").strip(" .")
        with _lock:
            hit = next((i for i in _load()["items"] if i["text"].lower() == key), None) \
                or next((i for i in _load()["items"] if key in i["text"].lower()), None)
        if not hit:
            return None
        remove_item(hit["id"])
        return f"Cancelled: {hit['text']}."

    m = _RT_DEL.match(low)
    if m:
        r = _find_routine(m.group("n") or m.group("n2"))
        if not r:
            return None
        remove_routine(r["id"])
        return f"Deleted your {r['name']} routine."
    m = _RT_OFF.match(low)
    if m:
        r = _find_routine(m.group("n"))
        if not r:
            return None
        set_routine_enabled(r["id"], False)
        return f"Paused your {r['name']} routine."
    m = _RT_ON.match(low)
    if m:
        r = _find_routine(m.group("n"))
        if not r:
            return None
        set_routine_enabled(r["id"], True)
        return f"Your {r['name']} routine is back on."
    m = _RT_RUN.match(low)
    if m:
        r = _find_routine(m.group("n"))
        if not r:
            return None   # maybe a Settings > Shortcuts routine ("gaming mode"): let it try
        _run_actions(list(r["actions"]), f"routine {r['name']}")
        return f"Running your {r['name']} routine."
    return None


def _phrase_trigger(s: str) -> str | None:
    key = re.sub(r"^(?:run|start|activate|do|launch|trigger|enable)\s+(?:my\s+|the\s+)?", "", s.lower().strip())
    key = re.sub(r"\s+routine$", "", key).strip(" .!?")
    if not key:
        return None
    with _lock:
        for r in _load()["routines"]:
            t = r["trigger"]
            if r.get("enabled", True) and t.get("type") == "phrase" and key in (t.get("phrase", ""), r["name"].lower()):
                actions = list(r["actions"])
                name = r["name"]
                break
        else:
            return None
    _run_actions(actions, f"routine {name}")
    return f"Running {name}."


def handle_command(command: str, now: datetime | None = None) -> str | None:
    """A sentence Lyra can speak, or None when the phrase is not for the scheduler."""
    try:
        s = sp.normalize(command)
        if not s:
            return None
        reply = _manage(s) or _phrase_trigger(s)
        if reply:
            return reply

        alarm = sp.parse_alarm(s, now)
        if alarm:
            if alarm.get("error"):
                return alarm["error"]
            now_dt = now or datetime.now()
            if alarm["due"] <= now_dt:
                return "That time has already passed."
            with _lock:
                d = _load()
                if len(d["items"]) >= MAX_ITEMS:
                    return "You have too many scheduled items. Cancel some first."
                due = alarm["due"].timestamp()
                if any(i["text"].lower() == alarm["text"].lower() and abs(i["due"] - due) < 60 for i in d["items"]):
                    return "That's already scheduled."
                item = {"id": _new_id("s"), "kind": "reminder", "text": alarm["text"], "due": due,
                        "created": int(time.time())}
                if alarm["alarm"]:
                    item["alarm"] = True
                d["items"].append(item)
                _save()
            _publish()
            word = "Alarm" if alarm["alarm"] else "Reminder"
            label = f": {alarm['text']}" if alarm["text"].lower() not in ("alarm", "reminder") else ""
            return f"{word} set for {sp.say_clock(alarm['due'])}{sp.say_day(alarm['due'], now_dt)}{label}"

        routine = sp.parse_routine(s, now)
        if routine:
            if routine.get("error"):
                return routine["error"]
            with _lock:
                d = _load()
                if len(d["routines"]) >= MAX_ROUTINES:
                    return "You already have too many routines. Delete one first."
                name = _unique_name(routine["name"])
                r = {"id": _new_id("r"), "name": name, "enabled": True, "trigger": routine["trigger"],
                     "actions": routine["actions"], "created": int(time.time())}
                d["routines"].append(r)
                _save()
            _publish()
            todo = _say_list(r["actions"])
            t = r["trigger"]["type"]
            if t == "time":
                return f"Saved your {name} routine: {_describe_trigger(r)} I'll {todo}."
            if t == "startup":
                return f"Done. Every time I start, I'll {todo}."
            return f"Done. Say \"{r['trigger']['phrase']}\" and I'll {todo}."

        one = sp.parse_oneshot(s, now)
        if one:
            now_dt = now or datetime.now()
            if one["due"] <= now_dt:
                return "That time has already passed."
            with _lock:
                d = _load()
                if len(d["items"]) >= MAX_ITEMS:
                    return "You have too many scheduled items. Cancel some first."
                due = one["due"].timestamp()
                if any(i["text"].lower() == one["text"].lower() and abs(i["due"] - due) < 60 for i in d["items"]):
                    return "That's already scheduled."
                d["items"].append({"id": _new_id("s"), "kind": one["kind"], "text": one["text"], "due": due,
                                   "created": int(time.time())})
                _save()
            _publish()
            when = sp.say_when(one["due"], now_dt)
            what = f"remind you to {one['text']}" if one["kind"] == "reminder" else one["text"]
            note = " I picked 9:00 since you didn't give a time." if one["defaulted"] and one["due"].hour == 9 and one["due"].minute == 0 else ""
            return f"Okay, I'll {what} {when}.{note}"
    except Exception as e:
        log.warning("scheduler: %s", e)
    return None
