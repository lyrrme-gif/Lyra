"""Lyra — app plugins (Phase 4).

A plugin file may add an "app" part next to (or instead of) its voice-command part. An app plugin is
something that lives on the notch: Spotify's playlists, WhatsApp chats, ... It never ships its own
screens. It hands Lyra plain data (a "view") and Lyra draws that in the notch's own style, so every app
looks like the notch and not like the app it connects to.

    PLUGIN = {
        "name": "Example",
        "description": "...",
        "app": {
            "id": "example",                    # short slug, unique
            "title": "Example",                 # shown in the notch and in Settings
            "icon": "chat",                     # an icon name the island knows
            "status":     status,               # status() -> {"connected": bool, "account": str, "message": str}
            "connect":    connect,              # connect() -> {"url": "..."} to open a login page, or {"message": "..."}
            "disconnect": disconnect,           # disconnect() -> None
            "view":       view,                 # view(route: str, params: dict) -> view dict (see below)
            "action":     action,               # action(name: str, params: dict) -> {"ok": bool, "message": str, "view"?: view}
            "poll":       poll,                 # poll() -> [{"id": "...", "sender": "...", "text": "..."}]  (new items only)
            "poll_seconds": 30,
            "settings": [{"key": "client_id", "label": "Client ID", "hint": "...", "secret": False}],
        },
    }

Only "id", "title" and "view" are required. Views (what Lyra draws):
    {"type": "list",    "title": str, "chip": str, "items": [{"id","title","subtitle","time","image","badge","action":{"name","params"}}],
                        "actions": [{"label","name","params"}], "empty": str}
    {"type": "thread",  "title": str, "messages": [{"id","from","text","time","mine"}],
                        "compose": {"action": "send", "params": {...}, "placeholder": str}}
    {"type": "message", "title": str, "text": str}
    {"type": "qr",      "title": str, "text": str, "image": "data:image/svg+xml;base64,..."}   (sign-in by scanning)
A thread message may also carry "media" ({"kind": voice|audio|image, "state": loading|ready|failed|off|viewonce|expired,
"url": "/apps/media?id=<app>&f=<file>", "seconds", "w", "h", "transcript", "tstate", "note"}); the app serves the file
through its optional "media" function (media(name) -> path or None) and the notch fetches it from GET /apps/media.
A thread's "compose" may carry "draft" (text for the reply box) and "suggest" ({"action", "params"}: a button that asks
the app for reply ideas; its action answers {"ok", "suggestions": [str, ...]}). Suggestions only fill the reply box.
Every view may carry "route": the string to pass back to view() to refresh it (the notch refreshes open threads).
"settings" are shown in Settings → Apps, where the user also signs in (a QR scan, a login page). Each is
{"key", "label", "hint", "secret", "type": "text" | "toggle", "default": bool}: text boxes (an API client id...) and
on/off switches. They are kept in store(app_id).get("settings"); read one with apps.setting(app_id, key, default).
Optional: "description" (one line for Settings) and "accent" (a #rrggbb colour the notch uses for this app).
A view is cleaned before it leaves this module (known keys only, short strings, at most 100 items,
https images only), so a plugin cannot make the notch draw anything but these shapes.

Rules, same as the voice-command plugins: a plugin that raises or takes longer than CALL_TIMEOUT_S is
skipped and logged; it never breaks Lyra. Plugins are normal Python with full access to the PC:
only add ones you trust. What a plugin needs to remember (tokens...) goes in store(app_id).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import ui

log = ui.log

CALL_TIMEOUT_S = 20.0
VIEW_TYPES = ("list", "thread", "message", "qr")
MAX_ITEMS = 100
_CALLABLES = ("status", "connect", "disconnect", "view", "action", "poll", "media", "upload")
_SEEN_LIMIT = 300


# ── Validation (called by plugins/loader.py when it reads a PLUGIN dict) ──────

def validate_app(raw: Any) -> dict:
    """Return a clean copy of a PLUGIN["app"] dict, or raise ValueError saying what is wrong."""
    if not isinstance(raw, dict):
        raise ValueError("PLUGIN['app'] must be a dict")
    app_id = raw.get("id")
    if not isinstance(app_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", app_id):
        raise ValueError("PLUGIN['app']['id'] must be a short lower-case slug (letters, digits, - or _)")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 40:
        raise ValueError("PLUGIN['app']['title'] must be a non-empty string (40 characters at most)")
    if not callable(raw.get("view")):
        raise ValueError("PLUGIN['app']['view'] must be a function view(route, params) -> dict")
    out: dict[str, Any] = {"id": app_id, "title": title.strip(), "icon": str(raw.get("icon") or "apps")[:24]}
    for key in _CALLABLES:
        fn = raw.get(key)
        if fn is None:
            continue
        if not callable(fn):
            raise ValueError(f"PLUGIN['app']['{key}'] must be a function")
        out[key] = fn
    try:
        seconds = float(raw.get("poll_seconds", 30))
    except (TypeError, ValueError):
        raise ValueError("PLUGIN['app']['poll_seconds'] must be a number") from None
    out["poll_seconds"] = max(10.0, min(seconds, 3600.0))
    fields = []
    for f in raw.get("settings") or []:
        if not isinstance(f, dict) or not isinstance(f.get("key"), str) or not re.fullmatch(r"[a-z0-9_]{1,32}", f["key"]):
            raise ValueError("PLUGIN['app']['settings'] entries need a key (lower-case letters, digits, _)")
        kind = "toggle" if f.get("type") == "toggle" else "text"
        fields.append({"key": f["key"], "label": _s(f.get("label") or f["key"], 60), "type": kind,
                       "hint": _s(f.get("hint"), 160), "secret": bool(f.get("secret")) and kind == "text",
                       "default": bool(f.get("default"))})
    out["settings"] = fields[:8]
    out["description"] = _s(raw.get("description"), 140)
    accent = str(raw.get("accent") or "")
    out["accent"] = accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else ""
    return out


# ── Per-app storage ───────────────────────────────────────────────────────────

class AppStore:
    """A small JSON file per app (<data dir>/plugin_data/<id>.json) for tokens and settings.
    Same trust level as llm_keys.json: plain text in the user's own data folder."""

    def __init__(self, app_id: str, folder: Optional[Path] = None):
        self.app_id = app_id
        self._folder = folder
        self._lock = threading.Lock()

    def _path(self) -> Path:
        if self._folder is None:
            import paths
            self._folder = paths.data_dir() / "plugin_data"
        self._folder.mkdir(parents=True, exist_ok=True)
        return self._folder / f"{self.app_id}.json"

    def _read(self) -> dict:
        try:
            data = json.loads(self._path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._read().get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._read()
            data[key] = value
            self._write(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(key, None) is not None:
                self._write(data)

    def clear(self) -> None:
        with self._lock:
            self._write({})

    def _write(self, data: dict) -> None:
        path = self._path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def store(app_id: str) -> AppStore:
    return AppStore(app_id)


# ── Cleaning what a plugin returns ────────────────────────────────────────────

def _s(v: Any, limit: int) -> str:
    return "" if v is None else str(v)[:limit]


def _clean_action(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]:
        return None
    params = raw.get("params")
    return {"name": raw["name"][:40], "params": params if isinstance(params, dict) else {}}


def _clean_image(v: Any) -> str:
    """https image links (500 characters at most) or a small inline picture (a QR code)."""
    raw = "" if v is None else str(v)
    if raw.startswith("https://"):
        return raw[:500]
    if raw.startswith("data:image/") and len(raw) <= 60000:
        return raw
    return ""


_MEDIA_STATES = ("loading", "ready", "failed", "off", "viewonce", "expired")
_TSTATES = ("", "pending", "done", "none", "failed", "off")
_MEDIA_URL = re.compile(r"/apps/media\?id=[a-z0-9][a-z0-9_-]{0,31}&f=[0-9a-f]{24}\.[a-z0-9]{2,4}")


def _clean_media(raw: Any) -> Optional[dict]:
    """A photo / voice message of a thread message: known keys, a url only of the /apps/media form."""
    if not isinstance(raw, dict) or raw.get("kind") not in ("voice", "audio", "image"):
        return None
    def n(key: str, top: int) -> int:
        try:
            return max(0, min(int(raw.get(key) or 0), top))
        except (TypeError, ValueError):
            return 0
    url = str(raw.get("url") or "")
    state = raw.get("state") if raw.get("state") in _MEDIA_STATES else "failed"
    ok_url = bool(_MEDIA_URL.fullmatch(url))
    return {"kind": raw["kind"], "state": state if (state != "ready" or ok_url) else "failed", "url": url if ok_url else "",
            "mime": _s(raw.get("mime"), 40), "seconds": n("seconds", 86400), "w": n("w", 20000), "h": n("h", 20000),
            "transcript": _s(raw.get("transcript"), 1500),
            "tstate": raw.get("tstate") if raw.get("tstate") in _TSTATES else "", "note": _s(raw.get("note"), 80)}


def sanitize_view(raw: Any) -> dict:
    """Known keys only, short strings, bounded lists. Anything else becomes a plain message."""
    if not isinstance(raw, dict) or raw.get("type") not in VIEW_TYPES:
        return {"type": "message", "title": "", "text": "This app did not return anything to show."}
    kind = raw["type"]
    view: dict[str, Any] = {"type": kind, "title": _s(raw.get("title"), 80), "route": _s(raw.get("route"), 80)}
    if kind == "message":
        view["text"] = _s(raw.get("text"), 600)
        return view
    if kind == "qr":
        view["text"] = _s(raw.get("text"), 300)
        view["image"] = _clean_image(raw.get("image"))
        return view
    if kind == "list":
        items = []
        for it in (raw.get("items") or [])[:MAX_ITEMS]:
            if not isinstance(it, dict):
                continue
            item = {
                "id": _s(it.get("id"), 80), "title": _s(it.get("title"), 80),
                "subtitle": _s(it.get("subtitle"), 120), "image": _clean_image(it.get("image")),
                "badge": _s(it.get("badge"), 12), "time": _s(it.get("time"), 12),
            }
            act = _clean_action(it.get("action"))
            if act:
                item["action"] = act
            items.append(item)
        view["items"] = items
        view["empty"] = _s(raw.get("empty"), 120)
        view["chip"] = _s(raw.get("chip"), 24)
        buttons = []
        for b in (raw.get("actions") or [])[:6]:
            act = _clean_action(b)
            if act and isinstance(b.get("label"), str):
                buttons.append({"label": b["label"][:24], **act})
        view["actions"] = buttons
        return view
    msgs = []
    for m in (raw.get("messages") or [])[-MAX_ITEMS:]:
        if isinstance(m, dict):
            msgs.append({
                "id": _s(m.get("id"), 80), "from": _s(m.get("from"), 60), "text": _s(m.get("text"), 1000),
                "time": _s(m.get("time"), 24), "mine": bool(m.get("mine")),
            })
            media = _clean_media(m.get("media"))
            if media:
                msgs[-1]["media"] = media
    view["messages"] = msgs
    view["notice"] = _s(raw.get("notice"), 140)       # e.g. "Not sent: ..." for the open chat
    compose = raw.get("compose")
    if isinstance(compose, dict) and _clean_action({"name": compose.get("action")}):
        view["compose"] = {
            "action": compose["action"][:40],
            "params": compose.get("params") if isinstance(compose.get("params"), dict) else {},
            "placeholder": _s(compose.get("placeholder"), 60),
            "draft": _s(compose.get("draft"), 1000),
            "attach": bool(compose.get("attach")),       # the reply box may carry one file (the + button)
        }
        suggest = _clean_action(compose.get("suggest"))
        if suggest:
            view["compose"]["suggest"] = suggest
    return view


# ── Calling a plugin safely ───────────────────────────────────────────────────

def _find(app_id: str):
    """(Plugin, app dict) for an enabled app plugin, or None."""
    from plugins import loader
    if not loader.feature_enabled():
        return None
    loader.ensure_loaded()
    for plugin in loader._active():  # noqa: SLF001 - same package
        if plugin.app and plugin.app["id"] == app_id:
            return plugin, plugin.app
    return None


def _call(app: dict, fn: str, *args: Any) -> tuple[bool, Any]:
    """Run app[fn](*args) with a time limit: (True, value) or (False, error text). Never raises."""
    func: Optional[Callable] = app.get(fn)
    if func is None:
        return False, f"{app['title']} has no {fn}"
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = func(*args)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=run, name=f"lyra-app-{app['id']}-{fn}", daemon=True)
    t.start()
    t.join(CALL_TIMEOUT_S)
    if t.is_alive():
        log.warning("app %r: %s did not answer within %.0f s", app["id"], fn, CALL_TIMEOUT_S)
        return False, f"{app['title']} took too long to answer."
    if "error" in box:
        e = box["error"]
        log.warning("app %r: %s raised %s: %s", app["id"], fn, type(e).__name__, e)
        return False, f"{app['title']} ran into a problem."
    return True, box.get("value")


# ── What the island / Settings call ───────────────────────────────────────────

def app_status(app: dict) -> dict:
    if "status" not in app:
        return {"connected": True, "account": "", "message": ""}
    ok, value = _call(app, "status")
    if not ok or not isinstance(value, dict):
        return {"connected": False, "account": "", "message": str(value) if not ok else ""}
    return {"connected": bool(value.get("connected")), "account": _s(value.get("account"), 80),
            "message": _s(value.get("message"), 160)}


def list_apps() -> dict:
    from plugins import loader
    loader.ensure_loaded()
    apps = []
    if loader.feature_enabled():
        for plugin in loader._active():  # noqa: SLF001
            if plugin.app:
                a = plugin.app
                apps.append({"id": a["id"], "title": a["title"], "icon": a["icon"], "accent": a["accent"],
                             "description": a["description"], "canConnect": "connect" in a, "settings": settings_fields(a), **app_status(a)})
    return {"enabled": loader.feature_enabled(), "apps": apps}


def settings_fields(app: dict) -> list[dict]:
    """The app's settings fields with what is saved: plain values come back, secret ones only say they are set."""
    saved = store(app["id"]).get("settings", {})
    saved = saved if isinstance(saved, dict) else {}
    out = []
    for f in app.get("settings", []):
        if f["type"] == "toggle":
            on = bool(saved[f["key"]]) if f["key"] in saved else f["default"]
            out.append({**f, "set": True, "value": on})
        else:
            value = str(saved.get(f["key"]) or "")
            out.append({**f, "set": bool(value), "value": "" if f["secret"] else value})
    return out


def setting(app_id: str, key: str, default: Any = None) -> Any:
    """What the user chose for one of an app's settings (a plugin calls this; no network, no login)."""
    saved = store(app_id).get("settings", {})
    return saved.get(key, default) if isinstance(saved, dict) else default


def save_settings(app_id: str, values: Any) -> dict:
    found = _find(app_id)
    if not found or not isinstance(values, dict):
        return {"ok": False, "message": "That app is not installed or is switched off."}
    kinds = {f["key"]: f["type"] for f in found[1].get("settings", [])}
    st = store(app_id)
    saved = st.get("settings", {})
    saved = dict(saved) if isinstance(saved, dict) else {}
    for key, value in values.items():
        if key in kinds:
            saved[key] = bool(value) if kinds[key] == "toggle" else str(value or "").strip()[:300]
    st.set("settings", saved)
    return {"ok": True, "settings": settings_fields(found[1])}


def get_view(app_id: str, route: str = "", params: Optional[dict] = None) -> dict:
    found = _find(app_id)
    if not found:
        return {"ok": False, "error": "That app is not installed or is switched off."}
    app = found[1]
    ok, value = _call(app, "view", str(route or "")[:80], params if isinstance(params, dict) else {})
    if not ok:
        return {"ok": False, "error": str(value)}
    return {"ok": True, "view": sanitize_view(value)}


def run_action(app_id: str, name: str, params: Optional[dict] = None) -> dict:
    found = _find(app_id)
    if not found:
        return {"ok": False, "message": "That app is not installed or is switched off."}
    app = found[1]
    ok, value = _call(app, "action", str(name or "")[:40], params if isinstance(params, dict) else {})
    if not ok:
        return {"ok": False, "message": str(value)}
    if not isinstance(value, dict):
        return {"ok": True, "message": ""}
    out: dict[str, Any] = {"ok": bool(value.get("ok", True)), "message": _s(value.get("message"), 200)}
    if "view" in value:
        out["view"] = sanitize_view(value["view"])
    if isinstance(value.get("suggestions"), list):
        out["suggestions"] = [_s(x, 300) for x in value["suggestions"][:3] if isinstance(x, str) and x.strip()]
    return out


UPLOAD_MAX_BYTES = 16 * 1024 * 1024


def save_upload(app_id: str, name: str, data: bytes) -> dict:
    """A file the user picked with the + button, handed to the app's optional upload(name, data) function, which
    answers {"ok", "token", "name", "kind", "size"} (or {"ok": False, "error"}). The file is only *kept*: it goes out
    when the user presses Send, together with the token."""
    found = _find(app_id)
    if not found or "upload" not in found[1]:
        return {"ok": False, "error": "This app cannot send files."}
    if not data or len(data) > UPLOAD_MAX_BYTES:
        return {"ok": False, "error": "That file is empty or larger than 16 MB."}
    ok, value = _call(found[1], "upload", _s(name, 120) or "file", data)
    if not ok or not isinstance(value, dict):
        return {"ok": False, "error": "Could not keep that file."}
    if not value.get("ok"):
        return {"ok": False, "error": _s(value.get("error"), 140) or "Could not keep that file."}
    return {"ok": True, "token": _s(value.get("token"), 40), "name": _s(value.get("name"), 80),
            "kind": "image" if value.get("kind") == "image" else "file", "size": max(0, int(value.get("size") or 0))}


MEDIA_MAX_BYTES = 25 * 1024 * 1024
_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}


def get_media(app_id: str, name: str) -> Optional[tuple[str, str]]:
    """(file path, content type) of a photo / voice message an app saved, or None. The app decides which file names
    exist (its media(name) function); this adds a second check: a plain file, a known picture / sound type, not huge."""
    found = _find(app_id)
    if not found or "media" not in found[1] or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
        return None
    ok, value = _call(found[1], "media", name)
    if not ok or not value:
        return None
    path = Path(str(value))
    ctype = _MEDIA_TYPES.get(path.suffix.lower())
    try:
        if not ctype or not path.is_file() or path.stat().st_size > MEDIA_MAX_BYTES:
            return None
    except OSError:
        return None
    return str(path), ctype


def connect(app_id: str) -> dict:
    found = _find(app_id)
    if not found or "connect" not in found[1]:
        return {"ok": False, "message": "This app has no sign-in."}
    ok, value = _call(found[1], "connect")
    if not ok:
        return {"ok": False, "message": str(value)}
    value = value if isinstance(value, dict) else {}
    url = _s(value.get("url"), 800)
    return {"ok": True, "url": url if url.startswith("https://") else "", "message": _s(value.get("message"), 200)}


def disconnect(app_id: str) -> dict:
    found = _find(app_id)
    if not found:
        return {"ok": False}
    if "disconnect" in found[1]:
        _call(found[1], "disconnect")
    _seen.pop(app_id, None)
    return {"ok": True}


# ── Notifications from apps ───────────────────────────────────────────────────

_seen: dict[str, list[str]] = {}
_poller: Optional[threading.Thread] = None


def poll_once(app: dict, push: Callable[..., Any]) -> int:
    """Ask one connected app for new items and push each one that has not been shown yet."""
    if "poll" not in app or not app_status(app)["connected"]:
        return 0
    ok, value = _call(app, "poll")
    if not ok or not isinstance(value, list):
        return 0
    seen = _seen.setdefault(app["id"], [])
    first_look = not seen and app["id"] not in _primed
    _primed.add(app["id"])
    shown = 0
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        key = _s(item.get("id"), 120) or f"{item.get('sender')}|{item.get('text')}"
        if key in seen:
            continue
        seen.append(key)
        if first_look:        # the first answer after start-up is the backlog: remember it, do not replay it
            continue
        chat = _s(item.get("chat"), 120)
        if chat:          # lets a click on the toast open that chat; older push functions without it still work
            try:
                push(app["id"], "message", _s(item.get("sender"), 80), _s(item.get("text"), 200), chat=chat)
            except TypeError:
                push(app["id"], "message", _s(item.get("sender"), 80), _s(item.get("text"), 200))
        else:
            push(app["id"], "message", _s(item.get("sender"), 80), _s(item.get("text"), 200))
        shown += 1
    del seen[:-_SEEN_LIMIT]
    return shown


_primed: set[str] = set()


def start_polling(push: Callable[..., Any]) -> None:
    """One background thread; each connected app is asked on its own poll_seconds."""
    global _poller
    if _poller is not None and _poller.is_alive():
        return

    def loop() -> None:
        due: dict[str, float] = {}
        while True:
            time.sleep(2.0)
            try:
                from plugins import loader
                if not loader.feature_enabled():
                    continue
                now = time.time()
                for plugin in loader._active():  # noqa: SLF001
                    app = plugin.app
                    if not app or "poll" not in app or due.get(app["id"], 0) > now:
                        continue
                    due[app["id"]] = now + app["poll_seconds"]
                    poll_once(app, push)
            except Exception as e:  # noqa: BLE001 - polling must never take Lyra down
                log.warning("apps: poll loop: %s", e)

    _poller = threading.Thread(target=loop, name="lyra-app-poll", daemon=True)
    _poller.start()
