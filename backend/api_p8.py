"""Lyra - verified updates: HTTP routes + state machine (phase 8). Logic lives in updater.py.

Routes (all under /p8/, same-origin guarded like the other personal endpoints):
  GET  /p8/status                      -> snapshot
  POST /p8/config   {enabled?, repo?, prereleases?}
  POST /p8/check    {current}          -> snapshot (manual check, works even when auto-check is off)
  POST /p8/download {current}          -> starts a background download, poll /p8/status for progress
  POST /p8/cancel
  POST /p8/skip     {version}
  POST /p8/install  {version, confirm: true, current}
        -> re-hashes the verified installer and returns {path, sha256, size, version}; the UI then hands it to the
           Tauri command `install_update`, which hashes it once more and starts it. This route never runs anything.

Rules this file enforces
  * Opt-in: the background thread does nothing unless the user enabled updates AND typed a repository.
  * The background thread only CHECKS. It never downloads and never installs; both need a click.
  * Install needs an explicit confirm:true for the exact version that finished verifying.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import updater as U

log = logging.getLogger("lyra.updater")

_LOCK = threading.RLock()
_CANCEL = threading.Event()
_STOP = threading.Event()
_bg_started = False
_S: dict = {"phase": "idle", "update": None, "error": None, "done": 0, "total": 0,
            "current": "", "throttled": False}


# --------------------------------------------------------------------------- helpers

def _set_current(value) -> str:
    v = U.clean_version(value)
    if v and U.parse_version(v) is not None:
        _S["current"] = v
    elif not _S["current"]:
        env = U.clean_version(os.environ.get("LYRA_APP_VERSION", ""))
        if env and U.parse_version(env) is not None:
            _S["current"] = env
    return _S["current"]


def _public_update(u: dict | None) -> dict | None:
    if not u:
        return None
    inst = u.get("installer") or {}
    return {"version": u["version"], "name": u["name"], "published_at": u["published_at"],
            "prerelease": u["prerelease"], "notes": u["notes"], "html_url": u["html_url"],
            "size": u["size"], "installer_name": inst.get("name", ""),
            "checksum_sources": u["checksum_sources"], "verifiable": u["verifiable"], "problem": u["problem"]}


def snapshot() -> dict:
    with _LOCK:
        st = U.load_state()
        ready = U.ready_info()
        return {"ok": True, "enabled": st["enabled"], "repo": st["repo"], "prereleases": st["prereleases"],
                "skipped": st["skipped"], "current": _S["current"], "phase": _S["phase"],
                "update": _public_update(_S["update"]), "error": _S["error"],
                "progress": {"done": _S["done"], "total": _S["total"]},
                "last_check": st["last_check"], "retry_at": st["next_allowed"],
                "ready_version": (ready or {}).get("version", ""), "throttled": _S["throttled"],
                "updates_dir": str(U.updates_dir())}


def _fail(err: U.UpdateError) -> None:
    _S["phase"] = "error"
    _S["error"] = err.as_dict()
    if err.code == "rate_limited" and err.retry_at:
        U.update_state(next_allowed=err.retry_at)


# --------------------------------------------------------------------------- actions

def run_check(current, manual: bool) -> dict:
    with _LOCK:
        cur = _set_current(current)
        _S["throttled"] = False
        if _S["phase"] in ("checking", "downloading"):
            return snapshot()
        st = U.load_state()
        now = time.time()
        if not st["repo"]:
            _fail(U.UpdateError("bad_repo", "Type the repository as owner/repo, for example acme/lyra."))
            return snapshot()
        if not cur:
            _fail(U.UpdateError("bad_version", "Lyra could not read its own version number."))
            return snapshot()
        if now < st["next_allowed"]:
            _fail(U.UpdateError("rate_limited", "GitHub is limiting requests right now. Try again later.", st["next_allowed"]))
            return snapshot()
        if manual and now - st["last_check"] < U.MANUAL_CHECK_COOLDOWN_S and _S["phase"] in ("available", "up_to_date", "ready"):
            _S["throttled"] = True
            return snapshot()
        _S["phase"], _S["error"] = "checking", None
    try:
        res = U.check(st["repo"], cur, st["prereleases"])
        err = None
    except U.UpdateError as e:
        res, err = None, e
    except Exception:                                                # noqa: BLE001  never leak a traceback to the UI
        log.exception("update check failed")
        res, err = None, U.UpdateError("network", "The update check failed.")
    with _LOCK:
        if err is not None:
            _fail(err)
            return snapshot()
        U.update_state(last_check=time.time(), next_allowed=0.0)
        upd = res["update"]
        if upd is None:
            _S["phase"], _S["update"] = "up_to_date", None
        elif upd["version"] == U.load_state()["skipped"] and not manual:
            _S["phase"], _S["update"] = "up_to_date", None
        else:
            _S["update"] = upd
            rec = U.ready_info()
            _S["phase"] = "ready" if rec and U.clean_version(rec.get("version")) == upd["version"] else "available"
        return snapshot()


def start_download(current) -> dict:
    with _LOCK:
        cur = _set_current(current)
        upd, st = _S["update"], U.load_state()
        if _S["phase"] == "downloading":
            return snapshot()
        if not upd or _S["phase"] not in ("available", "error"):
            return dict(snapshot(), ok=False, error={"code": "no_update", "message": "There is no update to download. Check first.", "retry_at": 0})
        if not upd["verifiable"]:
            _fail(U.UpdateError("no_checksum", upd["problem"] or "This update cannot be verified, so it will not be installed."))
            return snapshot()
        _CANCEL.clear()
        _S.update(phase="downloading", error=None, done=0, total=int(upd["size"]))
        repo = st["repo"]

    def work():
        def prog(done, total):
            with _LOCK:
                _S["done"], _S["total"] = done, total
        try:
            U.download(upd, repo, cur, progress=prog, cancel=_CANCEL)
            with _LOCK:
                _S["phase"], _S["error"] = "ready", None
        except U.UpdateError as e:
            with _LOCK:
                if e.code == "cancelled":
                    _S["phase"], _S["error"] = "available", None
                else:
                    _fail(e)
        except Exception:                                            # noqa: BLE001
            log.exception("update download failed")
            with _LOCK:
                _fail(U.UpdateError("network", "The download failed."))

    threading.Thread(target=work, name="lyra-update-download", daemon=True).start()
    return snapshot()


def cancel() -> dict:
    _CANCEL.set()
    return snapshot()


def skip(version) -> dict:
    v = U.clean_version(version)
    with _LOCK:
        if U.parse_version(v) is not None:
            U.update_state(skipped=v)
            if _S["update"] and _S["update"]["version"] == v:
                _S["phase"], _S["update"] = "up_to_date", None
        return snapshot()


def prepare_install(version, confirm, current) -> dict:
    """Hand-off for the Tauri side. Needs confirm:true and the exact version that finished verifying."""
    with _LOCK:
        cur = _set_current(current)
        if confirm is not True:
            return {"ok": False, "error": {"code": "not_confirmed", "message": "Installing needs your click.", "retry_at": 0}}
        if _S["phase"] == "downloading" or not U.ready_info():       # ready.json survives an app restart
            return {"ok": False, "error": {"code": "not_ready", "message": "No verified update is waiting.", "retry_at": 0}}
        if U.clean_version(version) != U.clean_version((U.ready_info() or {}).get("version")):
            return {"ok": False, "error": {"code": "version_changed", "message": "A different update is waiting now. Look again.", "retry_at": 0}}
        try:
            rec = U.verify_ready(cur)
        except U.UpdateError as e:
            _fail(e)
            return {"ok": False, "error": e.as_dict()}
        return {"ok": True, "path": rec["path"], "sha256": rec["sha256"], "size": rec["size"], "version": rec["version"]}


def set_config(msg: dict) -> tuple[int, dict]:
    changes: dict = {}
    if "enabled" in msg:
        changes["enabled"] = bool(msg["enabled"])
    if "prereleases" in msg:
        changes["prereleases"] = bool(msg["prereleases"])
    if "repo" in msg:
        raw = str(msg["repo"] or "").strip()
        repo = U.normalize_repo(raw) if raw else ""
        if raw and not repo:
            return 400, dict(snapshot(), ok=False, error={"code": "bad_repo", "retry_at": 0,
                             "message": "Type the repository as owner/repo, for example acme/lyra."})
        if repo != U.load_state()["repo"]:
            changes.update(repo=repo, skipped="", etags={}, cached={}, last_check=0.0, next_allowed=0.0)
            _CANCEL.set()
            U.clear_downloads()
            with _LOCK:
                _S.update(phase="idle", update=None, error=None, done=0, total=0)
    if changes:
        U.update_state(**changes)
    return 200, snapshot()


# --------------------------------------------------------------------------- background (check only)

def _bg_loop() -> None:
    if _STOP.wait(90):                                               # let the app finish starting first
        return
    while not _STOP.is_set():
        try:
            st = U.load_state()
            cur = _S["current"] or _set_current("")
            now = time.time()
            if (st["enabled"] and st["repo"] and cur and now >= st["next_allowed"]
                    and now - st["last_check"] >= U.AUTO_CHECK_INTERVAL_S and _S["phase"] not in ("checking", "downloading")):
                run_check(cur, manual=False)                         # check only: never downloads, never installs
        except Exception:                                            # noqa: BLE001
            log.exception("background update check failed")
        if _STOP.wait(900):
            return


def start_background() -> None:
    """Called once when the bridge starts. Idempotent. Does no network I/O by itself."""
    global _bg_started
    if _bg_started:
        return
    _bg_started = True
    cur = _set_current("")
    if cur:
        U.cleanup_stale(cur)
    threading.Thread(target=_bg_loop, name="lyra-update-check", daemon=True).start()


# --------------------------------------------------------------------------- dispatcher (see ui_bridge hook)

def http(method: str, path: str, msg: dict) -> tuple[int, dict]:
    route = path.split("?", 1)[0].rstrip("/")
    msg = msg if isinstance(msg, dict) else {}
    try:
        if method == "GET" and route == "/p8/status":
            return 200, snapshot()
        if method == "POST":
            if route == "/p8/config":
                return set_config(msg)
            if route == "/p8/check":
                return 200, run_check(msg.get("current"), manual=True)
            if route == "/p8/download":
                return 200, start_download(msg.get("current"))
            if route == "/p8/cancel":
                return 200, cancel()
            if route == "/p8/skip":
                return 200, skip(msg.get("version"))
            if route == "/p8/install":
                return 200, prepare_install(msg.get("version"), msg.get("confirm"), msg.get("current"))
        return 404, {"error": "unknown route"}
    except Exception:                                                # noqa: BLE001
        log.exception("update route failed: %s", route)
        return 500, {"error": "The update service hit a problem."}
