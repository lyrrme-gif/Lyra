"""Lyra — Windows installed-application scanner and launcher.

Builds a local index from:
  * Windows Start Menu shortcuts/executables
  * Windows Get-StartApps (including packaged/UWP apps)
  * App Paths registry entries
  * Uninstall registry entries (when they expose an executable)
  * executables available directly on PATH

The index is cached in memory. Lyra can refresh it with "rescan apps".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from dataclasses import dataclass
from pathlib import Path
from difflib import SequenceMatcher


@dataclass(frozen=True)
class AppEntry:
    name: str
    target: str
    kind: str = "file"  # file | uwp | command


_INDEX: dict[str, AppEntry] = {}
_SCANNED = False
_BUILD: dict[str, AppEntry] | None = None   # a scan fills this, then swaps it in: lookups never see a half-built index
_SCAN_LOCK = threading.Lock()
CACHE_MAX_AGE_S = 6 * 3600                  # a cached index older than this is rebuilt in the background


def normalize(text: str) -> str:
    text = (text or "").lower().strip()
    # Remove common Windows display-name noise.
    text = re.sub(r"\s*\((?:x64|x86|64-bit|32-bit)\)\s*$", "", text, flags=re.I)
    text = re.sub(r"\s*[-–—]\s*(?:desktop app|application)\s*$", "", text, flags=re.I)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _add(name: str, target: str, kind: str = "file") -> None:
    name = (name or "").strip()
    target = (target or "").strip()
    key = normalize(name)

    if not key or not target:
        return

    # Prefer real launchable entries over a weak/duplicate command entry.
    dest = _BUILD if _BUILD is not None else _INDEX
    old = dest.get(key)
    if old is None or (old.kind == "command" and kind != "command"):
        dest[key] = AppEntry(name=name, target=target, kind=kind)


def _scan_start_menu_files() -> None:
    roots = [
        Path(os.environ.get("PROGRAMDATA", ""))
        / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
    ]

    for root in roots:
        if not root.exists():
            continue
        try:
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".lnk", ".url", ".exe"}:
                    _add(path.stem, str(path), "file")
        except (OSError, PermissionError):
            continue


def _scan_start_apps() -> None:
    """Index packaged Windows apps using Get-StartApps."""
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return

    script = (
        "Get-StartApps | "
        "Select-Object Name,AppID | "
        "ConvertTo-Json -Compress"
    )

    try:
        result = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return

        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]

        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or "").strip()
            app_id = str(item.get("AppID") or "").strip()
            if name and app_id:
                _add(name, app_id, "uwp")
    except Exception:
        # Start Menu file scanning and registry scanning still work.
        return


def _scan_app_paths() -> None:
    locations = [
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    ]

    for hive, root_path in locations:
        try:
            with winreg.OpenKey(hive, root_path) as root:
                count = winreg.QueryInfoKey(root)[0]
                for i in range(count):
                    try:
                        sub_name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, sub_name) as sub:
                            exe_path, _ = winreg.QueryValueEx(sub, "")
                        if exe_path:
                            _add(Path(sub_name).stem, str(exe_path), "file")
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            continue


def _clean_exe_path(value: str) -> str:
    value = (value or "").strip().strip('"')
    # DisplayIcon is often: "C:\Program Files\App\App.exe",0
    value = re.sub(r'",?\s*-\d+\s*$', "", value)
    value = value.split(",")[0].strip().strip('"')
    return os.path.expandvars(value)


def _scan_uninstall_registry() -> None:
    """Use uninstall metadata to catch installed desktop apps missing a shortcut."""
    locations = [
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    for hive, root_path in locations:
        try:
            with winreg.OpenKey(hive, root_path) as root:
                count = winreg.QueryInfoKey(root)[0]
                for i in range(count):
                    try:
                        sub_name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, sub_name) as sub:
                            try:
                                display_name = str(
                                    winreg.QueryValueEx(sub, "DisplayName")[0]
                                ).strip()
                            except OSError:
                                continue

                            if not display_name:
                                continue

                            target = ""
                            for value_name in ("DisplayIcon", "InstallLocation"):
                                try:
                                    value = str(winreg.QueryValueEx(sub, value_name)[0])
                                except OSError:
                                    continue

                                value = _clean_exe_path(value)
                                if value.lower().endswith(".exe") and os.path.isfile(value):
                                    target = value
                                    break

                                if os.path.isdir(value):
                                    # Look only a few levels deep to avoid a huge
                                    # Program Files scan.
                                    try:
                                        candidates = []
                                        base = Path(value)
                                        for p in base.glob("*.exe"):
                                            candidates.append(p)
                                        for p in base.glob("*/*.exe"):
                                            candidates.append(p)
                                        if candidates:
                                            # Prefer an executable whose name resembles
                                            # the display name.
                                            wanted = normalize(display_name)
                                            candidates.sort(
                                                key=lambda p: SequenceMatcher(
                                                    None, normalize(p.stem), wanted
                                                ).ratio(),
                                                reverse=True,
                                            )
                                            target = str(candidates[0])
                                            break
                                    except OSError:
                                        pass

                            if target:
                                _add(display_name, target, "file")
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            continue


def _scan_path_executables() -> None:
    seen = set()
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder or folder in seen:
            continue
        seen.add(folder)

        path = Path(folder)
        if not path.is_dir():
            continue

        try:
            for exe in path.glob("*.exe"):
                _add(exe.stem, str(exe), "command")
        except (OSError, PermissionError):
            continue


def _cache_file() -> Path:
    from paths import data_path
    return data_path("app_index.json")


def _save_cache() -> None:
    try:
        rows = [[e.name, e.target, e.kind] for e in _INDEX.values()]
        tmp = _cache_file().with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": time.time(), "apps": rows}), "utf-8")
        os.replace(tmp, _cache_file())
    except Exception:  # noqa: BLE001 - the cache is an optimisation only
        pass


def _load_cache() -> float | None:
    """Fill the index from disk. Returns the cache's age in seconds, or None if there is no usable cache."""
    global _INDEX, _SCANNED
    try:
        data = json.loads(_cache_file().read_text("utf-8"))
        idx = {normalize(n): AppEntry(n, t, k) for n, t, k in data["apps"] if normalize(n) and t}
        if not idx:
            return None
        _INDEX, _SCANNED = idx, True
        return max(0.0, time.time() - float(data["at"]))
    except Exception:  # noqa: BLE001
        return None


def scan(force: bool = False) -> int:
    """Scan installed apps. Returns the number of indexed applications."""
    global _INDEX, _SCANNED, _BUILD

    if _SCANNED and not force:
        return len(_INDEX)

    with _SCAN_LOCK:
        if _SCANNED and not force and _BUILD is None:
            return len(_INDEX)
        _BUILD = {}
        try:
            _scan_start_menu_files()
            _scan_start_apps()
            _scan_app_paths()
            _scan_uninstall_registry()
            _scan_path_executables()
            _INDEX, _SCANNED = _BUILD, True
        finally:
            _BUILD = None
    _save_cache()
    return len(_INDEX)


def scan_startup() -> int:
    """Startup entry point. With a saved index the app list is ready at once and, when the saved copy is
    old, rebuilt on a background thread; the first-ever launch (or a missing cache) scans in the foreground."""
    age = _load_cache()
    if age is None:
        return scan(force=True)
    if age > CACHE_MAX_AGE_S:
        threading.Thread(target=lambda: scan(force=True), name="lyra-app-rescan", daemon=True).start()
    return len(_INDEX)


def _similarity(query: str, candidate: str) -> float:
    q = normalize(query)
    c = normalize(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.90

    # Token overlap helps with "visual studio code" vs "Visual Studio Code".
    q_tokens = set(q.split())
    c_tokens = set(c.split())
    overlap = len(q_tokens & c_tokens) / max(1, len(q_tokens | c_tokens))
    return max(SequenceMatcher(None, q, c).ratio(), overlap * 0.92)


def find(query: str, cutoff: float = 0.70) -> AppEntry | None:
    """Find an installed app by exact, partial, or fuzzy display name."""
    if not _SCANNED:
        scan()

    q = normalize(query)
    if not q:
        return None

    exact = _INDEX.get(q)
    if exact:
        return exact

    best = None
    best_score = 0.0

    for key, entry in _INDEX.items():
        score = _similarity(q, key)
        if score > best_score:
            best_score = score
            best = entry

    return best if best_score >= cutoff else None


def _process_started(proc: subprocess.Popen, settle_s: float = 0.20) -> bool:
    """Return True only when the launched process is still alive after a brief settle time."""
    try:
        deadline = time.monotonic() + settle_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False
            time.sleep(0.03)
        return proc.poll() is None
    except Exception:
        return False


def launch(entry: AppEntry) -> bool:
    """Launch a scanned app entry and only report success when the launch survives verification."""
    try:
        if entry.kind == "uwp":
            proc = subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{entry.target}"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # Explorer is the broker for packaged apps. A live broker means Windows
            # accepted the launch request; the old shell=True path falsely succeeded
            # even when the command itself did not exist.
            return _process_started(proc)
        if entry.kind == "file":
            target = os.path.expandvars(os.path.expanduser(entry.target))
            if not os.path.isfile(target):
                return False
            try:
                proc = subprocess.Popen([target], shell=False)
                return _process_started(proc)
            except (OSError, ValueError):
                # Non-executable file association: ShellExecute is the correct Windows
                # mechanism, but there is no portable process handle to verify. Treat
                # a successful os.startfile call as an accepted Windows shell launch.
                os.startfile(target)  # type: ignore[attr-defined]
                return True
        target = os.path.expandvars(os.path.expanduser(entry.target))
        proc = subprocess.Popen([target], shell=False)
        return _process_started(proc)
    except Exception:
        return False


def count() -> int:
    return len(_INDEX)


def names(limit: int | None = None) -> list[str]:
    values = sorted((entry.name for entry in _INDEX.values()), key=str.casefold)
    return values[:limit] if limit else values
