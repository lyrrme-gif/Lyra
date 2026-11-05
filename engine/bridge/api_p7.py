"""Lyra - Phase 7 bridge routes for the Notetaker panel (/p7/notetaker/*).

    GET  status | sessions | session?id= | export?id=
    POST start {title, acknowledged:true, language} | stop | delete {id} | delete-all | rename {id,title}
         rename-speaker {id, old, new} | summarize {id} | retry {id} | settings {p7_*...}
         diarization/download | diarization/cancel | diarization/delete

Recording refuses to start unless the request says acknowledged:true (the panel shows the "tell the
participants" reminder first). Summaries refuse unless the user switched them on. Foreign web origins are
refused: transcripts are private. Hooked into ui_bridge.Handler by ONE call: register(Handler)."""
from __future__ import annotations

import json
import logging
import threading
from urllib.parse import parse_qs, urlparse

try:
    from engine.bridge import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.api_p7")

PREFIX = "/p7/notetaker/"


def dispatch(method: str, path: str, query: dict | None = None, body: dict | None = None):
    """-> (code, obj) or None when the path is not ours."""
    if not path.startswith(PREFIX):
        return None
    route = path[len(PREFIX):].strip("/")
    q = {k: v[0] for k, v in (query or {}).items()}
    body = body if isinstance(body, dict) else {}
    try:
        from engine.notetaker import diarize, pipeline, service, settings, store
        from engine.notetaker.recorder import NotAcknowledged
    except Exception as e:
        return 503, {"ok": False, "error": f"notetaker unavailable: {e}"}
    try:
        if method == "GET":
            if route == "status":
                return 200, {"ok": True, **service.status()}
            if route == "sessions":
                return 200, {"ok": True, "sessions": store.list_sessions()}
            if route in ("session", "export"):
                sid = q.get("id", "")
                doc = store.get(sid) if store.valid_id(sid) else None
                if doc is None:
                    return 404, {"ok": False, "error": "not found"}
                return 200, {"ok": True, "session": doc} if route == "session" else {"ok": True, "markdown": store.export_markdown(doc)}
            return 404, {"ok": False, "error": "not found"}
        if method != "POST":
            return 405, {"ok": False, "error": "method not allowed"}
        sid = str(body.get("id", ""))
        if route == "start":
            try:
                return 200, {"ok": True, **service.start(str(body.get("title", "")), body.get("acknowledged") is True,
                                                          str(body.get("language", "")))}
            except NotAcknowledged as e:
                return 409, {"ok": False, "error": "acknowledge_required", "reminder": str(e)}
            except RuntimeError as e:
                return 409, {"ok": False, "error": str(e)}
        if route == "stop":
            return 200, {"ok": True, **service.stop()}
        if route in ("delete", "rename", "rename-speaker", "summarize", "retry") and not store.valid_id(sid):
            return 400, {"ok": False, "error": "bad id"}
        if route == "delete":
            gone = store.delete(sid)
            if pipeline.read_marker(sid) and service.recorder.status().get("id") != sid:
                pipeline.wipe_audio(sid)                      # also forgets a not-yet-processed session's audio
                gone = True
            return (200 if gone else 404), {"ok": gone}
        if route == "delete-all":
            return 200, {"ok": True, "deleted": store.delete_all()}
        if route == "rename":
            t = str(body.get("title", "")).strip()[:80]
            d = store.update(sid, title=t) if t else None
            return (200, {"ok": True}) if d else (404, {"ok": False, "error": "not found"})
        if route == "rename-speaker":
            d = store.rename_speaker(sid, str(body.get("old", "")), str(body.get("new", "")))
            return (200, {"ok": True, "session": d}) if d else (404, {"ok": False, "error": "not found"})
        if route == "summarize":
            if not settings.get("p7_summarize"):
                return 403, {"ok": False, "error": "Summaries are off. Turn them on in Notetaker settings first."}
            if not store.exists(sid):
                return 404, {"ok": False, "error": "not found"}
            threading.Thread(target=service._pipe.summarize, args=(sid,), daemon=True, name="lyra-notes-sum").start()
            return 202, {"ok": True}
        if route == "retry":
            ok = service.retry(sid)
            return (200 if ok else 404), {"ok": ok}
        if route == "settings":
            try:
                return 200, {"ok": True, "settings": settings.update(body)}
            except ValueError as e:
                return 400, {"ok": False, "error": str(e)}
        if route.startswith("diarization/"):
            m = diarize.manager()
            act = route.split("/", 1)[1]
            if act == "download":
                return 200, {"ok": True, "started": m.start(), **m.status()}
            if act == "cancel":
                m.cancel()
                return 200, {"ok": True, **m.status()}
            if act == "delete":
                ok, msg = m.delete()
                return (200 if ok else 409), {"ok": ok, "error": msg, **m.status()}
        return 404, {"ok": False, "error": "not found"}
    except Exception as e:
        log.warning("p7 route %s failed: %s", route, e)
        return 500, {"ok": False, "error": f"{e.__class__.__name__}: {e}"}


def _origin_ok(handler) -> bool:
    try:
        from engine.bridge import ui_bridge
        origin = handler.headers.get("Origin")
        return not origin or origin in ui_bridge._OWN_ORIGINS
    except Exception:
        return True


def register(handler_cls) -> None:
    """Wrap do_GET / do_POST (idempotent) and run the once-per-launch housekeeping in the background."""
    if getattr(handler_cls, "_p7_registered", False):
        return
    orig_get, orig_post = handler_cls.do_GET, handler_cls.do_POST

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith(PREFIX):
            if not _origin_ok(self):
                self._json(403, {"error": "forbidden"})
                return
            code, obj = dispatch("GET", u.path, parse_qs(u.query))
            self._json(code, obj)
            return
        return orig_get(self)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path.startswith(PREFIX):
            if not _origin_ok(self):
                self._json(403, {"error": "forbidden"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads((self.rfile.read(n) if n else b"{}").decode("utf-8") or "{}")
            except Exception:
                body = {}
            code, obj = dispatch("POST", u.path, None, body)
            self._json(code, obj)
            return
        return orig_post(self)

    handler_cls.do_GET, handler_cls.do_POST, handler_cls._p7_registered = do_GET, do_POST, True

    def _boot():
        try:
            from engine.notetaker import service
            service.startup()
        except Exception as e:
            log.warning("notetaker startup skipped: %s", e)
    threading.Thread(target=_boot, name="lyra-notes-boot", daemon=True).start()
