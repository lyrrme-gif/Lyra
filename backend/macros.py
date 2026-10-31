"""Lyra - macro recorder (Step 9).

Record clicks, scrolls and key presses once, replay them by voice.

  record a macro called X     asks first (everything typed is saved as plain text, passwords included)
  stop recording              saves it
  run macro X                 asks first, then replays; press Esc twice to abort
  list macros / rename macro X to Y / delete macro X (asks first)

"run macro X" is an ordinary command string, so it also works as a routine action
("every morning at 9 run macro X"). A routine goes through the typed-confirmation path like any other
caller: Lyra shows the confirmation on the island and nothing replays until you say yes.

PRIVACY RULES (the single source of truth is PRIVACY below; Settings and "what do you record" read it):
  * What is recorded: mouse clicks (where and which button), scrolls, every key pressed, and the timing.
    NOT recorded: screenshots, what is on screen, the clipboard, audio. Keys typed in a password field are
    dropped (best effort, Windows only). Typed text is saved as PLAIN TEXT, so don't type secrets.
  * Where it is stored: macros.json in Lyra's data folder on this PC. It is never uploaded, never sent to an AI
    model and never written to the log.
  * Easy delete: "delete macro X", "delete all macros" (voice or typed), or Settings -> Features -> Macros.
  * Consent: recording and replaying each need an explicit yes, every time. This is enforced here (see
    _confirm), not left to the global permission table, which lets unlisted actions through.

Data: paths.data_path("macros.json"), plain JSON. Nothing is stored in the project folder.

Input comes from `pynput` (new dependency), imported only when a macro is recorded or replayed. All
OS access lives in `PynputInput`; tests replace it with a fake through `_input_factory`.

Password safety (read this): the user is asked before every recording and told that keystrokes are
saved in plain text. While recording, a poller asks UI Automation whether the focused control is a
password box and drops key events while it is (best effort: needs pywinauto/comtypes, polls every
0.1 s, so it can miss the very first key). When detection is not available Lyra says so.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

FEATURE = "macros"

MAX_EVENTS = 5000
MAX_RECORD_S = 600          # a recording longer than this is stopped and saved
MAX_GAP_S = 10.0            # a pause longer than this is shortened on replay
LEAD_IN_S = 1.5             # time between the yes and the first replayed event
ESC_WINDOW_S = 1.5          # two Esc presses within this abort a replay
PASSWORD_POLL_S = 0.1
PASSWORD_STICKY_S = 0.4     # keep treating the field as a password box this long after the last hit

_lock = threading.RLock()
_rec: "_Recorder | None" = None
_play: "_Player | None" = None
_pm = None
_input_factory = None       # tests set this; None = real pynput


# ── helpers ──────────────────────────────────────────────────────────────────────────────────

def _log():
    try:
        import ui
        return ui.log
    except Exception:
        import logging
        return logging.getLogger("lyra")


def _enabled() -> bool:
    try:
        import settings_store
        return settings_store.feature_on(FEATURE)
    except Exception:
        return True


def _perm():
    global _pm
    if _pm is None:
        from permissions import PermissionManager
        _pm = PermissionManager()
    return _pm


def _confirm(action_id: str, description: str, ctx) -> tuple[bool, str]:
    """A REAL yes/no. permissions.check_permission() lets every action it doesn't know through, which would
    silently skip the recording warning, so the prompt is asked directly (voice, or typed via confirm_callback)."""
    if isinstance(ctx, dict) and ctx.get("confirmed"):          # the typed yes re-runs the command with this flag
        return True, ""
    log = _log()
    log.info("macros: asking before %s", action_id)
    return _perm()._prompt_user_confirmation(description, ctx)


PRIVACY = (
    "I record your mouse clicks, scrolls and every key you press, with the timing. I don't record screenshots, "
    "what's on screen, the clipboard or audio.",
    "What you type is saved as plain text, so never type a password while recording. I skip password fields "
    "when Windows lets me tell.",
    "Macros are saved only on this PC, in Lyra's data folder. They are never uploaded or sent to an AI model.",
    "Say delete macro and its name, or delete all macros, any time. You can also do it in Settings.",
)


def privacy_info() -> dict:
    """For Settings and the voice answer: the rules, where the file is, and what is saved right now."""
    try:
        where = str(_store_path())
    except Exception:
        where = ""
    return {"rules": list(PRIVACY), "path": where, "macros": list_macros()}


def _push(title: str, subtitle: str | None = None, phase: str = "info", ms: int = 3000) -> None:
    try:
        import ui_bridge
        ui_bridge.push_activity("macro", title, phase=phase, subtitle=subtitle, duration_ms=ms)
    except Exception:
        pass


def _chat(text: str) -> None:
    try:
        import ui
        ui.print_chat("lyra", text)
    except Exception:
        pass


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _clean(command: str) -> str:
    c = (command or "").strip()
    c = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", c, flags=re.I)
    return c.strip().rstrip("?.!").strip()


def _clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", " ", raw or "")).strip().strip("\"'").strip()[:60]


# ── storage ──────────────────────────────────────────────────────────────────────────────────

def _store_path() -> Path:
    import paths
    return paths.data_path("macros.json")


def _load() -> dict:
    try:
        d = json.loads(_store_path().read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("macros"), dict):
            return d
    except Exception:
        pass
    return {"macros": {}}


def _save(d: dict) -> None:
    p = _store_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        import os
        os.chmod(tmp, 0o600)                       # typed text is inside: owner only (best effort on Windows)
    except Exception:
        pass
    tmp.replace(p)


def list_macros() -> list[dict]:
    """[{name, steps, duration}] for the UI / tests."""
    return [{"name": m["name"], "steps": len(m.get("events", [])), "duration": m.get("duration", 0),
             "created": m.get("created", "")} for m in _load()["macros"].values()]


def delete_macro(name: str) -> bool:
    """Used by Settings (the button is the confirmation). True if something was deleted."""
    with _lock:
        d = _load()
        key = _find(name, d["macros"])
        if key is None:
            return False
        d["macros"].pop(key, None)
        _save(d)
    return True


def delete_all() -> int:
    """Wipe every macro and remove the file. Returns how many there were."""
    with _lock:
        n = len(_load()["macros"])
        for f in (_store_path(), _store_path().with_suffix(".tmp")):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
    return n


def _find(name: str, macros: dict) -> str | None:
    """Storage key of the macro `name` refers to (exact, unique prefix, then close match)."""
    key = _norm(name)
    if not key:
        return None
    by_norm = {_norm(k): k for k in macros}
    if key in by_norm:
        return by_norm[key]
    pre = [nk for nk in by_norm if nk.startswith(key)]
    if len(pre) == 1:
        return by_norm[pre[0]]
    close = difflib.get_close_matches(key, list(by_norm), n=1, cutoff=0.75)
    return by_norm[close[0]] if close else None


# ── key encoding ─────────────────────────────────────────────────────────────────────────────

def encode_key(key) -> str | None:
    """A pynput key -> "a" | "Key.enter" | "vk:65" (None when it cannot be named)."""
    ch = getattr(key, "char", None)
    if ch is not None and len(ch) == 1 and ch.isprintable():
        return ch
    name = getattr(key, "name", None)
    if name:
        return f"Key.{name}"
    vk = getattr(key, "vk", None)
    if vk is not None:
        return f"vk:{vk}"
    return None


def decode_key(s: str, keyboard):
    if s.startswith("Key."):
        return getattr(keyboard.Key, s[4:])
    if s.startswith("vk:"):
        return keyboard.KeyCode.from_vk(int(s[3:]))
    return s


# ── real input (pynput) ──────────────────────────────────────────────────────────────────────

class PynputInput:
    """The only class that touches the OS. Same interface as the fake used in tests."""

    def __init__(self):
        from pynput import keyboard, mouse           # ImportError -> caller explains
        self._kb, self._ms = keyboard, mouse
        self._kbc, self._msc = keyboard.Controller(), mouse.Controller()
        try:
            from computer.mouse import ensure_dpi_aware
            ensure_dpi_aware()                       # pynput coordinates are physical pixels
        except Exception:
            pass

    # recording -> emit({"k": "click"|"scroll"|"key", ...}) without the time stamp
    def start_recording(self, emit):
        def on_click(x, y, button, pressed, *a):
            emit({"k": "click", "x": int(x), "y": int(y), "b": getattr(button, "name", "left"), "d": bool(pressed)})

        def on_scroll(x, y, dx, dy, *a):
            emit({"k": "scroll", "x": int(x), "y": int(y), "dx": int(dx), "dy": int(dy)})

        def on_press(key, injected=False):
            if not injected and (k := encode_key(key)):
                emit({"k": "key", "key": k, "d": True})

        def on_release(key, injected=False):
            if not injected and (k := encode_key(key)):
                emit({"k": "key", "key": k, "d": False})

        ml = self._ms.Listener(on_click=on_click, on_scroll=on_scroll)
        kl = self._kb.Listener(on_press=on_press, on_release=on_release)
        ml.start(); kl.start()

        def stop():
            ml.stop(); kl.stop()
        return stop

    def listen_esc(self, on_esc):
        def on_press(key, injected=False):
            if key == self._kb.Key.esc:
                on_esc(bool(injected))
        kl = self._kb.Listener(on_press=on_press)
        kl.start()
        return kl.stop

    def do(self, ev: dict) -> None:
        k = ev["k"]
        if k == "click":
            self._msc.position = (ev["x"], ev["y"])
            btn = getattr(self._ms.Button, ev.get("b", "left"), self._ms.Button.left)
            (self._msc.press if ev["d"] else self._msc.release)(btn)
        elif k == "scroll":
            self._msc.position = (ev["x"], ev["y"])
            self._msc.scroll(ev.get("dx", 0), ev.get("dy", 0))
        elif k == "key":
            key = decode_key(ev["key"], self._kb)
            (self._kbc.press if ev["d"] else self._kbc.release)(key)

    def release_all(self, keys: set, buttons: set) -> None:
        for k in list(keys):
            try:
                self._kbc.release(decode_key(k, self._kb))
            except Exception:
                pass
        for b in list(buttons):
            try:
                self._msc.release(getattr(self._ms.Button, b, self._ms.Button.left))
            except Exception:
                pass

    def screen_size(self) -> tuple[int, int] | None:
        try:
            if sys.platform == "win32":
                import ctypes
                u = ctypes.windll.user32
                return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
            import pyautogui
            w, h = pyautogui.size()
            return int(w), int(h)
        except Exception:
            return None

    def password_focused(self) -> bool | None:
        """True/False from UI Automation, None when it cannot be asked (never raises).

        UNTESTED on a real Windows desktop in the build that produced this file."""
        if sys.platform != "win32":
            return None
        try:
            import comtypes
            from pywinauto.uia_defines import IUIA
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            el = IUIA().iuia.GetFocusedElement()
            return bool(el.CurrentIsPassword)
        except Exception:
            return None


def _new_input():
    return _input_factory() if _input_factory else PynputInput()


# ── recording ────────────────────────────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self, name: str, inp):
        self.name, self.inp = name, inp
        self.events: list[dict] = []
        self.t0 = 0.0
        self.skipped_password = 0
        self._stop_listeners = None
        self._pw = False
        self._pw_until = 0.0
        self._alive = False
        self._lk = threading.Lock()
        self.pw_supported = True
        self.auto_stopped = False

    def start(self):
        self.t0 = time.monotonic()
        self._alive = True
        self._stop_listeners = self.inp.start_recording(self._emit)
        threading.Thread(target=self._poll_password, daemon=True, name="macro-pw-poll").start()
        threading.Thread(target=self._watchdog, daemon=True, name="macro-watchdog").start()

    def _poll_password(self):
        while self._alive:
            try:
                v = self.inp.password_focused()
            except Exception:
                v = None
            if v is None:
                self.pw_supported = False
            elif v:
                self._pw_until = time.monotonic() + PASSWORD_STICKY_S
            time.sleep(PASSWORD_POLL_S)

    def _watchdog(self):
        while self._alive:
            time.sleep(0.5)
            if self._alive and time.monotonic() - self.t0 > MAX_RECORD_S:
                self.auto_stopped = True
                _auto_stop(self)
                return

    def _emit(self, ev: dict):
        if not self._alive:
            return
        now = time.monotonic()
        if ev["k"] == "key" and now < self._pw_until:
            self.skipped_password += 1
            return
        with self._lk:
            if len(self.events) >= MAX_EVENTS:
                if not self.auto_stopped:
                    self.auto_stopped = True
                    threading.Thread(target=_auto_stop, args=(self,), daemon=True).start()
                return
            ev["t"] = round(now - self.t0, 3)
            self.events.append(ev)

    def finish(self, typed_stop: bool) -> tuple[list[dict], int]:
        """Stop listening; return (events, trimmed). Events are shifted to start at t=0 and every
        key / button still held at the end gets a release."""
        self._alive = False
        try:
            if self._stop_listeners:
                self._stop_listeners()
        except Exception:
            pass
        with self._lk:
            ev = list(self.events)
        trimmed = 0
        if typed_stop:
            # The click that focused the chat box and the typed "stop recording" are not part of the macro.
            last = max((i for i, e in enumerate(ev) if e["k"] == "click" and e["d"]), default=None)
            if last is not None:
                trimmed = len(ev) - last
                ev = ev[:last]
        if ev:
            base = ev[0]["t"]
            for e in ev:
                e["t"] = round(e["t"] - base, 3)
            keys, btns = set(), set()
            for e in ev:
                if e["k"] == "key":
                    (keys.add if e["d"] else keys.discard)(e["key"])
                elif e["k"] == "click":
                    (btns.add if e["d"] else btns.discard)(e["b"])
            end = ev[-1]["t"] + 0.05
            last_xy = next(((e["x"], e["y"]) for e in reversed(ev) if "x" in e), (0, 0))
            for k in sorted(keys):
                ev.append({"k": "key", "key": k, "d": False, "t": end})
            for b in sorted(btns):
                ev.append({"k": "click", "x": last_xy[0], "y": last_xy[1], "b": b, "d": False, "t": end})
        return ev, trimmed


def _auto_stop(rec: "_Recorder") -> None:
    try:
        msg = _finish_recording(typed_stop=False, only=rec)
        _chat(msg)
    except Exception as e:  # noqa: BLE001
        _log().warning("macro auto-stop failed: %s", e)


def _finish_recording(typed_stop: bool, only: "_Recorder | None" = None) -> str:
    global _rec
    with _lock:
        rec = _rec
        if rec is None or (only is not None and rec is not only):
            return "I'm not recording a macro."
        _rec = None
    events, trimmed = rec.finish(typed_stop)
    if not events:
        _push("Macro not saved", "Nothing was recorded", phase="fail")
        return "Nothing was recorded, so I didn't save a macro."
    size = rec.inp.screen_size()
    data = {
        "name": rec.name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "screen": list(size) if size else None,
        "duration": events[-1]["t"],
        "events": events,
    }
    with _lock:
        d = _load()
        d["macros"][rec.name.lower()] = data
        _save(d)
    _push(f"Saved macro {rec.name}", f"{len(events)} steps", phase="success", ms=4000)
    bits = [f"Saved the macro {rec.name}: {len(events)} steps, about {max(1, round(data['duration']))} seconds."]
    if trimmed:
        bits.append(f"I left out the last {trimmed} steps, which were you stopping the recording.")
    if rec.auto_stopped:
        bits.append("I stopped it myself because it hit the length limit.")
    if rec.skipped_password:
        bits.append(f"I skipped {rec.skipped_password} keystrokes typed into a password field.")
    return " ".join(bits)


def _start_recording(m, ctx) -> str:
    global _rec
    name = _clean_name(m.group("name"))
    if not name:
        return "What should I call the macro?"
    with _lock:
        if _rec is not None:
            return f"I'm already recording {_rec.name}. Say stop recording first."
        if _play is not None:
            return "I'm replaying a macro right now. Press Esc twice to stop it first."
        if _norm(name) in {_norm(k) for k in _load()["macros"]}:
            return f"You already have a macro called {name}. Delete it or pick another name."
    try:
        inp = _new_input()
    except ImportError:
        return "Macros need the pynput package. Run: pip install pynput"
    except Exception as e:  # noqa: BLE001
        return f"I couldn't start the input hooks - {e}"
    detect = inp.password_focused() is not None
    warn = ("I save your clicks and everything you type as plain text, on this PC only"
            + ("" if detect else ", and I can't detect password fields on this PC")
            + ". Say no if you are going to type passwords. Say delete all macros to wipe them any time")
    ok, reason = _confirm("macro_record", f"record a macro called {name}. {warn}", ctx)
    if not ok:
        return reason or "Okay, I won't record."
    rec = _Recorder(name, inp)
    with _lock:
        if _rec is not None:
            return f"I'm already recording {_rec.name}."
        _rec = rec
    rec.start()
    _push(f"Recording {name}", "Say stop recording when you're done", phase="start", ms=6000)
    return (f"Recording {name}. Do the steps now and say stop recording when you're done."
            + ("" if detect else " I can't see password fields, so don't type any."))


def _stop_recording(m, ctx) -> str | None:
    if _rec is None:
        return None                       # "stop recording" may be meant for something else
    typed = not (isinstance(ctx, dict) and "recognizer" in ctx)
    return _finish_recording(typed_stop=typed)


# ── replay ───────────────────────────────────────────────────────────────────────────────────

class _Player:
    def __init__(self, macro: dict, inp):
        self.macro, self.inp = macro, inp
        self.abort = threading.Event()
        self._esc_times: list[float] = []
        self._self_esc_ts: list[float] = []
        self._held_keys: set = set()
        self._held_btns: set = set()
        self._stop_esc = None

    # called from the Esc listener thread
    def on_esc(self, injected: bool = False):
        now = time.monotonic()
        with _lock:
            own = injected or any(now - t < 0.25 for t in self._self_esc_ts)
        if own:                            # an Esc that replay itself sent
            return
        self._esc_times = [t for t in self._esc_times if now - t < ESC_WINDOW_S] + [now]
        if len(self._esc_times) >= 2:
            self.abort.set()

    def _sleep_until(self, t_abs: float) -> bool:
        while not self.abort.is_set():
            left = t_abs - time.monotonic()
            if left <= 0:
                return True
            time.sleep(min(left, 0.03))
        return False

    def run(self):
        global _play
        name = self.macro["name"]
        events = self.macro["events"]
        done = 0
        try:
            self._stop_esc = self.inp.listen_esc(self.on_esc)
            if not self._sleep_until(time.monotonic() + LEAD_IN_S):
                return self._end(name, done, len(events), aborted=True)
            t_clock = time.monotonic()
            prev_t = 0.0
            shift = 0.0
            last_push = 0.0
            for ev in events:
                gap = ev["t"] - prev_t
                if gap > MAX_GAP_S:
                    shift += gap - MAX_GAP_S
                prev_t = ev["t"]
                if not self._sleep_until(t_clock + ev["t"] - shift):
                    return self._end(name, done, len(events), aborted=True)
                if ev["k"] == "key" and ev["key"] == "Key.esc" and ev["d"]:
                    with _lock:
                        self._self_esc_ts.append(time.monotonic())
                self.inp.do(ev)
                if ev["k"] == "key":
                    (self._held_keys.add if ev["d"] else self._held_keys.discard)(ev["key"])
                elif ev["k"] == "click":
                    (self._held_btns.add if ev["d"] else self._held_btns.discard)(ev.get("b", "left"))
                done += 1
                now = time.monotonic()
                if now - last_push > 0.7:
                    last_push = now
                    _push(f"Running {name}", f"step {done} of {len(events)} - Esc twice to stop", phase="info")
            self._end(name, done, len(events), aborted=False)
        except Exception as e:  # noqa: BLE001
            _log().warning("macro replay failed: %s", e)
            self._end(name, done, len(events), aborted=True, error=str(e))
        finally:
            with _lock:
                if _play is self:
                    _play = None

    def _end(self, name, done, total, aborted, error=None):
        try:
            self.inp.release_all(self._held_keys, self._held_btns)
        except Exception:
            pass
        try:
            if self._stop_esc:
                self._stop_esc()
        except Exception:
            pass
        if error:
            msg = f"The macro {name} stopped after {done} of {total} steps: {error}"
            _push(f"{name} stopped", error[:100], phase="fail", ms=6000)
        elif aborted:
            msg = f"Stopped the macro {name} after {done} of {total} steps."
            _push(f"{name} stopped", f"{done} of {total} steps", phase="fail", ms=5000)
        else:
            msg = f"The macro {name} finished."
            _push(f"{name} finished", f"{total} steps", phase="success", ms=4000)
        _chat(msg)
        self.result = msg


def _run_macro(m, ctx) -> str:
    global _play
    macros = _load()["macros"]
    key = _find(m.group("name"), macros)
    if key is None:
        if not macros:
            return "You don't have any macros yet. Say record a macro called, then a name."
        return f"I don't have a macro called {_clean_name(m.group('name'))}. You have {_say_list([v['name'] for v in macros.values()])}."
    macro = macros[key]
    if _rec is not None:
        return f"I'm recording {_rec.name} right now. Say stop recording first."
    if _play is not None:
        return f"I'm already running {_play.macro['name']}."
    events = macro.get("events") or []
    if not events:
        return f"The macro {macro['name']} is empty."
    try:
        inp = _new_input()
    except ImportError:
        return "Macros need the pynput package. Run: pip install pynput"
    except Exception as e:  # noqa: BLE001
        return f"I couldn't start the input hooks - {e}"
    rec_size, now_size = macro.get("screen"), inp.screen_size()
    if rec_size and now_size and list(rec_size) != list(now_size):
        return (f"I won't replay {macro['name']}: it was recorded at {rec_size[0]} by {rec_size[1]} "
                f"and this screen is {now_size[0]} by {now_size[1]}. Record it again.")
    secs = max(1, round(macro.get("duration", 0)))
    ok, reason = _confirm(
        "macro_run",
        f"replay the macro {macro['name']} ({len(events)} steps, about {secs} seconds). "
        "It will move your mouse and press keys for you. Press Esc twice to stop it", ctx)
    if not ok:
        return reason or "Okay, I won't run it."
    player = _Player(macro, inp)
    with _lock:
        if _play is not None or _rec is not None:
            return "Something else started first. Try again."
        _play = player
    _push(f"Running {macro['name']}", "Starting in a moment - Esc twice to stop", phase="start", ms=4000)
    threading.Thread(target=player.run, daemon=True, name="macro-replay").start()
    return f"Running {macro['name']}. Don't touch the mouse. Press Esc twice to stop."


# ── list / rename / delete ───────────────────────────────────────────────────────────────────

def _say_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _list(m, ctx) -> str:
    macros = _load()["macros"]
    if not macros:
        return "You don't have any macros yet."
    bits = [f"{v['name']} ({len(v.get('events', []))} steps)" for v in macros.values()]
    return "Your macros: " + "; ".join(bits) + "."


def _rename(m, ctx) -> str:
    new = _clean_name(m.group("new"))
    with _lock:
        d = _load()
        key = _find(m.group("old"), d["macros"])
        if key is None:
            return f"I don't have a macro called {_clean_name(m.group('old'))}."
        if not new:
            return "What should I rename it to?"
        if _norm(new) in {_norm(k) for k in d["macros"] if k != key}:
            return f"You already have a macro called {new}."
        data = d["macros"].pop(key)
        old_name = data["name"]
        data["name"] = new
        d["macros"][new.lower()] = data
        _save(d)
    return f"Renamed {old_name} to {new}."


def _delete(m, ctx) -> str | None:
    d = _load()
    key = _find(m.group("name"), d["macros"])
    if key is None:
        return None                       # "delete macro ..." may be a file command
    name = d["macros"][key]["name"]
    ok, reason = _confirm("macro_delete", f"delete the macro {name}", ctx)
    if not ok:
        return reason or "Okay, I won't delete it."
    with _lock:
        d = _load()
        d["macros"].pop(key, None)
        _save(d)
    return f"Deleted the macro {name}."


def _privacy(m, ctx) -> str:
    info = privacy_info()
    n = len(info["macros"])
    saved = f"You have {n} saved macro{'s' if n != 1 else ''}." if n else "You have no saved macros."
    return " ".join(PRIVACY) + f" They are in {info['path']}. {saved}"


def _delete_all(m, ctx) -> str | None:
    n = len(_load()["macros"])
    if n == 0:
        return "You don't have any macros to delete."
    ok, reason = _confirm("macro_delete_all", f"delete all {n} of your macros. This can't be undone", ctx)
    if not ok:
        return reason or "Okay, I won't delete them."
    return f"Deleted {delete_all()} macro{'s' if n != 1 else ''}. Nothing is left on this PC."


# ── entry point ──────────────────────────────────────────────────────────────────────────────

_RECORD = re.compile(r"^(?:start\s+)?record(?:ing)?\s+(?:an?\s+|the\s+|new\s+)*macro\s+(?:called\s+|named\s+)?(?P<name>.+)$", re.I)
_STOP = re.compile(r"^(?:stop|finish|end|save)\s+(?:the\s+)?(?:macro\s+)?recording(?:\s+(?:the\s+)?macro)?$", re.I)
_RUN = re.compile(r"^(?:run|play|replay|play\s+back)\s+(?:the\s+)?macro\s+(?:called\s+|named\s+)?(?P<name>.+)$", re.I)
_RUN2 = re.compile(r"^(?:run|play|replay)\s+(?:the\s+)?(?P<name>.+?)\s+macro$", re.I)
_LIST = re.compile(r"^(?:(?:list|show)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?macros|what\s+macros\s+do\s+i\s+have)$", re.I)
_RENAME = re.compile(r"^rename\s+(?:the\s+)?macro\s+(?P<old>.+?)\s+to\s+(?P<new>.+)$", re.I)
_PRIVACY = re.compile(r"^(?:what\s+(?:do|does|will)\s+(?:you|macros?)\s+(?:record|save|store|capture)(?:\s+(?:in|for)\s+(?:a\s+)?macros?)?"
                      r"|where\s+(?:are|do\s+you\s+(?:save|store|keep))\s+(?:my\s+)?macros?(?:\s+(?:saved|stored|kept))?"
                      r"|macros?\s+privacy|is\s+macro\s+recording\s+(?:safe|private))$", re.I)
_DELETE_ALL = re.compile(r"^(?:delete|remove|erase|wipe|forget)\s+(?:all|every|all\s+of\s+my)\s+(?:my\s+)?macros$", re.I)
_DELETE = re.compile(r"^(?:delete|remove)\s+(?:the\s+)?macro\s+(?:called\s+|named\s+)?(?P<name>.+)$", re.I)

_HANDLERS = (
    (_STOP, _stop_recording),
    (_RECORD, _start_recording),
    (_RUN, _run_macro),
    (_RUN2, _run_macro),
    (_LIST, _list),
    (_RENAME, _rename),
    (_PRIVACY, _privacy),
    (_DELETE_ALL, _delete_all),
    (_DELETE, _delete),
)


def try_macro(command: str, engine_context=None) -> str | None:
    """The single entry point hooked into actions.try_handle_action(). Never raises."""
    try:
        if not _enabled():
            return None
        c = _clean(command)
        if not c:
            return None
        for pattern, handler in _HANDLERS:
            m = pattern.match(c)
            if not m:
                continue
            try:
                reply = handler(m, engine_context)
            except Exception as e:  # noqa: BLE001
                _log().warning("macros failed: %s", e)
                return f"The macro helper hit a problem - {e}"
            if reply:
                return reply
        return None
    except Exception as e:  # noqa: BLE001
        try:
            _log().warning("macros: %s", e)
        except Exception:
            pass
        return None
