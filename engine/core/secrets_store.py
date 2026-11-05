"""Lyra - secret storage (Phase 0, contract C2).

API keys and tokens live in the operating system's keychain (Windows Credential Manager,
through the `keyring` package) instead of plain-text JSON files.

    secrets_store.get(name, default="") -> str
    secrets_store.set(name, value)      -> bool    (an empty value deletes the secret)
    secrets_store.delete(name)          -> bool
    secrets_store.has(name)             -> bool

`name` is a flat, file-name-like string: "groq_api_key", "ha_token", "plugin.spotify.tokens".
Nobody else should read llm_keys.json directly; go through llm_store (unchanged public API) or here.

Fallback, in this order
  1. keychain available            -> secrets live there, nothing secret is written to disk.
  2. keychain missing / locked     -> the secret goes to <data dir>/secrets_fallback.json (plain
                                      text, the same trust level the old llm_keys.json had).
  llm_store and settings_store do NOT use that file: without a keychain they keep their old
  files exactly as before. Only new callers of get/set/delete (other phases) can end up there.
  Force a mode with env LYRA_SECRETS_BACKEND=file|keyring|auto (default auto).

Migration (one time, never destructive until confirmed)
  migrate_json_file() moves named fields out of a JSON file into the keychain:
    backup of the original file  ->  write every value  ->  read every value back and compare
    ->  only then blank the fields in the file.  The backup (<file>.bak-keychain) stays until the
  next launch has read the secrets back from the keychain (housekeeping()) or the user confirms
  (confirm()); then it is overwritten and deleted.  rollback() puts the original file back.
  Any failure leaves the original file untouched and Lyra keeps running from it.

Never logged: this module logs secret NAMES only.  Every log record of the "lyra" logger also goes
through redact() (install_log_filter()), which masks stored values and common key formats.

Windows Credential Manager caps one credential at 2560 bytes (UTF-16), so long values are split
into 1000-character chunks transparently.

This module must stay light: it imports only paths and the standard library.
CLI (never prints values):  python secrets_store.py status | migrate | confirm | rollback
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from engine.core import paths

_log = logging.getLogger("lyra")

SERVICE = os.environ.get("LYRA_KEYRING_SERVICE", "").strip() or "Lyra"
SENTINEL = "@keychain"                     # what a sealed value looks like inside a JSON file
BACKUP_SUFFIX = ".bak-keychain"
_CHUNK = 1000
_CHUNK_HEAD = "@@lyra-chunks:"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
_PROBE_NAME = "__lyra_probe__"

# settings.json keys that hold secrets (see settings_store hook).
SETTINGS_SECRET_KEYS = frozenset({"ha_token", "ntfy_token", "audd_api_token"})

_set = set                                   # the builtin: this module defines its own set()
_lock = threading.RLock()
_BOOT = f"{os.getpid()}-{time.time():.0f}"   # identifies this process; "next launch" = a different one
_cache: dict[str, str] = {}                  # found secrets only (so repeated reads stay instant)
_known: set[str] = set()                     # values seen this run, for redact()
_probed = False
_adapter: Any = None
_filter_installed = False


# ---------------------------------------------------------------------------------------------
# keychain adapter
# ---------------------------------------------------------------------------------------------
class _KeyringAdapter:
    """The three calls we need, over the `keyring` module (or a fake in tests)."""

    def __init__(self, mod: Any) -> None:
        self._k = mod

    def get(self, service: str, user: str) -> str | None:
        return self._k.get_password(service, user)

    def set(self, service: str, user: str, value: str) -> None:
        self._k.set_password(service, user, value)

    def delete(self, service: str, user: str) -> None:
        try:
            self._k.delete_password(service, user)
        except Exception as e:  # PasswordDeleteError = "was not there": fine
            if type(e).__name__ != "PasswordDeleteError":
                raise


def _probe() -> Any:
    """Find a usable keychain once. None = not usable (then the file fallback is used)."""
    mode = os.environ.get("LYRA_SECRETS_BACKEND", "auto").strip().lower()
    if mode == "file":
        return None
    try:
        import keyring  # type: ignore
        kr = keyring.get_keyring()
        mod = type(kr).__module__ or ""
        if ".fail" in mod or ".null" in mod or mod.endswith("fail") or mod.endswith("null"):
            return None
        ad = _KeyringAdapter(keyring)
        ad.set(SERVICE, _PROBE_NAME, "ok")          # a real round trip: some backends fail only here
        if ad.get(SERVICE, _PROBE_NAME) != "ok":
            return None
        ad.delete(SERVICE, _PROBE_NAME)
        return ad
    except Exception as e:
        _log.info("secrets: no usable OS keychain (%s) - using the plain-text fallback", type(e).__name__)
        return None


def _get_adapter() -> Any:
    global _probed, _adapter
    with _lock:
        if not _probed:
            _adapter, _probed = _probe(), True
        return _adapter


def keyring_available() -> bool:
    """True when secrets are really kept in the OS keychain."""
    return _get_adapter() is not None


def backend() -> str:
    return "keyring" if keyring_available() else "file"


def _use_adapter_for_tests(adapter: Any) -> None:
    """Tests only: inject a fake keychain (or None for 'no keychain') and forget all state."""
    global _probed, _adapter
    with _lock:
        _adapter, _probed = adapter, True
        _cache.clear()
        _known.clear()


def _reset_for_tests() -> None:
    global _probed, _adapter
    with _lock:
        _adapter, _probed = None, False
        _cache.clear()
        _known.clear()


def _check(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError("secret names are 1-100 chars of letters, digits, _ . -")
    return name


def _warn(action: str, name: str, err: Exception) -> None:
    # name + exception TYPE only: an exception message could contain the value
    _log.warning("secrets: keychain %s failed for %r (%s)", action, name, type(err).__name__)


# ---------------------------------------------------------------------------------------------
# keychain primitives (with chunking)
# ---------------------------------------------------------------------------------------------
def _kr_get(name: str) -> str | None:
    ad = _get_adapter()
    if ad is None:
        return None
    try:
        raw = ad.get(SERVICE, name)
        if raw is None:
            return None
        if raw.startswith(_CHUNK_HEAD):
            n = int(raw[len(_CHUNK_HEAD):])
            parts = [ad.get(SERVICE, f"{name}#{i}") for i in range(1, n + 1)]
            return None if any(p is None for p in parts) else "".join(parts)
        return raw
    except Exception as e:
        _warn("read", name, e)
        return None


def _kr_chunks(name: str) -> int:
    """How many chunk entries the current keychain item of `name` has (0 = not chunked)."""
    ad = _get_adapter()
    try:
        raw = ad.get(SERVICE, name) if ad else None
        if raw and raw.startswith(_CHUNK_HEAD):
            return int(raw[len(_CHUNK_HEAD):])
    except Exception:
        pass
    return 0


def _kr_set(name: str, value: str) -> bool:
    ad = _get_adapter()
    if ad is None:
        return False
    try:
        old = _kr_chunks(name)
        if len(value) <= _CHUNK:
            ad.set(SERVICE, name, value)
            n = 0
        else:
            parts = [value[i:i + _CHUNK] for i in range(0, len(value), _CHUNK)]
            for i, p in enumerate(parts, 1):
                ad.set(SERVICE, f"{name}#{i}", p)
            ad.set(SERVICE, name, f"{_CHUNK_HEAD}{len(parts)}")
            n = len(parts)
        for i in range(n + 1, old + 1):          # the value shrank: drop leftover chunks
            ad.delete(SERVICE, f"{name}#{i}")
        return True
    except Exception as e:
        _warn("write", name, e)
        return False


def _kr_delete(name: str) -> bool:
    ad = _get_adapter()
    if ad is None:
        return True
    try:
        for i in range(1, _kr_chunks(name) + 1):
            ad.delete(SERVICE, f"{name}#{i}")
        ad.delete(SERVICE, name)
        return True
    except Exception as e:
        _warn("delete", name, e)
        return False


# ---------------------------------------------------------------------------------------------
# small JSON files in the data dir (fallback secrets, name index, migration state)
# ---------------------------------------------------------------------------------------------
def _data_file(name: str) -> Path:
    return paths.data_path(name)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, obj: Any, *, ensure_ascii: bool = True, newline: bool = False,
                private: bool = False) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(obj, indent=2, ensure_ascii=ensure_ascii) + ("\n" if newline else "")
    tmp.write_text(text, encoding="utf-8")
    if private:
        _chmod_private(tmp)
    os.replace(tmp, path)


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)                    # no effect on Windows; harmless there
    except OSError:
        pass


def _file_get(name: str) -> str | None:
    d = _read_json(_data_file("secrets_fallback.json"), {})
    v = d.get(name) if isinstance(d, dict) else None
    return v if isinstance(v, str) and v else None


def _file_set(name: str, value: str | None) -> bool:
    try:
        p = _data_file("secrets_fallback.json")
        d = _read_json(p, {})
        d = d if isinstance(d, dict) else {}
        if value is None:
            if name not in d:
                return True
            d.pop(name)
        else:
            d[name] = value
        if d:
            _write_json(p, d, private=True)
        elif p.exists():
            p.unlink()
        return True
    except OSError as e:
        _log.warning("secrets: fallback file write failed (%s)", type(e).__name__)
        return False


def _index() -> list[str]:
    d = _read_json(_data_file("secrets_index.json"), [])
    return [x for x in d if isinstance(x, str)] if isinstance(d, list) else []


def _index_update(name: str, present: bool) -> None:
    try:
        names = _index()
        if present and name not in names:
            names.append(name)
        elif not present and name in names:
            names.remove(name)
        else:
            return
        _write_json(_data_file("secrets_index.json"), sorted(names))
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------
# public API (C2)
# ---------------------------------------------------------------------------------------------
def get(name: str, default: str = "") -> str:
    _check(name)
    with _lock:
        v = _cache.get(name)
        if v is None:
            v = _kr_get(name)
            if v is None:
                v = _file_get(name)
            if v:
                _cache[name] = v
                _remember(v)
        return v or default


def set(name: str, value: Any) -> bool:  # noqa: A001 - the contract name
    """Store a secret. Empty value = delete. True if it was stored somewhere (keychain or fallback)."""
    _check(name)
    value = "" if value is None else str(value)
    if not value.strip():
        return delete(name)
    with _lock:
        ok = _kr_set(name, value)
        if ok:
            _file_set(name, None)                # drop a stale plain-text copy
        else:
            if _get_adapter() is not None:
                _log.warning("secrets: keychain write failed for %r - using the fallback file", name)
            ok = _file_set(name, value)
        if ok:
            _cache[name] = value
            _index_update(name, True)
            _remember(value)
        return ok


def delete(name: str) -> bool:
    _check(name)
    with _lock:
        ok = _kr_delete(name)
        ok = _file_set(name, None) and ok
        _cache.pop(name, None)
        if ok:
            _index_update(name, False)
        return ok


def has(name: str) -> bool:
    return bool(get(name))


def where(name: str) -> str:
    """'keychain' | 'file' | 'none': where the secret is kept right now (never its value)."""
    _check(name)
    with _lock:
        if _kr_get(name) is not None:
            return "keychain"
        return "file" if _file_get(name) else "none"


def names() -> list[str]:
    """Names of the secrets Lyra stored (from a value-free index)."""
    return sorted(_index())


def delete_prefix(prefix: str) -> int:
    """Delete every secret whose name starts with `prefix` (e.g. 'plugin.spotify.'). Returns the count."""
    n = 0
    for nm in names():
        if nm.startswith(prefix) and delete(nm):
            n += 1
    return n


# ---------------------------------------------------------------------------------------------
# redaction: nothing secret in logs
# ---------------------------------------------------------------------------------------------
_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gsk_[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/\-]{16,}=*"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)"
               r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',}&]{6,}"),
]
_MIN_KNOWN = 8                                   # shorter values would mask ordinary words


def _remember(value: str) -> None:
    if len(value) >= _MIN_KNOWN:
        _known.add(value)
    _ensure_filter()


def redact(text: Any) -> str:
    """`text` with every known secret value and common key/token format replaced by ***."""
    s = text if isinstance(text, str) else str(text)
    if len(s) < 6:
        return s
    for v in sorted(_known, key=len, reverse=True):
        if v in s:
            s = s.replace(v, "***")
    for pat in _PATTERNS:
        s = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "***", s)
    return s


class RedactFilter(logging.Filter):
    """Masks secrets in every record that passes (message and arguments are merged first)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            clean = redact(msg)
            if clean != msg:
                record.msg, record.args = clean, ()
            if record.exc_info and record.exc_info[1] is not None and not record.exc_text:
                import traceback
                tb = "".join(traceback.format_exception(*record.exc_info))
                record.exc_text = redact(tb)
        except Exception:
            pass
        return True


_FILTER = RedactFilter()


def install_log_filter(logger: logging.Logger | None = None) -> None:
    """Attach the redaction filter to the logger and to its handlers. Idempotent."""
    lg = logger or _log
    if _FILTER not in lg.filters:
        lg.addFilter(_FILTER)
    for h in lg.handlers:
        if _FILTER not in h.filters:
            h.addFilter(_FILTER)


def _ensure_filter() -> None:
    global _filter_installed
    if not _filter_installed:
        _filter_installed = True
        install_log_filter()


# ---------------------------------------------------------------------------------------------
# migration of plain-text JSON files
# ---------------------------------------------------------------------------------------------
def _state_path() -> Path:
    return _data_file("secrets_migration.json")


def _state() -> dict:
    d = _read_json(_state_path(), {})
    return d if isinstance(d, dict) else {}


def _state_set(source: str, info: dict | None) -> None:
    try:
        d = _state()
        if info is None:
            d.pop(source, None)
        else:
            d[source] = info
        _write_json(_state_path(), d)
    except OSError:
        pass


def migration_state() -> dict:
    """{source: {state, names, at}} - no secrets in it."""
    return {k: {kk: vv for kk, vv in v.items() if kk != "backup"} for k, v in _state().items()
            if isinstance(v, dict)}


def _shred(path: Path) -> None:
    """Overwrite then delete (best effort: SSD wear levelling can keep old blocks)."""
    try:
        p = Path(path)
        if p.is_file():
            size = p.stat().st_size
            with open(p, "r+b") as f:
                f.write(b"\0" * size)
                f.flush()
                os.fsync(f.fileno())
            p.unlink()
    except OSError:
        try:
            Path(path).unlink()
        except OSError:
            pass


def migrate_json_file(source: str, path: Path | str, fields: Iterable[str], *,
                      ensure_ascii: bool = True, newline: bool = False) -> dict:
    """Move the non-empty `fields` of the JSON file `path` into the keychain.

    Returns {"state": "none"|"skipped"|"failed"|"migrated"|..., "moved": [names]}.
    Idempotent and safe: the file is only changed after every value was read back correctly."""
    path = Path(path)
    fields = tuple(fields)
    st = _state().get(source) or {}
    if st.get("state") == "rolled_back":
        return {"state": "rolled_back", "moved": []}
    if st.get("state") == "failed" and time.time() - float(st.get("ts") or 0) < 300:
        return {"state": "failed", "moved": []}   # do not retry (and log) on every read
    raw = _read_json(path, None) if path.is_file() else None
    if not isinstance(raw, dict):
        return {"state": st.get("state", "none"), "moved": []}
    pending = {f: raw[f] for f in fields if isinstance(raw.get(f), str) and raw[f].strip()}
    if not pending:
        return {"state": st.get("state", "none"), "moved": []}
    if not keyring_available():
        return {"state": "skipped", "reason": "no keychain", "moved": []}

    backup = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        if not backup.exists():
            shutil.copyfile(path, backup)        # exact copy of the original, kept until confirmed
            _chmod_private(backup)
        for name, value in pending.items():
            if not _kr_set(name, value) or _kr_get(name) != value:
                raise RuntimeError(f"could not verify {name} in the keychain")
        for name, value in pending.items():
            _cache[name] = value
            _remember(value)
            _index_update(name, True)
        blanked = dict(raw)
        for name in pending:
            blanked[name] = ""
        _write_json(path, blanked, ensure_ascii=ensure_ascii, newline=newline)
    except Exception as e:
        _log.warning("secrets: migration of %s failed (%s) - the original file is untouched", source, type(e).__name__)
        _state_set(source, {"state": "failed", "at": _now(), "ts": time.time(), "names": sorted(pending)})
        return {"state": "failed", "moved": []}
    old = st.get("names") if isinstance(st.get("names"), list) else []
    _state_set(source, {"state": "migrated", "at": _now(), "boot": _BOOT, "backup": str(backup),
                        "names": sorted(_set(old) | _set(pending))})
    _log.info("secrets: moved %d secret(s) of %s to the keychain: %s", len(pending), source, ", ".join(sorted(pending)))
    return {"state": "migrated", "moved": sorted(pending)}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def retry_failed() -> None:
    """Forget the 'failed' marker so the next read tries the migration again (button / CLI)."""
    for src, st in list(_state().items()):
        if isinstance(st, dict) and st.get("state") == "failed":
            _state_set(src, None)


def forget_backup_fields(source: str, fields: Iterable[str]) -> None:
    """The user changed or cleared these secrets: the old values must not survive in the backup."""
    st = _state().get(source) or {}
    bp = Path(st.get("backup") or "")
    if not st.get("backup") or not bp.is_file():
        return
    raw = _read_json(bp, None)
    if not isinstance(raw, dict):
        return
    touched = False
    for f in fields:
        if raw.get(f):
            raw[f] = ""
            touched = True
    if touched:
        try:
            _write_json(bp, raw, private=True)
        except OSError:
            pass


def _verify_against_backup(st: dict) -> bool:
    bp = Path(st.get("backup") or "")
    if not st.get("backup") or not bp.is_file():
        return True                              # nothing left to compare with
    raw = _read_json(bp, {})
    if not isinstance(raw, dict):
        return False
    for name in st.get("names", []):
        old = raw.get(name)
        if isinstance(old, str) and old.strip() and get(name) != old:
            return False
    return True


def confirm(source: str | None = None) -> dict:
    """Success confirmed: verify the keychain again, then shred the backup. {source: state}."""
    out: dict[str, str] = {}
    for src, st in list(_state().items()):
        if source and src != source:
            continue
        if not isinstance(st, dict) or st.get("state") != "migrated":
            continue
        if not _verify_against_backup(st):
            out[src] = "mismatch"                # keep the backup, something is off
            continue
        if st.get("backup"):
            _shred(Path(st["backup"]))
        _state_set(src, {"state": "confirmed", "at": _now(), "names": st.get("names", [])})
        out[src] = "confirmed"
    return out


def housekeeping() -> dict:
    """Call once per launch: a migration from an EARLIER launch that still reads back fine is confirmed."""
    ok = {src for src, st in _state().items()
          if isinstance(st, dict) and st.get("state") == "migrated" and st.get("boot") != _BOOT}
    res: dict[str, str] = {}
    for src in ok:
        res.update(confirm(src))
    return res


def rollback(source: str) -> bool:
    """Put the original plain-text file back from the backup (while it still exists)."""
    st = _state().get(source) or {}
    bp = Path(st.get("backup") or "")
    if st.get("state") != "migrated" or not st.get("backup") or not bp.is_file():
        return False
    target = bp.with_name(bp.name[: -len(BACKUP_SUFFIX)])
    shutil.copyfile(bp, target)
    _shred(bp)
    _state_set(source, {"state": "rolled_back", "at": _now(), "names": st.get("names", [])})
    return True


# ---------------------------------------------------------------------------------------------
# settings.json hooks (called from the phase-0 lines in settings_store.py)
# ---------------------------------------------------------------------------------------------
def settings_get(key: str) -> str | None:
    """Keychain value of a secret settings key, or None = 'use the normal settings value'."""
    if key not in SETTINGS_SECRET_KEYS or not keyring_available():
        return None
    return get(key) or None


def settings_set_many(data: dict, updates: dict) -> None:
    """After settings_store applied `updates` to `data`: move secret keys to the keychain and
    blank them in `data`, so the settings file never gets them. Without a keychain: unchanged."""
    if not keyring_available():
        return
    for k in SETTINGS_SECRET_KEYS & _set(updates):
        v = str(updates[k] or "").strip()
        if set(k, v) if v else delete(k):
            data[k] = ""
            forget_backup_fields("settings", [k])


def settings_migrate(data: dict, settings_path: Path | str) -> None:
    """One-time move of plain-text tokens in settings.json (+ blank them in the cache `data`)."""
    if not keyring_available():
        return
    res = migrate_json_file("settings", settings_path, sorted(SETTINGS_SECRET_KEYS),
                            ensure_ascii=False, newline=True)
    for k in res.get("moved", []):
        data[k] = ""


# ---------------------------------------------------------------------------------------------
# sealing parts of a JSON blob (plugin_data/<app>.json and similar; see MANIFEST "Requested hooks")
# ---------------------------------------------------------------------------------------------
_SECRETISH = re.compile(r"(token|secret|password|passwd|api[_-]?key|refresh|credential)", re.I)


def _secret_paths(data: dict, secret_keys: Iterable[str] | None) -> list[tuple[str, ...]]:
    if secret_keys is not None:
        return [tuple(k.split(".")) for k in secret_keys]
    found: list[tuple[str, ...]] = []
    for k, v in data.items():
        if _SECRETISH.search(k):
            found.append((k,))
        elif isinstance(v, dict):
            found += [(k, kk) for kk in v if _SECRETISH.search(kk)]
    return found


def _dig(d: Any, path: tuple[str, ...]) -> tuple[Any, str] | None:
    for p in path[:-1]:
        d = d.get(p) if isinstance(d, dict) else None
    return (d, path[-1]) if isinstance(d, dict) and path[-1] in d else None


def seal_dict(namespace: str, data: dict, secret_keys: Iterable[str] | None = None) -> dict:
    """Copy of `data` that is safe to write to disk: secret values (dotted paths in `secret_keys`,
    or every key that looks like token/secret/password/api key) are stored in the keychain as
    '<namespace>.<path>' and replaced by SENTINEL. Without a keychain the data comes back unchanged."""
    if not keyring_available():
        return data
    out = copy.deepcopy(data)
    for path in _secret_paths(out, secret_keys):
        hit = _dig(out, path)
        if not hit:
            continue
        parent, key = hit
        val = parent[key]
        if val == SENTINEL or not isinstance(val, (str, dict, list)) or not val:
            continue
        if set(f"{namespace}.{'.'.join(path)}", json.dumps(val)):
            parent[key] = SENTINEL
    return out


def unseal_dict(namespace: str, data: dict) -> tuple[dict, bool]:
    """(`data` with SENTINEL values replaced by their secrets, True if plain-text secrets were found
    that the caller should now write back through seal_dict to migrate them)."""
    out = copy.deepcopy(data)
    legacy = False

    def walk(d: dict, prefix: tuple[str, ...]) -> None:
        nonlocal legacy
        for k in list(d):
            v = d[k]
            path = prefix + (k,)
            if v == SENTINEL:
                raw = get(f"{namespace}.{'.'.join(path)}")
                if raw:
                    try:
                        d[k] = json.loads(raw)
                        continue
                    except ValueError:
                        pass
                del d[k]                          # the keychain item is gone: behave as "not set"
            elif _SECRETISH.search(k) and isinstance(v, (str, dict, list)) and v:
                legacy = True                     # a plain-text secret: the caller should seal it now
            elif isinstance(v, dict) and not prefix:
                walk(v, path)

    walk(out, ())
    return out, legacy and keyring_available()


# ---------------------------------------------------------------------------------------------
# CLI (never prints a secret)
# ---------------------------------------------------------------------------------------------
def _cli(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(f"backend: {backend()}\nnames:   {', '.join(names()) or '-'}")
        print(f"migration: {json.dumps(migration_state())}")
        return 0
    try:
        from engine.brain import llm_store
        if cmd == "migrate":
            _state_set("llm", None)
            _state_set("settings", None)
            llm_store.load()
            try:
                from engine.core import settings_store
                settings_store._ensure_loaded()
            except Exception as e:
                print(f"settings: {type(e).__name__}")
            print(json.dumps(migration_state()))
            return 0
    except Exception as e:
        print(f"failed: {type(e).__name__}")
        return 1
    if cmd == "confirm":
        print(json.dumps(confirm()))
        return 0
    if cmd == "rollback":
        print({s: rollback(s) for s in list(_state())})
        return 0
    print(__doc__.split("CLI", 1)[-1])
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
