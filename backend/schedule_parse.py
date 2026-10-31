"""Lyra - parsing for scheduled actions and routines (English, pure functions, no I/O).

One-off schedule:
    "search Amazon for gift wraps on 10 November at 11:00"   -> action
    "remind me to call Mom tomorrow at 9"                    -> reminder
Routines:
    "every morning at 9:00 open Unity and Spotify"           -> time trigger, two actions
    "when Lyra starts, open Spotify"                         -> startup trigger
    "when I say work mode, open VS Code and Unity"           -> phrase trigger

Relative times ("remind me in 20 minutes ...") belong to timers.py and are left alone.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_WD_RE = "|".join(WEEKDAYS)
_WD_STEM = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"

# default time of day when only "morning" / "evening"... is given
PART_TIME = {"morning": (9, 0), "afternoon": (14, 0), "evening": (19, 0), "night": (22, 0), "tonight": (20, 0)}

# Things Lyra can be asked to do later (the first word of the command).
ACTION_VERBS = {
    "open", "launch", "start", "run", "search", "google", "look", "find", "play", "pause", "resume",
    "mute", "unmute", "close", "quit", "lock", "send", "text", "message", "email", "call", "take",
    "screenshot", "show", "check", "download", "turn", "volume",
}
# Extra first words accepted inside a routine's action list.
ROUTINE_VERBS = ACTION_VERBS | {
    "set", "enable", "disable", "switch", "increase", "decrease", "lower", "raise", "remind", "tell",
    "read", "clear", "minimize", "minimise", "maximize", "maximise", "stop", "skip", "next", "previous",
}
_THIRD_PERSON = {"opens": "open", "launches": "launch", "starts": "start", "runs": "run", "searches": "search",
                 "plays": "play", "mutes": "mute", "unmutes": "unmute", "closes": "close", "quits": "quit",
                 "pauses": "pause", "locks": "lock", "shows": "show", "checks": "check"}
_DISTRIBUTIVE = {"open", "launch", "start", "close", "quit", "mute", "unmute", "run"}

_QUESTION = re.compile(r"^(?:what|what's|whats|when|where|who|who's|why|how|which|is|are|do|does|did|am|can i|could i)\b", re.I)
_POLITE = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?", re.I)
_SHORT_RELATIVE = re.compile(
    r"\b(?:in|after)\s+(?:\d+|an?|half\s+an?|one|two|three|four|five|ten|fifteen|twenty|thirty)\s*"
    r"(?:sec|secs|second|seconds|min|mins|minute|minutes|hr|hrs|hour|hours)\b", re.I)

_TIME_RES = [
    # 11:00, at 9:30 pm
    re.compile(r"\b(?:at\s+)?(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>a\.?m\.?|p\.?m\.?)?(?![\d:])", re.I),
    # at 9, at 9 pm, at 9 o'clock
    re.compile(r"\bat\s+(?P<h>\d{1,2})(?:\s*(?P<ap>a\.?m\.?|p\.?m\.?)|\s*o'?clock)?(?![\d:])", re.I),
    # 9pm
    re.compile(r"\b(?P<h>\d{1,2})\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?=\W|$)", re.I),
    # at noon / midnight
    re.compile(r"\bat\s+(?P<word>noon|midday|midnight)\b", re.I),
]
_DATE_RES = [
    ("dm", re.compile(rf"\b(?:on\s+)?(?:the\s+)?(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mo>{_MONTH_RE})\b(?:\s*,?\s*(?P<y>20\d\d))?", re.I)),
    ("md", re.compile(rf"\b(?:on\s+)?(?P<mo>{_MONTH_RE})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*:)(?:\s*,?\s*(?P<y>20\d\d))?", re.I)),
    ("rel", re.compile(r"\b(?P<w>day\s+after\s+tomorrow|tomorrow|today|tonight)\b", re.I)),
    ("wd", re.compile(rf"\b(?:on\s+)?(?P<mod>next\s+|this\s+|coming\s+)?(?P<wd>{_WD_RE})\b", re.I)),
    ("in", re.compile(r"\bin\s+(?P<n>\d+|a|an|one|two|three|four|five|six|seven)\s+(?P<u>day|days|week|weeks)\b", re.I)),
]
_PART_RE = re.compile(r"\b(?:(?:this|tomorrow|in\s+the)\s+)(?P<p>morning|afternoon|evening)\b|\b(?P<tn>tonight)\b", re.I)
_NUMWORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


# ── helpers ──────────────────────────────────────────────────────────────────────────────────

def normalize(command: str) -> str:
    s = re.sub(r"\s+", " ", (command or "").strip())
    s = _POLITE.sub("", s)
    s = re.sub(r"[\s,]*\bplease\s*[.!?]*$", "", s, flags=re.I)
    return s.strip(" .!?")


def _hour(h: int, ap: str | None) -> int | None:
    ap = (ap or "").lower().replace(".", "")
    if ap:
        if not 1 <= h <= 12:
            return None
        return (h % 12) + (12 if ap == "pm" else 0)
    return h if 0 <= h <= 23 else None


def _find_time(text: str):
    """(hour, minute, span, explicit) for the first time of day in `text`, or None.
    `explicit` is False for a bare 1-11 ("at 9"), where AM or PM is a guess."""
    for rx in _TIME_RES:
        for m in rx.finditer(text):
            gd = m.groupdict()
            if gd.get("word"):
                hh, mm = {"noon": (12, 0), "midday": (12, 0), "midnight": (0, 0)}[gd["word"].lower()]
                return hh, mm, m.span(), True
            h = _hour(int(gd["h"]), gd.get("ap"))
            mi = int(gd["m"]) if gd.get("m") else 0
            if h is None or not 0 <= mi <= 59:
                continue
            return h, mi, m.span(), bool(gd.get("ap")) or h >= 12 or h == 0
    return None


def _find_date(text: str, now: datetime):
    """(date, span, explicit_future_kind) for the first date phrase, or None."""
    for kind, rx in _DATE_RES:
        for m in rx.finditer(text):
            gd = m.groupdict()
            today = now.date()
            if kind in ("dm", "md"):
                mo, day = MONTHS[gd["mo"].lower()], int(gd["d"])
                year = int(gd["y"]) if gd.get("y") else today.year
                try:
                    d = date(year, mo, day)
                except ValueError:
                    continue
                if not gd.get("y") and d < today:
                    try:
                        d = date(year + 1, mo, day)
                    except ValueError:
                        continue
                return d, m.span(), kind
            if kind == "rel":
                w = re.sub(r"\s+", " ", gd["w"].lower())
                off = {"today": 0, "tonight": 0, "tomorrow": 1, "day after tomorrow": 2}[w]
                return today + timedelta(days=off), m.span(), "tonight" if w == "tonight" else "rel"
            if kind == "wd":
                wd = WEEKDAYS.index(gd["wd"].lower())
                mod = (gd.get("mod") or "").strip().lower()
                delta = (wd - today.weekday()) % 7
                if mod == "next" and delta == 0:
                    delta = 7
                return today + timedelta(days=delta), m.span(), "wd" if mod != "next" else "wdnext"
            if kind == "in":
                n = int(gd["n"]) if gd["n"].isdigit() else _NUMWORDS[gd["n"].lower()]
                days = n * (7 if gd["u"].lower().startswith("week") else 1)
                return today + timedelta(days=days), m.span(), "rel"
    return None


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    merged: list[list[int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in sorted(merged, reverse=True):
        text = text[:a] + " " + text[b:]
    return text


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    for _ in range(3):
        text = re.sub(r"(?:^|\s)(?:on|at|for|by|in|this|next|and)\s*$", "", text.strip(" ,;:-"), flags=re.I)
        text = re.sub(r"^(?:on|at|for|by|and)\s+", "", text.strip(" ,;:-"), flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" ,;:-.")


def parse_when(text: str, now: datetime | None = None):
    """Find a date/time in `text`.

    Returns (due, cleaned_text, defaulted_time) or None when there is no usable date or time.
    `cleaned_text` is `text` without the date/time words."""
    now = now or datetime.now()
    spans: list[tuple[int, int]] = []

    d_hit = _find_date(text, now)
    # the date phrase must not swallow the time ("on 10 November at 11:00" -> keep both)
    t_text = text if not d_hit else text[: d_hit[1][0]] + " " * (d_hit[1][1] - d_hit[1][0]) + text[d_hit[1][1]:]
    t_hit = _find_time(t_text)
    p_hit = _PART_RE.search(text)

    if not (d_hit or t_hit or p_hit):
        return None

    the_date = d_hit[0] if d_hit else None
    kind = d_hit[2] if d_hit else ""
    if d_hit:
        spans.append(d_hit[1])
    hh = mm = None
    defaulted = False
    guess_pm = False
    if t_hit:
        hh, mm = t_hit[0], t_hit[1]
        guess_pm = not t_hit[3]
        spans.append(t_hit[2])
    if p_hit:
        part = (p_hit.group("p") or p_hit.group("tn")).lower()
        if hh is None:
            hh, mm = PART_TIME[part]
            defaulted = True
        # "tomorrow morning": the word "tomorrow" is already the date; just drop "morning"
        if p_hit.group("tn") and the_date is None:
            the_date = now.date()
        spans.append(p_hit.span())
    if hh is None:
        hh, mm, defaulted = 9, 0, True
    if kind == "tonight" and not t_hit and not p_hit:
        hh, mm, defaulted = PART_TIME["tonight"][0], 0, True

    if the_date is None:
        due = datetime.combine(now.date(), datetime.min.time()).replace(hour=hh, minute=mm)
        if due <= now + timedelta(seconds=20):
            # "at 9" said at 10:00 most likely means 21:00 today, not 9:00 tomorrow
            pm = due + timedelta(hours=12)
            due = pm if (guess_pm and 1 <= hh <= 11 and pm > now + timedelta(seconds=20)) else due + timedelta(days=1)
    else:
        due = datetime.combine(the_date, datetime.min.time()).replace(hour=hh, minute=mm)
        if due <= now + timedelta(seconds=20):
            if kind == "wd":
                due += timedelta(days=7)
            elif kind in ("dm", "md") and not (d_hit and re.search(r"20\d\d", text[d_hit[1][0]:d_hit[1][1]])):
                due = due.replace(year=due.year + 1)
            # "today at 8" when it is already 10: keep it in the past on purpose -> caller says so
    return due, _tidy(_strip_spans(text, spans)), defaulted


# ── one-off schedule ─────────────────────────────────────────────────────────────────────────

_REMIND = [
    re.compile(r"^remind\s+me\s+(?P<b>.+)$", re.I),
    re.compile(r"^(?:set|add|create|make)\s+(?:me\s+)?(?:a\s+)?reminder\s+(?P<b>.+)$", re.I),
    re.compile(r"^reminder\s*[:\-]?\s+(?P<b>.+)$", re.I),
]
_SCHEDULE_PREFIX = re.compile(r"^(?:schedule|plan|queue)\s+(?:up\s+)?(?:a\s+|an\s+|the\s+|my\s+)?(?:task\s+|action\s+|command\s+)?(?:to\s+|for\s+)?", re.I)


def parse_oneshot(command: str, now: datetime | None = None) -> dict | None:
    """{'kind','text','due','defaulted'} for a reminder / scheduled action, else None."""
    now = now or datetime.now()
    s = normalize(command)
    if not s or _QUESTION.match(s) or re.match(r"^(?:every|each|daily)\b", s, re.I) or re.search(r"\bevery\s+\w+", s, re.I):
        return None
    if _SHORT_RELATIVE.search(s):
        return None   # "remind me in 20 minutes ..." is a timer
    for rx in _REMIND:
        m = rx.match(s)
        if not m:
            continue
        body = re.sub(r"^for\s+(?=\d)", "at ", m.group("b"), flags=re.I)
        hit = parse_when(body, now)
        if not hit:
            return None
        due, text, defaulted = hit
        text = re.sub(r"^(?:to|that|about|for)\s+", "", text, flags=re.I).strip(" ,;:-.")
        if not text:
            return None
        return {"kind": "reminder", "text": text, "due": due, "defaulted": defaulted}
    body = _SCHEDULE_PREFIX.sub("", s)
    hit = parse_when(body, now)
    if not hit:
        return None
    due, text, defaulted = hit
    first = text.split(" ", 1)[0].lower() if text else ""
    if first not in ACTION_VERBS:
        return None
    return {"kind": "action", "text": text, "due": due, "defaulted": defaulted}


# ── alarms, and reminders with no text ───────────────────────────────────────────────────────
# "set an alarm for 7 am", "wake me up at 6:30", "remind me at 4pm", "set a reminder for 4pm".
# A reminder that has text ("remind me to call Mom at 4") is parse_oneshot's job: not handled here.

_ALARM_RE = re.compile(
    r"^(?:(?:set|add|create|make|schedule|start)\s+(?:me\s+)?(?:up\s+)?(?:(?P<art>an?|my|the)\s+)?(?:new\s+)?alarm"
    r"|alarm)\b(?P<b>.*)$", re.I)
_WAKE_RE = re.compile(r"^wake\s+me(?:\s+up)?\b(?P<b>.*)$", re.I)
_REMIND_ONLY = [
    re.compile(r"^remind\s+me\b(?P<b>.*)$", re.I),
    re.compile(r"^(?:set|add|create|make)\s+(?:me\s+)?(?:an?\s+)?(?:new\s+)?reminder\b(?P<b>.*)$", re.I),
    re.compile(r"^reminder\b\s*[:\-]?(?P<b>.*)$", re.I),
]
_REPEAT = re.compile(r"\b(?:every|each|daily|weekdays?|weekends?)\b", re.I)
_REL_DUR = re.compile(
    r"^(?:in|after|for)\s+(?:(?P<half>half\s+an?)|(?P<n>\d+(?:\.\d+)?|an?|one|two|three|four|five|six|seven))\s*"
    r"(?P<u>seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", re.I)
_LABEL_LEAD = re.compile(r"^(?:(?:called|named|labell?ed|titled)\s+|to\s+|that\s+|about\s+|for\s+)", re.I)
_ASK_TIME = "What time should I set it for? For example: set an alarm for 7:30 am."
_ASK_WHEN = "When should I remind you? For example: remind me at 4 pm."


def _rel_seconds(m: re.Match) -> float:
    if m.group("half"):
        n = 0.5
    else:
        raw = m.group("n").lower()
        n = float(raw) if raw[0].isdigit() else _NUMWORDS[raw]
    u = m.group("u").lower()
    return n * (3600 if u.startswith(("h")) else 60 if u.startswith("m") else 1)


def parse_alarm(command: str, now: datetime | None = None) -> dict | None:
    """An alarm, or a reminder with no text of its own.

    -> {'kind': 'reminder', 'alarm': bool, 'text': str, 'due': datetime, 'defaulted': False}
    -> {'error': msg}  when it is clearly an alarm / reminder but the time is missing
    -> None            when the phrase is not for this parser.
    A time that has already passed rolls over to tomorrow (parse_when does that)."""
    now = now or datetime.now()
    s = normalize(command)
    if not s or _QUESTION.match(s):
        return None
    alarm, wake, art, m = True, False, "", _ALARM_RE.match(s)
    if m:
        art = (m.group("art") or "").lower()
    else:
        m = _WAKE_RE.match(s)
        wake = bool(m)
        if not m:
            alarm = False
            for rx in _REMIND_ONLY:
                m = rx.match(s)
                if m:
                    break
            if not m:
                return None
    b = m.group("b").strip(" ,.")
    if _REPEAT.search(b):
        # a repeating reminder is a routine (handled later); a repeating alarm is not supported
        return {"error": "I can't repeat alarms yet. Say 'set an alarm for 7 am' for a one-time alarm."} if alarm else None

    # "in 20 minutes" / "for 20 minutes" with nothing else to say: a relative time (not a timer, it is saved to disk)
    rel = _REL_DUR.match(b)
    if rel:
        label = _LABEL_LEAD.sub("", b[rel.end():].strip(" ,.")).strip(" ,;:-.\"'")
        if label and not alarm:
            return None                      # "remind me in 20 minutes to call mum" belongs to timers.py
        due = now + timedelta(seconds=_rel_seconds(rel))
        return {"kind": "reminder", "alarm": alarm, "text": label or ("Alarm" if alarm else "Reminder"),
                "due": due.replace(microsecond=0), "defaulted": False}
    if not alarm and _SHORT_RELATIVE.search(b):
        return None

    body = re.sub(r"^(?:for|at|to\s+go\s+off\s+at)\s+(?=\d)", "at ", b, flags=re.I)
    t_hit = _find_time(body)
    if wake and t_hit and not t_hit[3] and 1 <= t_hit[0] <= 11:
        body = body[: t_hit[2][1]] + " am" + body[t_hit[2][1]:]     # "wake me at 6:30" is the morning
    has_time = bool(t_hit) or bool(_PART_RE.search(body))
    hit = parse_when(body, now)              # any date / time of day
    if not has_time:
        if alarm:
            if art == "the":
                return None                  # "set the alarm to away": a smart-home panel
            if not b or re.fullmatch(r"(?:for\s+me|please)", b, re.I) or hit:
                return {"error": _ASK_TIME}
            return None                      # "alarm settings", "set alarm panel ...": not ours
        # a reminder: "remind me to call Mom tomorrow" (a date, no time) is parse_oneshot's job
        if hit and _LABEL_LEAD.sub("", hit[1]).strip(" ,;:-.\"'"):
            return None
        return {"error": _ASK_WHEN}
    due, text, _ = hit
    text = _LABEL_LEAD.sub("", text).strip(" ,;:-.\"'")
    if not alarm and text:
        return None                          # "remind me to call Mom at 4": parse_oneshot's job
    return {"kind": "reminder", "alarm": alarm, "text": text or ("Alarm" if alarm else "Reminder"),
            "due": due, "defaulted": False}


# ── routines ─────────────────────────────────────────────────────────────────────────────────

_DAYSPEC = (rf"(?P<spec>day|morning|afternoon|evening|night|weekdays?|weekends?|"
            rf"{_WD_STEM}s?(?:\s*(?:,|and|&)\s*{_WD_STEM}s?)*)")
_EVERY = re.compile(rf"\b(?:every|each)\s+{_DAYSPEC}\b|\b(?P<daily>daily)\b", re.I)
_EVERY_N = re.compile(r"\bevery\s+(?:\d+|other|few)\s*(?:min|minute|minutes|hour|hours|hr|hrs)\b|\bevery\s+(?:hour|minute)\b", re.I)
_STARTUP = [
    re.compile(r"^(?:at|on)\s+(?:start\s*up|boot|login|log\s*in)\s*[, ]+\s*(?:then\s+)?(?P<a>.+)$", re.I),
    re.compile(r"^(?:when|whenever|each\s+time|every\s+time)\s+(?:lyra|you|i)\s+(?:start|starts|starting|boot|boots|launch|launches|"
               r"wake|wakes|turn\s+on|turns\s+on|log\s+in|logs\s+in)(?:\s+up)?\s*[, ]+\s*(?:then\s+)?(?P<a>.+)$", re.I),
]
_PHRASE = re.compile(r"^(?:when|whenever|if)\s+i\s+say\s+[\"“'‘]?(?P<p>.+?)[\"”'’]?\s*[,:]\s*(?:then\s+)?(?P<a>.+)$", re.I)
_NAMED = re.compile(r"^(?:create|make|add|set\s*up|new)\s+(?:a\s+|an\s+)?(?:new\s+)?routine\s+(?:called|named)\s+"
                    r"[\"“'‘]?(?P<n>[\w' -]+?)[\"”'’]?\s*(?:[:,\-]|that|to|which|with)\s*(?P<a>.+)$", re.I)


def _days_from_spec(spec: str | None, daily: bool):
    if daily or not spec:
        return list(range(7))
    sp = spec.lower()
    if sp in ("day", "morning", "afternoon", "evening", "night"):
        return list(range(7))
    if sp.startswith("weekday"):
        return [0, 1, 2, 3, 4]
    if sp.startswith("weekend"):
        return [5, 6]
    days = []
    for stem, idx in (("mon", 0), ("tue", 1), ("wed", 2), ("thu", 3), ("fri", 4), ("sat", 5), ("sun", 6)):
        if re.search(rf"\b{stem}", sp):
            days.append(idx)
    return days or list(range(7))


def split_actions(text: str) -> list[str]:
    """'open Unity and Spotify' -> ['open Unity', 'open Spotify']; 'open X then play Y' -> two."""
    out: list[str] = []
    fw = text.strip().split(" ", 1)
    if fw and fw[0].lower() in _THIRD_PERSON:   # "a routine that opens Steam and Discord"
        text = _THIRD_PERSON[fw[0].lower()] + (" " + fw[1] if len(fw) > 1 else "")
    parts = re.split(r",?\s+(?:and\s+then|then|after\s+that|and\s+also)\s+|\s*;\s*", text.strip())
    for part in parts:
        part = part.strip(" ,.")
        if not part:
            continue
        segs = [x for x in re.split(r"\s*,\s*|\s+and\s+", part) if x.strip()]
        acc: list[str] = []
        verb = ""
        for seg in segs:
            first = seg.split(" ", 1)[0]
            if first.lower() in ROUTINE_VERBS:
                acc.append(seg.strip())
                verb = first if first.lower() in _DISTRIBUTIVE else ""
            elif verb and acc:
                acc.append(f"{verb} {seg.strip()}")        # "open Unity and Spotify"
            elif acc:
                acc[-1] += f" and {seg.strip()}"            # "play lofi and chill"
            else:
                acc.append(seg.strip())
        out.extend(acc)
    return [a for a in out if a][:8]


def parse_routine(command: str, now: datetime | None = None) -> dict | None:
    """A routine definition, {'error': msg} when it is clearly a routine but incomplete, or None."""
    s = normalize(command)
    if not s:
        return None

    m = _NAMED.match(s)
    if m:
        actions = split_actions(m.group("a"))
        name = m.group("n").strip()
        if not actions:
            return {"error": "What should that routine do?"}
        return {"name": name, "trigger": {"type": "phrase", "phrase": name.lower()}, "actions": actions}

    m = _PHRASE.match(s)
    if m:
        actions = split_actions(m.group("a"))
        phrase = m.group("p").strip().lower()
        if not actions or not phrase:
            return {"error": "What should it do when you say that?"}
        return {"name": phrase, "trigger": {"type": "phrase", "phrase": phrase}, "actions": actions}

    for rx in _STARTUP:
        m = rx.match(s)
        if m:
            actions = split_actions(m.group("a"))
            if not actions:
                return {"error": "What should I do at startup?"}
            return {"name": "startup", "trigger": {"type": "startup"}, "actions": actions}

    if _QUESTION.match(s):
        return None   # "what time is it every day"

    if _EVERY_N.search(s):
        return {"error": "Routines run at a set time of day, on startup, or when you say a phrase - not every few minutes."}
    em = _EVERY.search(s)
    if not em:
        return None
    spec = em.group("spec")
    days = _days_from_spec(spec, bool(em.group("daily")))
    rest = s[: em.start()] + " " + s[em.end():]
    t_hit = _find_time(rest)
    hh = mm = None
    if t_hit:
        hh, mm = t_hit[0], t_hit[1]
        rest = _strip_spans(rest, [t_hit[2]])
    part = (spec or "").lower()
    if hh is None and part in PART_TIME:
        hh, mm = PART_TIME[part]
    if hh is None:
        return {"error": "At what time? For example: every morning at 9:00 open Spotify."}
    text = _tidy(rest)
    text = re.sub(r"^(?:then|to|please)\s+", "", text, flags=re.I)
    actions = split_actions(text)
    if not actions:
        return {"error": "What should I do then?"}
    name = part if part in ("morning", "afternoon", "evening", "night") else ""
    if not name and part.startswith("weekday"):
        name = "weekday"
    if not name and part.startswith("weekend"):
        name = "weekend"
    return {"name": name, "trigger": {"type": "time", "days": days, "time": f"{hh:02d}:{mm:02d}"}, "actions": actions}


# ── wording ──────────────────────────────────────────────────────────────────────────────────

def say_when(due: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    t = f"{due.hour}:{due.minute:02d}"
    days = (due.date() - now.date()).days
    if days == 0:
        return f"today at {t}"
    if days == 1:
        return f"tomorrow at {t}"
    if 1 < days < 7:
        return f"on {WEEKDAYS[due.weekday()].capitalize()} at {t}"
    month = [k for k, v in MONTHS.items() if v == due.month and len(k) > 3][0].capitalize()
    tail = f" {due.year}" if due.year != now.year else ""
    return f"on {due.day} {month}{tail} at {t}"


def say_clock(due: datetime) -> str:
    """4:00 PM"""
    return f"{due.hour % 12 or 12}:{due.minute:02d} {'AM' if due.hour < 12 else 'PM'}"


def say_day(due: datetime, now: datetime | None = None) -> str:
    """'' for today, else ' tomorrow' / ' on Friday' / ' on 10 November' (to follow say_clock)."""
    now = now or datetime.now()
    days = (due.date() - now.date()).days
    if days <= 0:
        return ""
    if days == 1:
        return " tomorrow"
    if days < 7:
        return f" on {WEEKDAYS[due.weekday()].capitalize()}"
    month = [k for k, v in MONTHS.items() if v == due.month and len(k) > 3][0].capitalize()
    tail = f" {due.year}" if due.year != now.year else ""
    return f" on {due.day} {month}{tail}"


def say_days(days: list[int]) -> str:
    if sorted(days) == list(range(7)):
        return "every day"
    if sorted(days) == [0, 1, 2, 3, 4]:
        return "every weekday"
    if sorted(days) == [5, 6]:
        return "every weekend"
    return "every " + " and ".join(WEEKDAYS[d].capitalize() for d in sorted(days))
