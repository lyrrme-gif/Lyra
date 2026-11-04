"""Lyra — where files live.

Two kinds of location:
  * data dir  - everything Lyra writes at runtime (settings, keys, memory, logs...).
                Always: %APPDATA%\\Lyra  (or $XDG_CONFIG_HOME/Lyra on non-Windows)
                Override for tests/dev: env LYRA_DATA_DIR
  * app dir   - read-only files shipped with the app (contacts.example.json, ...).

Previously, running from source wrote into the backend/ folder and polluted the
repo. Runtime data now always goes under the user profile data directory.

This module must stay dependency-free (ui.py imports it before logging exists),
so startup messages are collected in NOTES and logged by ui.py.

One-time copy of an existing dev setup into the data dir (never overwrites,
never deletes the originals):
    backend\\venv\\Scripts\\python.exe paths.py --migrate [--from DIR]
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
_SOURCE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(getattr(sys, "_MEIPASS", None) or _SOURCE_DIR)

# Runtime files worth carrying over from an old backend folder (relative paths).
# ui.log is deliberately not copied; lyrr_memory.db is ignored (unused).
MIGRATE_FILES = (
    "settings.json",
    "llm_keys.json",
    "lyra_memory.db",
    "app_profiles.json",
    "contacts.json",
    "shortcuts/shortcuts.json",
    "decks.json",
    "updater.json",
)

NOTES: list[str] = []  # drained by ui.py once the logger exists
_ready = False


def _installed_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Lyra"
    # Linux / macOS: XDG or ~/.config/Lyra
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "Lyra"


def _resolve() -> Path:
    override = os.environ.get("LYRA_DATA_DIR", "").strip()
    if override:
        return Path(override)
    # Always use the user data directory — never the source backend/ folder.
    return _installed_dir()


def migrate(src: Path, dst: Path) -> list[str]:
    """Copy MIGRATE_FILES from src to dst where dst doesn't have them. Returns copied names."""
    copied: list[str] = []
    try:
        if src.resolve() == dst.resolve():
            return copied
    except OSError:
        return copied
    for rel in MIGRATE_FILES:
        s, d = src / rel, dst / rel
        if d.exists() or not s.is_file():
            continue
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            copied.append(rel)
        except OSError as e:
            NOTES.append(f"data migration: skip {rel}: {e}")
    return copied


def data_dir() -> Path:
    """The writable data directory (created on first use)."""
    global _ready
    d = _resolve()
    if not _ready:
        _ready = True
        d.mkdir(parents=True, exist_ok=True)
        # One-time: pull legacy files out of the old backend/ source location.
        legacy = os.environ.get("LYRA_LEGACY_DIR", "").strip()
        if not legacy and not FROZEN:
            legacy = str(_SOURCE_DIR)
        if legacy:
            done = migrate(Path(legacy), d)
            if done:
                NOTES.append(f"data migration: copied {', '.join(done)} from {legacy} to {d}")
    return d


def data_path(*parts: str) -> Path:
    """A file inside the data dir; its parent folder is created."""
    p = data_dir().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def app_path(*parts: str) -> Path:
    """A read-only file shipped with the app."""
    return APP_DIR.joinpath(*parts)


if __name__ == "__main__":
    if "--migrate" in sys.argv:
        src = _SOURCE_DIR
        if "--from" in sys.argv:
            src = Path(sys.argv[sys.argv.index("--from") + 1])
        dst = Path(os.environ.get("LYRA_DATA_DIR", "").strip() or _installed_dir())
        dst.mkdir(parents=True, exist_ok=True)
        done = migrate(src, dst)
        print(f"From: {src}\nTo:   {dst}")
        print("Copied: " + (", ".join(done) if done else "nothing (already there or not found)"))
        for n in NOTES:
            print(n)
    else:
        print(f"data dir: {data_dir()}\napp dir:  {APP_DIR}\nfrozen:   {FROZEN}")
