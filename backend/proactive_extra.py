"""Proactive heads-ups 2.0: Lyra speaks up at the right moment (proactive.py is untouched).

Alert types, each with its own Settings switch:
  * deadlines  - scheduled reminders / actions (schedule.json) one day and one hour before they are due
  * break      - you have been active for N minutes without a pause (idle time via GetLastInputInfo)
  * hydration  - optional "drink some water" every N minutes while you are at the PC
  * bedtime    - optional wind-down nudge at a chosen time of night
  * resources  - sustained high CPU / RAM usage (names the top process) and a nearly full system drive

Everything is delivered as a quiet island notification (ui_bridge.push_notification) and, when the
mascot is switched on, also as a mascot emote event. The existing quiet hours (Settings ->
Notifications) are respected: an alert held back by quiet hours does not use up its cooldown.

Settings (settings_store, group "features"):
  feature_proactive_extra   master switch (background thread only starts when on)
  proactive_deadlines / proactive_break / proactive_hydration / proactive_bedtime / proactive_resources
  proactive_break_minutes, proactive_hydration_minutes, proactive_bedtime_time (HH:MM)
  proactive_cpu_percent, proactive_ram_percent, proactive_disk_percent
  proactive_debug_seconds   0 = normal. > 0 = test mode: every enabled type fires on this short
                            interval (thresholds ignored) and still obeys its cooldown.

Debug from a Python shell inside backend/:  python proactive_extra.py --once
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
from datetime import datetime

import ui

log = ui.log

TICK_S = 30                 # how often the loop looks around
STARTUP_DELAY_S = 20
IDLE_RESET_S = 300          # 5 minutes without input = a real break; the activity timer restarts
SUSTAIN_TICKS = 3           # CPU / RAM must stay high this many ticks in a row (about 90 s)

# Cooldowns per type, in seconds. Break and hydration use their own interval (>= these floors).
COOLDOWN_S = {"cpu": 15 * 60, "ram": 15 * 60, "disk": 6 * 3600, "break": 20 * 60, "hydration": 20 * 60}

# Mascot emote per type (only sent when the mascot feature is on).
_EMOTES = {"deadline": "surprised", "break": "tired", "hydration": "thirsty", "bedtime": "tired",
           "cpu": "hot", "ram": "hot", "disk": "sad"}

_started = False
_lock = threading.Lock()
_last_fired: dict[str, float] = {}      # cooldown key -> time of last delivery
_hi_count = {"cpu": 0, "ram": 0}        # consecutive ticks over the threshold
_active_since: float | None = None      # when the current stretch of activity began
_last_water: float | None = None
_bedtime_day: str = ""                  # date of the last bedtime nudge
_fired_deadlines: set[str] | None = None
_first_seen: dict[str, float] = {}


# ── settings helpers ─────────────────────────────────────────────────────────────────────────

def _s(key: str, default):
    try:
        import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:
        return default


def _on(key: str, default: bool = True) -> bool:
    return _s(key, default) is not False and bool(_s(key, default))


def _debug_s() -> int:
    try:
        return max(0, int(_s("proactive_debug_seconds", 0) or 0))
    except (TypeError, ValueError):
        return 0


# ── delivery ─────────────────────────────────────────────────────────────────────────────────

def _quiet_now() -> bool:
    """True while the user's notification quiet hours are active."""
    try:
        import notifications
        if _s("notify_quiet_enabled", False):
            return bool(notifications._in_quiet_hours(str(_s("notify_quiet_start", "22:00")),
                                                      str(_s("notify_quiet_end", "07:00"))))
    except Exception as e:  # noqa: BLE001
        log.debug("proactive_extra quiet check failed: %s", e)
    return False


def _alert(kind: str, sender: str, text: str) -> bool:
    """Show one heads-up. Returns False (and sends nothing) during quiet hours."""
    if _quiet_now():
        log.debug("proactive_extra: %s held back by quiet hours", kind)
        return False
    try:
        import ui_bridge
        ui_bridge.push_notification("reminder", "reminder", sender, text)
        if _s("feature_mascot", False) is True:
            ui_bridge._push_event({"type": "mascot", "cmd": "emote", "name": _EMOTES.get(kind, "happy")})
        if kind in ("break", "bedtime"):
            import mochi_sounds
            mochi_sounds.emit("tired", text)      # the yawn + a subtitle (no-op in "speak" mode)
    except Exception as e:  # noqa: BLE001
        log.debug("proactive_extra notify failed: %s", e)
        return False
    log.info("proactive_extra: %s - %s", kind, text)
    return True


def _cool(key: str, seconds: float, now: float) -> bool:
    """True if `key` has not fired within `seconds`."""
    return now - _last_fired.get(key, 0.0) >= seconds


# ── idle time ────────────────────────────────────────────────────────────────────────────────

def idle_seconds() -> float | None:
    """Seconds since the last keyboard / mouse input (None if it cannot be read, e.g. not Windows)."""
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ticks = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((ticks - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception as e:  # noqa: BLE001
        log.debug("proactive_extra idle read failed: %s", e)
        return None


# ── deadlines ────────────────────────────────────────────────────────────────────────────────

def _state_file():
    import paths
    return paths.data_path("proactive_extra.json")


def _load_fired() -> set[str]:
    global _fired_deadlines
    if _fired_deadlines is None:
        _fired_deadlines = set()
        try:
            raw = json.loads(_state_file().read_text(encoding="utf-8"))
            _fired_deadlines = {str(k) for k in raw.get("fired", [])}
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            log.debug("proactive_extra: could not read state: %s", e)
    return _fired_deadlines


def _save_fired(now: float) -> None:
    fired = _load_fired()
    # keys end with the due time; forget anything due more than a day ago
    keep = set()
    for k in fired:
        try:
            if float(k.rsplit(":", 1)[1]) > now - 86400:
                keep.add(k)
        except (IndexError, ValueError):
            pass
    fired.intersection_update(keep)
    try:
        _state_file().write_text(json.dumps({"fired": sorted(fired)}), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.debug("proactive_extra: could not save state: %s", e)


def _schedule_items() -> list[dict]:
    """Upcoming scheduled reminders / actions with their creation time (copied, safe to read)."""
    try:
        import scheduler
        with scheduler._lock:
            return [dict(x) for x in scheduler._load()["items"]]
    except Exception as e:  # noqa: BLE001
        log.debug("proactive_extra: schedule read failed: %s", e)
        return []


def _when_words(due: float, now: float) -> str:
    d = datetime.fromtimestamp(due)
    n = datetime.fromtimestamp(now)
    day = "today" if d.date() == n.date() else "tomorrow" if (d.date() - n.date()).days == 1 else d.strftime("%A")
    return f"{day} at {d.strftime('%H:%M')}"


def due_alerts(items: list[dict], now: float, fired: set[str]) -> list[tuple[str, str, str]]:
    """Pure helper: which (key, sender, text) heads-ups are due right now.

    A one-day heads-up is sent while more than an hour remains, a one-hour heads-up during the
    last hour. An item created after a window opened (you just set it) never gets that window's
    heads-up. Sending the hour warning also retires the day warning.
    """
    out: list[tuple[str, str, str]] = []
    for it in items:
        try:
            due = float(it["due"])
            ident = str(it["id"])
            text = str(it["text"])
        except (KeyError, TypeError, ValueError):
            continue
        rem = due - now
        if rem <= 0:
            continue
        created = float(it.get("created") or _first_seen.setdefault(f"{ident}:{int(due)}", now))
        day_key, hour_key = f"{ident}:day:{int(due)}", f"{ident}:hour:{int(due)}"
        if rem <= 3600:
            if hour_key not in fired and created <= due - 3600:
                mins = max(1, int(round(rem / 60)))
                out.append((hour_key, "Coming up", f"{text} - in about {mins} minute{'s' if mins != 1 else ''}."))
            fired.add(day_key)      # too late for the one-day warning
        elif rem <= 86400:
            if day_key not in fired and created <= due - 86400:
                out.append((day_key, "Tomorrow" if "tomorrow" in _when_words(due, now) else "Coming up",
                            f"{text} - {_when_words(due, now)}."))
    return out


def _check_deadlines(now: float, debug: int) -> None:
    items = _schedule_items()
    if debug:
        if not _cool("deadline", debug, now):
            return
        upcoming = sorted((i for i in items if float(i.get("due", 0)) > now), key=lambda i: i["due"])
        text = f"{upcoming[0]['text']} - {_when_words(float(upcoming[0]['due']), now)}." if upcoming else "Test heads-up: nothing is scheduled."
        if _alert("deadline", "Coming up (test)", text):
            _last_fired["deadline"] = now
        return
    fired = _load_fired()
    changed = False
    for key, sender, text in due_alerts(items, now, fired):
        if _alert("deadline", sender, text):
            fired.add(key)
            changed = True
    if changed:
        _save_fired(now)


# ── break / hydration / bedtime ──────────────────────────────────────────────────────────────

def _check_activity(now: float, debug: int) -> None:
    global _active_since, _last_water
    idle = idle_seconds()
    present = idle is not None and idle < IDLE_RESET_S
    if idle is not None and idle >= IDLE_RESET_S:
        _active_since = None
    elif present and _active_since is None:
        _active_since = now
    if _last_water is None:
        _last_water = now

    if _on("proactive_break") and _active_since is not None:
        every = debug or max(COOLDOWN_S["break"], int(_s("proactive_break_minutes", 50)) * 60)
        if now - _active_since >= every and _cool("break", debug or COOLDOWN_S["break"], now):
            mins = max(1, int((now - _active_since) // 60))
            msg = f"You've been at it for {mins} minutes. Stand up and stretch for a moment."
            if debug:
                msg = "Test heads-up: break reminder."
            if _alert("break", "Break time?", msg):
                _last_fired["break"] = now
                _active_since = now

    if _on("proactive_hydration", False) and present:
        every = debug or max(COOLDOWN_S["hydration"], int(_s("proactive_hydration_minutes", 90)) * 60)
        if now - _last_water >= every and _cool("hydration", debug or COOLDOWN_S["hydration"], now):
            msg = "Test heads-up: hydration reminder." if debug else "Time for a glass of water."
            if _alert("hydration", "Hydration", msg):
                _last_fired["hydration"] = now
                _last_water = now


def _check_bedtime(now: float, debug: int) -> None:
    global _bedtime_day
    n = datetime.fromtimestamp(now)
    today = n.strftime("%Y-%m-%d")
    if debug:
        if _cool("bedtime", debug, now) and _alert("bedtime", "Wind down", "Test heads-up: bedtime reminder."):
            _last_fired["bedtime"] = now
        return
    if _bedtime_day == today:
        return
    h, _, m = str(_s("proactive_bedtime_time", "23:00")).partition(":")
    if not (h.isdigit() and m.isdigit()):
        return
    start = int(h) * 60 + int(m)
    cur = n.hour * 60 + n.minute
    if start <= cur < start + 45:       # the nudge may be late by up to 45 minutes (Lyra started then)
        if _alert("bedtime", "Wind down", "It's getting late. Time to save your work and wind down."):
            _bedtime_day = today


# ── CPU / RAM / disk ─────────────────────────────────────────────────────────────────────────

def _clean(name: str) -> str:
    n = (name or "a process").strip()
    return n[:-4] if n.lower().endswith(".exe") else n


_IGNORE = {"system idle process", "idle", "system", "memcompression", "registry"}


def _top_cpu():
    import psutil
    procs = []
    for p in psutil.process_iter(["name"]):
        try:
            p.cpu_percent(None)
            procs.append(p)
        except Exception:
            pass
    time.sleep(0.5)
    ncpu = psutil.cpu_count() or 1
    best = (None, 0.0)
    for p in procs:
        try:
            name = (p.info.get("name") or "")
            if p.pid == os.getpid() or name.lower() in _IGNORE:
                continue
            c = p.cpu_percent(None) / ncpu
            if c > best[1]:
                best = (name, c)
        except Exception:
            pass
    return best


def _top_ram():
    import psutil
    best = (None, 0)
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            name = (p.info.get("name") or "")
            mi = p.info.get("memory_info")
            if mi is None or name.lower() in _IGNORE:
                continue
            if mi.rss > best[1]:
                best = (name, mi.rss)
        except Exception:
            pass
    return best


def _check_resources(now: float, debug: int) -> None:
    import psutil
    cpu_thr = 0 if debug else int(_s("proactive_cpu_percent", 90))
    ram_thr = 0 if debug else int(_s("proactive_ram_percent", 90))
    disk_thr = 0 if debug else int(_s("proactive_disk_percent", 92))
    sustain = 1 if debug else SUSTAIN_TICKS

    cpu = psutil.cpu_percent(None)
    _hi_count["cpu"] = _hi_count["cpu"] + 1 if cpu >= cpu_thr else 0
    if _hi_count["cpu"] >= sustain and _cool("cpu", debug or COOLDOWN_S["cpu"], now):
        name, c = _top_cpu()
        who = f" {_clean(name)} is the busiest, using about {c:.0f}%." if name else ""
        if _alert("cpu", "High CPU", f"Your CPU is at {cpu:.0f}%.{who}"):
            _last_fired["cpu"] = now
            _hi_count["cpu"] = 0

    ram = psutil.virtual_memory().percent
    _hi_count["ram"] = _hi_count["ram"] + 1 if ram >= ram_thr else 0
    if _hi_count["ram"] >= sustain and _cool("ram", debug or COOLDOWN_S["ram"], now):
        name, rss = _top_ram()
        who = f" {_clean(name)} is using {rss / 1024 ** 3:.1f} GB." if name else ""
        if _alert("ram", "High memory", f"Memory is {ram:.0f}% full.{who}"):
            _last_fired["ram"] = now
            _hi_count["ram"] = 0

    drive = (os.environ.get("SystemDrive") or "C:") + "\\" if sys.platform == "win32" else "/"
    try:
        du = psutil.disk_usage(drive)
    except Exception:
        return
    if du.percent >= disk_thr and _cool("disk", debug or COOLDOWN_S["disk"], now):
        free_gb = du.free / 1024 ** 3
        if _alert("disk", "Disk almost full", f"Drive {drive.rstrip(chr(92))} is {du.percent:.0f}% full, {free_gb:.1f} GB free."):
            _last_fired["disk"] = now


# ── loop ─────────────────────────────────────────────────────────────────────────────────────

def tick(now: float | None = None) -> None:
    """One look around. Each type is guarded on its own so one failure never blocks the others."""
    now = time.time() if now is None else now
    try:
        import settings_store
        if not settings_store.feature_on("proactive_extra"):
            return
    except Exception:
        pass
    debug = _debug_s()
    with _lock:
        if _on("proactive_deadlines"):
            try:
                _check_deadlines(now, debug)
            except Exception as e:  # noqa: BLE001
                log.debug("proactive_extra deadlines: %s", e)
        try:
            _check_activity(now, debug)
        except Exception as e:  # noqa: BLE001
            log.debug("proactive_extra activity: %s", e)
        if _on("proactive_bedtime", False):
            try:
                _check_bedtime(now, debug)
            except Exception as e:  # noqa: BLE001
                log.debug("proactive_extra bedtime: %s", e)
        if _on("proactive_resources"):
            try:
                _check_resources(now, debug)
            except Exception as e:  # noqa: BLE001
                log.debug("proactive_extra resources: %s", e)


def _loop() -> None:
    try:
        import psutil
        psutil.cpu_percent(None)        # the first reading is meaningless; prime it
    except Exception:
        pass
    time.sleep(STARTUP_DELAY_S)
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            log.debug("proactive_extra loop: %s", e)
        d = _debug_s()
        time.sleep(min(TICK_S, d) if d else TICK_S)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="lyra-proactive-extra", daemon=True).start()


if __name__ == "__main__":
    if "--once" in sys.argv:
        tick()
        print("proactive_extra: one tick done (see ui.log)")
