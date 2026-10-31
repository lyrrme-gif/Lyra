"""Lyra - coding helper (Step 8).

Voice and typed commands for developers. Every function returns a short sentence Lyra can
speak, or None when the phrase is not for this module (so the normal router carries on).

  explain this error          explain the error on the clipboard / the last terminal text, in short steps
  open project X in VS Code   open a project from your own list
  run <script>                run a script that sits inside an allowed folder        (asks first)
  git status                  short summary of the active project's repository
  commit with message ...     commit the tracked changes, never push               (asks first)

Management commands (so the lists below can be filled by voice or typing):
  add project X at <folder> / remove project X / list my projects
  allow scripts in <folder>   (asks first) / stop allowing scripts in <folder> / list script folders

Safety rules, all enforced here and not only in the prompt:
  * Scripts run ONLY when they resolve (symlinks followed) to a file inside an allowed folder,
    have a known extension, and the user confirmed. No arguments are ever passed, never a shell.
  * Git commit is always confirmed, uses `commit -a` (tracked files only, no `git add -A`), and
    there is no code path that pushes.
  * Confirmation goes through permissions.PermissionManager (ids dev_run_script, dev_git_commit,
    dev_allow_folder are not in its table, so they fall through to the CONFIRM tier).

State lives in the data folder: dev_config.json (projects, allowed folders, last project).
Nothing is stored in the project folder.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

FEATURE = "dev"

_IS_WIN = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

SCRIPT_EXTS = (".py", ".bat", ".cmd", ".ps1", ".js")
_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "site-packages", ".tox"}
_MAX_SCAN = 4000          # directory entries looked at while searching for a script
_SCAN_DEPTH = 2           # sub-folder levels below an allowed folder
_DEFAULT_TIMEOUT_S = 300  # a script that runs longer than this is stopped
_ERR_TAIL_CHARS = 4000    # how much of an error is sent to the LLM (the end of a traceback matters most)

_cfg_lock = threading.Lock()
_run_lock = threading.Lock()
_running: dict | None = None            # {"name": str, "proc": Popen} while a script runs
_last_output: dict | None = None        # {"source", "ok", "text", "ts"} of the last script / git failure
_pm = None


# ── small helpers ────────────────────────────────────────────────────────────────────────────

def _trace(msg: str) -> None:
    try:
        import ui
        ui.trace(msg)
    except Exception:
        pass


def _log():
    try:
        import ui
        return ui.log
    except Exception:
        import logging
        return logging.getLogger("lyra")


def _enabled() -> bool:
    try:
        import settings_store
        return settings_store.feature_on(FEATURE)
    except Exception:
        return True


def _perm():
    global _pm
    if _pm is None:
        from permissions import PermissionManager
        _pm = PermissionManager()
    return _pm


def _confirm(action_id: str, description: str, engine_context) -> tuple[bool, str]:
    """Ask through the shared permission layer (voice yes/no, or the queued typed yes)."""
    return _perm().check_permission(action_id, description, engine_context)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _speak_list(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _clean(command: str) -> str:
    c = (command or "").strip()
    c = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", c, flags=re.I)
    return c.strip().rstrip("?.!").strip()


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


# ── config (data folder) ─────────────────────────────────────────────────────────────────────

def _cfg_path() -> Path:
    import paths
    return paths.data_path("dev_config.json")


def _load() -> dict:
    cfg: dict = {}
    try:
        data = json.loads(_cfg_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg = data
    except Exception:
        pass
    if not isinstance(cfg.get("projects"), dict):
        cfg["projects"] = {}
    if not isinstance(cfg.get("allowed_folders"), list):
        cfg["allowed_folders"] = []
    cfg.setdefault("last_project", "")
    return cfg


def _save(cfg: dict) -> None:
    _cfg_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _update(mutator) -> dict:
    with _cfg_lock:
        cfg = _load()
        mutator(cfg)
        _save(cfg)
        return cfg


# ── folders and projects ─────────────────────────────────────────────────────────────────────

def _resolve_dir(raw: str) -> str | None:
    """A spoken or typed folder ("documents", "d:\\code\\lyra") -> an existing absolute folder, or None."""
    raw = (raw or "").strip().strip("\"'").rstrip(".")
    if not raw:
        return None
    cand: list[str] = []
    try:
        from actions.files import resolve_location
        r = resolve_location(raw)
        if r:
            cand.append(str(r))
    except Exception:
        pass
    cand.append(os.path.expandvars(os.path.expanduser(raw)))
    for c in cand:
        try:
            if os.path.isdir(c):
                return os.path.realpath(c)
        except Exception:
            continue
    return None


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _is_inside(path: str, folder: str) -> bool:
    try:
        p = os.path.normcase(os.path.realpath(path))
        f = os.path.normcase(os.path.realpath(folder))
        return os.path.commonpath([p, f]) == f
    except (ValueError, OSError):
        return False


def _unsafe_folder(folder: str) -> str | None:
    """Why this folder must not be allowed for scripts, or None when it is fine."""
    f = os.path.realpath(folder)
    if os.path.dirname(f) == f:
        return "That's a whole drive. Pick a specific folder."
    try:
        if _same(f, str(Path.home())):
            return "That's your whole user folder. Pick a specific project folder."
    except Exception:
        pass
    for var in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        root = os.environ.get(var)
        if root and _is_inside(f, root):
            return "I won't run scripts from a system folder."
    return None


def _match_project(name: str, projects: dict) -> tuple[str, str] | None:
    key = _norm(name)
    if not key or not projects:
        return None
    norm_map = {_norm(k): k for k in projects}
    if key in norm_map:
        k = norm_map[key]
        return k, projects[k]
    starts = [nk for nk in norm_map if nk.startswith(key) or key.startswith(nk)]
    if len(starts) == 1:
        k = norm_map[starts[0]]
        return k, projects[k]
    close = difflib.get_close_matches(key, list(norm_map), n=1, cutoff=0.75)
    if close:
        k = norm_map[close[0]]
        return k, projects[k]
    return None


def _active_project(explicit: str | None = None) -> tuple[tuple[str, str] | None, str]:
    """(name, path) of the project a command is about, plus an error sentence when there is none."""
    cfg = _load()
    projects = cfg["projects"]
    if not projects:
        return None, "You haven't added any projects yet. Say add project, then a name, then at, then the folder."
    if explicit:
        hit = _match_project(explicit, projects)
        if not hit:
            return None, f"I don't have a project called {explicit}. Your projects are {_speak_list(list(projects))}."
        pick = hit
    else:
        last = cfg.get("last_project") or ""
        if last in projects:
            pick = (last, projects[last])
        elif len(projects) == 1:
            k = next(iter(projects))
            pick = (k, projects[k])
        else:
            return None, f"Which project? Your projects are {_speak_list(list(projects))}."
    if not os.path.isdir(pick[1]):
        return None, f"The folder for {pick[0]} is missing: {pick[1]}."
    return pick, ""


# ── explain this error ───────────────────────────────────────────────────────────────────────

_ERRORISH = re.compile(
    r"error|exception|traceback|failed|failure|fatal|cannot|can't|could not|undefined|not found|denied|"
    r"errno|npm err|\bTS\d{4}\b|segmentation|panic|stack trace|\bat .+:\d+",
    re.I,
)


def _clipboard_text() -> str:
    try:
        from actions.system import get_clipboard_text
        return (get_clipboard_text() or "").strip()
    except Exception:
        return ""


def _terminal_text() -> str:
    """Best effort: the text of the focused terminal window through UI Automation (needs pywinauto).

    UNTESTED on a real Windows Terminal / console in the build that produced this file; it is
    wrapped so any failure just returns an empty string.
    """
    if not _IS_WIN:
        return ""
    try:
        from computer import uia
        win = uia.foreground_window()
        if win is None:
            return ""
        title = uia.window_title(win).lower()
        cls = ""
        try:
            cls = (win.element_info.class_name or "").lower()
        except Exception:
            pass
        if not (cls in ("cascadia_hosting_window_class", "consolewindowclass")
                or any(w in title for w in ("powershell", "command prompt", "cmd", "terminal", "bash"))):
            return ""
        targets = []
        try:
            targets = list(win.descendants(class_name="TermControl"))
        except Exception:
            pass
        targets.append(win)
        for t in targets:
            try:
                txt = t.iface_text.DocumentRange.GetText(-1)
            except Exception:
                continue
            if txt and txt.strip():
                return txt.strip()
    except Exception as e:  # noqa: BLE001
        _log().debug("dev: terminal read failed: %s", e)
    return ""


def _recent_failed_output() -> str:
    lo = _last_output
    if lo and not lo.get("ok") and time.time() - lo.get("ts", 0) < 1800:
        return lo.get("text", "")
    return ""


def _grab_error_text(hint: str) -> str:
    """hint: "clipboard" | "terminal" | "auto"."""
    if hint == "clipboard":
        return _clipboard_text()
    clip = _clipboard_text()
    if hint == "terminal":
        return _recent_failed_output() or _terminal_text() or clip
    # auto: a clipboard that looks like an error wins, then what Lyra ran herself, then the terminal
    if clip and _ERRORISH.search(clip):
        return clip
    return _recent_failed_output() or _terminal_text() or clip


_EXPLAIN = re.compile(
    r"^(?:explain|what(?:'s|\s+is|\s+does)|help\s+me\s+(?:with|understand|fix)|"
    r"why\s+(?:am\s+i\s+getting|did\s+i\s+get|do\s+i\s+get))\s+"
    r"(?:this|that|the|my|last|the\s+last)\s+(?:\w+\s+){0,2}?"
    r"(?:errors?|exceptions?|tracebacks?|stack\s*traces?|crash(?:es)?|failures?|"
    r"compiler\s+output|terminal\s+output|console\s+output|build\s+output)"
    r"(?:\s+(?:message|mean|means|in|from|on|i|just|copied|the|my|terminal|console|clipboard|output|window))*$",
    re.I,
)


def _explain_error(c: str, engine_context) -> str | None:
    low = c.lower()
    hint = "clipboard" if re.search(r"clipboard|copied", low) else ("terminal" if re.search(r"terminal|console|output|last", low) else "auto")
    text = _strip_ansi(_grab_error_text(hint)).strip()
    if not text:
        return "I don't see an error. Copy it first, or tell me to run a script and I'll read the output."
    text = text[-_ERR_TAIL_CHARS:]
    try:
        import ui
        ui.set_state("thinking")
    except Exception:
        pass
    prompt = (
        "You are a senior developer helping someone by voice. Explain the error below in at most 5 short "
        "numbered steps: what it means, the most likely cause, and how to fix it. Plain text only, no markdown, "
        "no code blocks; if a command or code change is needed, say it inline in one short line. "
        "Keep it under 90 words. Reply with ONLY the steps.\n\nError:\n" + text
    )
    try:
        import brain
        reply = brain.ask_brain(prompt, [], intent="chat")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't explain it - {e}"
    return (reply or "").strip() or "I couldn't get an explanation."


# ── open project in VS Code ──────────────────────────────────────────────────────────────────

_OPEN = re.compile(
    r"^(?:open|launch|start|load)\s+(?:the\s+)?(?:(?P<proj>project)\s+)?(?P<name>.+?)(?:\s+project)?"
    r"(?:\s+(?:in|with|using|on)\s+(?P<ed>vs\s*code|v\s*s\s*code|visual\s+studio\s+code|vscode|code))?$",
    re.I,
)


def _find_vscode() -> str | None:
    try:
        override = _load().get("vscode_path")
        if override and os.path.isfile(override):
            return override
    except Exception:
        pass
    code = shutil.which("code")
    if not _IS_WIN:
        return code
    cands: list[str] = []
    if code:
        cands.append(str(Path(code).resolve().parent.parent / "Code.exe"))
    for var, rel in (("LOCALAPPDATA", r"Programs\Microsoft VS Code\Code.exe"),
                     ("ProgramFiles", r"Microsoft VS Code\Code.exe"),
                     ("ProgramFiles(x86)", r"Microsoft VS Code\Code.exe")):
        base = os.environ.get(var)
        if base:
            cands.append(os.path.join(base, rel))
    for c in cands:
        if os.path.isfile(c):
            return c
    return code  # last resort: code.cmd


def _open_project(c: str, engine_context) -> str | None:
    m = _OPEN.match(c)
    if not m:
        return None
    explicit = bool(m.group("proj")) or bool(re.search(r"\sproject$", c, re.I))
    if not (explicit or m.group("ed")):
        return None                       # plain "open chrome": not ours
    name = m.group("name").strip()
    projects = _load()["projects"]
    hit = _match_project(name, projects)
    if not hit:
        if not explicit:
            return None                   # "open notes.txt in code": leave it to the other handlers
        if not projects:
            return "You haven't added any projects yet. Say add project, then a name, then at, then the folder."
        return f"I don't have a project called {name}. Your projects are {_speak_list(list(projects))}."
    pname, ppath = hit
    if not os.path.isdir(ppath):
        return f"The folder for {pname} is missing: {ppath}."
    exe = _find_vscode()
    if not exe:
        return "I can't find VS Code. Install it, or put its path in dev_config.json as vscode_path."
    try:
        subprocess.Popen([exe, ppath], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=(_DETACHED if _IS_WIN else 0))
    except Exception as e:  # noqa: BLE001
        return f"I couldn't start VS Code - {e}"
    _update(lambda cfg: cfg.__setitem__("last_project", pname))
    _trace(f"action: open project '{pname}' in VS Code")
    return f"Opening {pname} in VS Code."


# ── projects and allowed folders (management) ────────────────────────────────────────────────

_ADD_PROJECT = re.compile(
    r"^(?:add|register|save)\s+(?:a\s+)?(?:new\s+)?project\s+(?:called\s+|named\s+)?(?P<name>.+?)\s+"
    r"(?:at|in|from|path|located\s+at)\s+(?P<path>.+)$", re.I)
_REMOVE_PROJECT = re.compile(r"^(?:remove|delete|forget)\s+(?:the\s+)?project\s+(?P<name>.+)$", re.I)
_LIST_PROJECTS = re.compile(r"^(?:list|show|what\s+are)\s+(?:me\s+)?(?:all\s+)?(?:my\s+)?(?:coding\s+|dev\s+)?projects$", re.I)
_ALLOW_FOLDER = re.compile(
    r"^(?:allow|permit|trust)\s+(?:running\s+)?scripts?\s+(?:in|from|inside)\s+(?:the\s+)?(?:folder\s+)?(?P<path>.+)$", re.I)
_DISALLOW_FOLDER = re.compile(
    r"^(?:stop\s+allowing|disallow|don'?t\s+allow|do\s+not\s+allow)\s+(?:running\s+)?scripts?\s+(?:in|from|inside)\s+"
    r"(?:the\s+)?(?:folder\s+)?(?P<path>.+)$", re.I)
_LIST_FOLDERS = re.compile(
    r"^(?:(?:list|show)\s+(?:me\s+)?(?:my\s+)?(?:allowed\s+)?script\s+folders|"
    r"which\s+folders\s+(?:can|may)\s+(?:you\s+)?run\s+scripts(?:\s+in)?)$", re.I)


def _add_project(m, engine_context) -> str:
    name = m.group("name").strip().strip("\"'")
    folder = _resolve_dir(m.group("path"))
    if not name:
        return "What should the project be called?"
    if not folder:
        return f"I can't find the folder {m.group('path').strip()}."
    _update(lambda cfg: cfg["projects"].__setitem__(name, folder))
    return f"Added the project {name}: {folder}."


def _remove_project(m, engine_context) -> str | None:
    cfg = _load()
    hit = _match_project(m.group("name"), cfg["projects"])
    if not hit:
        return None                       # "delete project folder ...": a file command, not ours
    def drop(c):
        c["projects"].pop(hit[0], None)
        if c.get("last_project") == hit[0]:
            c["last_project"] = ""
    _update(drop)
    return f"Removed the project {hit[0]} from your list. The folder itself is untouched."


def _list_projects(m, engine_context) -> str:
    projects = _load()["projects"]
    if not projects:
        return "You haven't added any projects yet."
    return "Your projects: " + _speak_list(list(projects)) + "."


def _allow_folder(m, engine_context) -> str:
    folder = _resolve_dir(m.group("path"))
    if not folder:
        return f"I can't find the folder {m.group('path').strip()}."
    why = _unsafe_folder(folder)
    if why:
        return why
    if any(_same(folder, f) for f in _load()["allowed_folders"] if os.path.isdir(f)):
        return f"{folder} is already allowed."
    ok, reason = _confirm("dev_allow_folder", f"let me run scripts from {folder}", engine_context)
    if not ok:
        return reason or "Okay, I won't allow it."
    _update(lambda c: c["allowed_folders"].append(folder))
    return f"Scripts in {folder} are now allowed. I'll still ask before each run."


def _disallow_folder(m, engine_context) -> str:
    raw = m.group("path").strip()
    target = _resolve_dir(raw) or raw
    cfg = _load()
    keep, dropped = [], []
    for f in cfg["allowed_folders"]:
        (dropped if (_norm(f) == _norm(target) or (os.path.isdir(f) and os.path.isdir(target) and _same(f, target))) else keep).append(f)
    if not dropped:
        return f"{raw} wasn't in the allowed list."
    _update(lambda c: c.__setitem__("allowed_folders", keep))
    return f"I won't run scripts from {dropped[0]} any more."


def _list_folders(m, engine_context) -> str:
    folders = _load()["allowed_folders"]
    if not folders:
        return "No folders are allowed for scripts yet. Say allow scripts in, then a folder."
    return "I can run scripts from: " + "; ".join(folders) + "."


# ── run a script ─────────────────────────────────────────────────────────────────────────────

_RUN = re.compile(r"^(?:run|execute)\s+(?:the\s+)?(?:(?P<kw>script)\s+)?(?P<name>.+?)(?:\s+(?P<kw2>script))?$", re.I)


def _spoken_name(name: str) -> str:
    name = re.sub(r"\s*\bunderscore\b\s*", "_", name, flags=re.I)
    name = re.sub(r"\s*\bdot\b\s*(py|bat|cmd|ps1|js)\b", r".\1", name, flags=re.I)
    return name.strip().strip("\"'")


def _scripts_in(folder: str):
    """Yield script files below `folder` (depth-limited, skipping dependency folders)."""
    seen = 0
    base_depth = folder.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        if root.rstrip("\\/").count(os.sep) - base_depth >= _SCAN_DEPTH:
            dirs[:] = []
        for f in files:
            seen += 1
            if seen > _MAX_SCAN:
                return
            if os.path.splitext(f)[1].lower() in SCRIPT_EXTS:
                yield os.path.join(root, f)


def _find_scripts(name: str, folders: list[str]) -> list[str]:
    """Real paths of allowed scripts matching `name`. Anything outside the allowed folders is dropped."""
    folders = [f for f in folders if os.path.isdir(f)]
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str):
        rp = os.path.realpath(p)
        key = os.path.normcase(rp)
        if key in seen or not os.path.isfile(rp):
            return
        if os.path.splitext(rp)[1].lower() not in SCRIPT_EXTS:
            return
        if not any(_is_inside(rp, f) for f in folders):
            return
        seen.add(key)
        out.append(rp)

    if re.search(r"[\\/]|^[a-zA-Z]:", name):
        add(os.path.expandvars(os.path.expanduser(name)))
        return out
    ext = os.path.splitext(name)[1].lower()
    want_file = name.lower() if ext in SCRIPT_EXTS else None
    want_stem = _norm(name)
    for f in folders:
        for p in _scripts_in(f):
            base = os.path.basename(p)
            if want_file is not None:
                if base.lower() == want_file or _norm(base) == _norm(want_file):
                    add(p)
            elif _norm(os.path.splitext(base)[0]) == want_stem:
                add(p)
    return out


def _interpreter(path: str) -> tuple[list[str] | None, str]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        py = shutil.which("python") or shutil.which("python3")
        if py:
            return [py, path], ""
        pyl = shutil.which("py")
        if pyl:
            return [pyl, "-3", path], ""
        return None, "Python isn't on the PATH, so I can't run that."
    if ext == ".js":
        node = shutil.which("node")
        return ([node, path], "") if node else (None, "Node.js isn't on the PATH, so I can't run that.")
    if ext == ".ps1":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        return ([ps, "-NoProfile", "-File", path], "") if ps else (None, "PowerShell isn't available.")
    if ext in (".bat", ".cmd"):
        return ([path], "") if _IS_WIN else (None, "Batch files only run on Windows.")
    return None, "I don't know how to run that kind of file."


def _push(kind: str, title: str, subtitle: str | None = None, phase: str = "info", ms: int = 4000) -> None:
    try:
        import ui_bridge
        ui_bridge.push_activity(kind, title, phase=phase, subtitle=subtitle, duration_ms=ms)
    except Exception:
        pass


def _say_in_chat(text: str) -> None:
    try:
        import ui
        ui.print_chat("lyra", text)
    except Exception:
        pass


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if _IS_WIN:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=_NO_WINDOW, timeout=10)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_worker(proc: subprocess.Popen, name: str, timeout_s: int) -> None:
    """Read the output, stream the newest line to the island, summarise at the end."""
    global _running, _last_output
    tail: deque[str] = deque(maxlen=200)
    timed_out = {"v": False}

    def _timeout():
        timed_out["v"] = True
        _kill_tree(proc)

    timer = threading.Timer(timeout_s, _timeout)
    timer.daemon = True
    timer.start()
    last_push = 0.0
    last_line = ""
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = _strip_ansi(raw).rstrip()
            if not line.strip():
                continue
            tail.append(line)
            last_line = line.strip()
            now = time.monotonic()
            if now - last_push >= 0.5:
                last_push = now
                _push("dev_run", last_line[:80], subtitle=f"{name} - running", phase="info", ms=3000)
        rc = proc.wait()
    except Exception as e:  # noqa: BLE001
        rc = -1
        tail.append(f"(output reading failed: {e})")
    finally:
        timer.cancel()
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass
        with _run_lock:
            _running = None
    ok = rc == 0 and not timed_out["v"]
    text = "\n".join(tail)
    _last_output = {"source": name, "ok": ok, "text": text, "ts": time.time()}
    if timed_out["v"]:
        title, summary = f"{name} stopped", f"{name} ran longer than {timeout_s} seconds, so I stopped it."
    elif ok:
        title, summary = f"{name} finished", f"{name} finished." + (f" Last line: {last_line[:120]}" if last_line else "")
    else:
        title, summary = f"{name} failed", f"{name} failed with exit code {rc}." + (f" Last line: {last_line[:120]}" if last_line else "") + " Say explain this error if you want help."
    _push("dev_run", title, subtitle=(last_line[:120] if last_line else None), phase=("success" if ok else "fail"), ms=6000)
    _say_in_chat(summary)


def _start_run(path: str) -> tuple[bool, str]:
    global _running
    argv, err = _interpreter(path)
    if argv is None:
        return False, err
    name = os.path.basename(path)
    with _run_lock:
        if _running is not None:
            return False, f"{_running['name']} is still running. I'll run one script at a time."
        try:
            timeout_s = int(_load().get("run_timeout_s") or _DEFAULT_TIMEOUT_S)
        except Exception:
            timeout_s = _DEFAULT_TIMEOUT_S
        try:
            proc = subprocess.Popen(
                argv, cwd=os.path.dirname(path), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=_NO_WINDOW,
            )
        except Exception as e:  # noqa: BLE001
            return False, f"I couldn't start {name} - {e}"
        _running = {"name": name, "proc": proc}
    _push("dev_run", f"Running {name}", subtitle="Starting...", phase="start", ms=3000)
    threading.Thread(target=_run_worker, args=(proc, name, timeout_s), daemon=True, name="dev-run").start()
    return True, f"Running {name}. I'll show the latest output on the island."


def _run_script(c: str, engine_context) -> str | None:
    m = _RUN.match(c)
    if not m:
        return None
    name = _spoken_name(m.group("name"))
    explicit = bool(m.group("kw") or m.group("kw2")) or os.path.splitext(name)[1].lower() in SCRIPT_EXTS
    folders = _load()["allowed_folders"]
    if not folders:
        if not explicit:
            return None                   # "run routine Y" and the like: other modules
        return "No folders are allowed for scripts yet. Say allow scripts in, then a folder."
    found = _find_scripts(name, folders)
    if not found:
        if not explicit:
            return None
        return f"I couldn't find a script called {name} in your allowed folders."
    if len(found) > 1:
        shown = "; ".join(found[:3])
        return f"I found {len(found)} scripts called {name}: {shown}. Say the full path."
    path = found[0]
    ok, reason = _confirm("dev_run_script", f"run the script {os.path.basename(path)} from {os.path.dirname(path)}", engine_context)
    if not ok:
        return reason or "Okay, I won't run it."
    # Re-check right before launching: the allowlist and the file must still agree.
    if not any(_is_inside(path, f) for f in _load()["allowed_folders"]):
        return "That script is no longer in an allowed folder."
    started, msg = _start_run(path)
    _trace(f"action: dev run '{path}' -> {'started' if started else msg}")
    return msg


# ── git ──────────────────────────────────────────────────────────────────────────────────────

_PUSH = re.compile(r"^(?:git\s+push\b.*|push\s+(?:my\s+|the\s+)?(?:changes|commits?|code|branch|repo|it)\b.*|push\s+to\s+(?:github|origin|remote|master|main)\b.*)$", re.I)
_GIT_STATUS = re.compile(
    r"^(?:(?:show\s+(?:me\s+)?|what(?:'s|\s+is)\s+)?(?:the\s+)?git\s+status)"
    r"(?:\s+(?:for|of|in|on)\s+(?:the\s+)?(?:project\s+)?(?P<proj>.+?))?(?:\s+project)?$", re.I)
_GIT_COMMIT = re.compile(
    r"^(?:git\s+)?commit(?:\s+(?:all|my|the))?(?:\s+(?:changes|code|work|files))?\s+(?:with|using)\s+(?:the\s+)?"
    r"(?:commit\s+)?message(?:[\s:,-]+(?P<msg>.+))?$", re.I)


def _git(repo: str, *args: str, timeout: int = 30) -> tuple[int, str]:
    git = shutil.which("git")
    if not git:
        return 127, "Git isn't installed or isn't on the PATH."
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat")
    try:
        r = subprocess.run([git, "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env, stdin=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return 124, "git took too long and was stopped."
    except Exception as e:  # noqa: BLE001
        return 1, str(e)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def _status_info(repo: str) -> tuple[dict | None, str]:
    rc, out = _git(repo, "status", "--porcelain=v1", "-b")
    if rc != 0:
        if "not a git repository" in out.lower():
            return None, "That project isn't a git repository."
        return None, f"git status failed - {out[:160]}"
    branch, staged, modified, untracked, conflicts, files = "", 0, 0, 0, 0, []
    for line in out.splitlines():
        if line.startswith("## "):
            branch = line[3:].split("...")[0].strip()
            ab = re.search(r"\[(.+)\]", line)
            if ab:
                branch += f" ({ab.group(1)})"
            continue
        if len(line) < 4:
            continue
        x, y, path = line[0], line[1], line[3:]
        if x == "?" and y == "?":
            untracked += 1
        elif x == "U" or y == "U" or (x == "A" and y == "A") or (x == "D" and y == "D"):
            conflicts += 1
            files.append(path)
        else:
            if x not in " ?":
                staged += 1
            if y not in " ?":
                modified += 1
            files.append(path)
    tracked = len(files)
    return {"branch": branch, "staged": staged, "modified": modified, "untracked": untracked,
            "conflicts": conflicts, "tracked": tracked, "files": files}, ""


def _git_status(m, engine_context) -> str:
    pick, err = _active_project(m.group("proj"))
    if not pick:
        return err
    name, repo = pick
    info, err = _status_info(repo)
    if info is None:
        return err
    parts = []
    if info["staged"]:
        parts.append(f"{info['staged']} staged")
    if info["modified"]:
        parts.append(f"{info['modified']} modified")
    if info["untracked"]:
        parts.append(f"{info['untracked']} untracked")
    if info["conflicts"]:
        parts.append(f"{info['conflicts']} with conflicts")
    _update(lambda cfg: cfg.__setitem__("last_project", name))
    head = f"{name} is on branch {info['branch'] or 'unknown'}"
    if not parts:
        return head + " and the working tree is clean."
    names = [os.path.basename(f.split(' -> ')[-1].strip('"')) for f in info["files"][:4]]
    tail = f" Changed: {_speak_list(names)}." if names else ""
    return f"{head}: {_speak_list(parts)}.{tail}"


def _clean_message(raw: str) -> str:
    msg = re.sub(r"[\x00-\x1f]", " ", raw or "")
    msg = re.sub(r"\s+", " ", msg).strip().strip("\"'\u201c\u201d\u2018\u2019").strip()
    return msg[:200]


def _git_commit(m, engine_context) -> str:
    global _last_output
    msg = _clean_message(m.group("msg") or "")
    if len(msg) < 3:
        return "What should the commit message say?"
    pick, err = _active_project(None)
    if not pick:
        return err
    name, repo = pick
    info, err = _status_info(repo)
    if info is None:
        return err
    if info["conflicts"]:
        return f"{name} has merge conflicts. Resolve them first, then ask again."
    if info["tracked"] == 0:
        extra = f" There are {info['untracked']} untracked files, which I leave alone." if info["untracked"] else ""
        return f"Nothing to commit in {name}.{extra}"
    n = info["tracked"]
    ok, reason = _confirm(
        "dev_git_commit",
        f"commit {n} changed file{'s' if n != 1 else ''} in {name} on {info['branch'] or 'the current branch'} with the message \"{msg}\"",
        engine_context)
    if not ok:
        return reason or "Okay, I won't commit."
    rc, out = _git(repo, "commit", "-a", "-m", msg, timeout=60)
    if rc != 0:
        _last_output = {"source": "git commit", "ok": False, "text": out, "ts": time.time()}
        first = next((ln for ln in out.splitlines() if ln.strip()), "git commit failed")
        return f"The commit failed - {first[:160]}. Say explain this error if you want help."
    rc2, short = _git(repo, "rev-parse", "--short", "HEAD")
    h = f" ({short})" if rc2 == 0 and short else ""
    extra = f" {info['untracked']} untracked files were left out." if info["untracked"] else ""
    _update(lambda cfg: cfg.__setitem__("last_project", name))
    _trace(f"action: dev git commit in '{name}'")
    return f"Committed {n} file{'s' if n != 1 else ''} in {name}{h}. I haven't pushed.{extra}"


def _refuse_push(m, engine_context) -> str:
    return "I never push. Please run git push yourself when you're ready."


# ── entry point ──────────────────────────────────────────────────────────────────────────────

def _handlers():
    return (
        (_PUSH, _refuse_push),
        (_EXPLAIN, lambda m, ctx: _explain_error(m.string, ctx)),
        (_LIST_PROJECTS, _list_projects),
        (_ADD_PROJECT, _add_project),
        (_REMOVE_PROJECT, _remove_project),
        (_ALLOW_FOLDER, _allow_folder),
        (_DISALLOW_FOLDER, _disallow_folder),
        (_LIST_FOLDERS, _list_folders),
        (_GIT_STATUS, _git_status),
        (_GIT_COMMIT, _git_commit),
        (_OPEN, lambda m, ctx: _open_project(m.string, ctx)),
        (_RUN, lambda m, ctx: _run_script(m.string, ctx)),
    )


def try_dev(command: str, engine_context=None) -> str | None:
    """The single entry point hooked into actions.try_handle_action(). Never raises."""
    try:
        if not _enabled():
            return None
        c = _clean(command)
        if not c:
            return None
        for pattern, handler in _handlers():
            m = pattern.match(c)
            if not m:
                continue
            try:
                reply = handler(m, engine_context)
            except Exception as e:  # noqa: BLE001
                _log().warning("dev helper failed: %s", e)
                return f"The coding helper hit a problem - {e}"
            if reply:
                return reply
        return None
    except Exception as e:  # noqa: BLE001
        try:
            _log().warning("dev helper: %s", e)
        except Exception:
            pass
        return None
