"""Lyra — create files and folders, find them by name, open them. Voice / typed commands and agent tools.

  create   "create a bat file called backup"                -> backup.bat on the Desktop (no place said = Desktop)
           "make a python file test in documents"           -> Documents\\test.py
           "create a folder school stuff on D:\\projects"    -> that exact place (made if it isn't there yet)
           "create a text file notes that says buy milk"    -> notes.txt with that text
  find     "find the file called budget", "find budget.xlsx", "where is my resume file", "find the folder photos"
  open     "open the file budget", "open budget.xlsx", "open the downloads folder", "open the first one"

Every reply is built from what actually happened on disk (checked after the action), never assumed.
Programs and scripts (.bat .cmd .ps1 .py .js .vbs .msi ...) are never run by "open": they are shown in Explorer.
Same entry point as the other action modules: try_file_ops(command) -> reply text, or None when it isn't about files.
"""
from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ── where things live ────────────────────────────────────────────────────────────────────────

_KNOWN_GUIDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}
_ALIASES = {"my documents": "documents", "document": "documents", "docs": "documents", "download": "downloads",
            "picture": "pictures", "photos": "pictures", "pics": "pictures", "video": "videos", "movies": "videos",
            "songs": "music", "desk top": "desktop", "the desktop": "desktop", "screen": "desktop"}


def _windows_known_folder(name: str) -> str | None:
    """The real folder Windows uses (follows OneDrive redirection of Desktop / Documents / Pictures)."""
    guid = _KNOWN_GUIDS.get(name)
    if not guid or sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        g = GUID()
        ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p("{" + guid + "}"), ctypes.byref(g))
        buf = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(buf)) != 0:
            return None
        path = buf.value
        ctypes.windll.ole32.CoTaskMemFree(buf)
        return path
    except Exception:  # noqa: BLE001
        return None


def known_dir(name: str) -> str:
    """desktop / documents / downloads / pictures / music / videos -> an existing folder path ('' if none)."""
    name = _ALIASES.get(name.lower().strip(), name.lower().strip())
    p = _windows_known_folder(name)
    if p and os.path.isdir(p):
        return p
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    leaf = name.capitalize()
    for c in (home / leaf, home / "OneDrive" / leaf):
        if c.is_dir():
            return str(c)
    return ""


def desktop_dir() -> str:
    return known_dir("desktop") or str(Path(os.environ.get("USERPROFILE") or Path.home()))


def _label(folder: str) -> str:
    """How to say a folder out loud: 'your Desktop', 'your Documents', or its path."""
    norm = os.path.normcase(os.path.normpath(folder))
    for n in ("desktop", "documents", "downloads", "pictures", "music", "videos"):
        d = known_dir(n)
        if d and os.path.normcase(os.path.normpath(d)) == norm:
            return f"your {n.capitalize()}"
    return folder


def _at(folder: str) -> str:
    """'on your Desktop', 'in your Documents', 'in D:\\projects'."""
    lab = _label(folder)
    return ("on " if lab == "your Desktop" else "in ") + lab


def _remembered(alias: str) -> str | None:
    """A folder the user taught Lyra ('remember this folder as projects')."""
    try:
        from engine.brain import memory
        return memory.get_path(alias)
    except Exception:  # noqa: BLE001
        return None


def _protected(path: str) -> bool:
    p0 = os.path.normpath(path)
    m = re.match(r"^([A-Za-z]):\\?$", p0)
    if m and m.group(1).upper() != (os.environ.get("SystemDrive", "C:")[:1] or "C").upper():
        return False                                        # the top of another drive (D:\) is fine; Windows' own drive is not
    if m:
        return True
    try:
        from engine.actions.files import is_protected_path
        return is_protected_path(path)
    except Exception:  # noqa: BLE001
        p = os.path.normpath(path).lower()
        return p.startswith((r"c:\windows", r"c:\program files", r"c:\programdata", r"c:\boot"))


_SKIP_DIRS = {"appdata", "windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
              "system volume information", "node_modules", ".git", "__pycache__", "venv", ".venv", "site-packages",
              ".cache", "$windows.~bt", "recovery", "msocache", "perflogs"}


def _roots_primary() -> list[str]:
    """Where people keep their things: the usual folders, OneDrive, and the folders Lyra indexes."""
    out: list[str] = []
    for n in _KNOWN_GUIDS:
        d = known_dir(n)
        if d:
            out.append(d)
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        d = os.environ.get(env)
        if d and os.path.isdir(d):
            out.append(d)
    try:
        from engine.features import file_index
        out += [f for f in file_index.folders() if os.path.isdir(f)]
    except Exception:  # noqa: BLE001
        pass
    norm = sorted({os.path.normpath(r) for r in out}, key=len)
    keep: list[str] = []
    for r in norm:                                          # drop folders that sit inside another root
        if not any(os.path.normcase(r).startswith(os.path.normcase(k) + os.sep) for k in keep):
            keep.append(r)
    return keep


def _roots_wide() -> list[str]:
    """Everything else worth looking in when the usual folders have nothing: the whole user profile, then the drives."""
    roots = [str(Path(os.environ.get("USERPROFILE") or Path.home()))]
    if sys.platform == "win32":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            d = f"{letter}:\\"
            if os.path.isdir(d):
                roots.append(d)
    return roots


# ── find by name ─────────────────────────────────────────────────────────────────────────────

_EXT_WORDS = {"pdf": (".pdf",), "word": (".docx", ".doc"), "docx": (".docx",), "excel": (".xlsx", ".xls", ".xlsm"),
              "xlsx": (".xlsx",), "powerpoint": (".pptx", ".ppt"), "pptx": (".pptx",), "txt": (".txt",), "csv": (".csv",),
              "python": (".py",), "zip": (".zip",), "mp3": (".mp3",), "mp4": (".mp4",), "png": (".png",),
              "jpg": (".jpg", ".jpeg"), "jpeg": (".jpeg", ".jpg"), "bat": (".bat",), "json": (".json",)}


def _norm_query(q: str) -> str:
    q = re.sub(r"\s+dot\s+(\w{1,5})\s*$", r".\1", q.strip(), flags=re.I)        # "budget dot pdf" -> "budget.pdf"
    q = re.sub(r"\s*\.\s+(\w{1,5})\s*$", r".\1", q)                              # "budget. pdf"
    q = q.strip().strip("\"'“”").rstrip(".!?, ")
    return re.sub(r"\s+", " ", q)


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[\s_\-.()\[\],']+", s.lower()) if t]


def _split_query(q: str) -> tuple[str, tuple[str, ...]]:
    """'budget.xlsx' -> ('budget', ('.xlsx',));  'budget pdf' -> ('budget', ('.pdf',));  'budget' -> ('budget', ())."""
    q = _norm_query(q)
    m = re.match(r"^(.*\S)\.([A-Za-z0-9]{1,5})$", q)
    if m and (m.group(2).lower() in {e.lstrip(".") for v in _EXT_WORDS.values() for e in v}
              or re.fullmatch(r"[A-Za-z]{2,4}", m.group(2))):
        return m.group(1), ("." + m.group(2).lower(),)
    words = q.split()
    if len(words) > 1 and words[-1].lower() in _EXT_WORDS:
        return " ".join(words[:-1]), _EXT_WORDS[words[-1].lower()]
    return q, ()


def search_names(query: str, want: str = "any", limit: int = 6, budget: float = 12.0) -> list[dict]:
    """Files / folders whose NAME matches. want = file | folder | any. Looks in the usual folders first and only goes
    wider (user profile, drives) when nothing is found there. Best match first, then newest.
    Each row: {name, path, is_dir, mtime, score}."""
    stem_q, exts = _split_query(query)
    qtoks = _tokens(stem_q)
    if not qtoks:
        return []
    qnorm = " ".join(qtoks)
    deadline = time.monotonic() + budget
    stems: list[tuple[str, str, bool, float]] = []        # every name seen, for the fuzzy pass: (stem, path, is_dir, mtime)
    hits: dict[str, dict] = {}

    def consider(name: str, path: str, is_dir: bool, mtime: float) -> None:
        if want == "file" and is_dir or want == "folder" and not is_dir:
            return
        base, ext = (name, "") if is_dir else os.path.splitext(name)
        if exts and (is_dir or ext.lower() not in exts):
            return
        if len(stems) < 80000:
            stems.append((" ".join(_tokens(base)), path, is_dir, mtime))
        ntoks = _tokens(base)
        joined = " ".join(ntoks)
        if not all(t in joined for t in qtoks):
            return
        if joined == qnorm:
            score = 100
        elif joined.startswith(qnorm):
            score = 80
        elif all(t in ntoks for t in qtoks):
            score = 65
        else:
            score = 50
        hits[path] = {"name": name, "path": path, "is_dir": is_dir, "mtime": mtime, "score": score}

    def walk(root: str) -> None:
        stack = [root]
        while stack and time.monotonic() < deadline:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        try:
                            is_dir = e.is_dir(follow_symlinks=False)
                            low = e.name.lower()
                            if is_dir and (low in _SKIP_DIRS or (low.startswith(".") and low not in (".config",))):
                                continue
                            consider(e.name, e.path, is_dir, e.stat(follow_symlinks=False).st_mtime)
                            if is_dir:
                                stack.append(e.path)
                        except OSError:
                            continue
            except OSError:
                continue

    for stage in (_roots_primary, _roots_wide):
        for root in stage():
            walk(root)
        if hits:
            break
    if not hits and stems:                                # speech-to-text often mishears names: try close spellings
        pool: dict[str, list] = {}
        for s, p, d, m in stems:
            pool.setdefault(s, []).append((p, d, m))
        for close in difflib.get_close_matches(qnorm, list(pool), n=limit * 2, cutoff=0.74):
            for p, d, m in pool[close]:
                hits[p] = {"name": os.path.basename(p), "path": p, "is_dir": d, "mtime": m,
                           "score": int(40 * difflib.SequenceMatcher(None, qnorm, close).ratio())}
    return sorted(hits.values(), key=lambda r: (-r["score"], -r["mtime"]))[:limit]


def _short_folder(path: str) -> str:
    parts = Path(path).parent.parts[-2:]
    return " › ".join(p.strip("\\/") for p in parts if p.strip("\\/"))


# ── open ─────────────────────────────────────────────────────────────────────────────────────

# Opening these would RUN them (or hand them to a script host). Lyra shows them in Explorer instead.
_RUNS_CODE = {".exe", ".bat", ".cmd", ".com", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf",
              ".wsh", ".scr", ".reg", ".jar", ".hta", ".cpl", ".dll", ".py", ".pyw", ".sh", ".lnk"}


def reveal_in_explorer(path: str) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])


def open_path(path: str) -> tuple[bool, str]:
    """Open a file / folder with its default app. Returns (ok, 'opened' | 'shown' | error). Code is only shown."""
    try:
        if not os.path.exists(path):
            return False, "it isn't there any more"
        if os.path.isfile(path) and Path(path).suffix.lower() in _RUNS_CODE:
            reveal_in_explorer(path)
            return True, "shown"
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
        return True, "opened"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:100]


_last: list[dict] = []
_last_at = 0.0
_TTL = 300.0


def _remember(rows: list[dict]) -> None:
    global _last, _last_at
    _last, _last_at = [dict(r) for r in rows], time.time()


def _show_on_island(query: str, rows: list[dict]) -> None:
    try:
        from engine.bridge import ui_bridge
        ui_bridge.push_search_results(query, [
            {"title": r["name"], "description": "Folder" if r["is_dir"] else "File", "url": r["path"],
             "source": _short_folder(r["path"]), "provider": "files"} for r in rows], provider="files")
    except Exception:  # noqa: BLE001
        pass


def _dismiss_island() -> None:
    try:
        from engine.bridge import ui_bridge
        ui_bridge.push_search_dismiss()
    except Exception:  # noqa: BLE001
        pass


def find(query: str, want: str = "any") -> str:
    """Search by name, show the matches on the island, remember them for 'open the first one'."""
    rows = search_names(query, want)
    what = {"file": "file", "folder": "folder"}.get(want, "file or folder")
    if not rows:
        return f"I couldn't find a {what} called {_norm_query(query)}. Try part of the name, or tell me which folder it's in."
    _remember(rows)
    _show_on_island(_norm_query(query), rows)
    if len(rows) == 1:
        r = rows[0]
        return f"I found {r['name']} {_at(os.path.dirname(r['path']))}. Say open it to open it."
    return f"I found {len(rows)} matches for {_norm_query(query)}, best first. Say open the first one, or open number 2."


def open_by_name(query: str, want: str = "file") -> str:
    rows = search_names(query, want)
    q = _norm_query(query)
    if not rows:
        return f"I couldn't find a {'folder' if want == 'folder' else 'file'} called {q}. Try part of the name, or tell me which folder it's in."
    top = rows[0]
    tied = [r for r in rows if r["score"] == top["score"]]
    if len(tied) > 1:                                       # equally good matches: ask instead of guessing
        _remember(rows)
        _show_on_island(q, rows)
        return f"I found {len(tied)} that match {q} equally well. Say open the first one, or open number 2."
    ok, how = open_path(top["path"])
    if not ok:
        return f"I found {top['name']} but couldn't open it, {how}."
    where = _label(os.path.dirname(top["path"]))
    if how == "shown":
        return f"{top['name']} is a program or script, so I showed it in Explorer instead of running it. It's {_at(os.path.dirname(top['path']))}."
    return f"Opening {top['name']} from {where}."


_ORD = {"top": 1, "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
        "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "last": -1}


def _open_nth(n: int | None, word: str | None) -> str | None:
    """'open the first one' / 'open number 2' / 'open it' for the matches from my last search. None = not mine."""
    if not _last or time.time() - _last_at > _TTL:
        return None
    try:                                                    # a newer content search ("find files about...") owns "open the first one"
        from engine.features import file_index
        if getattr(file_index, "_last_files_at", 0.0) > _last_at:
            return None
    except Exception:  # noqa: BLE001
        pass
    if word is None and n is None:
        idx = 0
        if len(_last) != 1:
            return None
    else:
        k = _ORD[word.lower()] if word else int(n)
        idx = len(_last) - 1 if k == -1 else k - 1
    if not 0 <= idx < len(_last):
        return f"I only showed {len(_last)} matches."
    r = _last[idx]
    ok, how = open_path(r["path"])
    if not ok:
        return f"I couldn't open {r['name']}, {how}."
    _dismiss_island()
    if how == "shown":
        return f"{r['name']} is a program or script, so I showed it in Explorer instead of running it."
    return f"Opening {r['name']}."


# ── create ───────────────────────────────────────────────────────────────────────────────────

# type word -> (extension, starting text). Only batch files get a first line: every .bat starts with it.
_TYPES: dict[str, tuple[str, str]] = {
    "bat": (".bat", "@echo off\r\n"), "batch": (".bat", "@echo off\r\n"), "cmd": (".cmd", "@echo off\r\n"),
    "python": (".py", ""), "py": (".py", ""), "text": (".txt", ""), "txt": (".txt", ""), "notepad": (".txt", ""),
    "markdown": (".md", ""), "md": (".md", ""), "json": (".json", ""), "html": (".html", ""), "css": (".css", ""),
    "javascript": (".js", ""), "js": (".js", ""), "powershell": (".ps1", ""), "ps1": (".ps1", ""),
    "shell": (".sh", ""), "sh": (".sh", ""), "csv": (".csv", ""), "word": (".docx", ""), "docx": (".docx", ""),
    "powerpoint": (".pptx", ""), "pptx": (".pptx", ""),
}
_TYPE_RE = "|".join(sorted(_TYPES, key=len, reverse=True))
_CREATE = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?(?:create|make|generate|new)\s+(?:me\s+)?(?:(?:a|an|the|new|another)\s+)*"
    r"(?:(?P<type>" + _TYPE_RE + r")\s+)?(?P<kind>files?|folders?|directory|directories|documents?|scripts?|presentations?)\b(?P<rest>.*)$",
    re.I)
_CREATE_TYPE_ONLY = re.compile(      # "create a bat called backup" (no 'file')
    r"^(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?(?:create|make|generate)\s+(?:me\s+)?(?:(?:a|an|the|new)\s+)*"
    r"(?P<type>bat|batch|cmd|py|ps1|docx|pptx|csv|json|markdown)\b(?P<rest>\s+(?:called|named)\b.*)$", re.I)
_PREP = r"(?:in|on|at|inside|into|under|within)"
_CONTENT = re.compile(
    r"\s+(?:with\s+(?:the\s+)?(?:content|contents|text)|containing|that\s+(?:says|contains|has|reads)|which\s+(?:says|contains)|"
    r"saying|and\s+(?:write|put|type))\s*[:,]?\s+(?P<c>.+)$", re.I)
_NAME_CALLED = re.compile(r"\b(?:called|named|titled|name\s+it)\s+(?P<n>.+?)(?=\s+" + _PREP + r"\s+\S|\s*$)", re.I)
_LOC = re.compile(r"(?:^|\s)" + _PREP + r"\s+(?:the\s+|my\s+)?(?P<loc>.+?)\s*$", re.I)
_BAD_WORDS = {"that", "which", "who", "for", "about", "using", "to", "so", "because", "where", "when", "how", "if",
              "and", "but", "can", "will", "should", "would", "from", "like"}
_BAD_CHARS = re.compile(r'[\\/:*?"<>|]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _clean_name(raw: str) -> str:
    n = _norm_query(raw)
    n = re.sub(r"^(?:called|named)\s+", "", n, flags=re.I).strip()
    return n.rstrip(". ")


def _find_folder_named(name: str) -> str | None:
    """An existing folder called `name` in the usual places (depth 3), newest first."""
    rows = search_names(name, "folder", limit=3, budget=4.0)
    exact = [r for r in rows if r["score"] >= 80]
    return exact[0]["path"] if exact else None


def resolve_dir(text: str) -> tuple[str | None, str]:
    """A spoken / typed place -> (folder path, '') or (None, why). Taught aliases, Desktop/Documents/..., drive
    letters, full paths, then a folder with that name in the usual places."""
    t = _norm_query(text)
    t = re.sub(r"\s+(?:folder|directory|please)$", "", t, flags=re.I).strip()
    t = re.sub(r"^(?:the|my)\s+", "", t, flags=re.I)
    if not t:
        return None, "no place"
    low = t.lower()
    if low in ("here", "this folder"):
        return desktop_dir(), ""
    alias = _remembered(low)
    if alias:
        return os.path.normpath(alias), ""
    key = _ALIASES.get(low, low)
    if key in _KNOWN_GUIDS:
        d = known_dir(key)
        return (d, "") if d else (None, f"I couldn't find your {key.capitalize()} folder")
    if key in ("home", "user folder", "my user folder"):
        return str(Path(os.environ.get("USERPROFILE") or Path.home())), ""
    if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/|~)", t) or re.search(r"[\\/]", t):       # an actual path: D:\work, \\server\x, /home/x, ~/x, docs/sub
        return os.path.normpath(os.path.expandvars(os.path.expanduser(t))), ""
    try:
        from engine.actions.files import resolve_location       # "d drive", "the deer drive" (speech slips), drive letters
        loc = resolve_location(t)
    except Exception:  # noqa: BLE001
        loc = None
    if loc:
        return loc, ""
    found = _find_folder_named(t)
    if found:
        return found, ""
    return None, f"I couldn't find a folder called {t}"


def _unique(folder: str, name: str, ext: str = "") -> str:
    """'New Folder', 'New Folder (2)', ... the first one that is free."""
    cand, n = name, 2
    while os.path.exists(os.path.join(folder, cand + ext)):
        cand = f"{name} ({n})"
        n += 1
    return cand + ext


def _write_file(path: str, ext: str, start: str, content: str) -> None:
    ext = ext.lower()
    if ext == ".docx":
        from docx import Document
        doc = Document()
        if content:
            doc.add_paragraph(content)
        doc.save(path)
    elif ext == ".pptx":
        from pptx import Presentation
        prs = Presentation()
        if content:
            slide = prs.slides.add_slide(prs.slide_layouts[0])
            slide.shapes.title.text = content
        prs.save(path)
    elif ext in (".bat", ".cmd", ".ps1"):
        body = start + (content.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n" if content else "")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(body)
    else:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(start + (content + "\n" if content else ""))


def _undo_hint(path: str) -> None:
    try:
        from engine.features import activity_log
        activity_log.set_undo({"type": "delete_path", "path": path})
    except Exception:  # noqa: BLE001
        pass


def create_item(kind: str, name: str = "", location: str = "", content: str = "", ftype: str = "") -> tuple[bool, str]:
    """Make a folder or file and check it exists. kind = folder | file. ftype = a _TYPES word ('bat', 'python', ...).
    No location = the Desktop. Returns (ok, sentence to say)."""
    folder_mode = kind == "folder"
    if location:
        folder, why = resolve_dir(location)
        if folder is None:
            return False, why + ". Tell me the full path, or say on the Desktop."
        explicit = True
    else:
        folder, explicit = desktop_dir(), False
    if _protected(folder):
        return False, "That's a protected system location, so I won't create anything there."
    name = _clean_name(name)
    ext, start = _TYPES.get(ftype.lower(), (".txt", "")) if not folder_mode else ("", "")
    if name and _BAD_CHARS.search(name) or name in (".", "..") or ".." in name:
        return False, "That name has characters Windows doesn't allow."
    if name and os.path.splitext(name)[0].lower() in _RESERVED:
        return False, "Windows doesn't allow that name."
    if not folder_mode:
        have = os.path.splitext(name)[1].lower() if name else ""
        if have and not ftype:                               # the name says the type: "notes.md", "run.bat"
            for e, s in _TYPES.values():
                if e == have:
                    ext, start = e, s
                    break
            else:
                ext, start = have, ""
            name = name[: -len(have)]
        elif have == ext:
            name = name[: -len(have)]
    label = "folder" if folder_mode else "file"
    try:
        os.makedirs(folder, exist_ok=True)
        if not name:                                         # nothing said: "New Folder", "New File.bat"
            leaf = _unique(folder, "New Folder" if folder_mode else "New File", ext)
        else:
            leaf = name + ext
            if os.path.exists(os.path.join(folder, leaf)):
                return False, f"There is already a {'folder' if os.path.isdir(os.path.join(folder, leaf)) else 'file'} called {leaf} {_at(folder)}."
        target = os.path.join(folder, leaf)
        if folder_mode:
            os.mkdir(target)
        else:
            _write_file(target, ext, start, content)
    except ImportError:
        return False, f"I need the {'python-docx' if ext == '.docx' else 'python-pptx'} package to make that kind of file."
    except Exception as e:  # noqa: BLE001
        return False, f"I couldn't create it, {str(e)[:100]}."
    if not os.path.exists(target):                           # never say it worked unless it is really there
        return False, "I tried, but the " + label + " isn't there. Windows may have blocked it."
    _undo_hint(target)
    msg = f"Created the {label} {leaf} {_at(folder)}."
    if explicit:
        try:
            reveal_in_explorer(target)
        except Exception:  # noqa: BLE001
            pass
    return True, msg


def parse_create(command: str) -> dict | None:
    """'create a bat file called backup in documents' -> {kind, ftype, name, location, content}. None = not a plain
    create request (e.g. 'make a python script that scrapes a website' is a coding request, not an empty file)."""
    text = re.sub(r"[.!?\s]+$", "", command.strip())
    text = re.sub(r"(?:[,\s]+(?:please|for\s+me|now|thanks|sir))+\s*$", "", text, flags=re.I)
    m = _CREATE.match(text)
    ftype = ""
    if m:
        kind_word = m.group("kind").lower()
        ftype = (m.group("type") or "").lower()
        rest = m.group("rest")
        if kind_word.startswith("document") and not ftype:
            ftype = "word"
        if kind_word.startswith("presentation"):
            ftype = ftype or "powerpoint"
        if kind_word.startswith("script") and not ftype:
            return None
        kind = "folder" if kind_word.startswith(("folder", "director")) else "file"
        if kind == "folder" and ftype:
            return None
    else:
        m2 = _CREATE_TYPE_ONLY.match(text)
        if not m2:
            return None
        ftype, rest, kind = m2.group("type").lower(), m2.group("rest"), "file"
    content = ""
    cm = _CONTENT.search(rest)
    if cm:
        content = cm.group("c").strip().strip("\"'“”")
        rest = rest[: cm.start()]
    rest = rest.strip()
    name, location = "", ""
    nm = _NAME_CALLED.search(rest)
    if nm:
        name = nm.group("n").strip()
        rest = (rest[: nm.start()] + " " + rest[nm.end():]).strip()
    lm = _LOC.search(rest)
    if lm:
        location = lm.group("loc").strip()
        rest = rest[: lm.start()].strip()
    if not name and rest:
        bare = re.sub(r"^(?:called|named)\s+", "", rest, flags=re.I).strip()
        words = re.findall(r"[\w'.\-]+", bare.lower())
        if len(words) > 6 or any(w in _BAD_WORDS for w in words):
            return None                                       # a request in a sentence, not a name: leave it to the AI
        name = bare
    elif rest:
        return None                                           # words left over besides the name and place: not a plain create
    return {"kind": kind, "ftype": ftype, "name": name, "location": location, "content": content}


# ── command routing ──────────────────────────────────────────────────────────────────────────

_PRE = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?", re.I)
_POST = re.compile(r"(?:[,\s]+(?:please|for\s+me|now|thanks|thank\s+you|sir))+\s*$", re.I)
_CONTENT_WORDS = r"(?:about|on|regarding|mentioning|containing|with|for|related|that|which|in|inside|from|of|to|by)"
_EXTS = (r"pdf|docx?|pptx?|xlsx?|xlsm|csv|txt|md|rtf|odt|png|jpe?g|gif|webp|svg|mp4|mkv|mov|avi|mp3|wav|flac|zip|rar|7z|"
         r"json|xml|py|html?|bat|cmd|ps1")
_FINDV = r"(?:find|locate|search\s+for|look\s+for|where\s+is|where's|where\s+are|show\s+me|get\s+me)"
_F1 = re.compile(r"^" + _FINDV + r"\s+(?:me\s+)?(?:all\s+)?(?:(?:the|my|a|an)\s+)?(?P<kind>files?|folders?|directory|directories)\s+"
                 r"(?:(?:called|named)\s+)?(?P<q>.+)$", re.I)
_F2 = re.compile(r"^" + _FINDV + r"\s+(?:me\s+)?(?:(?:the|my|a|an)\s+)?(?P<q>.+?)\s+(?P<kind>file|folder|directory)$", re.I)
_F3 = re.compile(r"^" + _FINDV + r"\s+(?:me\s+)?(?:(?:the|my)\s+)?(?P<q>[\w\-. ()']+\.(?:" + _EXTS + r"))$", re.I)
_F4 = re.compile(r"^(?:search|look)\s+(?:through\s+|in\s+)?(?:my\s+|the\s+)?(?:pc|computer|laptop|drives?|disk|hard\s+drive)\s+for\s+"
                 r"(?:(?:the|a)\s+)?(?:(?P<kind>files?|folders?)\s+)?(?:(?:called|named)\s+)?(?P<q>.+)$", re.I)
_O1 = re.compile(r"^(?:open|show|launch|start)\s+(?:me\s+)?(?:(?:the|my)\s+)?(?:file|document)\s+(?:(?:called|named)\s+)?(?P<q>.+)$", re.I)
_O2 = re.compile(r"^open\s+(?:(?:the|my)\s+)?(?P<q>[\w\-. ()']+\.(?:" + _EXTS + r"))$", re.I)
_O3 = re.compile(r"^(?:open|show|go\s+to)\s+(?:me\s+)?(?:(?:the|my)\s+)?(?:folder|directory)\s+(?:(?:called|named)\s+)?(?P<q>.+)$", re.I)
_O4 = re.compile(r"^(?:open|show|go\s+to)\s+(?:me\s+)?(?:(?:the|my)\s+)?(?P<q>.+?)\s+(?:folder|directory)$", re.I)
_NTH = re.compile(r"^(?:open|show)\s+(?:(?:the|that|this)\s+)?(?:(?P<w>top|first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|last)"
                  r"(?:\s+(?:one|file|result|match))?|(?:file|result|match|number|no\.?|#)\s*(?P<n>[1-9])|(?P<it>it|that|this)(?:\s+one)?)$", re.I)


def _clean(command: str) -> str:
    t = re.sub(r"[.!?\s]+$", "", (command or "").strip())
    t = _POST.sub("", t)
    return _PRE.sub("", t).strip()


def _kind(word: str | None) -> str:
    w = (word or "").lower()
    return "folder" if w.startswith(("folder", "director")) else "file" if w else "any"


def _try(command: str) -> str | None:
    text = _clean(command)
    if not text:
        return None
    # ── open "the first one" from the last search ──
    m = _NTH.match(text)
    if m:
        return _open_nth(int(m.group("n")) if m.group("n") else None, m.group("w"))
    # ── create ──
    spec = parse_create(text)
    if spec:
        return create_item(spec["kind"], spec["name"], spec["location"], spec["content"], spec["ftype"])[1]
    # ── find by name ──
    for pat in (_F1, _F2, _F3, _F4):
        m = pat.match(text)
        if not m:
            continue
        q = (m.groupdict().get("q") or "").strip(" ,")
        if not q or re.match(r"^" + _CONTENT_WORDS + r"\b", q, re.I):
            return None                                        # "find files about X" is the content search's job
        return find(q, _kind(m.groupdict().get("kind")))
    # ── open a folder ──
    for pat in (_O3, _O4):
        m = pat.match(text)
        if m:
            folder, _ = resolve_dir(m.group("q"))
            if folder and os.path.isdir(folder):
                ok, how = open_path(folder)
                return f"Opening {_label(folder)}." if ok else f"I couldn't open it, {how}."
            if pat is _O3:
                return open_by_name(m.group("q"), "folder")
            return None                                        # "open the spotify folder"-style phrases fall through to other handlers
    # ── open a file ──
    for pat in (_O1, _O2):
        m = pat.match(text)
        if m:
            return open_by_name(m.group("q").strip(" ,"), "file")
    return None


def try_file_ops(command: str) -> str | None:
    """Hook for actions.try_handle_action / quick: never raises. None = not a file command."""
    try:
        return _try(command)
    except Exception as e:  # noqa: BLE001
        try:
            from engine.bridge import ui
            ui.trace(f"file_ops skipped: {e}")
        except Exception:  # noqa: BLE001
            pass
        return None
