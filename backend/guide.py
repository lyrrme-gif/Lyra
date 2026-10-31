"""Lyra — guide mode (Step 3): the mascot walks around the screen and points while Lyra explains.

Phrases: "show me where X is", "how do I ... in <app>", "explain this screen",
"walk me through ...", "what is this button".  While a tour runs: "next", "repeat", "stop".

Flow: capture the monitor under the cursor -> ask the vision model for <= 6 steps (text + a short
description of the target) -> locate each target with the same grid + zoomed refinement used for
clicking (actions.apps._refine_point / _vision_complete) -> push ONE mascot "tour" event -> speak
each step with TTS in sync.  Guide mode itself never clicks, types or scrolls: it only looks.  Acting ("do it for me") lives in
guide_act.py, behind the guide_level setting (look / step / auto); controls are found through Windows UI
Automation by name there, and here too whenever the app exposes them (vision is the fallback).

Mascot events follow the shared contract (see MERGE_NOTES_STEP03.md).  A step whose target could not
be found carries no x/y: it is spoken and shown as a bubble only, with no movement.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import ui

log = ui.log

MAX_STEPS = 6
_PLAN_SIDE = 1280          # longest side of the screenshot sent for planning
_GRID_SIDE = 1600          # longest side of the gridded screenshot sent for locating
_INTRO_WAIT_S = 4.0        # how long to wait for the "Okay, follow me." reply to start/finish
_NO_VISION = "Vision isn't configured — add an API key in Lyra settings."

# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------
_LEAD = r"(?:please\s+|can\s+you\s+|could\s+you\s+|hey\s+)*"
_TAIL = r"[\s?!.]*$"

_SHOW_WHERE = re.compile(
    r"^" + _LEAD + r"show\s+me\s+where\s+(?:to\s+find\s+)?(?:the\s+|my\s+|a\s+)?(?P<what>.+?)"
    r"(?:\s+(?:is|are))?(?:\s+on\s+(?:my\s+|the\s+)?(?:screen|desktop))?" + _TAIL, re.I)
_HOW_DO_I = re.compile(
    r"^" + _LEAD + r"how\s+(?:do|can|would|should)\s+i\s+(?P<task>.+?)\s+"
    r"(?:in|on|using|with|inside)\s+(?P<app>[^?!.]+?)" + _TAIL, re.I)
_EXPLAIN = re.compile(
    r"^" + _LEAD + r"explain\s+(?:(?:this|the|my|current)\s+(?:screen|page|window|app|interface)"
    r"|what(?:'s|\s+is)\s+(?:on|in)\s+(?:my\s+|the\s+|this\s+)?(?:screen|window))" + _TAIL, re.I)
# "can you explain the exercise on screen", "explique cet exercice", "what's this page about"...
_SCREEN_VERB = (r"(?:explain|describe|summari[sz]e|understand|solve|read|tell\s+me\s+about|help\s+me\s+with|"
                r"look\s+at|what(?:'s|\s+is)\s+(?:this|that|on)|explique\w*|d[ée]cris|r[ée]sume\w*|"
                r"aide[\s-]+moi|regarde)")
_SCREEN_REF = (r"(?:on\s+(?:my\s+|the\s+|this\s+)?(?:screen|display|monitor)|on-?screen|(?:my|the)\s+screen\b|"
               r"(?:this|that|the)\s+(?:exercise|exercice|problem|question|page|slide|document|code|text|formula|"
               r"diagram|table|image|figure|chart|article|email|error)\b|"
               r"(?:cet|cette|ce)\s+\w+|[àa]\s+l.[ée]cran|sur\s+l.[ée]cran)")
_SCREEN_EXPLAIN = re.compile(r"\b" + _SCREEN_VERB + r"\b.*" + _SCREEN_REF, re.I)

_WALK = re.compile(r"^" + _LEAD + r"walk\s+me\s+through\s+(?P<what>.+?)" + _TAIL, re.I)
_THIS_THING = re.compile(
    r"^what(?:'s|\s+is|\s+does)\s+this\s+(?P<kind>button|icon|menu|option|setting|tab|toggle|control)"
    r"(?:\s+do)?" + _TAIL, re.I)

_OK = r"^(?:ok(?:ay)?[,\s]+)?"
_NEXT = re.compile(_OK + r"(?:next(?:\s+(?:step|one))?|continue|go\s+on|skip(?:\s+(?:this|that|it))?)[\s.!]*$", re.I)
_REPEAT = re.compile(_OK + r"(?:repeat(?:\s+(?:that|it))?|say\s+(?:that|it)\s+again|again|once\s+more)[\s.!]*$", re.I)
_STOP = re.compile(
    _OK + r"(?:stop|cancel|enough|that'?s\s+enough|never\s*mind|exit)"
    r"(?:\s+(?:the\s+)?(?:guide|guiding|tour|explaining|explanation|it|that))?[\s.!]*$", re.I)

# "do it for me" (acts on the last how-do-I / walk-me-through request) and "<task> for me on screen / in <app>".
_DO_IT = re.compile(r"^" + _LEAD + r"(?:do|handle|take\s+care\s+of)\s+(?:it|this|that|the\s+(?:whole\s+)?(?:thing|task))"
                    r"\s+for\s+me" + _TAIL, re.I)
_FOR_ME = re.compile(
    r"^" + _LEAD + r"(?P<task>.+?)\s+for\s+me\s+(?:on\s+(?:my\s+|the\s+)?(?:screen|desktop)"
    r"|(?:in|on|using|with|inside)\s+(?P<app>[^?!.]+?))" + _TAIL, re.I)

_APP_NOISE = {"the", "a", "an", "my", "app", "application", "program", "window", "this", "that", "here", "screen"}

_PLAN_PROMPT = (
    "You are guiding a user around their computer. The screenshot shows their screen right now.\n"
    "Task: {goal}\n"
    "Reply with ONLY a JSON array of at most {n} steps, in the order the user should follow them. "
    'Each step is {{"text": "<one short sentence, at most 18 words, spoken aloud>", '
    '"target": "<short description of ONE visible UI element to point at, e.g. blue Save button in the '
    'top-right toolbar>" or null}}.\n'
    "Rules: point only at things you can actually see in the screenshot; use null when a step has nothing "
    "visible to point at; if what the user needs is not on screen, give one step saying so, with null; "
    "no markdown; write the text in the language the user used."
)


# ---------------------------------------------------------------------------
# Session (one tour at a time)
# ---------------------------------------------------------------------------
class _Session:
    def __init__(self, steps: list[dict], stay: bool = False):
        self.steps = steps
        self.stay = stay          # True: the Mochi stays out on the screen when the tour ends
        self.idx = 0
        self.start_at = 0
        self.cancel = threading.Event()
        self.restart = threading.Event()


_current: _Session | None = None
_lock = threading.Lock()


def active() -> bool:
    with _lock:
        if _current is not None and not _current.cancel.is_set():
            return True
    try:
        import guide_act
        return guide_act.active()
    except Exception:  # noqa: BLE001
        return False


def _clear(s: _Session) -> None:
    global _current
    with _lock:
        if _current is s:
            _current = None


def _push(cmd: str, **kw) -> None:
    try:
        import ui_bridge
        ui_bridge._push_event({"type": "mascot", "cmd": cmd, **kw})
    except Exception as e:  # noqa: BLE001 - the mascot is optional
        log.debug("guide: mascot event dropped: %s", e)


def _step_ms(text: str) -> int:
    return int(min(14000, max(2500, len(text) * 75 + 1200)))


def _step_event(step: dict) -> dict:
    ev = {"text": step["text"], "ms": step["ms"]}
    if step.get("x") is not None and step.get("y") is not None:
        ev.update(x=round(float(step["x"]), 4), y=round(float(step["y"]), 4), emote="point")
    if step.get("draw"):
        ev["draw"] = step["draw"]
    return ev


# ---------------------------------------------------------------------------
# Speech helpers
# ---------------------------------------------------------------------------
def _speak(text: str) -> None:
    import voice
    voice.speak(text)


def _audio_busy() -> bool:
    try:
        import voice
        return bool(voice.pygame.mixer.get_init() and voice.pygame.mixer.music.get_busy())
    except Exception:  # noqa: BLE001
        return False


def _stop_audio() -> None:
    try:
        import voice
        voice.pygame.mixer.music.stop()
    except Exception:  # noqa: BLE001
        pass


def _wait_for_main_reply(s: _Session) -> None:
    """The short reply returned to main.py ("Okay, follow me.") is spoken by main.py itself.
    Wait for it to finish so the two voices never overlap."""
    t0 = time.monotonic()
    seen, idle_since = False, None
    while not s.cancel.is_set():
        now = time.monotonic()
        if _audio_busy():
            seen, idle_since = True, None
        elif seen:
            idle_since = idle_since or now
            if now - idle_since > 0.4:
                return
        elif now - t0 > _INTRO_WAIT_S:
            return
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# Tour playback
# ---------------------------------------------------------------------------
def _per_step(s: _Session) -> bool:
    """Steps with drawings are sent one at a time, so the bubble and drawing follow the voice
    instead of a pre-timed script that drifts away from it."""
    return any(st.get("draw") for st in s.steps)


def _play(s: _Session, k: int) -> bool:
    """Speak steps k.. in sync with the pointer. True if interrupted by next/repeat."""
    for i in range(k, len(s.steps)):
        s.idx = i
        step = s.steps[i]
        deadline = time.monotonic() + step["ms"] / 1000.0
        if _per_step(s):                            # drawn explanations: show each step as its voice starts
            _push("tour", steps=[dict(_step_event(step), ms=int(step["ms"] * 1.8))])
        try:
            _speak(step["text"])
        except Exception as e:  # noqa: BLE001
            log.warning("guide: TTS failed: %s", e)
        if s.cancel.is_set():
            return False
        if s.restart.is_set():
            return True
        while time.monotonic() < deadline:          # let the pointer finish its time
            if s.cancel.is_set():
                return False
            if s.restart.wait(0.05):
                return True
    return False


def _run(s: _Session) -> None:
    try:
        while not s.cancel.is_set():
            _wait_for_main_reply(s)
            s.restart.clear()
            k = s.start_at
            if k >= len(s.steps) or s.cancel.is_set():
                break
            if not _per_step(s):
                _push("tour", steps=[_step_event(st) for st in s.steps[k:]])
            if not _play(s, k):
                break
        if not s.cancel.is_set():
            if not s.stay:
                _push("home")
            _push("emote", name="happy")
    except Exception as e:  # noqa: BLE001
        log.warning("guide: tour failed: %s", e)
        _push("stop")
        _push("home")
    finally:
        _clear(s)


def _start(steps: list[dict], stay: bool = False) -> None:
    global _current
    with _lock:
        old = _current
        s = _Session(steps, stay)
        _current = s
    if old is not None:
        old.cancel.set()
        _stop_audio()
        _push("stop")
    threading.Thread(target=_run, args=(s,), name="lyra-guide", daemon=True).start()


def _end(s: _Session, happy: bool) -> None:
    s.cancel.set()
    _stop_audio()
    _push("stop")
    _push("home")
    if happy:
        _push("emote", name="happy")
    _clear(s)


def _control(cmd: str) -> str | None:
    try:                                   # a task Lyra is acting on: stop / yes / no go to it first
        import guide_act
        reply = guide_act.control(cmd)
        if reply:
            return reply
    except Exception as e:  # noqa: BLE001
        log.debug("guide: act control skipped: %s", e)
    with _lock:
        s = _current
    if s is None or s.cancel.is_set():
        return None
    if _STOP.match(cmd):
        _end(s, happy=False)
        return "Okay, stopped."
    if _NEXT.match(cmd):
        if s.idx + 1 >= len(s.steps):
            _end(s, happy=True)
            return "That was the last step."
        s.start_at = s.idx + 1
    elif _REPEAT.match(cmd):
        s.start_at = s.idx
    else:
        return None
    s.restart.set()
    _stop_audio()
    _push("stop")
    return "Next." if s.start_at > s.idx else "Sure."


# ---------------------------------------------------------------------------
# Screen + vision
# ---------------------------------------------------------------------------
def _cursor_pos():
    try:
        import pyautogui
        p = pyautogui.position()
        return int(p.x), int(p.y)
    except Exception:  # noqa: BLE001
        return None


def _capture():
    """(image of the monitor under the cursor, cursor position as 0-1 fractions of it or None)."""
    import screen_capture
    mons = screen_capture.monitors()
    pos = _cursor_pos()
    idx, frac = 0, None
    for m in mons:
        if pos and m["left"] <= pos[0] < m["left"] + m["width"] and m["top"] <= pos[1] < m["top"] + m["height"]:
            idx = m["index"]
            frac = ((pos[0] - m["left"]) / m["width"], (pos[1] - m["top"]) / m["height"])
            break
    return screen_capture.capture_monitor(idx).convert("RGB"), frac


def _b64(img, max_side: int) -> str:
    im = img
    if max(im.size) > max_side:
        im = im.copy()
        im.thumbnail((max_side, max_side))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _ask(prompt: str, b64_png: str, max_tokens: int):
    from actions import apps
    raw, err = apps._vision_complete([{
        "role": "user",
        "content": [{"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_png}"}}],
    }], max_tokens=max_tokens)
    return re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip(), err


def _parse_steps(raw: str) -> list[dict]:
    a, b = raw.find("["), raw.rfind("]")
    if a < 0 or b <= a:
        return []
    try:
        data = json.loads(raw[a:b + 1])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        target = item.get("target")
        target = str(target).strip() if target and str(target).strip().lower() not in ("null", "none") else None
        out.append({"text": text[:220], "target": target})
        if len(out) >= MAX_STEPS:
            break
    return out


def _grid_b64(img) -> str:
    """The screenshot with the same red A1..L8 grid actions/apps.py draws for clicking."""
    from PIL import ImageDraw, ImageFont
    from actions import apps
    im = img.copy()
    if max(im.size) > _GRID_SIDE:
        im.thumbnail((_GRID_SIDE, _GRID_SIDE))
    w, h = im.size
    cw, ch = w / apps.GRID_COLS, h / apps.GRID_ROWS
    px = max(14, int(min(cw, ch) * 0.22))
    try:
        font = ImageFont.truetype("arial.ttf", px)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    d = ImageDraw.Draw(im)
    for c in range(apps.GRID_COLS):
        for r in range(apps.GRID_ROWS):
            x0, y0 = c * cw, r * ch
            d.rectangle([x0, y0, x0 + cw, y0 + ch], outline="red", width=2)
            d.rectangle([x0, y0, x0 + max(28, int(px * 1.6)), y0 + max(16, px + 6)], fill="red")
            d.text((x0 + 2, y0 + 1), f"{chr(65 + c)}{r + 1}", fill="white", font=font)
    return _b64(im, _GRID_SIDE)


def _locate(grid_b64: str, full, description: str):
    """Target description -> (x, y) as 0-1 fractions of the captured monitor, or None. Never clicks."""
    from actions import apps
    try:                                       # pixel-exact: coarse cell, then numbered regions inside it
        import circle_explain
        box = circle_explain.locate_box(full, description)
        if box:
            return (min(0.98, max(0.02, (box[0] + box[2]) / 2)), min(0.98, max(0.02, (box[1] + box[3]) / 2)))
    except Exception as e:  # noqa: BLE001
        log.debug("guide: exact locate skipped: %s", e)
    prompt = (
        "This screenshot has a red grid. Cell labels are A1..L8 "
        "(columns A-L left→right, rows 1-8 top→bottom).\n"
        f"User wants to find: {description}\n"
        "Reply in exactly this format on one line:\n"
        "CELL|short label of target|confidence\n"
        "Example: G4|blue Subscribe button|high\n"
        "If you cannot find it, reply: NONE|not found|low"
    )
    try:
        clean, err = _ask(prompt, grid_b64, 40)
        if err and not clean:
            return None
        parts = [p.strip() for p in clean.replace("\n", " ").split("|")]
        if not parts or parts[0].upper().startswith("NONE"):
            return None
        m = apps.CELL_LABEL_PATTERN.search(parts[0])
        conf = (parts[2] if len(parts) > 2 else "medium").lower()
        if not m or "low" in conf:                 # unsure: better to say it than point at the wrong thing
            return None
        col = max(0, min(ord(m.group(1).upper()) - 65, apps.GRID_COLS - 1))
        row = max(0, min(int(m.group(2)) - 1, apps.GRID_ROWS - 1))
        W, H = full.size
        cw, ch = W / apps.GRID_COLS, H / apps.GRID_ROWS
        x, y = (col + 0.5) * cw, (row + 0.5) * ch
        fine = apps._refine_point(full, W, H, col, row, cw, ch, description)
        if fine:
            x, y = fine
        return min(0.98, max(0.02, x / W)), min(0.98, max(0.02, y / H))
    except Exception as e:  # noqa: BLE001
        log.warning("guide: locate failed: %s", e)
        return None


def _locate_all(img, targets: list[str | None]) -> list:
    todo = [t for t in targets if t]
    if not todo:
        return [None] * len(targets)
    grid = _grid_b64(img)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(_locate, grid, img, t) if t else None for t in targets]
        return [f.result() if f else None for f in futs]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
def _app_is_open(app: str) -> bool:
    """'how do I ... in <app>' only counts when <app> is an open window (or "this app"):
    otherwise it is an ordinary question for the chat brain."""
    words = [w for w in re.findall(r"[\w'+#.-]+", app.lower()) if w not in _APP_NOISE]
    if not words:
        return True
    try:
        import pygetwindow as gw
        titles = " | ".join(t.lower() for t in gw.getAllTitles() if t)
    except Exception:  # noqa: BLE001
        return True
    return all(w in titles for w in words)


def _parse(cmd: str):
    m = _SHOW_WHERE.match(cmd)
    if m:
        return {"kind": "where", "what": m.group("what").strip()}
    m = _THIS_THING.match(cmd)
    if m:
        return {"kind": "cursor", "what": m.group("kind").lower()}
    m = _EXPLAIN.match(cmd)
    if m:
        return {"kind": "plan", "goal": "Explain what is on this screen: its main areas and what each one is for."}
    if len(cmd) <= 140 and _SCREEN_EXPLAIN.search(cmd) and not _SHOW_WHERE.match(cmd):
        return {"kind": "screen", "question": cmd}
    m = _WALK.match(cmd)
    if m:
        return {"kind": "plan", "act_goal": m.group("what").strip(),
                "goal": f"Walk the user through {m.group('what').strip()} on this screen, step by step."}
    m = _HOW_DO_I.match(cmd)
    if m and _app_is_open(m.group("app")):
        return {"kind": "plan", "act_goal": f"{m.group('task').strip()} in {m.group('app').strip()}",
                "goal": f"Show the user how to {m.group('task').strip()} in {m.group('app').strip()}, step by step."}
    if _DO_IT.match(cmd):
        try:
            import guide_act
            goal = guide_act.last_goal()
        except Exception:  # noqa: BLE001
            goal = None
        return {"kind": "act", "goal": goal} if goal else None      # nothing to refer to: not for the guide
    m = _FOR_ME.match(cmd)
    if m and (not m.group("app") or _app_is_open(m.group("app"))):
        task = m.group("task").strip()
        return {"kind": "act", "goal": f"{task} in {m.group('app').strip()}" if m.group("app") else task}
    return None


def _plan_where(img, what: str):
    pos = None
    try:
        import guide_act
        pos = guide_act.locate(what)                     # UI Automation: exact, by name
    except Exception as e:  # noqa: BLE001
        log.debug("guide: UIA locate skipped: %s", e)
    if not pos:
        pos = _locate(_grid_b64(img), img, what)         # no such named control: look at the pixels
    if not pos:
        return None, f"I can't find the {what} on this screen."
    text = f"Here's the {what}."
    return [{"text": text, "ms": _step_ms(text), "x": pos[0], "y": pos[1]}], None


def _plan_cursor(img, frac, what: str):
    if frac is None:
        return None, "I can't tell where your mouse is."
    from PIL import ImageDraw
    im = img.copy()
    d = ImageDraw.Draw(im)
    cx, cy, r = frac[0] * im.width, frac[1] * im.height, max(14, im.width // 90)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline="red", width=3)
    d.line([cx - 2 * r, cy, cx + 2 * r, cy], fill="red", width=2)
    d.line([cx, cy - 2 * r, cx, cy + 2 * r], fill="red", width=2)
    text, err = _ask(
        f"A red crosshair marks the mouse pointer. In one or two short sentences, explain what the {what} "
        "under the crosshair is and what it does. Do not mention the crosshair. If you cannot tell, say so.",
        _b64(im, _PLAN_SIDE), 120)
    if not text:
        return None, f"I couldn't read that {what} — {err}" if err else f"I couldn't tell what that {what} is."
    text = text[:300]
    return [{"text": text, "ms": _step_ms(text), "x": frac[0], "y": frac[1]}], None


def _plan_steps(img, goal: str):
    try:
        import guide_act
        uia_steps = guide_act.plan_tour(goal)            # None when the app exposes too little: vision below
    except Exception as e:  # noqa: BLE001
        log.debug("guide: UIA tour skipped: %s", e)
        uia_steps = None
    if uia_steps:
        for st in uia_steps:
            st["ms"] = _step_ms(st["text"])
        return uia_steps, None
    raw, err = _ask(_PLAN_PROMPT.format(goal=goal, n=MAX_STEPS), _b64(img, _PLAN_SIDE), 600)
    plan = _parse_steps(raw)
    if not plan:
        return None, f"I couldn't work out the steps — {err}" if err else "I couldn't work out the steps from the screen."
    spots = _locate_all(img, [p["target"] for p in plan])
    steps = []
    for p, pos in zip(plan, spots):
        st = {"text": p["text"], "ms": _step_ms(p["text"])}
        if pos:
            st["x"], st["y"] = pos
        steps.append(st)
    return steps, None


def _pick_area(img, question: str):
    """Whole-screen request -> the part of the screen the question is about, like a Ctrl+Shift circle.
    One cheap vision call over a numbered 4x3 grid; returns (crop, (x0, y0, x1, y1) px) or None when
    it can't tell or the content fills the screen anyway (then the whole screen is used)."""
    try:
        import circle_explain
        cols, rows = 4, 3
        q = (question or "").replace('"', "'")[:200]
        raw, _ = circle_explain._ask_img(
            f"The screen is split into {cols * rows} numbered cells ({cols} columns x {rows} rows, numbered left to "
            f"right then top to bottom). The user asks: \"{q}\". Which cells contain the content they are asking "
            "about (the exercise, document, code or message itself - NOT browser toolbars, sidebars, the taskbar "
            "or empty margins)? Reply with ONLY a JSON array of cell numbers, e.g. [2,3,6].",
            circle_explain._cells_b64(img, cols, rows), 40)
        a, b = raw.find("["), raw.rfind("]")
        ids = [int(x) for x in json.loads(raw[a:b + 1]) if isinstance(x, (int, float))] if 0 <= a < b else []
        ids = sorted({i for i in ids if 1 <= i <= cols * rows})
        log.info("guide: area pick reply=%r -> cells %s", raw[:60], ids)
        if not ids:
            return None
        W, H = img.size
        cw, ch = W / cols, H / rows
        cs = [(i - 1) % cols for i in ids]
        rs = [(i - 1) // cols for i in ids]
        mx, my = cw * 0.15, ch * 0.15
        x0, x1 = max(0, int(min(cs) * cw - mx)), min(W, int((max(cs) + 1) * cw + mx))
        y0, y1 = max(0, int(min(rs) * ch - my)), min(H, int((max(rs) + 1) * ch + my))
        if (x1 - x0) * (y1 - y0) > 0.8 * W * H or x1 - x0 < 100 or y1 - y0 < 60:
            return None
        return img.crop((x0, y0, x1, y1)), (x0, y0, x1, y1)
    except Exception as e:  # noqa: BLE001
        log.debug("guide: area pick failed: %s", e)
        return None


def _explain_screen(question: str) -> str | None:
    """Whole-screen explanation with the same accurate drawing pipeline as circle-to-ask: the monitor
    under the mouse is the 'circled' picture. None when the Mochi is off or it could not start."""
    try:
        import settings_store
        if settings_store.get("feature_mascot") is not True:
            return None
        import circle_ask
        import circle_explain
        import screen_capture
        cx, cy = circle_ask._cursor()
        mons = screen_capture.monitors()
        m = next((m for m in mons if m["left"] <= cx < m["left"] + m["width"]
                  and m["top"] <= cy < m["top"] + m["height"]), mons[0] if mons else None)
        if not m:
            return None
        img = screen_capture.capture_monitor(m["index"]).convert("RGB")
        rect = (m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"])
        try:                          # OCR gives exact text lines for the whole screen: no crop needed
            ocr_ok = len(circle_explain.ocr_regions(img)[0]) >= 3
        except Exception:  # noqa: BLE001
            ocr_ok = False
        area = None if ocr_ok else _pick_area(img, question)
        if area:                      # same as Ctrl+Shift: a small "circled" crop of just the content
            crop, (ax0, ay0, ax1, ay1) = area
            rect = (m["left"] + ax0, m["top"] + ay0, m["left"] + ax1, m["top"] + ay1)
            img = crop
            log.info("guide: explaining the picked area %s (%dx%d) instead of the whole screen",
                     [int(v) for v in rect], img.width, img.height)
        pending = circle_ask.pending_from_image(img, rect, side=1920)
        return circle_explain.explain(pending, question)
    except Exception as e:  # noqa: BLE001
        log.warning("guide: screen explanation failed: %s", e)
        return None


def answer_pending(command: str) -> str | None:
    """A yes / no to a step Lyra asked about while acting (main.py asks this before anything else).
    None when no question is waiting."""
    try:
        import guide_act
        return guide_act.answer(command) if guide_act.awaiting() else None
    except Exception as e:  # noqa: BLE001
        log.debug("guide: answer_pending skipped: %s", e)
        return None


def try_guide(command: str, engine_context=None) -> str | None:
    """Router entry point. Returns the short reply to speak, or None if this isn't a guide command.
    Never raises."""
    try:
        cmd = (command or "").strip()
        if not cmd:
            return None
        reply = _control(cmd)
        if reply:
            return reply
        req = _parse(cmd)
        if not req:
            return None
        try:
            import settings_store
            if not settings_store.feature_on("guide"):
                return None
        except Exception:  # noqa: BLE001
            pass
        from actions import apps
        if not apps._vision_candidates():
            return _NO_VISION
        note = ""
        if req["kind"] == "act":
            import guide_act
            if guide_act.level() != "look":
                return guide_act.start(req["goal"])
            note = guide_act._SHOW_ONLY + " "                # set to show only: do the same task as a tour
            req = {"kind": "plan", "goal": f"Show the user how to {req['goal']}, step by step."}
        elif req["kind"] == "plan" and req.get("act_goal"):
            try:
                import guide_act
                guide_act.remember(req["act_goal"])          # so a later "do it for me" knows what you meant
            except Exception:  # noqa: BLE001
                pass
        ui.set_state("thinking")
        img, frac = _capture()
        if req["kind"] == "screen":
            reply = _explain_screen(req["question"])
            if reply:
                return reply
            steps, err = _plan_steps(img, "Explain what is on this screen, answering: " + req["question"])
        elif req["kind"] == "where":
            steps, err = _plan_where(img, req["what"])
        elif req["kind"] == "cursor":
            steps, err = _plan_cursor(img, frac, req["what"])
        else:
            steps, err = _plan_steps(img, req["goal"])
        if not steps:
            return err or "I couldn't do that."
        _start(steps)
        return note + "Okay, follow me."
    except Exception as e:  # noqa: BLE001
        log.warning("guide: %s", e)
        return f"I couldn't guide you — {e}"
