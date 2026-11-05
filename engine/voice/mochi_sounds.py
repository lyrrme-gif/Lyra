"""Lyra — Mochi sounds: short acknowledgements as a Mochi sound instead of speech.

Every TASK answer ("Opening Spotify, sir.", "Done.", "I couldn't capture the screen.", "Volume is at 40%")
is the Mochi's own sound (each character has its own set) + an animation + a subtitle, not speech. Lyra
only speaks for real information (answers from the chat brain: questions, research, conversation, long
texts) and for anything that asks you to confirm a risky action (those are never classified here).

Setting (Settings -> Voice): mochi_sound_mode
  speak  - Lyra speaks every reply (the old behaviour; no Mochi sounds at all)
  mochi  - an acknowledgement is a Mochi sound + animation + subtitle, not spoken   (default)
  both   - the Mochi sound and Lyra's voice together

How it reaches the screen:
  * replies:   ui_bridge adds mochi fields to the "chat" event (plan() below); voice.speak() skips the
               speech when silences(text) is true, so both always decide from the same text.
  * stand-alone moods (tired, overheating): emit() sends a "mochi_sound" event.
  * dizzy / thinking are played by the UI itself (src/core/mochiSound.ts), using the same mode.
"""

from __future__ import annotations

import re
import threading
import time

from engine.bridge import ui

log = ui.log

MODES = ("speak", "mochi", "both")
DEFAULT_MODE = "mochi"

# kind -> (mascot emote the notch / floating Mochi plays, how long in ms)
KINDS: dict[str, tuple[str, int]] = {
    "done": ("celebrate", 2200),
    "opened": ("wink", 1800),
    "error": ("error", 2800),
    "thinking": ("think", 2500),
    "question": ("listen", 3000),
    "happy": ("happy", 2200),
    "dizzy": ("dizzy", 2800),
    "tired": ("tired", 3600),
    "overheating": ("hot", 5000),
    "info": ("happy", 3000),         # a task result with a bit of content ("Volume is at 40%"): sound + subtitle
}

# ── Replies that are NOT acknowledgements: they stay spoken ──────────────────────────────────
# Risky-action confirmations ("Are you sure, sir?", "Should I go ahead?", "Say yes or no") and
# offers ("Do you want me to ...") must always be heard.
_CONFIRM = re.compile(
    r"are you sure|should i go ahead|shall i\b|say yes|yes or no|yes to confirm|\bconfirm|needs your ok"
    r"|want me to|would you like|do you want",
    re.I,
)
_ERROR = re.compile(
    r"^(?:i (?:couldn'?t|could not|can'?t|cannot|wasn'?t able|was unable|am unable)|sorry\b|unable to|oops\b"
    r"|something went wrong|failed\b|error\b|that (?:failed|didn'?t work))|\b(?:failed|couldn'?t|could not)\b",
    re.I,
)
_THINK = re.compile(
    r"^(?:let me (?:think|check|look|see)|one (?:moment|sec)|just a (?:moment|sec)|give me a (?:moment|second)"
    r"|hold on|hmm+\b|checking\b|looking\b)",
    re.I,
)
_HAPPY = re.compile(
    r"^(?:you'?re welcome|anytime\b|my pleasure|happy to help|glad (?:i could|to) help|no problem|sure thing)",
    re.I,
)
_OPEN = re.compile(r"^(?:opening|opened|launching|launched|starting)\s+(.+?)(?:,?\s*sir)?[.!]*$", re.I)
_DONE = re.compile(
    r"^(?:done\b|all set|finished\b|completed\b|okay\b|ok\b|got it|alright\b|muted\b|unmuted\b|locked\b"
    r"|volume\b|brightness\b|screenshot (?:saved|taken)|timer set|reminder set|created\b|deleted\b|renamed\b"
    r"|written\b|saved\b|copied\b|cleared\b|closed\b|minimi[sz]ed\b|maxi[sz]ed\b|paused\b|resumed\b|skipped\b"
    r"|playing\b|searching\b|turned (?:on|off)\b|switched\b)",
    re.I,
)
_SIR = re.compile(r",?\s*sir\s*(?=[.!?]*\s*$)", re.I)
_MID_STOP = re.compile(r"[.!?]\s+\S")        # a second sentence inside the text


def mode() -> str:
    """speak | mochi | both (Settings -> Voice)."""
    try:
        from engine.core import settings_store
        m = settings_store.get("mochi_sound_mode")
        return m if m in MODES else DEFAULT_MODE
    except Exception:
        return DEFAULT_MODE


def _clean(text: str) -> str:
    """The reply as a subtitle: no ', sir', no closing full stop."""
    return _SIR.sub("", text).strip().rstrip(".!").strip()


def classify(text: str) -> tuple[str, str] | None:
    """(kind, subtitle) when `text` is a short acknowledgement the Mochi can say, else None."""
    raw = text or ""
    t = " ".join(raw.split())
    if not t or len(t) > 140 or "\n" in raw.strip():
        return None
    if _CONFIRM.search(t):
        return None
    one_sentence = not _MID_STOP.search(t[:-1] if t[-1] in ".!?" else t)
    if t.endswith("?"):
        # a short clarifying question ("Spotify or YouTube?"): the Mochi asks, the subtitle is the question
        if len(t) <= 80 and one_sentence:
            return "question", t
        return None
    if not one_sentence:
        return None
    if _ERROR.search(t):
        return "error", _clean(t)[:120]
    if len(t) <= 60 and _THINK.match(t):
        return "thinking", "Thinking…"
    if len(t) <= 60 and _HAPPY.match(t):
        return "happy", _clean(t)
    m = _OPEN.match(t)
    if m and len(t) <= 90:
        name = re.sub(r"\s+app$", "", m.group(1).strip(), flags=re.I)
        return "opened", f"Opened {name}" if name else "Opened"
    if len(t) <= 90 and _DONE.match(t):
        sub = _clean(t)
        return "done", sub if 0 < len(sub) <= 60 else "Done"
    return None


def _task_result(text: str) -> tuple[str, str] | None:
    """A TASK answer that is not a stock acknowledgement ("Volume is at 40%", "Timer set for 5 minutes. I'll
    tell you when it ends."): still a Mochi sound + subtitle, as long as it is short enough to read."""
    raw = text or ""
    t = " ".join(raw.split())
    if not t or len(t) > 220 or "\n" in raw.strip():
        return None
    if _CONFIRM.search(t) or t.endswith("?"):
        return None                     # offers and questions to you are always heard
    if _ERROR.search(t):
        return "error", _clean(t)[:200]
    return "info", _clean(t)[:200]


# What plan() decided for a text a moment ago, so voice.speak() makes the very same decision.
_recent: dict[str, tuple[float, dict | None]] = {}


def _remember(text: str, p: dict | None) -> None:
    now = time.monotonic()
    for k in [k for k, (ts, _) in _recent.items() if now - ts > 60]:
        _recent.pop(k, None)
    _recent[(text or "").strip()] = (now, p)


_info: dict[str, float] = {}


def mark_info(text: str) -> None:
    """Call right before showing a real ANSWER (from the chat brain): only stock acknowledgements ("Done.")
    are silenced for it, everything else stays spoken. Task results are the default and need no call."""
    now = time.monotonic()
    for k in [k for k, ts in _info.items() if now - ts > 60]:
        _info.pop(k, None)
    _info[(text or "").strip()] = now


def plan(text: str, info: bool = False) -> dict | None:
    """What to do for a reply Lyra is about to show: None (nothing special) or
    {kind, subtitle, silent, emote, ms}. `silent` = the speech is replaced by the Mochi sound.
    `info=True` marks a real answer (from the chat brain): only the stock acknowledgements are silenced."""
    m = mode()
    if m == "speak":
        return None
    info = info or (text or "").strip() in _info
    hit = classify(text) or (None if info else _task_result(text))
    if not hit:
        _remember(text, None)
        return None
    kind, subtitle = hit
    emote, ms = KINDS[kind]
    if kind == "info":
        ms = max(2800, min(9000, 1800 + 60 * len(subtitle)))      # long enough to read it
    p = {"kind": kind, "subtitle": subtitle, "silent": m == "mochi", "emote": emote, "ms": ms}
    _remember(text, p)
    return p


def silences(text: str) -> bool:
    """True when voice.speak() should stay quiet for `text` (a Mochi sound stands in for it)."""
    try:
        seen = _recent.get((text or "").strip())
        p = seen[1] if seen and time.monotonic() - seen[0] <= 60 else plan(text)
        return bool(p and p["silent"] and mode() == "mochi")      # the setting may have changed since plan()
    except Exception:
        return False


def emit(kind: str, subtitle: str = "", ms: int | None = None) -> bool:
    """A stand-alone Mochi sound (tired, overheating ...): sound + animation + subtitle, no reply.
    Does nothing in "speak" mode. Returns True when it was sent."""
    if kind not in KINDS or mode() == "speak":
        return False
    emote, default_ms = KINDS[kind]
    ms = int(ms or default_ms)
    try:
        from engine.bridge import ui_bridge
        ui_bridge._push_event({"type": "mochi_sound", "kind": kind, "subtitle": subtitle, "ms": ms})
        ui_bridge.push_mascot_emote(emote, ms)
    except Exception as e:  # noqa: BLE001
        log.debug("mochi sound %s failed: %s", kind, e)
        return False
    return True


# ── Overheating: the Mochi feels the CPU load the Stats tab shows ─────────────────────────────
HOT_PCT = 90            # CPU load that counts as "hot"
HOT_SAMPLES = 5         # ... for this many samples in a row
SAMPLE_S = 3
RECOVER_PCT = 70        # it has to cool down below this before it can overheat again
COOLDOWN_S = 600        # and never more than once in this time

_started = False


def _cpu_watch() -> None:
    time.sleep(20)
    hot = 0
    armed = True
    last = 0.0
    while True:
        try:
            if mode() != "speak":
                from engine.features import sysstats
                pct = int(sysstats._cpu_pct())      # the same sampler the Stats tab reads
                hot = hot + 1 if pct >= HOT_PCT else 0
                if pct < RECOVER_PCT:
                    armed = True
                if hot >= HOT_SAMPLES and armed and time.monotonic() - last >= COOLDOWN_S:
                    if emit("overheating", f"CPU at {pct}% — I'm overheating!"):
                        armed = False
                        last = time.monotonic()
        except Exception as e:  # noqa: BLE001
            log.debug("mochi cpu watch: %s", e)
        time.sleep(SAMPLE_S)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_cpu_watch, name="lyra-mochi-cpu", daemon=True).start()
