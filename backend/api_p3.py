"""Phase 3 - HTTP side of the dictation statistics (read by ui/src/settings/panels/p3_statistics.ts).

  GET  /dictation/stats?range=week|month|all   -> summary (see dictation_stats.Store.summary)
  POST /dictation/stats/enabled {"enabled": bool}
  POST /dictation/stats/clear   {}              -> {"ok": true, "removed": N}

All of it sits under /dictation, which ui_bridge already guards against web pages (own windows only).

Hook (ui_bridge.py, one line after the Handler class):  `from api_p3 import register; register(Handler)`
`register` wraps Handler.do_GET / do_POST so the big if-chains stay untouched; every other path falls
straight through to the original method.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import dictation_stats

PREFIX = "/dictation/stats"


def http(method: str, path: str, query: dict, msg: dict | None) -> tuple[int, dict] | None:
    """(status, body) for a statistics request, or None when the path is not ours."""
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return None
    try:
        if method == "GET" and path == PREFIX:
            rng = (query.get("range") or ["week"])[0]
            return 200, dictation_stats.summary(rng)
        if method == "POST" and path == PREFIX + "/enabled":
            dictation_stats.set_enabled(bool((msg or {}).get("enabled", True)))
            return 200, {"ok": True, "enabled": dictation_stats.enabled()}
        if method == "POST" and path == PREFIX + "/clear":
            return 200, {"ok": True, "removed": dictation_stats.clear()}
    except Exception as e:
        return 500, {"ok": False, "error": f"statistics unavailable: {e}"}
    return 404, {"error": "not found"}


def register(handler_cls) -> None:
    """Add the statistics endpoints to ui_bridge.Handler without editing its routing chains."""
    if getattr(handler_cls, "_p3_registered", False):
        return
    orig_get, orig_post = handler_cls.do_GET, handler_cls.do_POST

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == PREFIX:
            if not self._own_origin(parsed.path):
                self._json(403, {"error": "forbidden"})
                return
            code, obj = http("GET", parsed.path, parse_qs(parsed.query), None)
            self._json(code, obj)
            return
        orig_get(self)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith(PREFIX + "/"):
            if not self._own_origin(parsed.path):
                self._json(403, {"error": "forbidden"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                msg = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8") or "{}")
            except Exception:
                self._json(400, {"error": "bad json"})
                return
            code, obj = http("POST", parsed.path, parse_qs(parsed.query), msg)
            self._json(code, obj)
            return
        orig_post(self)

    handler_cls.do_GET, handler_cls.do_POST = do_GET, do_POST
    handler_cls._p3_registered = True
