"""Lyra - Phase 0 HTTP endpoints: "is this key stored?" status dots.

  GET  /p0/secrets/status    -> which secrets are stored, and where (never the values)
  POST /p0/secrets/migrate   -> run the one-time move of plain-text keys to the keychain now
  POST /p0/secrets/confirm   -> migration looks good: delete the plain-text backup now

Hooked in with ONE block at the end of ui_bridge.py: register(bridge) wraps Handler.do_GET /
do_POST, so no routing code had to be added inside ui_bridge. The /p0/ paths are protected like
the other personal-data endpoints: a request that carries a foreign Origin header is refused.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

PREFIX = "/p0/"
log = logging.getLogger("lyra")

# name, label, where the old plain-text copy lived ("llm" = llm_keys.json, "settings" = settings.json)
ITEMS = (
    ("openrouter_api_key", "OpenRouter API key", "llm"),
    ("gemini_api_key", "Gemini API key", "llm"),
    ("groq_api_key", "Groq API key", "llm"),
    ("spotify_client_secret", "Spotify client secret", "llm"),
    ("ha_token", "Home Assistant token", "settings"),
    ("ntfy_token", "ntfy token", "settings"),
    ("audd_api_token", "AudD token", "settings"),
)


def _plain_copy(name: str, source: str) -> bool:
    """True if a non-empty plain-text copy of the secret still sits in its old JSON file."""
    try:
        if source == "llm":
            from engine.brain import llm_store
            raw = json.loads(llm_store.PATH.read_text(encoding="utf-8"))
            return bool(isinstance(raw, dict) and str(raw.get(name) or "").strip())
        from engine.core import settings_store
        raw = json.loads(settings_store.SETTINGS_PATH.read_text(encoding="utf-8"))
        return bool(isinstance(raw, dict) and str(raw.get(name) or "").strip())
    except Exception:
        return False


def _item(name: str, label: str, where: str) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "stored": where != "none",
        "where": where,                              # keychain | file | none
        "secure": where == "keychain",
        "dot": "green" if where != "none" else "red",
    }


def status() -> dict[str, Any]:
    """Never contains a secret value."""
    import engine.core.secrets_store as ss
    items = []
    known = set()
    for name, label, source in ITEMS:
        known.add(name)
        where = ss.where(name)
        if where == "none" and _plain_copy(name, source):
            where = "file"
        items.append(_item(name, label, where))
    for name in ss.names():                           # plugin / integration secrets sealed by the hooks
        if name not in known:
            items.append(_item(name, name.replace(".", " / "), ss.where(name)))
    keychain = ss.keyring_available()
    warnings = []
    if not keychain and any(i["stored"] for i in items):
        warnings.append("No OS keychain is available: keys are kept in plain text files in Lyra's data folder.")
    mig = ss.migration_state()
    if any(v.get("state") == "migrated" for v in mig.values()):
        warnings.append("Your old key file is backed up until the next launch confirms the move; then it is deleted.")
    if any(v.get("state") == "failed" for v in mig.values()):
        warnings.append("Moving keys to the keychain failed; they stay in the old file and keep working.")
    return {"ok": True, "backend": ss.backend(), "keychain": keychain, "items": items,
            "migration": mig, "warnings": warnings}


def handle(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Pure routing (no HTTP): returns (status code, JSON object)."""
    try:
        import engine.core.secrets_store as ss
        if method == "GET" and path == "/p0/secrets/status":
            return 200, status()
        if method == "POST" and path == "/p0/secrets/migrate":
            from engine.brain import llm_store
            ss.retry_failed()
            llm_store.load()
            try:
                from engine.core import settings_store
                settings_store._ensure_loaded()
            except Exception:
                pass
            return 200, status()
        if method == "POST" and path == "/p0/secrets/confirm":
            result = ss.confirm()
            return 200, {**status(), "confirmed": result}
    except Exception as e:                            # type only: never echo anything that could hold a value
        log.warning("p0 endpoint failed: %s", type(e).__name__)
        return 500, {"ok": False, "error": "secret storage error"}
    return 404, {"ok": False, "error": "unknown endpoint"}


def register(bridge: Any) -> None:
    """Wrap ui_bridge.Handler so /p0/... is served here; also installs log redaction. Idempotent."""
    handler = bridge.Handler
    if getattr(handler, "_p0_wrapped", False):
        return
    try:
        from engine.core import secrets_store
        secrets_store.install_log_filter()
        secrets_store.housekeeping()                  # confirm a migration from an earlier launch
    except Exception as e:
        log.warning("secret storage housekeeping skipped: %s", type(e).__name__)
    orig_get, orig_post = handler.do_GET, handler.do_POST

    def own_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return not origin or origin in getattr(bridge, "_OWN_ORIGINS", ())

    def do_GET(self):
        path = urlparse(self.path).path
        if not path.startswith(PREFIX):
            return orig_get(self)
        if not own_origin(self):
            return self._json(403, {"error": "forbidden"})
        code, obj = handle("GET", path)
        return self._json(code, obj)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith(PREFIX):
            return orig_post(self)
        if not own_origin(self):
            return self._json(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(min(length, 65536))   # body unused: drain it
        except Exception:
            pass
        code, obj = handle("POST", path, {})
        return self._json(code, obj)

    handler.do_GET, handler.do_POST = do_GET, do_POST
    handler._p0_wrapped = True
