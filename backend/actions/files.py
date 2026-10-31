"""Lyra — filesystem actions: create/delete/rename files & folders, list dirs."""

import os
import re
import shutil
import subprocess

import memory

# ---------------------------------------------------------------------------

COMMON_LOCATIONS = {
    "desktop": lambda: os.path.join(os.path.expanduser("~"), "Desktop"),
    "documents": lambda: os.path.join(os.path.expanduser("~"), "Documents"),
    "my documents": lambda: os.path.join(os.path.expanduser("~"), "Documents"),
    "downloads": lambda: os.path.join(os.path.expanduser("~"), "Downloads"),
    "pictures": lambda: os.path.join(os.path.expanduser("~"), "Pictures"),
    "music": lambda: os.path.join(os.path.expanduser("~"), "Music"),
    "videos": lambda: os.path.join(os.path.expanduser("~"), "Videos"),
    "home": lambda: os.path.expanduser("~"),
}
DRIVE_LETTER_PATTERN = re.compile(r"^([a-zA-Z]+)\s*drive$")

# Google STT often mishears a single spoken letter — this maps common
# mishearings (and phonetic spellings) back to the actual drive letter, e.g.
# "the deer drive" -> "D:\", "the sea drive" -> "C:\".
_DRIVE_LETTER_ALIASES = {
    "a": "a", "ay": "a",
    "b": "b", "bee": "b", "be": "b",
    "c": "c", "cee": "c", "see": "c", "sea": "c",
    "d": "d", "dee": "d", "deer": "d",
    "e": "e", "ee": "e",
    "f": "f", "ef": "f",
    "g": "g", "gee": "g",
    "h": "h", "aitch": "h",
    "i": "i", "eye": "i",
    "j": "j", "jay": "j",
    "k": "k", "kay": "k",
    "l": "l", "el": "l",
    "m": "m", "em": "m",
    "n": "n", "en": "n",
    "o": "o", "oh": "o",
    "p": "p", "pee": "p",
    "q": "q", "cue": "q", "queue": "q",
    "r": "r", "are": "r",
    "s": "s", "es": "s",
    "t": "t", "tee": "t",
    "u": "u", "you": "u",
    "v": "v", "vee": "v",
    "w": "w",
    "x": "x", "ex": "x", "eks": "x",
    "y": "y", "why": "y",
    "z": "z", "zee": "z", "zed": "z",
}


def resolve_location(text: str):
    text = text.strip().lower().rstrip(".")
    text = re.sub(r"^(the|my)\s+", "", text).strip()
    remembered = memory.get_path(text)
    if remembered:
        return remembered
    if text in COMMON_LOCATIONS:
        return COMMON_LOCATIONS[text]()
    m = DRIVE_LETTER_PATTERN.match(text)
    if m:
        letter = _DRIVE_LETTER_ALIASES.get(m.group(1).lower())
        if letter:
            return f"{letter.upper()}:\\"
    if ":" in text or "\\" in text or "/" in text:
        return os.path.normpath(os.path.expandvars(text))
    return None


_PROTECTED_PATH_PREFIXES = [
    os.path.expandvars(r"%WINDIR%").lower(),
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\programdata",
    r"c:\users\default",
    r"c:\windows",
    r"c:\boot",
]


def is_protected_path(path: str) -> bool:
    p = os.path.normpath(path).lower()
    if re.match(r"^[a-z]:\\?$", p):
        return True
    return any(p == prefix or p.startswith(prefix + os.sep) for prefix in _PROTECTED_PATH_PREFIXES)


def create_folder(path: str):
    path = os.path.normpath(path)
    if is_protected_path(path):
        return False, "that's a protected system location, so I won't create folders there"
    try:
        os.makedirs(path, exist_ok=True)
        return True, path
    except Exception as e:
        return False, str(e)


def create_text_file(path: str, content: str = ""):
    path = os.path.normpath(path)
    if is_protected_path(path):
        return False, "that's a protected system location, so I won't create files there"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        mode = "a" if os.path.exists(path) else "w"
        with open(path, mode, encoding="utf-8") as f:
            if content:
                f.write(content + "\n")
        return True, path
    except Exception as e:
        return False, str(e)


def find_in_common_locations(name: str):
    """Look for a bare filename/foldername (no path/drive given) across the
    common user folders. Returns the first match, or None."""
    for get_dir in COMMON_LOCATIONS.values():
        candidate = os.path.join(get_dir(), name)
        if os.path.exists(candidate):
            return candidate
    return None


def delete_path(path: str):
    """Delete a file or folder (recursively). Refuses protected paths."""
    path = os.path.normpath(path)
    if is_protected_path(path):
        return False, "that's a protected system location, so I won't delete anything there"
    if not os.path.exists(path):
        return False, f"I couldn't find anything at {path}"
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True, path
    except Exception as e:
        return False, str(e)


def rename_path(old: str, new: str):
    old = os.path.normpath(old)
    new = os.path.normpath(new)
    if is_protected_path(old) or is_protected_path(new):
        return False, "that's a protected system location, so I won't rename anything there"
    if not os.path.exists(old):
        return False, f"I couldn't find {old}"
    try:
        os.rename(old, new)
        return True, new
    except Exception as e:
        return False, str(e)


def open_explorer_at(path: str) -> None:
    try:
        os.startfile(path)
    except Exception:
        subprocess.Popen(f'explorer "{path}"', shell=True)


def list_directory(path: str, max_entries: int = 30) -> str:
    """List contents of a directory — used so users can ask 'what's on my desktop'."""
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return f"{path} isn't a directory I can read."
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return f"I don't have permission to list {path}."
    except Exception as e:
        return f"I couldn't list that directory: {e}"
    if not entries:
        return f"{path} is empty."
    if len(entries) > max_entries:
        entries = entries[:max_entries] + [f"...and {len(entries) - max_entries} more"]
    return f"In {path}: " + ", ".join(entries)


FOLDER_CREATE_PATTERN = re.compile(
    r"(?:create|make)\s+(?:a\s+|new\s+)*folder\s+(?:called\s+|named\s+)?(?P<name>.+?)\s+(?:on|in)\s+(?P<location>.+)",
    re.IGNORECASE,
)
FOLDER_CREATE_NO_NAME_PATTERN = re.compile(
    r"(?:create|make)\s+(?:a\s+|new\s+)*folder\s+(?:on|in)\s+(?P<location>.+)", re.IGNORECASE
)
FILE_CREATE_PATTERN = re.compile(
    r"(?:create|make)\s+(?:a\s+|new\s+)*(?:text\s+)?file\s+(?:called\s+|named\s+)?(?P<name>.+?)\s+(?:on|in)\s+(?P<location>.+)",
    re.IGNORECASE,
)
WRITE_TO_FILE_PATTERN = re.compile(
    r"write\s+(?:['\"](?P<quoted_content>.+?)['\"]|(?P<content>.+?))\s+(?:to|in)(?:to)?\s+(?:the\s+)?file\s+(?P<name>.+)",
    re.IGNORECASE,
)
DELETE_PATTERN = re.compile(
    r"(?:delete|remove|trash)\s+(?:the\s+)?(?:folder|file|directory)?\s*(?P<path>.+)$", re.IGNORECASE
)
RENAME_PATTERN = re.compile(
    r"rename\s+(?:the\s+)?(?:file|folder|directory)?\s*(?P<old>.+?)\s+to\s+(?P<new>.+)$", re.IGNORECASE
)
LIST_DIR_PATTERN = re.compile(
    r"(?:what(?:'s)?\s+)?(?:in|on|at)\s+(?:my\s+)?(?P<location>desktop|documents?|downloads|pictures|music|videos|home|D:[\w\\\s]*|C:[\w\\\s]*|E:[\w\\\s]*)",
    re.IGNORECASE,
)
EXPLORER_OPEN_PATTERN = re.compile(
    r"open\s+(?:file\s+|windows\s+)*explorer(?:\s+(?:to|at|on)\s+(?P<location>.+))?", re.IGNORECASE
)

