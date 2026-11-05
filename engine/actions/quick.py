"""Quick abilities and phrase normalisation.

Two jobs, both cheap and deterministic (no LLM, no network except live currency rates):

1. normalize(): rewrite the many ways people phrase a command into the one wording the older
   pattern matchers understand ("lock my pc" -> "lock the pc").
2. try_quick(): small abilities that did not exist yet - battery, active window, disk space,
   time left on a timer, percentages, simple maths, unit and currency conversion, and creating
   a folder or file without naming a location (it goes on the Desktop).

Everything here answers with a short sentence that Lyra can speak.
"""
from __future__ import annotations

import ast
import json
import operator
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path

# ── 1. phrase normalisation ──────────────────────────────────────────────────────────────────

_NORMALIZE: list[tuple[re.Pattern, str]] = [
    # lock
    (re.compile(r"\block\s+(?:my|this|the|that)?\s*(?:pc|computer|laptop|screen|device|windows)\b", re.I),
     "lock the pc"),
    # clipboard
    (re.compile(r"\b(?:what(?:'s|\s+is|s)\s+(?:in|on)\s+(?:my|the)\s+clipboard"
                r"|what\s+did\s+i\s+(?:just\s+)?copy"
                r"|(?:read|show|tell)(?:\s+me)?(?:\s+out)?\s+(?:my|the)\s+clipboard)\b", re.I),
     "read the clipboard"),
    # snap a window
    (re.compile(r"\b(?:snap|dock)\s+(?:this\s+|the\s+|that\s+)?(?:window\s+)?(?:to\s+|on\s+)?(?:the\s+)?"
                r"(?P<side>left|right|up|down)\b", re.I), r"snap \g<side>"),
    (re.compile(r"\b(?:put|move|send)\s+(?:this|that|the)\s+window\s+(?:to\s+|on\s+)?(?:the\s+)?"
                r"(?P<side>left|right)\b", re.I), r"snap \g<side>"),
    # list windows
    (re.compile(r"\b(?:what\s+windows\s+(?:are|do\s+i\s+have)\s+open"
                r"|(?:list|show)(?:\s+me)?\s+(?:my\s+|all\s+|the\s+)?(?:open\s+)?windows"
                r"|what(?:'s|\s+is)\s+open)\b", re.I), "list windows"),
]


def normalize(command: str) -> str:
    out = command
    for pat, repl in _NORMALIZE:
        out = pat.sub(repl, out)
    return out


# ── helpers ──────────────────────────────────────────────────────────────────────────────────

def _num(s: str) -> float:
    return float(s.replace(",", ".")) if s.count(",") == 1 and "." not in s else float(s.replace(",", ""))


def _fmt(x: float) -> str:
    if abs(x) >= 1000:
        return f"{x:,.0f}" if abs(x - round(x)) < 0.005 else f"{x:,.2f}"
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _gb(n: float) -> str:
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024 ** 3:.0f} GB" if n >= 10 * 1024 ** 3 else f"{n / 1024 ** 3:.1f} GB"


# ── 2a. battery ──────────────────────────────────────────────────────────────────────────────

def _battery(low: str) -> str | None:
    if not re.search(r"\b(?:battery|charge level|plugged in|how much charge|charging)\b", low):
        return None
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return "I can't read a battery on this PC. It may be a desktop."
    pct = int(round(b.percent))
    if b.power_plugged:
        return f"Battery is at {pct} percent and plugged in" + (", fully charged." if pct >= 99 else ", charging.")
    left = ""
    if b.secsleft and b.secsleft > 0:
        h, m = divmod(int(b.secsleft) // 60, 60)
        left = f", about {h} hour{'s' if h != 1 else ''} {m} minutes left" if h else f", about {m} minutes left"
    return f"Battery is at {pct} percent and not charging{left}."


# ── 2b. active window ────────────────────────────────────────────────────────────────────────

def _active_window(low: str) -> str | None:
    if not re.search(r"\b(?:which|what)\s+(?:window|app|application)\s+(?:is|am\s+i)\s*(?:currently\s+)?"
                     r"(?:active|focused|in\s+front|open|in|using|on)\b"
                     r"|\b(?:active|current|focused|foreground)\s+(?:window|app)\b", low):
        return None
    try:
        import ctypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        title = buf.value.strip()
    except Exception:
        return "I couldn't read the active window."
    return f"You're in {title}." if title else "No window seems to be active."


# ── 2c. disk space ───────────────────────────────────────────────────────────────────────────

def _disk(low: str) -> str | None:
    if not re.search(r"\b(?:disk|drive|storage)\s+space\b|\bhow\s+much\s+(?:space|storage)\b|\bfree\s+space\b"
                     r"|\bspace\s+(?:left|free)\b|\bstorage\s+left\b", low):
        return None
    try:
        import psutil
        parts = []
        for p in psutil.disk_partitions(all=False):
            if "cdrom" in p.opts.lower() or not p.fstype:
                continue
            try:
                u = shutil.disk_usage(p.mountpoint)
            except Exception:
                continue
            if u.total == 0:
                continue
            parts.append(f"{p.mountpoint.rstrip(chr(92)).rstrip(':')}: {_gb(u.free)} free of {_gb(u.total)}")
        if parts:
            return "Disk space - " + "; ".join(parts) + "."
    except Exception:
        pass
    return "I couldn't read the disk space."


# ── 2d. time left on timers ──────────────────────────────────────────────────────────────────

def _time_left(low: str) -> str | None:
    if not re.search(r"\bhow\s+(?:much\s+)?(?:time|long)\s+(?:is\s+)?(?:left|remaining)\b"
                     r"|\btime\s+(?:left|remaining)\b|\bwhat(?:'s|\s+is)\s+left\s+on\s+(?:the|my)\s+timer\b", low):
        return None
    try:
        from engine.features import timers
        items = timers.snapshot()["items"]
        say = timers._say
    except Exception:
        return "I couldn't read your timers."
    lines = []
    for it in items:
        name = (it.get("label") or it["kind"]).strip()
        if it["kind"] == "stopwatch":
            lines.append(f"the stopwatch is at {say((it.get('elapsed_ms') or 0) / 1000)}")
        elif it.get("remaining_ms") is not None:
            tail = " (paused)" if it["state"] == "paused" else " left"
            lines.append(f"{name}: {say(it['remaining_ms'] / 1000)}{tail}")
    if not lines:
        return "No timer is running."
    return "; ".join(lines).capitalize() + "."


# ── 2e. percentages and simple maths ─────────────────────────────────────────────────────────

_PERCENT_OF = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:%|percent)\s+of\s+(\d+(?:[.,]\d+)?)")
_WORD_OPS = [
    (r"\bmultiplied\s+by\b|\btimes\b|\bx\b|\u00d7", "*"), (r"\bdivided\s+by\b|\bover\b|\u00f7", "/"),
    (r"\bplus\b", "+"), (r"\bminus\b", "-"), (r"\bto\s+the\s+power\s+of\b", "**"),
]
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _safe_eval(expr: str) -> float:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 12 or abs(a) > 1e6):
                raise ValueError("too big")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("unsupported")
    return ev(ast.parse(expr, mode="eval"))


def _percent_of(low: str) -> str | None:
    m = _PERCENT_OF.search(low)
    if not m:
        return None
    p, base = _num(m.group(1)), _num(m.group(2))
    return f"{_fmt(p)} percent of {_fmt(base)} is {_fmt(base * p / 100.0)}."


def _calc(low: str) -> str | None:
    m = re.match(r"^(?:what(?:'s|\s+is)|calculate|compute|how\s+much\s+is)\s+(.+)$", low)
    if not m:
        return None
    expr = m.group(1)
    for pat, op in _WORD_OPS:
        expr = re.sub(pat, f" {op} ", expr)
    expr = expr.replace(",", "")
    if not re.fullmatch(r"[\d\s.+\-*/()]+", expr) or not re.search(r"\d\s*[+\-*/]+\s*\d|\d\s*\*\*\s*\d", expr.replace(" ", "")):
        return None
    try:
        val = _safe_eval(expr.strip())
    except Exception:
        return None
    return f"{m.group(1).strip()} is {_fmt(val)}."


# ── 2f. unit and currency conversion ─────────────────────────────────────────────────────────

# unit -> (category, factor to the category's base unit)
_UNITS: dict[str, tuple[str, float]] = {}


def _add(cat: str, base_factor: float, *names: str) -> None:
    for n in names:
        _UNITS[n] = (cat, base_factor)


_add("len", 1.0, "m", "meter", "meters", "metre", "metres")
_add("len", 1000.0, "km", "kilometer", "kilometers", "kilometre", "kilometres", "k")
_add("len", 0.01, "cm", "centimeter", "centimeters", "centimetre", "centimetres")
_add("len", 0.001, "mm", "millimeter", "millimeters")
_add("len", 1609.344, "mi", "mile", "miles")
_add("len", 0.3048, "ft", "foot", "feet")
_add("len", 0.0254, "in", "inch", "inches")
_add("len", 0.9144, "yd", "yard", "yards")
_add("mass", 1.0, "kg", "kilo", "kilos", "kilogram", "kilograms")
_add("mass", 0.001, "g", "gram", "grams")
_add("mass", 0.45359237, "lb", "lbs", "pound", "pounds")
_add("mass", 0.028349523, "oz", "ounce", "ounces")
_add("vol", 1.0, "l", "liter", "liters", "litre", "litres")
_add("vol", 0.001, "ml", "milliliter", "milliliters", "millilitre", "millilitres")
_add("vol", 3.785411784, "gal", "gallon", "gallons")
_add("vol", 0.2365882365, "cup", "cups")
_add("spd", 1.0, "kmh", "kph", "km/h")
_add("spd", 1.609344, "mph", "mi/h")
_TEMP = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}

_CURRENCY = {
    "usd": "USD", "dollar": "USD", "dollars": "USD", "buck": "USD", "bucks": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "gbp": "GBP", "sterling": "GBP", "quid": "GBP",
    "mad": "MAD", "dirham": "MAD", "dirhams": "MAD",
    "jpy": "JPY", "yen": "JPY", "cad": "CAD", "aud": "AUD", "chf": "CHF", "cny": "CNY", "yuan": "CNY",
    "inr": "INR", "rupee": "INR", "rupees": "INR", "aed": "AED", "sar": "SAR", "egp": "EGP",
    "try": "TRY", "lira": "TRY", "rub": "RUB", "ruble": "RUB", "rubles": "RUB", "brl": "BRL",
}
_rates: dict[str, tuple[float, dict]] = {}

_CONVERT = re.compile(
    r"^(?:convert\s+|how\s+(?:much|many)\s+is\s+|what(?:'s|\s+is)\s+)?(\d+(?:[.,]\d+)?)\s*(?:degrees?\s+)?"
    r"([a-z/\u00b0 ]+?)\s+(?:to|in|into|=)\s+(?:the\s+)?(?:degrees?\s+)?([a-z/\u00b0 ]+)$"
)


def _clean_unit(u: str) -> str:
    return u.strip().replace("\u00b0", "").strip()


def _fetch_rates(base: str) -> dict | None:
    hit = _rates.get(base)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        with urllib.request.urlopen(f"https://open.er-api.com/v6/latest/{base}", timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("result") == "success":
            _rates[base] = (time.time(), data["rates"])
            return data["rates"]
    except Exception:
        return None
    return None


def _convert(low: str) -> str | None:
    m = _CONVERT.match(low)
    if not m:
        return None
    val = _num(m.group(1))
    a, b = _clean_unit(m.group(2)), _clean_unit(m.group(3))
    # temperature
    if a in _TEMP and b in _TEMP:
        fa, fb = _TEMP[a], _TEMP[b]
        c = val if fa == "c" else (val - 32) * 5 / 9 if fa == "f" else val - 273.15
        out = c if fb == "c" else c * 9 / 5 + 32 if fb == "f" else c + 273.15
        names = {"c": "degrees Celsius", "f": "degrees Fahrenheit", "k": "kelvin"}
        return f"{_fmt(val)} {names[fa]} is {_fmt(out)} {names[fb]}."
    # physical units
    ua, ub = _UNITS.get(a), _UNITS.get(b)
    if ua and ub and ua[0] == ub[0]:
        out = val * ua[1] / ub[1]
        return f"{_fmt(val)} {a} is {_fmt(out)} {b}."
    # currency
    ca, cb = _CURRENCY.get(a), _CURRENCY.get(b)
    if ca and cb:
        rates = _fetch_rates(ca)
        if not rates or cb not in rates:
            return "I couldn't get live exchange rates right now."
        return f"{_fmt(val)} {ca} is about {_fmt(val * rates[cb])} {cb}."
    return None


# ── 2h. "why is my PC slow" ──────────────────────────────────────────────────────────────────

def _pc_slow(low: str) -> str | None:
    if not re.search(r"\bwhy\s+is\s+(?:my\s+)?(?:pc|computer|laptop|it)\s+(?:so\s+)?(?:slow|lagging|laggy|freezing)\b"
                     r"|\b(?:pc|computer|laptop)\s+(?:is\s+)?(?:slow|lagging|laggy|running\s+slow)\b"
                     r"|\bwhat(?:'s|\s+is)\s+(?:using|eating|hogging)\s+(?:my\s+)?(?:cpu|ram|memory)\b"
                     r"|\b(?:cpu|ram|memory)\s+usage\b", low):
        return None
    try:
        import psutil
        procs = list(psutil.process_iter(["name"]))
        for p in procs:
            try:
                p.cpu_percent(None)
            except Exception:
                pass
        cpu = psutil.cpu_percent(interval=0.8)
        vm = psutil.virtual_memory()
        by_cpu: dict[str, float] = {}
        by_mem: dict[str, float] = {}
        for p in procs:
            try:
                name = (p.info.get("name") or "?").replace(".exe", "")
                by_cpu[name] = by_cpu.get(name, 0.0) + p.cpu_percent(None) / (psutil.cpu_count() or 1)
                by_mem[name] = by_mem.get(name, 0.0) + p.memory_info().rss
            except Exception:
                continue
        top_cpu = sorted(by_cpu.items(), key=lambda kv: -kv[1])[:3]
        top_mem = sorted(by_mem.items(), key=lambda kv: -kv[1])[:3]
        cpu_txt = ", ".join(f"{n} {v:.0f}%" for n, v in top_cpu if v >= 1) or "nothing heavy"
        mem_txt = ", ".join(f"{n} {_gb(v)}" for n, v in top_mem)
        verdict = ""
        if vm.percent >= 85:
            verdict = " Memory is nearly full - closing some of those would help."
        elif cpu >= 85:
            verdict = " The processor is busy - the first ones are the likely cause."
        return (f"CPU is at {cpu:.0f} percent and memory at {vm.percent:.0f} percent. "
                f"Busiest: {cpu_txt}. Most memory: {mem_txt}.{verdict}")
    except Exception:
        return "I couldn't read the system load."


# ── 2i. weather and the morning brief ────────────────────────────────────────────────────────

_WEATHER = re.compile(r"\b(?:weather|temperature\s+outside|how\s+(?:hot|cold|warm)\s+is\s+it|is\s+it\s+(?:going\s+to\s+)?(?:rain|raining|snow|sunny)|will\s+it\s+rain)\b")
_CITY = re.compile(r"\b(?:in|for|at)\s+([a-z][a-z .'-]{1,30}?)(?:\s+(?:today|tomorrow|now|tonight|right\s+now))?$")


def _weather_sentence(city: str = "") -> str | None:
    try:
        from engine.features import weather_widget
        if not city:
            try:
                from engine.core import settings_store
                city = str(settings_store.get("weather_city") or settings_store.get("city") or "").strip()
            except Exception:
                city = ""
        w = weather_widget.get(city)
    except Exception:
        return None
    if not w or not w.get("ok"):
        return None
    cond = (w.get("condition") or "").lower()
    where = f" in {w['city']}" if w.get("city") else ""
    return f"It's {w['temp']} degrees{(' and ' + cond) if cond else ''}{where}, high {w['hi']}, low {w['lo']}."


def _weather(low: str) -> str | None:
    if not _WEATHER.search(low) or re.match(r"^(?:open|launch|start|close|run|show\s+me\s+the)\b", low):
        return None
    m = _CITY.search(low)
    city = m.group(1).strip() if m else ""
    if city in ("my", "the", "it", "my city", "here", "my area", "my location"):
        city = ""
    sentence = _weather_sentence(city)
    if not sentence:
        return "I couldn't get the weather right now - check the internet connection."
    if re.search(r"\b(?:rain|raining|snow|sunny)\b", low):
        try:
            from engine.features import weather_widget
            w = weather_widget.get(city)
            kinds = {w.get("kind")} | {h["kind"] for h in w.get("hourly", [])}
            wet = kinds & {"rain", "storm"}
            snow = "snow" in kinds
            lead = "Yes, rain is likely soon. " if wet else "Yes, snow is likely soon. " if snow else "No rain expected in the next hours. "
            return lead + sentence
        except Exception:
            pass
    return sentence


_BRIEF = re.compile(r"\b(?:morning\s+brief(?:ing)?|daily\s+brief(?:ing)?|brief\s+me|what(?:'s|\s+is)\s+my\s+day|how(?:'s|\s+is)\s+my\s+day\s+looking"
                    r"|catch\s+me\s+up|start\s+my\s+day)\b")


def _brief(low: str) -> str | None:
    if not _BRIEF.search(low):
        return None
    hour = time.localtime().tm_hour
    parts = ["Good morning." if hour < 12 else "Good afternoon." if hour < 18 else "Good evening."]
    w = _weather_sentence()
    if w:
        parts.append(w)
    try:
        from engine.features import timers
        left = [it for it in timers.snapshot()["items"] if it.get("remaining_ms") is not None]
        if left:
            parts.append(f"You have {len(left)} timer{'s' if len(left) != 1 else ''} running.")
    except Exception:
        pass
    try:
        from engine.actions import notes
        todo = notes._load()["lists"].get("to-do", [])
        if todo:
            parts.append(f"On your to-do list: {notes._say_items(todo[:5])}.")
        else:
            parts.append("Your to-do list is empty.")
    except Exception:
        pass
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is not None and not b.power_plugged and b.percent <= 30:
            parts.append(f"Battery is low at {int(b.percent)} percent.")
    except Exception:
        pass
    return " ".join(parts)


# ── 2g. create a folder / file without a location -> Desktop ─────────────────────────────────

_CREATE_HERE = re.compile(
    r"^(?:please\s+)?(?:create|make)\s+(?:a\s+|an\s+|new\s+)*(?P<kind>folder|directory|file|text\s+file)\s+"
    r"(?:called\s+|named\s+)?(?P<name>[\w][\w .\-]{0,60}?)\s*$"
)


def _undo_hint(hint: dict) -> None:
    try:
        from engine.features import activity_log
        activity_log.set_undo(hint)
    except Exception:
        pass


def _desktop() -> Path:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for c in (home / "Desktop", home / "OneDrive" / "Desktop", home):
        if c.is_dir():
            return c
    return home


def _create_here(low: str, original: str) -> str | None:
    m = _CREATE_HERE.match(low)
    if not m or re.search(r"\b(?:in|on)\s+\S", low):
        return None  # a location was given: the older file handlers deal with it
    # keep the user's capitalisation for the name
    mo = re.search(r"(?:called|named)\s+(.+?)\s*$|(?:folder|directory|file)\s+(.+?)\s*$", original.strip().rstrip(".!"), re.I)
    name = (mo.group(1) or mo.group(2)) if mo else m.group("name")
    name = re.sub(r"^(?:called|named)\s+", "", name.strip(), flags=re.I).strip().strip("\"'")
    if not name or re.search(r"[\\/:*?\"<>|]", name) or name in (".", "..") or ".." in name:
        return "That name has characters Windows doesn't allow."
    kind = m.group("kind")
    target = _desktop() / name
    try:
        if kind in ("folder", "directory"):
            if target.exists():
                return f"There is already a folder called {name} on your Desktop."
            target.mkdir()
            _undo_hint({"type": "delete_path", "path": str(target)})
            return f"Created the folder {name} on your Desktop."
        if "." not in name:
            target = target.with_name(name + ".txt")
        if target.exists():
            return f"There is already a file called {target.name} on your Desktop."
        target.touch()
        _undo_hint({"type": "delete_path", "path": str(target)})
        return f"Created the file {target.name} on your Desktop."
    except Exception as e:
        return f"I couldn't create it - {e}"


# ── entry point ──────────────────────────────────────────────────────────────────────────────

_STATS_SHOW = re.compile(
    r"^(?:(?:show|open|pull\s+up|bring\s+up|go\s+to)(?:\s+me)?\s+(?:the\s+|my\s+)?(?:(?:pc|computer|system)\s+)?"
    r"(?:stats|statistics|performance)(?:\s+(?:tab|page|view))?"
    r"|(?:pc|computer|system)\s+stats)$")


def _stats_view(low: str) -> str | None:
    """\"show my stats\": open the island's Stats tab."""
    if not _STATS_SHOW.match(low):
        return None
    try:
        from engine.bridge import ui_bridge
        ui_bridge.push_stats(True)
    except Exception:
        return "I couldn't open the stats."
    return "Here are your stats."


def _tabs_view(low: str) -> str | None:
    """\"open the timer tab\", \"show the tab wheel\": see actions/tabs.py."""
    try:
        from engine.actions import tabs
        return tabs.try_tabs(low)
    except Exception:
        return None


def _window_cmd(low: str) -> str | None:
    """"minimize", "maximize", "restore", "snap left", "close window": done directly. Without this the planner
    treated them as "click the minimize button" and the screen reader could not find it."""
    if len(low.split()) > 6:
        return None
    try:
        from engine.actions import windows_mgr as w
    except Exception:
        return None
    if w.MINIMIZE_PATTERN.search(low):
        return w.minimize_active()
    if w.MAXIMIZE_PATTERN.search(low):
        return w.maximize_active()
    if re.fullmatch(r"(?:restore|unmaximize|unmaximise)(?:\s+(?:the\s+|this\s+)?window)?", low):
        return w.restore_active()
    m = w.SNAP_PATTERN.search(low)
    if m:
        return w.snap_active(m.group("side"))
    if re.fullmatch(r"close\s+(?:the\s+|this\s+)?window", low):
        return w.close_active()
    return None


def try_quick(command: str) -> str | None:
    original = command.strip()
    low = original.lower().rstrip("?.! ")
    low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", low).strip()
    if not low:
        return None
    for fn in (_stats_view, _tabs_view, _window_cmd, _battery, _active_window, _disk, _time_left, _pc_slow, _brief, _weather, _percent_of, _calc, _convert):
        r = fn(low)
        if r:
            return r
    try:                                  # create files/folders anywhere (default Desktop), find and open them by name
        from engine.actions import file_ops
        r = file_ops.try_file_ops(original)
        if r:
            return r
    except Exception:
        pass
    r = _create_here(low, original)
    if r:
        return r
    # More helpers, each with its own safe import so one broken module never blocks the rest.
    for mod, fn in (("notes", "try_notes"), ("help_cmd", "try_help"), ("downloads_sort", "try_sort"),
                    ("snippets", "try_snippets"), ("game_volume", "try_game_volume"),
                    ("listen_summary", "try_listen"), ("screen_tools", "try_screen")):
        try:
            import importlib
            reply = getattr(importlib.import_module(f"engine.actions.{mod}"), fn)(original)
        except Exception:
            continue
        if reply:
            return reply
    return None
