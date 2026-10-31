"""Lyra — Phase 2 (dictation polish) HTTP API.

`register(bridge)` is called once from ui_bridge.py. It adds the /p2/... endpoints WITHOUT editing the
bridge's own routing: the Handler's do_GET / do_POST are wrapped so that only /p2/ paths come here.
All logic lives in `handle()` (plain function: method, path, query, body -> (status, dict)), which is
what the tests call.

  GET  /p2/settings                     all Phase 2 preferences
  POST /p2/settings          {key: v}   validate + save (400 with the offending key on a bad value)
  GET  /p2/styles                       {default, styles: {app: style}, builtin: {...}, ai: bool}
  POST /p2/styles            {app, style}  style "" removes your choice for that app
  GET  /p2/history?q=&limit=&offset=    {items, count, enabled, retention_days}
  POST /p2/history           {op: delete, id} | {op: clear, confirm: true}
  POST /p2/erase-everything  {confirm: true}  history + dictionary + snippets + styles + stats + prefs
  GET  /p2/status                       what is available (engines, stats, history size)
"""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import dictation_clean


def _int(v, default, lo, hi) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _erase_stats() -> bool:
    """Ask Phase 3's statistics to forget everything, if that module exists and offers a way."""
    try:
        import dictation_stats
    except ImportError:
        return False
    for name in ("erase", "clear", "reset", "erase_all"):
        fn = getattr(dictation_stats, name, None)
        if callable(fn):
            try:
                fn()
                return True
            except Exception:
                return False
    return False


def erase_everything() -> dict:
    """Everything dictation has learned or kept: history, dictionary, snippets, styles, statistics."""
    done = {}
    import history_store, dictionary_store
    snippets = dictionary_store.snippets_module()
    done["history"] = history_store.clear()
    dictionary_store.clear()
    done["snippets"] = snippets.clear_all()
    done["stats"] = _erase_stats()
    dictation_clean.reset_prefs()
    done["preferences"] = True
    try:
        import dictation
        dictation._last_text = ""
    except Exception:
        pass
    return done


def _styles_payload() -> dict:
    return {"default": dictation_clean.pref("p2_cleanup_style"), "styles": dictation_clean.user_styles(),
            "builtin": dict(dictation_clean.BUILTIN_STYLES), "ai": dictation_clean.ai_enabled(),
            "choices": list(dictation_clean.STYLES)}


def handle(method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    """Route one /p2/ request. Never raises: errors become (status, {"ok": False, "error": ...})."""
    try:
        return _handle(method, path, query, body or {})
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return 500, {"ok": False, "error": str(e)}


def _handle(method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    import history_store
    route = path.rstrip("/")
    get = method == "GET"

    if route == "/p2/settings":
        if get:
            return 200, {"ok": True, "settings": dictation_clean.all_prefs()}
        return 200, {"ok": True, "settings": dictation_clean.set_prefs(body)}

    if route == "/p2/styles":
        if not get:
            app = dictation_clean.app_key(body.get("app", ""))
            style = body.get("style", "")
            if not app:
                raise ValueError("app: required")
            mine = dictation_clean.user_styles()
            if style == "":
                mine.pop(app, None)
            elif style in dictation_clean.STYLES:
                mine[app] = style
            else:
                raise ValueError("style: not an allowed value")
            dictation_clean.set_prefs({"p2_app_styles": json.dumps(mine)})
        return 200, {"ok": True, **_styles_payload()}

    if route == "/p2/history":
        if not get:
            op = body.get("op")
            if op == "delete":
                history_store.delete(_int(body.get("id"), -1, -1, 1 << 62))
            elif op == "clear":
                if body.get("confirm") is not True:
                    return 400, {"ok": False, "error": "confirm required"}
                history_store.clear()
            else:
                raise ValueError("unknown op")
        q = (query.get("q") or [""])[0]
        items = history_store.search(q, _int((query.get("limit") or [50])[0], 50, 1, 500),
                                     _int((query.get("offset") or [0])[0], 0, 0, 1 << 30))
        return 200, {"ok": True, "items": items, "count": history_store.count(),
                     "enabled": history_store.enabled(), "retention_days": history_store.retention_days()}

    if route == "/p2/erase-everything" and not get:
        if body.get("confirm") is not True:
            return 400, {"ok": False, "error": "confirm required"}
        return 200, {"ok": True, "erased": erase_everything()}

    if route == "/p2/status" and get:
        engines = None
        try:
            import stt_engines
            engines = stt_engines.status()
        except Exception:
            pass
        try:
            import dictation_stats  # noqa: F401
            stats = True
        except ImportError:
            stats = False
        return 200, {"ok": True, "engines": engines, "stats_available": stats, "history_count": history_store.count(),
                     "ai_cleanup": dictation_clean.ai_enabled()}

    return 404, {"ok": False, "error": "not found"}


def register(bridge) -> None:
    """Hook the /p2/ endpoints into ui_bridge (called once, at the end of ui_bridge.py)."""
    handler = bridge.Handler
    if getattr(handler, "_p2_wrapped", False):
        return
    bridge._GUARDED = tuple(bridge._GUARDED) + ("/p2/",)     # only Lyra's own windows may use these
    orig_get, orig_post = handler.do_GET, handler.do_POST

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/p2/"):
            return orig_get(self)
        if not self._own_origin(parsed.path):
            return self._json(403, {"error": "forbidden"})
        code, obj = handle("GET", parsed.path, parse_qs(parsed.query), {})
        return self._json(code, obj)

    def do_POST(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/p2/"):
            return orig_post(self)
        if not self._own_origin(parsed.path):
            return self._json(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            msg = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8") or "{}")
            if not isinstance(msg, dict):
                raise ValueError
        except Exception:
            return self._json(400, {"error": "bad json"})
        code, obj = handle("POST", parsed.path, parse_qs(parsed.query), msg)
        return self._json(code, obj)

    handler.do_GET, handler.do_POST = do_GET, do_POST
    handler._p2_wrapped = True
