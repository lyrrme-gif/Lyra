"""Lyra - Phase 1 bridge routes: the Settings > Voice engine panel talks to these.

    GET  /p1/voice/status          engine, mode, pill text, download progress, language tables
    POST /p1/voice/engine          {"engine": "auto" | "parakeet" | "whisper"}
    POST /p1/voice/download        start or resume the ~670 MB download
    POST /p1/voice/cancel          stop it (the partial file is kept, the next start resumes)
    POST /p1/voice/delete          remove the downloaded model
    POST /p1/voice/verify          re-check the files against their SHA-256 (a few seconds)

Hooked into ui_bridge.Handler with ONE call: register(Handler). It wraps do_GET / do_POST, answers its
own /p1/voice/ paths and passes everything else to the original methods untouched.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

try:
    import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.api_p1")

PREFIX = "/p1/voice/"


def dispatch(method: str, path: str, body: dict | None = None) -> tuple[int, dict] | None:
    """Handle one request. None = not ours. Pure function of (method, path, body) + stt_engines state."""
    if not path.startswith(PREFIX):
        return None
    route = path[len(PREFIX):].strip("/")
    body = body if isinstance(body, dict) else {}
    try:
        import stt_engines
    except Exception as e:                                   # the package itself failed to import
        return 503, {"ok": False, "error": f"voice engines unavailable: {e}"}
    try:
        if method == "GET" and route == "status":
            return 200, {"ok": True, **stt_engines.status()}
        if method != "POST":
            return 405, {"ok": False, "error": "method not allowed"}
        if route == "engine":
            try:
                stt_engines.set_engine(str(body.get("engine", "")))
            except ValueError as e:
                return 400, {"ok": False, "error": str(e)}
            return 200, {"ok": True, **stt_engines.status()}
        if route == "download":
            started = stt_engines.start_download()
            return 200, {"ok": True, "started": started, **stt_engines.status()}
        if route == "cancel":
            stt_engines.cancel_download()
            return 200, {"ok": True, **stt_engines.status()}
        if route == "delete":
            ok, msg = stt_engines.delete_model()
            return (200 if ok else 409), {"ok": ok, "error": msg, **stt_engines.status()}
        if route == "verify":
            ok, msg = stt_engines.get_downloader().verify_installed()
            return 200, {"ok": ok, "error": msg, **stt_engines.status()}
        return 404, {"ok": False, "error": "not found"}
    except Exception as e:                                   # a panel must never get a stack trace
        log.warning("p1 voice route %s failed: %s", route, e)
        return 500, {"ok": False, "error": f"{e.__class__.__name__}: {e}"}


def _origin_ok(handler) -> bool:
    """Same rule as the bridge's guarded routes: a request from a web page on another origin is refused
    (these routes can delete a model or change settings)."""
    try:
        import ui_bridge
        origin = handler.headers.get("Origin")
        return not origin or origin in ui_bridge._OWN_ORIGINS
    except Exception:
        return True


def register(handler_cls) -> None:
    """Wrap handler_cls.do_GET / do_POST (idempotent)."""
    if getattr(handler_cls, "_p1_registered", False):
        return
    orig_get, orig_post = handler_cls.do_GET, handler_cls.do_POST

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith(PREFIX):
            if not _origin_ok(self):
                self._json(403, {"error": "forbidden"})
                return
            code, obj = dispatch("GET", path)
            self._json(code, obj)
            return
        return orig_get(self)

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith(PREFIX):
            if not _origin_ok(self):
                self._json(403, {"error": "forbidden"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8") or "{}")
            except Exception:
                body = {}
            code, obj = dispatch("POST", path, body)
            self._json(code, obj)
            return
        return orig_post(self)

    handler_cls.do_GET = do_GET
    handler_cls.do_POST = do_POST
    handler_cls._p1_registered = True
