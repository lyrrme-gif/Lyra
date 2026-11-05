"""Lyra ↔ Lyra UI bridge over local HTTP (more reliable than WS on Windows).

  POST http://127.0.0.1:8765/chat   body: {"text":"..."}
  GET  http://127.0.0.1:8765/events?since=0
       → {"events":[...], "next": N}

Events:
  {"type":"state","state":"thinking"}
  {"type":"chat","role":"lyra","text":"..."}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from engine.bridge import ui

log = ui.log

HOST = "127.0.0.1"
PORT = 8765

_started = False
_STARTED_AT = time.time()
_PID = os.getpid()  # module-level: do_GET has a local "import os" in its /about branch
_PREF_PATHS = ("/settings/privacy", "/settings/notifications", "/settings/ai", "/settings/voice", "/settings/advanced", "/settings/features", "/settings/recordings")
# Endpoints that show or change personal data. Browsers always send Origin on cross-site
# requests, so only Lyra's own windows (and curl, which sends none) may use these.
_GUARDED = ("/clipboard", "/memory", "/shortcuts", "/settings/export", "/settings/import", 
            "/settings/reset", "/backend/", "/logs/", "/about", "/volume/", "/diagnostics", "/screen/", "/todos", "/calendar", "/schedule", "/routines", "/stats", "/controls", "/wallpaper", "/files/", "/plugins", "/apps", "/gcal", "/google/", "/record", "/camera", "/study", "/mascot", "/dictation", "/voice/offline", "/trace", "/settings-log", "/spotify/play")
_OWN_ORIGINS = {
    "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
    "http://localhost:1420", "http://127.0.0.1:1420",
}
# >>> phase-8 begin
_GUARDED = _GUARDED + ("/p8/",)      # updates: only Lyra's own windows may check, download or hand off an installer
# <<< phase-8 end

def _ollama_configured() -> bool:
    try:
        from engine.brain import ollama_support
        return ollama_support.configured()
    except Exception:
        return False


def _export_bundle() -> dict:
    """Everything Settings can restore. API keys and memory are deliberately NOT included."""
    from engine.core import settings_store; from engine.brain import llm_store
    from engine.shortcuts import settings_api
    llm = llm_store.load()
    return {
        "app": "lyra-settings",
        "version": 1,
        "prefs": {g: settings_store.get_group(g) for g in settings_store.PREF_GROUPS},
        "llm": {k: llm.get(k) for k in ("provider", "openrouter_model", "gemini_model", "groq_model", "ollama_model")},
        "shortcuts": settings_api.list_all()["shortcuts"],
    }


def _import_bundle(data: dict) -> tuple[int, list]:
    """Apply what validates, skip the rest. Returns (applied count, skipped messages)."""
    import re as _re
    from engine.core import settings_store; from engine.brain import llm_store
    from engine.shortcuts import settings_api
    applied, skipped = 0, []

    for group, values in (data.get("prefs") or {}).items():
        if group not in settings_store.PREF_GROUPS or not isinstance(values, dict):
            continue
        for key, value in values.items():
            try:
                settings_store.update_group(group, {key: value})
                applied += 1
            except ValueError as e:
                skipped.append(str(e))

    llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
    upd = {}
    if llm.get("provider") in ("openrouter", "gemini", "groq", "ollama"):
        upd["provider"] = llm["provider"]
    for k in ("openrouter_model", "gemini_model", "groq_model"):
        if isinstance(llm.get(k), str) and _re.fullmatch(r"[\w./:\-]{1,100}", llm[k]):
            upd[k] = llm[k]
    if upd:
        llm_store.save(upd)
        applied += len(upd)


    existing = {x["id"] for x in settings_api.list_all()["shortcuts"]}
    for sc in data.get("shortcuts") or []:
        if not isinstance(sc, dict):
            continue
        try:
            sid = str(sc.get("id") or "")
            settings_api.save({**sc, "id": sid if sid in existing else ""})
            applied += 1
        except ValueError as e:
            skipped.append(f"{sc.get('name', 'shortcut')}: {e}")
    return applied, skipped
_ctx = threading.local()   # .from_ui = True while handling a message typed in the island's chat box
_history: list = []
_chat_lock = threading.Lock()

# Ring buffer of events for UI polling
_events: list[dict[str, Any]] = []
_events_lock = threading.Lock()
_event_seq = 0
_MAX_EVENTS = 1500


def _push_event(payload: dict) -> None:
    global _event_seq
    with _events_lock:
        # Safety net: an identical state event right after the previous one adds nothing.
        if payload.get("type") == "state" and _events and _events[-1].get("type") == "state" \
                and _events[-1].get("state") == payload.get("state") \
                and payload.get("state") in ("standby", "idle"):
            return
        _event_seq += 1
        item = dict(payload)
        item["_id"] = _event_seq
        # Let a window distinguish a just-published event from old events
        # returned by its first poll after the bridge has been running awhile.
        item.setdefault("ts", int(time.time() * 1000))
        _events.append(item)
        if len(_events) > _MAX_EVENTS:
            del _events[: len(_events) - _MAX_EVENTS]
    try:
        from engine.bridge import ui_trace
        ui_trace.event_out(item)           # live trace: what the backend pushed to the UI
    except Exception:
        pass


def _events_since(since: int) -> tuple[list[dict], int]:
    with _events_lock:
        out = [e for e in _events if int(e.get("_id", 0)) > since]
        nxt = _event_seq
    return out, nxt


def push_mascot_emote(name: str, ms: int = 3000) -> None:
    """Make the Mochi show a mood for `ms` milliseconds (only while the mascot feature is on)."""
    try:
        from engine.core import settings_store
        if settings_store.get("feature_mascot") is not True:
            return
    except Exception:
        return
    _push_event({"type": "mascot", "cmd": "emote", "name": str(name), "ms": int(ms)})


def push_notification(app: str, kind: str, sender: str, text: str, force: bool = False, chat: str = "") -> None:
    """Show a notification in the island's compact notch.

    app:  "discord" | "whatsapp" | "telegram" | "reminder"
    kind: "message" | "call" | "reminder"
    Called by notifications.py for Windows toasts, and by Lyra's own reminders.
    Settings (per-app switches, calls-only, quiet hours) filter it unless `force` (test button).
    chat:  optional id of the conversation (WhatsApp): a click on the toast opens that chat on the notch.
    """
    app = str(app or "").lower()
    try:
        from engine.features import notification_history
        notification_history.add(app, kind, sender, text)
    except Exception:
        pass
    if not force:
        try:
            from engine.features import notifications
            if not notifications.should_show(app, kind):
                log.info("notification %s/%s hidden by settings", app, kind)
                return
        except Exception as e:  # a settings problem must never swallow a notification
            log.warning("notification filter failed: %s", e)
    try:
        from engine.core import settings_store
        duration_ms = int(settings_store.get("notify_toast_ms"))
    except Exception:
        duration_ms = 4000
    _push_event({
        "type": "notify",
        "app": app,
        "duration_ms": duration_ms,
        "kind": kind if kind in ("message", "call", "reminder") else "message",
        "sender": str(sender or "")[:80],
        "text": str(text or "")[:200],
        "chat": str(chat or "")[:120],
        "ts": int(time.time() * 1000),   # the island ignores events older than a few seconds
    })
    if kind == "call":
        push_mascot_emote("ring", 6000)


def push_dictation_polish(text: str = "Polished version ready") -> None:
    """Island toast with an "Apply polished version" button (the dictated text was not replaced
    by itself because the window or caret changed). The button posts to /dictation/apply."""
    _push_event({
        "type": "notify",
        "app": "dictation",
        "duration_ms": 12000,
        "kind": "dictation",
        "sender": "Dictation",
        "text": str(text or "")[:200],
        "ts": int(time.time() * 1000),
    })


def push_live_text(text: str, source: str = "ptt", final: bool = False, app: str = "") -> None:
    """What the user is saying RIGHT NOW, for the island: the streaming recognizer's partial words
    (final=False), or the accurate text once it is known (final=True: the island swaps its live guess
    for it). Display only: commands and pasted dictation always use the accurate transcript."""
    _push_event({
        "type": "live_text",
        "text": str(text or "")[:400],
        "source": "dictation" if source == "dictation" else "ptt",
        "final": bool(final),
        "app": str(app or "")[:60],
        "ts": int(time.time() * 1000),
    })


def push_schedule(open_view: bool = False) -> None:
    """Tell the island the scheduled actions / routines changed (and optionally to show them)."""
    _push_event({"type": "schedule", "open": bool(open_view), "ts": int(time.time() * 1000)})


def push_todos(open_view: bool = False) -> None:
    """Tell the island the to-do list changed; open_view=True also shows the To-do view."""
    _push_event({"type": "todos", "open": bool(open_view), "ts": int(time.time() * 1000)})


def push_controls(open_view: bool = False) -> None:
    """Tell the island the quick controls changed (by voice); open_view=True also shows the Controls tab."""
    _push_event({"type": "controls", "open": bool(open_view), "ts": int(time.time() * 1000)})


def push_appearance(fields: dict) -> None:
    """Tell the UI to change theme / accent / density / motion / island_theme (by voice). The island applies it."""
    _push_event({"type": "appearance", "fields": dict(fields), "ts": int(time.time() * 1000)})


def push_wallpaper(open_view: bool = False) -> None:
    """Tell the island the wallpaper changed (by voice); open_view=True also shows the Wallpaper tab."""
    _push_event({"type": "wallpaper", "open": bool(open_view), "ts": int(time.time() * 1000)})


def push_record(recording: bool, started_at_ms: int = 0, paused: bool = False, elapsed_ms: int = 0) -> None:
    """Tell the island a SCREEN recording started / paused / stopped (the red pill). It used to find out only from a
    30 s poll, so the pill appeared late and stayed after the recording had ended. `started_at_ms` is the moment
    that makes now - started_at = the recorded time (pauses excluded); while `paused` the pill shows `elapsed_ms` frozen."""
    ev = {"type": "record", "recording": bool(recording), "started_at": int(started_at_ms), "ts": int(time.time() * 1000)}
    if paused:
        ev.update({"paused": True, "elapsed_ms": int(elapsed_ms)})
    _push_event(ev)


def push_call_record(recording: bool, started_at_ms: int = 0) -> None:
    """Tell the island a Notetaker call recording started / stopped, so it does not have to ask."""
    _push_event({"type": "call_record", "recording": bool(recording), "started_at": int(started_at_ms),
                 "ts": int(time.time() * 1000)})


# The tabs the island knows (ids = the island's view names; see ui/src/core/destinations.ts).
OPEN_VIEWS = ("overview", "music", "prompt", "timer", "calendar", "todo", "weather", "stats",
              "controls", "volume", "recognition", "clipboard", "camera", "wallpaper", "translate", "record", "apps")


def push_open_view(view: str) -> None:
    """Show a tab on the island (a backend feature that needs one can call this)."""
    if view not in OPEN_VIEWS:
        return
    _push_event({"type": "open_view", "view": view, "ts": int(time.time() * 1000)})


ADDED_KINDS = ("todo", "reminder", "alarm", "timer", "stopwatch", "focus", "event")


def push_added(view: str, text: str = "", kind: str = "", status: str = "ok") -> None:
    """Something was added by voice (a to-do, a reminder, an alarm, a timer, an event): the island opens that
    tab and plays the matching animation with `text` in it. `kind` picks the animation (ADDED_KINDS; empty =
    the tab's default); `status` is "ok" (success) or "fail" (it could not be added: red pill + shake).
    See tab_sync.py."""
    if view not in OPEN_VIEWS:
        return
    _push_event({"type": "added", "view": view, "text": str(text or "")[:90],
                 "kind": kind if kind in ADDED_KINDS else "",
                 "status": "fail" if status == "fail" else "ok",
                 "ts": int(time.time() * 1000)})


def push_translate(src: str, dst: str, text: str, translation: str) -> None:
    """Show a translation on the island's Translate tab (opens it): what was said, the language it was
    in, the target language and the result."""
    _push_event({"type": "translate", "src": str(src or "")[:24], "dst": str(dst or "")[:24],
                 "text": str(text or "")[:1500], "result": str(translation or "")[:1500],
                 "ts": int(time.time() * 1000)})


def push_wheel(action: str) -> None:
    """Drive the tab wheel (a circle of tabs in its own window): open | stay | commit come from the
    held shortcut (it appears at the cursor), show from voice (middle of the screen), close."""
    if action not in ("open", "stay", "commit", "show", "close"):
        return
    _push_event({"type": "wheel", "action": action, "ts": int(time.time() * 1000)})


def push_stats(open_view: bool = True) -> None:
    """Show the Stats tab on the island (\"show my stats\")."""
    _push_event({"type": "stats", "open": bool(open_view), "ts": int(time.time() * 1000)})


def push_search_results(query: str, results: list[dict], provider: str = "", card: dict | None = None,
                        related: list[str] | None = None, more: list[dict] | None = None,
                        more_title: str = "") -> None:
    """Publish structured real search results to the Island."""
    try:
        from engine.features import yt_island
        yt_island.remember(results)  # so "open the second one" can pick from them
    except Exception:
        pass
    _push_event({
        "type": "web_search",
        "query": query,
        "provider": provider,
        "results": results[:8],
        "card": card or None,
        "related": [str(x)[:80] for x in (related or [])[:4]],
        "more": (more or [])[:3],
        "more_title": str(more_title or "")[:80],
        "ts": int(time.time() * 1000),
    })


def push_search_dismiss() -> None:
    """Tell the island the results on screen are finished (a result was opened by voice)."""
    _push_event({"type": "search_dismiss", "ts": int(time.time() * 1000)})


def push_activity(
    kind: str,
    title: str,
    *,
    phase: str = "info",
    subtitle: str | None = None,
    app: str | None = None,
    duration_ms: int = 2800,
    icon_url: str | None = None,
    url: str | None = None,
) -> None:
    """Reusable Island activity feedback (app launch, recognition, etc.).

    kind:   logical category, e.g. "app_launch", "music_recognition"
    phase:  "start" | "success" | "fail" | "info"
    title:  primary label shown on the Island (e.g. "Opening Discord…")
    Never claim success unless the backend action actually succeeded.
    """
    payload = {
        "type": "activity",
        "kind": str(kind or "info"),
        "phase": str(phase or "info"),
        "title": str(title or "")[:80],
        "subtitle": (str(subtitle)[:120] if subtitle else None),
        "app": (str(app).lower()[:40] if app else None),
        "icon_url": (str(icon_url)[:2_000_000] if icon_url else None),
        "url": (str(url)[:500] if url else None),
        "duration_ms": int(duration_ms),
        "ts": int(time.time() * 1000),
    }
    _push_event(payload)


def _on_ui_event(event: str, **kwargs) -> None:
    if event == "state":
        _push_event({"type": "state", "state": kwargs.get("state", "idle")})
    elif event == "wave":
        _push_event({
            "type": "wave",
            "levels": kwargs.get("levels") or [],
            "hz": kwargs.get("hz", 25),
            "start_ms": kwargs.get("start_ms", 0),
        })
    elif event == "chat":
        text = kwargs.get("text", "")
        if not text:
            return
        item = {
            "type": "chat",
            "role": kwargs.get("role", ""),
            "text": text,
            "detail": kwargs.get("detail") or "",
            # True only for messages the island itself typed (it already shows
            # those locally). Spoken / terminal messages are local=False, so the
            # island must display them.
            "local": bool(getattr(_ctx, "from_ui", False)),
        }
        if str(kwargs.get("role", "")).lower() == "lyra":
            # A short acknowledgement ("Opening Spotify, sir.", "Done.") can be a Mochi sound instead of
            # speech (Settings -> Voice). voice.speak() decides from the same text, see mochi_sounds.
            try:
                from engine.voice import mochi_sounds
                plan = mochi_sounds.plan(text)
            except Exception:
                plan = None
            if plan:
                item.update(mochi=plan["kind"], mochi_subtitle=plan["subtitle"], mochi_silent=plan["silent"], mochi_ms=plan["ms"])
                push_mascot_emote(plan["emote"], plan["ms"])
        _push_event(item)


def _handle_chat_message(text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    with _chat_lock:
        _ctx.from_ui = True
        try:
            ui.set_state("thinking") 
            from engine.core import perf
            perf.begin("typed", text)
            import engine.core.main as engine
            engine.process_command_chain(text, _history)
        except Exception as e:
            log.exception("UI chat failed: %s", e)
            err = f"Sorry — something went wrong: {e}"
            ui.print_chat("lyra", err)
        finally:
            _ctx.from_ui = False
            ui.set_state("idle")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter
        log.debug("http " + fmt, *args)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _own_origin(self, path: str) -> bool:
        if not path.startswith(_GUARDED):
            return True
        origin = self.headers.get("Origin")
        return not origin or origin in _OWN_ORIGINS

    def _note_slow(self) -> None:
        """Trace only: how long the BACKEND needed for this request (from do_GET/do_POST until the reply was
        written). The "(NNN ms)" in the UI's http lines also counts time the page spent too busy to read the
        answer, so it cannot tell a slow backend from a busy page. Quiet unless it took 40 ms or more, so a
        log with no http-slow line for a request means the backend answered fast."""
        try:
            t0 = getattr(self, "_t0", None)
            if t0 is None:
                return
            ms = (time.perf_counter() - t0) * 1000.0
            if ms < 40.0:
                return
            from engine.core import dbg
            if dbg.on() and self.path.split("?")[0] not in ("/trace", "/events", "/pidprobe", "/status", "/settings-log"):
                dbg.ev("http-slow", f"backend needed {ms:.0f} ms for {self.command} {self.path.split('?')[0]}")
        except Exception:
            pass

    def _apps_upload(self, parsed):
        """POST /apps/upload?id=<app>&name=<file name>, raw file bytes as the body (the + button of a chat app)."""
        from engine.plugins import apps
        q = parse_qs(parsed.query)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > apps.UPLOAD_MAX_BYTES:
            self.close_connection = True
            self._json(413, {"ok": False, "error": "That file is empty or larger than 16 MB."})
            return
        data = self.rfile.read(length)
        self._json(200, apps.save_upload((q.get("id") or [""])[0], (q.get("name") or ["file"])[0], data))

    def _file(self, path: str, ctype: str):
        """Send one saved photo / voice message (GET /apps/media). Never cached by shared caches, never sniffed."""
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self._json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=3600")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _json(self, code: int, obj: dict):
        if code >= 400:
            try:
                from engine.bridge import ui_trace
                ui_trace.http_error(self.command, self.path, code, obj)
            except Exception:
                pass
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A webview often cancels an in-flight settings request while
            # navigating or closing. The request is already over; don't emit
            # an exception traceback from the server thread.
            return
        self._note_slow()

    def _serve_clipboard_file(self, parsed):
        """Streams a stored Clipboard-tab file (used to drag it out of the island)."""
        import mimetypes
        import shutil
        from urllib.parse import quote
        from engine.features import clipboard_session
        qs = parse_qs(parsed.query)
        try:
            iid = int((qs.get("id") or ["0"])[0])
            idx = int((qs.get("i") or ["0"])[0])
        except ValueError:
            self._json(400, {"error": "bad request"})
            return
        path = clipboard_session.file_path(iid, idx)
        if path is None:
            self._json(404, {"error": "not found"})
            return
        try:
            size = path.stat().st_size
            f = open(path, "rb")
        except OSError:
            self._json(404, {"error": "not found"})
            return
        with f:
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(path.name))
            self._cors()
            self.end_headers()
            try:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _clipboard_upload(self, parsed):
        """POST /clipboard/upload?name=<file name>, raw file bytes as the body."""
        from engine.features import clipboard_session
        name = (parse_qs(parsed.query).get("name") or ["file"])[0]
        raw_len = self.headers.get("Content-Length")
        try:
            length = int(raw_len) if raw_len is not None else None
        except ValueError:
            length = None
        try:
            item = clipboard_session.add_upload(name, self.rfile, length)
        except clipboard_session.UploadError as e:
            self.close_connection = True
            self._json(413, {"ok": False, "error": str(e)})
            return
        except Exception as e:
            log.exception("clipboard upload failed: %s", e)
            self.close_connection = True
            self._json(500, {"ok": False, "error": str(e)})
            return
        self._json(200, {"ok": True, "id": item["id"], "items": clipboard_session.snapshot()})

    def _camera_upload(self, parsed):
        """POST /camera/upload?kind=photo|video&ext=png|jpg|webp|mp4|webm, raw file bytes as the body."""
        from engine.features import camera_store
        q = parse_qs(parsed.query)
        kind = (q.get("kind") or ["photo"])[0]
        ext = (q.get("ext") or [""])[0]
        raw_len = self.headers.get("Content-Length")
        try:
            length = int(raw_len) if raw_len is not None else None
        except ValueError:
            length = None
        try:
            self._json(200, camera_store.save_stream(kind, ext, self.rfile, length))
        except camera_store.CameraSaveError as e:
            self.close_connection = True
            self._json(413 if "large" in str(e) else 400, {"ok": False, "error": str(e)})
        except Exception as e:
            log.exception("camera upload failed: %s", e)
            self.close_connection = True
            self._json(500, {"ok": False, "error": str(e)})

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._t0 = time.perf_counter()      # server-side clock for the trace (see _note_slow)
        parsed = urlparse(self.path)
        if not self._own_origin(parsed.path):
            self._json(403, {"error": "forbidden"})
            return
        try:
            from engine.bridge import ui_trace
            ui_trace.http_in("GET", parsed.path)     # live trace: what the UI asked for
        except Exception:
            pass
        if parsed.path in ("/", "/health"):
            downloading = False
            try:
                from engine.core import model_setup
                downloading = bool(model_setup.state()["downloading"])
            except Exception:
                pass
            self._json(200, {"ok": True, "service": "lyra-ui-bridge", "pid": _PID,
                             "downloading_models": downloading})
            return
        if parsed.path.startswith("/voice/offline"):
            from engine.voice import tts_offline
            res = tts_offline.dispatch("GET", parsed.path)
            if res is not None:
                self._json(res[0], res[1])
                return
        if parsed.path == "/diagnostics":
            try:
                from engine.core import model_setup
                self._json(200, model_setup.diagnostics())
            except Exception as e:
                self._json(500, {"ok": False, "problems": [str(e)], "error": str(e)})
            return
        if parsed.path == "/clipboard/file":
            self._serve_clipboard_file(parsed)
            return
        if parsed.path == "/clipboard/session":
            try:
                from engine.features import clipboard_session
                self._json(200, {"ok": True, "items": clipboard_session.snapshot()})
            except Exception as e:
                self._json(500, {"ok": False, "items": [], "error": str(e)})
            return
        if parsed.path == "/clipboard":
            try:
                from engine.features import clipboard_history
                self._json(200, clipboard_history.snapshot())
            except Exception as e:
                self._json(200, {"kind": "empty", "text": "", "files": [], "error": str(e)})
            return
        if parsed.path == "/events":
            qs = parse_qs(parsed.query)
            since = int((qs.get("since") or ["0"])[0] or 0)
            events, nxt = _events_since(since)
            self._json(200, {"events": events, "next": nxt})
            return
        if parsed.path.startswith("/files/"):
            from engine.features import file_index
            code, obj = file_index.http("GET", parsed.path, {})
            self._json(code, obj)
            return
        # >>> phase-8 begin
        if parsed.path.startswith("/p8/"):
            from engine.bridge import api_p8
            code, obj = api_p8.http("GET", parsed.path, {})
            self._json(code, obj)
            return
        # <<< phase-8 end
        if parsed.path == "/plugins":
            from engine.plugins import loader
            self._json(200, loader.describe())
            return
        if parsed.path == "/apps":
            from engine.plugins import apps
            self._json(200, apps.list_apps())
            return
        if parsed.path == "/apps/media":        # a saved photo / voice message (guarded like every /apps call)
            from engine.plugins import apps
            qs = parse_qs(parsed.query)
            found = apps.get_media((qs.get("id") or [""])[0], (qs.get("f") or [""])[0])
            if found:
                self._file(*found)
            else:
                self._json(404, {"error": "not found"})
            return
        if parsed.path == "/settings/ollama":
            from engine.brain import ollama_support
            qs = parse_qs(parsed.query)
            self._json(200, ollama_support.status(probe=(qs.get("probe") or [""])[0] in ("1", "true")))
            return
        if parsed.path == "/google/status":
            from engine.features import google_workspace
            self._json(200, google_workspace.status())
            return
        if parsed.path == "/gcal/events":
            from engine.features import google_workspace
            qs = parse_qs(parsed.query)
            self._json(200, google_workspace.ui_events((qs.get("from") or [""])[0], (qs.get("to") or [""])[0]))
            return
        if parsed.path == "/settings/llm/models":
            from engine.brain.llm_store import list_models
            qs = parse_qs(parsed.query)
            self._json(200, list_models(str(qs.get("provider", [""])[0] or "") or None))
            return

        if parsed.path == "/settings/llm":
            from engine.brain.llm_store import load
            data = load()
            # mask keys
            out = {
                "provider": data.get("provider", "openrouter"),
                "openrouter_model": data.get("openrouter_model"),
                "gemini_model": data.get("gemini_model"),
                "groq_model": data.get("groq_model"),
                "ollama_model": data.get("ollama_model", ""),
                "ollama_vision_model": data.get("ollama_vision_model", ""),
                "ollama_base_url": data.get("ollama_base_url", "http://localhost:11434"),
                "ollama_timeout": data.get("ollama_timeout", 25),
                "ollama_keep_alive": data.get("ollama_keep_alive", "1m"),
                "has_openrouter": bool(data.get("openrouter_api_key")),
                "has_gemini": bool(data.get("gemini_api_key")),
                "has_groq": bool(data.get("groq_api_key")),
                "has_ollama": _ollama_configured(),
            }
            self._json(200, out)
            return
        if parsed.path == "/lyrics":
            qs = parse_qs(parsed.query)
            q = lambda k: (qs.get(k) or [""])[0]
            try:
                dur = int(float(q("duration"))) if q("duration") else None
            except ValueError:
                dur = None
            from engine.features import lyrics
            self._json(200, lyrics.lookup(q("title"), q("artist"), q("album"), dur))
            return
            return
        if parsed.path == "/timers":
            from engine.features import timers
            self._json(200, timers.snapshot())
            return
        if parsed.path == "/todos":
            from engine.actions import notes
            self._json(200, {"ok": True, "items": notes.todo_items()})
            return
        if parsed.path == "/schedule":
            from engine.features import scheduler
            self._json(200, {"ok": True, **scheduler.snapshot()})
            return
        if parsed.path == "/wallpaper":
            from engine.features import wallpaper
            src = (parse_qs(parsed.query).get("source") or ["recent"])[0]
            self._json(200, wallpaper.snapshot(src))
            return
        if parsed.path == "/controls":
            from engine.features import controls
            self._json(200, controls.snapshot())
            return
        if parsed.path == "/record/status":
            from engine.features import screen_record
            self._json(200, screen_record.status())
            return
        if parsed.path == "/record/hotkeys":
            from engine.features import record_hotkey
            self._json(200, {"ok": True, **record_hotkey.status()})
            return
        if parsed.path == "/screen/monitors":
            try:
                from engine.features import screen_capture
                mons = screen_capture.monitors()
                self._json(200, {"ok": True, "monitors": [{**m, "number": m["index"] + 1, "label": screen_capture.screen_label(m, mons)} for m in mons]})
            except Exception as e:
                self._json(500, {"ok": False, "monitors": [], "error": str(e)})
            return
        if parsed.path == "/record/list":
            from engine.features import screen_record
            self._json(200, screen_record.list_recordings())
            return
        if parsed.path == "/record/folder":
            from engine.features import screen_record
            self._json(200, screen_record.folder_info())
            return
        if parsed.path == "/record/thumb":
            from engine.features import screen_record
            data = screen_record.thumbnail((parse_qs(parsed.query).get("name") or [""])[0])
            if data is None:
                self._json(404, {"error": "no preview"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        if parsed.path == "/stats":
            from engine.features import sysstats
            self._json(200, sysstats.snapshot())
            return
        if parsed.path == "/stats/speedtest":
            from engine.features import sysstats
            self._json(200, sysstats.speedtest_state())
            return
        if parsed.path == "/weather":
            from engine.features import weather_widget
            self._json(200, weather_widget.get((parse_qs(parsed.query).get("city") or [""])[0][:60]))
            return
        if parsed.path in _PREF_PATHS:
            from engine.core import settings_store
            self._json(200, settings_store.get_group(parsed.path.rsplit("/", 1)[1]))
            return
        if parsed.path == "/shortcuts":
            from engine.shortcuts import settings_api
            self._json(200, settings_api.list_all())
            return
        if parsed.path == "/memory":
            from engine.brain import memory
            self._json(200, memory.snapshot())
            return
        if parsed.path == "/settings/export":
            self._json(200, _export_bundle())
            return
        if parsed.path == "/volume/apps":
            from engine.features import app_volume
            try:
                self._json(200, {"apps": app_volume.list_apps()})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path == "/app/icon":  # icon of an installed app, by name (?name=WhatsApp)
            try:
                import urllib.parse
                from engine.actions import apps as _apps
                nm = urllib.parse.parse_qs(parsed.query).get("name", [""])[0]
                self._json(200, {"icon": _apps.app_icon_for_name(nm)})
            except Exception as e:
                self._json(200, {"icon": None, "error": str(e)})
            return

        if parsed.path == "/music/history":  # the island fetches this with GET
            try:
                from engine.features import music_recognition
                self._json(200, {"items": music_recognition.history(), "session": music_recognition.SESSION_ID})
            except Exception as e:
                self._json(500, {"items": [], "error": str(e)})
            return

        if parsed.path == "/settings-log":             # per-tab CPU/GPU/loading table of the Settings window
            from engine.core import settings_log
            self._json(200, settings_log.http_get())
            return
        if parsed.path == "/pidprobe":                 # window <-> process mapping (dbg.py)
            from engine.core import dbg
            self._json(200, dbg.pidprobe((parse_qs(parsed.query).get("win") or [""])[0][:20]))
            return
        if parsed.path == "/status":
            from engine.features import status_watch
            self._json(200, status_watch.snapshot())
            return
        if parsed.path == "/about":
            import platform, sys
            from engine.bridge.ui import LOG_PATH
            try:
                log_kb = round(LOG_PATH.stat().st_size / 1024)
            except OSError:
                log_kb = 0
            self._json(200, {
                "python": platform.python_version(),
                "os": platform.platform(),
                "pid": os.getpid(),
                "uptime_s": int(time.time() - _STARTED_AT),
                "log_path": str(LOG_PATH),
                "log_kb": log_kb,
            })
            return
        if parsed.path == "/voice/devices":
            # Pickers for Settings > Voice. Each list is empty if the device layer is unavailable.
            from engine.core import settings_store
            try:
                from engine.voice import mic_devices
                inputs = mic_devices.list_input_devices()
            except Exception:
                inputs = []
            try:
                from engine.voice import voice
                outputs = voice.list_output_devices()
            except Exception:
                outputs = []
            self._json(200, {"inputs": inputs, "outputs": outputs, "voices": list(settings_store.TTS_VOICES)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        self._t0 = time.perf_counter()
        parsed = urlparse(self.path)
        if not self._own_origin(parsed.path):
            self._json(403, {"error": "forbidden"})
            return
        if parsed.path == "/clipboard/upload":
            self._clipboard_upload(parsed)
            return
        if parsed.path == "/camera/upload":
            self._camera_upload(parsed)
            return
        if parsed.path == "/apps/upload":
            self._apps_upload(parsed)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            msg = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._json(400, {"error": "bad json"})
            return

        if parsed.path == "/trace":                   # the UI's own click / state / error trace (ui/src/core/trace.ts)
            from engine.bridge import ui_trace
            ui_trace.ingest(msg)
            try:
                from engine.core import dbg
                off = not dbg.on()
            except Exception:
                off = False
            self._json(200, {"ok": True, "off": off})   # off: LYRA_TRACE=0, the page stops its own tracer
            return
        if parsed.path == "/settings-log":            # snapshot of the Settings window (ui/src/settings/settingsLog.ts)
            from engine.core import settings_log
            self._json(200, settings_log.ingest(msg))
            return
        try:
            from engine.bridge import ui_trace
            ui_trace.http_in("POST", parsed.path, msg)   # live trace: what the UI asked the backend to do
        except Exception:
            pass

        if parsed.path.startswith("/files/"):
            from engine.features import file_index
            code, obj = file_index.http("POST", parsed.path, msg)
            self._json(code, obj)
            return
        # >>> phase-8 begin
        if parsed.path.startswith("/p8/"):
            from engine.bridge import api_p8
            code, obj = api_p8.http("POST", parsed.path, msg)
            self._json(code, obj)
            return
        # <<< phase-8 end
        if parsed.path in ("/record/delete", "/record/open", "/record/reveal"):
            from engine.features import screen_record
            name = str(msg.get("name") or "")
            if parsed.path == "/record/delete":
                self._json(200, screen_record.delete_recording(name))
            else:
                self._json(200, screen_record.open_recording(name, reveal=parsed.path == "/record/reveal"))
            return
        if parsed.path == "/record/folder":
            from engine.features import screen_record
            action = str(msg.get("action") or "")
            if action == "pick":
                picked = screen_record.pick_folder()
                if picked.get("path"):
                    self._json(200, {**screen_record.set_folder(picked["path"]), "picked": True})
                else:
                    self._json(200, {**screen_record.folder_info(), "picked": False, **({"ok": False, "error": picked["error"]} if not picked.get("ok") else {})})
            elif action == "set":
                self._json(200, screen_record.set_folder(str(msg.get("path") or "")))
            elif action == "reset":
                self._json(200, screen_record.set_folder(""))
            else:
                self._json(400, {"ok": False, "error": "unknown action"})
            return
        if parsed.path in ("/camera/reveal", "/camera/privacy-settings"):
            from engine.features import camera_store
            self._json(200, camera_store.reveal_last() if parsed.path == "/camera/reveal" else camera_store.open_privacy_settings())
            return
        if parsed.path == "/record/builtin-test":
            from engine.features import wgc_recorder
            self._json(200, wgc_recorder.selftest())
            return
        if parsed.path == "/record/obs-test":
            from engine.features import screen_record
            self._json(200, screen_record.obs_test())
            return
        if parsed.path in ("/record/start", "/record/stop", "/record/pause", "/record/resume", "/record/open-folder"):
            from engine.features import screen_record
            if parsed.path == "/record/start":
                self._json(200, screen_record.start())
            elif parsed.path == "/record/stop":
                self._json(200, screen_record.stop())
            elif parsed.path == "/record/pause":
                self._json(200, screen_record.pause())
            elif parsed.path == "/record/resume":
                self._json(200, screen_record.resume())
            else:
                self._json(200, {"ok": screen_record.open_folder()})
            return
        if parsed.path == "/translate":
            from engine.features import translate_mode
            self._json(200, translate_mode.translate_text(str(msg.get("text") or ""), str(msg.get("to") or "English")))
            return
        if parsed.path == "/mascot":
            if not isinstance(msg, dict) or msg.get("type") != "mascot":
                self._json(400, {"error": "expected a mascot event"})
                return
            _push_event(msg)
            self._json(200, {"ok": True})
            return
        if parsed.path in ("/plugins", "/plugins/reload", "/plugins/toggle"):
            from engine.plugins import loader
            action = str(msg.get("action") or ("reload" if parsed.path.endswith("reload") else ""))
            if parsed.path.endswith("toggle"):
                self._json(200, {"ok": loader.set_enabled(str(msg.get("name") or ""), bool(msg.get("enabled")))})
            elif action == "reload":
                self._json(200, loader.reload())
            elif action in ("enable", "disable"):
                self._json(200, {"ok": loader.set_enabled(str(msg.get("name") or ""), action == "enable")})
            else:
                self._json(400, {"error": "unknown plugin action"})
            return
        if parsed.path in ("/apps/view", "/apps/action", "/apps/connect", "/apps/disconnect", "/apps/settings"):
            from engine.plugins import apps
            app_id = str(msg.get("id") or "")
            if parsed.path == "/apps/view":
                self._json(200, apps.get_view(app_id, str(msg.get("route") or ""), msg.get("params")))
            elif parsed.path == "/apps/action":
                self._json(200, apps.run_action(app_id, str(msg.get("name") or ""), msg.get("params")))
            elif parsed.path == "/apps/settings":
                self._json(200, apps.save_settings(app_id, msg.get("values")))
            elif parsed.path == "/apps/connect":
                self._json(200, apps.connect(app_id))
            else:
                self._json(200, apps.disconnect(app_id))
            return
        if parsed.path == "/google/connect":
            from engine.features import google_workspace
            self._json(200, {"ok": True, "message": google_workspace.connect()})
            return
        if parsed.path == "/google/disconnect":
            from engine.features import google_workspace
            self._json(200, {"ok": True, "message": google_workspace.disconnect()})
            return

        if parsed.path == "/clipboard/capture":
            try:
                from engine.features import clipboard_session
                ok=clipboard_session.capture()
                self._json(200,{"ok":ok,"items":clipboard_session.snapshot()})
            except Exception as e:self._json(500,{"ok":False,"error":str(e)})
            return
        if parsed.path == "/clipboard/capture-files":
            try:
                from engine.features import clipboard_session
                ok, items = clipboard_session.capture_files(msg.get("paths") or [])
                self._json(200,{"ok":ok,"items":items})
            except Exception as e:self._json(500,{"ok":False,"error":str(e)})
            return
        if parsed.path == "/clipboard/restore":
            try:
                from engine.features import clipboard_session
                ok,note=clipboard_session.restore(int(msg.get("id") or 0),str(msg.get("action") or "copy"))
                self._json(200,{"ok":ok,"message":note,"items":clipboard_session.snapshot()})
            except Exception as e:self._json(500,{"ok":False,"error":str(e)})
            return
        if parsed.path == "/clipboard/clear":
            try:
                from engine.features import clipboard_session
                clipboard_session.clear()
                self._json(200,{"ok":True,"items":clipboard_session.snapshot()})
            except Exception as e:self._json(500,{"ok":False,"error":str(e)})
            return
        if parsed.path == "/clipboard/copy":
            try:
                from engine.features import clipboard_history
                ok = clipboard_history.set_text(str(msg.get("text") or ""))
                self._json(200, {"ok": ok})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/chat":
            text = str(msg.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "empty text"})
                return
            threading.Thread(
                target=_handle_chat_message,
                args=(text,),
                daemon=True,
                name="lyra-ui-chat",
            ).start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/notify":
            # Test hook (curl). A browser always sends Origin on cross-site posts,
            # so a web page can't make the island show fake notifications.
            if self.headers.get("Origin"):
                self._json(403, {"error": "forbidden"})
                return
            push_notification(
                str(msg.get("app") or "reminder"),
                str(msg.get("kind") or "message"),
                str(msg.get("sender") or ""),
                str(msg.get("text") or ""),
            )
            self._json(200, {"ok": True})
            return
            return

        if parsed.path == "/clipboard/keep":
            from engine.features import clipboard_session
            clipboard_session.set_keep(bool(msg.get("keep")))
            self._json(200, {"ok": True})
            return

        if parsed.path == "/dictation/apply":
            # The island's "Apply polished version" button: replace the dictated text in its window.
            try:
                from engine.voice import dictation
                ok, message = dictation.apply_pending()
                push_activity("dictation", message, phase="success" if ok else "fail", duration_ms=2600)
                if not ok and message.startswith("Put the cursor"):
                    push_dictation_polish("Move the cursor to the end, then Apply")
                self._json(200, {"ok": ok, "message": message})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/dictation/toggle":
            # The mic in the WhatsApp chat bar: dictation (it types what you say into the focused box, here the chat bar or
            # the search box) instead of talking to Lyra. One click starts it hands-free, the next click (or 8 s of silence) ends it.
            # action: "toggle" (default) | "stop" | "status".
            try:
                from engine.voice import dictation
                action = str(msg.get("action") or "toggle")
                if action == "status":
                    self._json(200, {"ok": True, "active": dictation.is_active()})
                elif dictation.is_active():
                    dictation.stop()
                    self._json(200, {"ok": True, "active": False})
                elif action == "stop":
                    self._json(200, {"ok": True, "active": False})
                else:
                    started = dictation.start()
                    if started:
                        dictation.set_handsfree()           # keeps listening until the next click; the words are pasted at the end
                    self._json(200, {"ok": started, "active": started})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/dictation/dismiss":
            # The X on the island's Apply toast: drop the waiting offer.
            try:
                from engine.voice import dictation
                dictation.dismiss_pending()
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/voice/toggle":
            try:
                from engine.voice import ptt
                self._json(200, {"ok": True, "listening": ptt.toggle_listen()})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if parsed.path.startswith("/wallpaper/"):
            from engine.features import wallpaper
            try:
                if parsed.path == "/wallpaper/set":
                    self._json(200, {"ok": wallpaper.set_wallpaper(str(msg.get("id") or ""))})
                elif parsed.path == "/wallpaper/folder":
                    before = wallpaper.get_folder()
                    chosen = wallpaper.choose_folder()
                    self._json(200, {"ok": True, "folder": wallpaper.get_folder(), "changed": bool(chosen) and chosen != before})
                else:
                    self._json(404, {"error": "unknown"})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if parsed.path.startswith("/controls/"):
            from engine.features import controls
            try:
                if parsed.path == "/controls/toggle":
                    on = msg.get("on")
                    self._json(200, controls.toggle(str(msg.get("id") or ""), None if on is None else bool(on)))
                elif parsed.path == "/controls/volume":
                    ok = controls.set_volume(int(msg.get("level", 0)))
                    self._json(200, {"ok": ok})
                elif parsed.path == "/controls/mute":
                    ok = controls.set_mute(bool(msg.get("on")))
                    self._json(200, {"ok": ok})
                elif parsed.path == "/controls/power":
                    self._json(200, controls.power(str(msg.get("action") or "")))
                else:
                    self._json(404, {"error": "unknown"})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if parsed.path == "/stats/speedtest":
            from engine.features import sysstats
            started = sysstats.start_speedtest()
            self._json(200, {"ok": True, "started": started, **sysstats.speedtest_state()})
            return

        if parsed.path in ("/todos/add", "/todos/toggle", "/todos/remove"):
            from engine.actions import notes
            text = str(msg.get("text") or "")
            if parsed.path == "/todos/add":
                ok = notes.todo_add(text)
            elif parsed.path == "/todos/toggle":
                ok = notes.todo_set_done(text, bool(msg.get("done")))
            else:
                ok = notes.todo_remove(text)
            self._json(200, {"ok": ok, "items": notes.todo_items()})
            return

        if parsed.path == "/calendar/sync":
            # The Calendar tab keeps its events in the browser; the island mirrors them here so Lyra can read them out.
            from engine.features import tab_sync
            self._json(200, {"ok": True, "count": tab_sync.set_calendar_items(msg.get("items"))})
            return

        if parsed.path in ("/schedule/remove", "/routines/toggle", "/routines/remove"):
            from engine.features import scheduler
            sid = str(msg.get("id") or "")
            if parsed.path == "/schedule/remove":
                ok = scheduler.remove_item(sid)
            elif parsed.path == "/routines/toggle":
                ok = scheduler.set_routine_enabled(sid, bool(msg.get("enabled")))
            else:
                ok = scheduler.remove_routine(sid)
            self._json(200, {"ok": ok, **scheduler.snapshot()})
            return

        if parsed.path == "/timers/control":
            from engine.features import timers
            action = str(msg.get("action") or "")
            try:
                tid = int(msg.get("id") or 0)
                secs = float(msg.get("seconds") or 0)
            except (TypeError, ValueError):
                self._json(400, {"error": "bad number"})
                return
            if action == "pause":
                ok = timers.pause(tid)
            elif action == "resume":
                ok = timers.resume(tid)
            elif action == "cancel":
                ok = timers.cancel(tid) is not None
            elif action == "start_timer" and secs > 0:
                ok = timers.start_timer(secs, str(msg.get("label") or "")[:40]) is not None
            elif action == "start_pomodoro":
                ok = timers.start_pomodoro() is not None
            elif action == "start_stopwatch":
                ok = timers.start_stopwatch() is not None
            else:
                self._json(400, {"error": "unknown action"})
                return
            self._json(200, {"ok": ok, **timers.snapshot()})
            return

        if parsed.path == "/settings/llm":
            from engine.brain.llm_store import save, load
            updates = {}
            for k in (
                "provider", "openrouter_api_key", "gemini_api_key", "groq_api_key",
                "openrouter_model", "gemini_model", "groq_model",
                "ollama_model", "ollama_vision_model", "ollama_base_url", "ollama_timeout", "ollama_keep_alive",
            ):
                if k in msg and msg[k] is not None:
                    val = str(msg[k]).strip()
                    # empty key string means "don't change" for secrets unless clear=true
                    if k.endswith("_api_key") and val == "" and not msg.get("clear"):
                        continue
                    updates[k] = val
            if msg.get("clear_provider_key"):
                prov = str(msg.get("provider") or load().get("provider") or "openrouter")
                key_map = {
                    "openrouter": "openrouter_api_key",
                    "gemini": "gemini_api_key",
                    "groq": "groq_api_key",
                }
                updates[key_map.get(prov, "openrouter_api_key")] = ""
            data = save(updates)
            self._json(200, {
                "ok": True,
                "provider": data.get("provider"),
                "has_openrouter": bool(data.get("openrouter_api_key")),
                "has_gemini": bool(data.get("gemini_api_key")),
                "has_groq": bool(data.get("groq_api_key")),
                "has_ollama": _ollama_configured(),
            })
            return

        if parsed.path in _PREF_PATHS:
            from engine.core import settings_store
            group = parsed.path.rsplit("/", 1)[1]
            try:
                data = settings_store.update_group(group, msg)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            if group == "privacy" and "screen_watch_mode" in data:
                try:
                    from engine.features import screen_watch
                    screen_watch.get_watch().interval = max(0.3, float(settings_store.get("screen_watch_interval", 1.0) or 1.0))
                    screen_watch.set_mode(str(data.get("screen_watch_mode") or "off"))
                    settings_store.set_many({"screen_watch_enabled": False})   # no background watching any more
                except Exception as e:
                    log.warning("screen watch mode apply failed: %s", e)
            if any(k in msg for k in ("shortcut_record_combo", "shortcut_pause_combo")):
                try:
                    from engine.features import record_hotkey
                    record_hotkey.apply()
                except Exception as e:
                    log.warning("record hotkeys apply failed: %s", e)
            if any(str(k).startswith("shortcut_") and str(k).endswith("_combo") for k in msg):
                try:
                    from engine.core import hotkeys
                    hotkeys.apply()
                except Exception as e:
                    log.warning("keyboard shortcuts apply failed: %s", e)
            self._json(200, {"ok": True, **data})
            return

        if parsed.path == "/shortcuts/save":
            from engine.shortcuts import settings_api
            try:
                self._json(200, {"ok": True, "shortcut": settings_api.save(msg)})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            return

        if parsed.path == "/shortcuts/delete":
            from engine.shortcuts import settings_api
            self._json(200, {"ok": settings_api.delete(str(msg.get("id") or ""))})
            return

        if parsed.path == "/memory/forget":
            from engine.brain import memory
            ok = memory.forget(str(msg.get("kind") or ""), str(msg.get("key") or ""))
            self._json(200, {"ok": ok})
            return

        if parsed.path == "/memory/clear":
            from engine.brain import memory
            kind = msg.get("kind")
            if kind not in (None, *memory.KINDS):
                self._json(400, {"error": "unknown memory kind"})
                return
            n = memory.clear(kind)
            log.info("memory cleared from Settings: %s (%d items)", kind or "everything", n)
            self._json(200, {"ok": True, "removed": n})
            return

        if parsed.path == "/settings/import":
            if length > 1_000_000:
                self._json(413, {"error": "file too large"})
                return
            if msg.get("app") != "lyra-settings":
                self._json(400, {"error": "this is not a Lyra settings file"})
                return
            applied, skipped = _import_bundle(msg)
            self._json(200, {"ok": True, "applied": applied, "skipped": skipped[:10]})
            return

        if parsed.path == "/volume/app":
            from engine.features import app_volume
            try:
                level = app_volume.set_app(str(msg.get("exe") or ""), float(msg.get("level")))
            except (ValueError, TypeError) as e:
                self._json(400, {"error": str(e)})
                return
            except Exception as e:
                self._json(500, {"error": f"could not set the volume: {e}"})
                return
            self._json(200, {"ok": True, "level": level})
            return

        if parsed.path == "/volume/mute":
            from engine.features import app_volume
            try:
                exe = str(msg.get("exe") or "")
                muted_arg = msg.get("muted")
                if muted_arg is None:
                    muted = app_volume.mute_app(exe, None)
                else:
                    muted = app_volume.mute_app(exe, bool(muted_arg))
            except (ValueError, TypeError) as e:
                self._json(400, {"error": str(e)})
                return
            except Exception as e:
                self._json(500, {"error": f"could not mute: {e}"})
                return
            self._json(200, {"ok": True, "muted": muted})
            return

        

        if parsed.path == "/screen/watch":
            from engine.features import screen_watch
            try:
                mode = str(msg.get("mode") or "").strip().lower()
                if mode:
                    selected = screen_watch.set_mode(mode)
                else:
                    selected = screen_watch.get_watch().mode()
                self._json(200, {"ok": True, "mode": selected, "status": screen_watch.get_watch().status_text()})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/screen/capture":
            try:
                from engine.features import screen_capture
                mode = str(msg.get("mode") or "full")
                monitor = int(msg.get("monitor") or 0)
                region = msg.get("region")
                reg = tuple(int(x) for x in region) if isinstance(region, (list, tuple)) and len(region) == 4 else None
                result = screen_capture.capture(mode, monitor=monitor, region=reg, keep_context=True)
                if result.get("ok"):
                    push_activity("screen_capture", "Screen captured", phase="success", subtitle=f"{result.get('width')}×{result.get('height')}", duration_ms=1600)
                self._json(200, result)
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if parsed.path == "/screen/changed":
            try:
                from engine.features import screen_watch
                frame = screen_watch.get_watch().ensure_context_frame()
                if frame is None:
                    self._json(200, {"ok": False, "changed": False, "error": "No screen frame available."})
                    return
                changed = screen_watch.get_watch().compare_with_previous(frame.image)
                self._json(200, {"ok": True, "changed": bool(changed)})
            except Exception as e:
                self._json(500, {"ok": False, "changed": False, "error": str(e)})
            return

        if parsed.path == "/screen/analyze":
            try:
                import base64
                from engine.features import screen_watch
                frame = screen_watch.get_watch().ensure_context_frame()
                if frame is None:
                    raise RuntimeError("Could not capture the screen.")
                prompt = str(msg.get("prompt") or "What am I looking at? Describe the important visible UI and answer briefly.")
                from engine.actions.apps import analyze_image_bytes
                push_activity("screen_vision", "Analyzing screen…", phase="start", subtitle="Using the latest local screen frame.", duration_ms=9000)
                answer = analyze_image_bytes(screen_capture.to_png_bytes(frame.image), prompt)
                _push_event({"type":"screen_result", "ok":True, "answer":answer, "ts":int(time.time()*1000)})
                self._json(200, {"ok": True, "answer": answer})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return
        if parsed.path == "/search":
            from engine.brain import search
            query = str(msg.get("query") or "").strip()
            if not query:
                self._json(400, {"ok": False, "error": "empty query"})
                return
            try:
                rows = search.web_search_results(query, max_results=max(1, min(int(msg.get("limit") or 5), 8)))
                self._json(200, {"ok": bool(rows), "query": query, "results": rows, "provider": rows[0].get("provider") if rows else ""})
            except Exception as e:
                self._json(500, {"ok": False, "query": query, "results": [], "error": str(e)})
            return

        if parsed.path == "/spotify/play":                # a song card on the island was tapped
            import threading as _th; import engine.features.spotify_island as _si
            ref = str(msg.get("uri") or "")
            if not _si.is_song_ref(ref):
                self._json(400, {"error": "not a spotify track"})
                return
            _th.Thread(target=_si.play, args=(ref,), daemon=True).start()   # art download must not block the tap
            self._json(200, {"ok": True})
            return
        if parsed.path == "/music/recognize":
            from engine.features import music_recognition
            try:
                force = bool((msg or {}).get("force_audio"))
                push_activity("music_recognition", "Listening…", phase="start", subtitle="Checking a short system-audio sample.", duration_ms=12000)
                # Hard time limit: whatever hangs (capture device, network), the tab must answer.
                import concurrent.futures
                _ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                try:
                    result = _ex.submit(music_recognition.recognize, force).result(timeout=40)
                except concurrent.futures.TimeoutError:
                    result = music_recognition.RecognitionResult(
                        False,
                        message="Recognition took too long. Check that audio is playing on the default output device and try again.",
                        source="timeout",
                    )
                finally:
                    _ex.shutdown(wait=False)
                if result.ok:
                    music_recognition.save_history(result)
                    _push_event({"type": "music_recognition", **result.to_dict(), "ts": int(time.time() * 1000)})
                else:
                    _push_event({"type": "music_recognition", **result.to_dict(), "ts": int(time.time() * 1000)})
                self._json(200, result.to_dict())
            except Exception as e:
                self._json(500, {"ok": False, "message": str(e)})
            return

        if parsed.path == "/music/history":
            try:
                from engine.features import music_recognition
                self._json(200, {"items": music_recognition.history(), "session": music_recognition.SESSION_ID})
            except Exception as e:
                self._json(500, {"items": [], "error": str(e)})
            return

        if parsed.path == "/logs/open":
            # Opens Explorer with ui.log selected. Fixed path, nothing from the request is used.
            import subprocess
            from engine.bridge.ui import LOG_PATH
            try:
                if LOG_PATH.exists():
                    subprocess.Popen(["explorer", "/select,", str(LOG_PATH)])
                else:
                    os.startfile(str(LOG_PATH.parent))  # type: ignore[attr-defined]
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(500, {"error": f"could not open the log folder: {e}"})
            return

        if parsed.path == "/settings/reset":
            from engine.core import settings_store
            n = settings_store.reset_prefs()
            log.info("Settings reset to defaults from the Settings window (%d values)", n)
            self._json(200, {"ok": True, "reset": n})
            return

        if parsed.path == "/backend/restart":
            # Start a fresh copy of this same command, then exit. The new copy needs a few
            # seconds to load, by which time this one has released the port and the microphone.
            import subprocess, sys

            def _restart():
                time.sleep(0.5)  # let the HTTP reply go out first
                try:
                    if getattr(sys, "frozen", False):
                        # Installed build: sys.executable IS lyra-backend.exe and argv[0] is that
                        # same exe, so relaunch it bare. No console, and not detached from the
                        # parent's job so the UI can still stop it on quit.
                        cmd = [sys.executable]
                        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                    else:
                        _spec = getattr(sys.modules.get("__main__"), "__spec__", None)
                        if _spec is not None and _spec.name:         # started as `python -m engine.core.main`: relaunch the same way
                            cmd = [sys.executable, "-m", _spec.name, *sys.argv[1:]]
                        else:
                            cmd = [sys.executable, *sys.argv]
                        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                    subprocess.Popen(cmd, cwd=os.getcwd(), creationflags=flags, close_fds=True)
                except Exception as e:
                    log.error("backend restart failed: %s", e)
                    return
                log.info("Backend restarting from the Settings window")
                logging.shutdown()
                os._exit(0)

            threading.Thread(target=_restart, daemon=True, name="lyra-restart").start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/settings/llm/test":
            # "Test connection": asks the provider whether the saved key is accepted (no tokens used).
            from engine.brain.llm_store import test_connection
            self._json(200, test_connection(str(msg.get("provider") or "") or None))
            return

        if parsed.path.startswith("/voice/offline"):
            from engine.voice import tts_offline
            res = tts_offline.dispatch("POST", parsed.path)
            if res is not None:
                self._json(res[0], res[1])
                return

        if parsed.path == "/voice/test":
            # "Test voice": speak a sample line with the current voice, speaker and speed.
            # Optional "text": a short preview line (the setup wizard speaks "Opening Spotify"). Plain text only, capped.
            _line = " ".join(str(msg.get("text") or "").split())[:80] if isinstance(msg, dict) else ""

            def _run():
                try:
                    from engine.voice import voice
                    voice.test_voice(_line) if _line else voice.test_voice()
                except Exception as e:
                    log.warning("test voice failed: %s", e)
            threading.Thread(target=_run, daemon=True, name="lyra-voice-test").start()
            self._json(200, {"ok": True})
            return

        if parsed.path == "/notify/test":
            # The Settings window's "send test notification": fixed text, known apps only,
            # and it bypasses the filters so you always see how a toast looks.
            app = str(msg.get("app") or "discord").lower()
            if app not in ("discord", "whatsapp", "telegram", "reminder"):
                app = "discord"
            kind = "reminder" if app == "reminder" else "message"
            push_notification(app, kind, "Lyra", "Test notification — this is how a toast looks.", force=True)
            self._json(200, {"ok": True})
            return

        self._json(404, {"error": "not found"})


# >>> phase-1 begin
try:
    from engine.bridge.api_p1 import register as _p1_register
    _p1_register(Handler)          # /p1/voice/* routes for the voice-engine settings panel
except Exception as _e:
    log.warning("voice engine API not loaded: %s", _e)
# <<< phase-1 end
# >>> phase-3 begin
from engine.bridge.api_p3 import register as _p3_register; _p3_register(Handler)
# <<< phase-3 end
# >>> phase-7 begin
try:
    from engine.bridge.api_p7 import register as _p7_register
    _p7_register(Handler)          # /p7/notetaker/* routes for the Notetaker panel
except Exception as _e:
    log.warning("notetaker API not loaded: %s", _e)
# <<< phase-7 end


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind a port that is already in use,
    # which would hide a second backend / a foreign program on 8765. Keep it off.
    allow_reuse_address = False
    daemon_threads = True


def _bind_server(wait_s: float = 8.0) -> "_Server":
    """Bind 127.0.0.1:PORT, retrying briefly (a restarting backend is still releasing it)."""
    deadline = time.time() + wait_s
    while True:
        try:
            return _Server((HOST, PORT), Handler)
        except OSError:
            if time.time() >= deadline:
                raise
            time.sleep(0.5)


def _port_holder_is_lyra() -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("service") == "lyra-ui-bridge"
    except Exception:
        return False


def _report_port_problem(err: Exception) -> None:
    from engine.core import paths
    from engine.core import model_setup
    if _port_holder_is_lyra():
        code = "already_running"
        msg = (f"Another Lyra backend is already running on {HOST}:{PORT}. "
               "Quit it first (tray icon > Quit) and start Lyra again.")
    else:
        code = "port_busy"
        msg = (f"Port {PORT} on {HOST} is in use by another program ({err}). "
               "Lyra's UI cannot connect until it is free - close that program and restart Lyra.")
    log.error("UI bridge: %s", msg)
    model_setup.write_backend_status("error", code, msg)
    if paths.FROZEN and code == "port_busy":
        try:  # windowed build: a log line alone would go unnoticed
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "Lyra", 0x10)
        except Exception:
            pass


def _serve(server: "_Server") -> None:
    log.info("Lyra UI bridge listening on http://%s:%d", HOST, PORT)
    server.serve_forever()


def start_bridge() -> bool:
    """Bind the port and start serving. Returns False (and logs why) if the port is unavailable."""
    global _started
    if _started:
        return True
    try:
        server = _bind_server()
    except OSError as e:
        _report_port_problem(e)
        return False
    _started = True
    try:
        from engine.core import model_setup
        model_setup.write_backend_status("ok")
    except Exception:
        pass
    ui.add_listener(_on_ui_event)
    t = threading.Thread(target=_serve, args=(server,), name="lyra-ui-bridge", daemon=True)
    t.start()
    # >>> phase-8 begin
    try:
        from engine.bridge import api_p8
        api_p8.start_background()          # no network until the user turns updates on
    except Exception as e:
        log.warning("update checker failed to start: %s", e)
    # <<< phase-8 end
    import engine.core.settings_store as _ss
    try:
        if _ss.feature_on("notifications"):
            from engine.features import notifications
            notifications.start(push_notification)
        else:
            log.info("Notification mirroring is switched off in Settings -> Features")
    except Exception as e:  # notifications are optional; never block the bridge
        log.warning("notifications watcher failed to start: %s", e)
    try:
        from engine.features import timers
        # Timer state feeds the compact countdown chip; completion still uses the
        # existing notification/toast pipeline. No Timer tab is registered.
        timers.set_publisher(lambda snap: _push_event({"type": "timers", **snap}))
        timers.set_notifier(push_notification)
    except Exception as e:  # timers are optional; never block the bridge
        log.warning("timers failed to connect to the notification bridge: %s", e)
    try:
        from engine.features import scheduler
        # Scheduled actions and routines run through the same path as a typed chat message.
        scheduler.start(_handle_chat_message, push_notification, push_schedule)
    except Exception as e:  # the scheduler is optional; never block the bridge
        log.warning("scheduler failed to start: %s", e)
    try:
        if _ss.feature_on("circle_ask"):
            from engine.features import circle_ask
            # Tap Ctrl, then hold it, and circle something on screen; the answer runs as a normal command.
            circle_ask.start(on_ask=_handle_chat_message)
    except Exception as e:  # optional; never block the bridge
        log.warning("Circle to ask failed to start: %s", e)
    try:
        if _ss.feature_on("tab_wheel"):
            from engine.features import tab_wheel
            # Hold Ctrl+CapsLock: the island's tab wheel.
            tab_wheel.start()
    except Exception as e:  # optional; never block the bridge
        log.warning("Tab wheel shortcut failed to start: %s", e)
    try:
        from engine.core import game_mode
        # While a game runs, the key listeners (Right Ctrl, Ctrl+Win, tap-and-hold Ctrl, Ctrl+CapsLock) are removed. The watcher
        # reads the "Game mode" switch every poll, so Settings works both ways without a restart.
        game_mode.start(_push_event)
    except Exception as e:  # optional; never block the bridge
        log.warning("Game mode failed to start: %s", e)
    try:
        if _ss.feature_on("clipboard"):
            from engine.features import clipboard_session
            clipboard_session.start(_push_event)
            log.info("Clipboard session watcher started")
    except Exception as e:
        log.warning("Clipboard session watcher failed: %s", e)
    if _ss.feature_on("downloads"):
        try:
            from engine.features import downloads_watch
            downloads_watch.start(_push_event)
            log.info("Downloads watcher started")
        except Exception as e:
            log.warning("downloads watcher failed to start: %s", e)
        try:
            from engine.features import browser_downloads_watch
            browser_downloads_watch.start(_push_event)
            log.info("Browser downloads watcher started")
        except Exception as e:
            log.warning("browser downloads watcher failed to start: %s", e)
    if _ss.feature_on("volume"):
        try:
            from engine.features import app_volume
            app_volume.start(_push_event)
        except Exception as e:
            log.warning("app volume watch failed to start: %s", e)
    try:
        # Song recognition has its own Off / Manual / Automatic switch (Settings -> Voice).
        from engine.features import music_recognition
        music_recognition.start_auto(_push_event)
    except Exception as e:
        log.warning("music recognition auto failed to start: %s", e)
    if _ss.feature_on("status"):
        try:
            from engine.features import status_watch
            status_watch.start(_push_event)
        except Exception as e:
            log.warning("status watch failed to start: %s", e)
    try:
        from engine.features import proactive
        proactive.start()
    except Exception as e:
        log.warning("proactive alerts failed to start: %s", e)
    try:
        from engine.voice import mochi_sounds
        mochi_sounds.start()                # the Mochi overheats with the CPU load the Stats tab shows
    except Exception as e:
        log.warning("mochi sounds failed to start: %s", e)
    for _feature, _module in (("file_search", "engine.features.file_index"), ("proactive_extra", "engine.features.proactive_extra")):
        if _ss.feature_on(_feature):
            try:
                import importlib
                importlib.import_module(_module).start()
            except Exception as e:
                log.warning("%s feature failed to start: %s", _feature, e)
    try:
        from engine.plugins import loader
        if loader.feature_enabled():
            loader.ensure_loaded()
            from engine.plugins import apps
            apps.start_polling(push_notification)   # notifications from connected app plugins
    except Exception as e:
        log.warning("plugins failed to load: %s", e)
    try:
        from engine.core import settings_store; from engine.features import screen_watch
        mode = str(settings_store.get("screen_watch_mode") or "off")
        if mode == "off" and settings_store.get("screen_watch_enabled", False):
            mode = "always"
        screen_watch.get_watch().interval = max(0.3, float(settings_store.get("screen_watch_interval", 1.0) or 1.0))
        screen_watch.set_mode(mode)
        log.info("screen watch mode: %s", mode)
    except Exception as e:
        log.warning("screen watch startup failed: %s", e)
    return True


# >>> phase-0 begin
try:  # key-storage status endpoints (/p0/...) + log redaction; Handler is defined above
    import sys as _p0_sys
    from engine.bridge.api_p0 import register as _p0_register
    _p0_register(_p0_sys.modules[__name__])
except Exception as _p0_err:  # never stop the bridge for this
    log.warning("phase 0 (secret storage endpoints) not loaded: %s", type(_p0_err).__name__)
# <<< phase-0 end
# >>> phase-2 begin
try:
    import sys as _sys_p2
    from engine.bridge.api_p2 import register as _register_p2
    _register_p2(_sys_p2.modules[__name__])      # /p2/... endpoints (dictation polish)
except Exception as _e_p2:
    log.warning("Phase 2 dictation API unavailable: %s", _e_p2)
# <<< phase-2 end
