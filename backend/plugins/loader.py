"""Lyra — plugin loader.

Loads .py files from two folders:
  * the bundled plugins/ folder (next to this file; read-only in the installed build)
  * the user's plugins folder: <data dir>\\plugins  (%APPDATA%\\Lyra\\plugins when installed)
    When running from source both are the same folder (backend/plugins), and it is only scanned once.

Each file defines:

    PLUGIN = {
        "name": "Dice roll",                 # unique; shown in Settings
        "description": "Rolls dice.",        # shown in Settings and given to the agent
        "patterns": [r"\\broll\\b.*\\bdice\\b"],   # regexes, matched case-insensitively with re.search
        "handler": handler,                  # handler(command: str) -> str | None
        "permission": "SAFE",                # "SAFE" runs at once, "CONFIRM" asks the user first
    }

Rules this module keeps:
  * A broken plugin (syntax error, bad PLUGIN dict, bad regex, handler that raises or hangs) is
    logged and skipped. It never raises into Lyra's command router.
  * A handler that returns None (or "") means "not mine": the command falls through to the rest of
    Lyra.
  * Every enabled plugin is also registered as an agent tool "plugin_<name>" (same permission).
  * The user switches single plugins off in Settings (setting "plugins_disabled"); the whole feature
    has its own switch (feature_plugins).
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import ui

log = ui.log

HANDLER_TIMEOUT_S = 15.0        # a handler that takes longer is abandoned (its thread is a daemon)
_SKIP_FILES = {"loader.py", "__init__.py", "apps.py"}
_PERMISSIONS = ("SAFE", "CONFIRM")

RELOAD_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:reload|refresh|rescan)\s+(?:all\s+|the\s+|my\s+)?plugins?\s*[.!]?\s*$",
    re.IGNORECASE,
)


@dataclass
class Plugin:
    name: str
    description: str
    patterns: list = field(default_factory=list)     # compiled regexes
    handler: Optional[Callable[[str], Any]] = None
    permission: str = "CONFIRM"
    source: str = "bundled"                           # "bundled" | "user"
    file: str = ""
    app: Optional[dict] = None                        # the notch-app part (see plugins/apps.py), or None


_lock = threading.RLock()
_plugins: dict[str, Plugin] = {}
_errors: list[dict] = []
_loaded = False
_tools_synced_for: Optional[bool] = None   # feature state the agent tool list was built for


# ── Folders ───────────────────────────────────────────────────────────────────

def plugin_dirs() -> list[tuple[str, Path]]:
    """[(source, folder)] to scan, bundled first. The user folder is created if missing."""
    import paths
    dirs: list[tuple[str, Path]] = [("bundled", paths.app_path("plugins"))]
    try:
        user = paths.data_dir() / "plugins"
        user.mkdir(parents=True, exist_ok=True)
        dirs.append(("user", user))
    except OSError as e:
        log.warning("plugins: cannot use the user plugins folder: %s", e)
    seen: set[str] = set()
    out: list[tuple[str, Path]] = []
    for source, folder in dirs:
        try:
            key = str(folder.resolve()).lower()
        except OSError:
            key = str(folder).lower()
        if key not in seen:
            seen.add(key)
            out.append((source, folder))
    return out


# ── Settings ──────────────────────────────────────────────────────────────────

def feature_enabled() -> bool:
    try:
        import settings_store
        return settings_store.feature_on("plugins")
    except Exception:  # noqa: BLE001
        return True


def disabled_names() -> set[str]:
    try:
        import settings_store
        raw = settings_store.get("plugins_disabled") or []
        return {str(x) for x in raw} if isinstance(raw, (list, tuple)) else set()
    except Exception:  # noqa: BLE001
        return set()


def is_enabled(name: str) -> bool:
    return name not in disabled_names()


def set_enabled(name: str, enabled: bool) -> bool:
    """Switch one plugin on/off (saved). False if there is no such plugin."""
    ensure_loaded()
    with _lock:
        if name not in _plugins:
            return False
    import settings_store
    off = disabled_names()
    (off.discard if enabled else off.add)(name)
    settings_store.set_many({"plugins_disabled": sorted(off)})
    _sync_tools()
    return True


# ── Loading ───────────────────────────────────────────────────────────────────

def _validate(raw: Any, path: Path, source: str) -> Plugin:
    """Turn a PLUGIN dict into a Plugin, or raise ValueError saying what is wrong."""
    if not isinstance(raw, dict):
        raise ValueError("PLUGIN must be a dict")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
        raise ValueError("PLUGIN['name'] must be a non-empty string (60 characters at most)")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ValueError("PLUGIN['description'] must be a string")
    app = None
    if "app" in raw:
        from plugins import apps
        app = apps.validate_app(raw["app"])
    pats = raw.get("patterns")
    if app is not None and pats is None:
        pats = []                      # an app-only plugin has no voice commands of its own
    if isinstance(pats, str):
        pats = [pats]
    if not isinstance(pats, (list, tuple)) or (not pats and app is None) or not all(isinstance(p, str) and p for p in pats):
        raise ValueError("PLUGIN['patterns'] must be a non-empty list of regex strings")
    compiled = []
    for p in pats:
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error as e:
            raise ValueError(f"bad regex {p!r}: {e}") from None
    handler = raw.get("handler")
    if not callable(handler) and not (app is not None and handler is None):
        raise ValueError("PLUGIN['handler'] must be a function handler(command) -> str | None")
    permission = str(raw.get("permission", "CONFIRM")).strip().upper()  # missing = ask first
    if permission not in _PERMISSIONS:
        raise ValueError("PLUGIN['permission'] must be \"SAFE\" or \"CONFIRM\"")
    return Plugin(name.strip(), description.strip(), compiled, handler, permission, source, str(path), app)


def _load_file(path: Path, source: str) -> Plugin:
    uid = hashlib.sha1(str(path).lower().encode("utf-8", "replace")).hexdigest()[:8]
    spec = importlib.util.spec_from_file_location(f"lyra_plugin_{path.stem}_{uid}", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "PLUGIN"):
        raise ValueError("no PLUGIN dict")
    return _validate(module.PLUGIN, path, source)


def load_all() -> dict:
    """(Re)read every plugin file. Never raises. Returns {"loaded": [names], "errors": [...]}."""
    global _loaded
    found: dict[str, Plugin] = {}
    errors: list[dict] = []
    for source, folder in plugin_dirs():
        try:
            files = sorted(p for p in folder.glob("*.py") if p.name not in _SKIP_FILES and not p.name.startswith("_"))
        except OSError as e:
            errors.append({"file": str(folder), "error": f"cannot read folder: {e}"})
            continue
        for path in files:
            try:
                plugin = _load_file(path, source)
            except (Exception, SystemExit) as e:  # noqa: BLE001 - one broken plugin must never stop Lyra
                msg = f"{type(e).__name__}: {e}"
                log.warning("plugin %s skipped: %s", path.name, msg)
                errors.append({"file": path.name, "error": msg})
                continue
            if plugin.name in found:
                log.warning("plugin name %r from %s replaces the one from %s", plugin.name, path.name, found[plugin.name].file)
            found[plugin.name] = plugin  # the user folder is scanned last, so it wins
    with _lock:
        _plugins.clear()
        _plugins.update(found)
        _errors[:] = errors
        _loaded = True
    _sync_tools()
    log.info("plugins: %d loaded, %d skipped", len(found), len(errors))
    return {"loaded": sorted(found), "errors": errors}


def ensure_loaded() -> None:
    if not _loaded:
        load_all()


def reload() -> dict:
    return load_all()


# ── Running ───────────────────────────────────────────────────────────────────

def _call(plugin: Plugin, command: str) -> Optional[str]:
    """Run plugin.handler(command) with a time limit. Returns the reply, or None (nothing to say,
    an error, or a timeout; errors are logged and the plugin is skipped for this command)."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = plugin.handler(command)  # type: ignore[misc]
        except (Exception, SystemExit) as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=run, name=f"lyra-plugin-{plugin.name}", daemon=True)
    t.start()
    t.join(HANDLER_TIMEOUT_S)
    if t.is_alive():
        log.warning("plugin %r did not answer within %.0f s: skipped", plugin.name, HANDLER_TIMEOUT_S)
        return None
    if "error" in box:
        e = box["error"]
        log.warning("plugin %r raised %s: %s (skipped)", plugin.name, type(e).__name__, e)
        return None
    value = box.get("value")
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text if text.strip() else None


def _active() -> list[Plugin]:
    off = disabled_names()
    with _lock:
        return [p for p in _plugins.values() if p.name not in off]


def try_plugin(command: str, engine_context=None) -> Optional[str]:
    """The one entry point for actions/__init__.py. Returns a reply, or None to fall through."""
    try:
        text = (command or "").strip()
        if not text:
            return None
        if _loaded and feature_enabled() != _tools_synced_for:
            _sync_tools()  # the Features switch was flipped: add/remove the agent tools
        if RELOAD_PATTERN.match(text):
            if not feature_enabled():
                return "Plugins are switched off. You can turn them on in Settings, under Features."
            return _reload_reply(load_all())
        if not feature_enabled():
            return None
        ensure_loaded()
        for plugin in _active():
            if not any(p.search(text) for p in plugin.patterns):
                continue
            if plugin.permission == "CONFIRM":
                from permissions import PermissionManager
                allowed, reason = PermissionManager().check_permission(
                    f"plugin_{_slug(plugin.name)}",
                    f"run the {plugin.name} plugin",
                    engine_context,
                )
                if not allowed:
                    return reason or "I won't run that plugin without your confirmation."
            reply = _call(plugin, text)
            if reply is not None:
                ui.trace(f"action: plugin '{plugin.name}'")
                return reply
        return None
    except Exception as e:  # noqa: BLE001 - never let plugins break the router
        log.warning("plugins: %s", e)
        return None


def _reload_reply(result: dict) -> str:
    n = len(result["loaded"])
    bad = len(result["errors"])
    text = f"Reloaded {n} plugin{'s' if n != 1 else ''}."
    if bad:
        text += f" {bad} file{'s were' if bad != 1 else ' was'} skipped because of an error; the details are in Settings, under Features."
    return text


# ── Agent tools ───────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "plugin"


def _sync_tools() -> None:
    """Make the agent's tool list match the enabled plugins (removes stale ones first)."""
    global _tools_synced_for
    try:
        from tools.base import ToolCategory, ToolDefinition, ToolParameter, ToolResult
        from tools.registry import get_registry, register_tool
        _tools_synced_for = feature_enabled()
        reg = get_registry()
        for tool_id in [k for k in reg.tools if k.startswith("plugin_")]:
            reg.tools.pop(tool_id, None)
        if not feature_enabled():
            return

        def make(plugin: Plugin):
            def execute(command: str):
                if not feature_enabled() or not is_enabled(plugin.name):
                    return ToolResult(False, f"The {plugin.name} plugin is switched off.")
                reply = _call(plugin, str(command))
                if reply is None:
                    return ToolResult(False, f"The {plugin.name} plugin had nothing to say for that.")
                return ToolResult(True, reply)
            return execute

        for plugin in [p for p in _active() if p.handler is not None]:
            register_tool(ToolDefinition(
                id=f"plugin_{_slug(plugin.name)}",
                name=plugin.name,
                description=plugin.description or f"Plugin: {plugin.name}",
                category=ToolCategory.SYSTEM,
                parameters=[ToolParameter("command", "string", "The request, worded the way the user would say it")],
                permission_level=plugin.permission,
                execute=make(plugin),
            ))
    except Exception as e:  # noqa: BLE001
        log.warning("plugins: could not update the agent tools: %s", e)


# ── For the Settings window ──────────────────────────────────────────────────

def describe() -> dict:
    ensure_loaded()
    off = disabled_names()
    with _lock:
        items = [
            {
                "name": p.name, "description": p.description, "permission": p.permission,
                "source": p.source, "file": Path(p.file).name, "enabled": p.name not in off, "app": p.app is not None,
            }
            for p in sorted(_plugins.values(), key=lambda x: x.name.lower())
        ]
        errors = list(_errors)
    user = next((str(f) for s, f in plugin_dirs() if s == "user"), "")
    folders = [str(f) for _, f in plugin_dirs()]
    return {"enabled": feature_enabled(), "plugins": items, "errors": errors, "folder": user or folders[0], "folders": folders}
