"""Lyra — self-code access: read, list, and carefully edit her own project files.

Safety:
  - Only files under the Lyra package root
  - No writing outside that tree
  - Blocked: .db, secrets-looking names, __pycache__, .pyc
  - Writes create a .bak backup first
  - Optional syntax check for .py after write
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import time
from pathlib import Path

import ui

log = ui.log

# Package root = directory of this file
ROOT = Path(__file__).resolve().parent

_BLOCKED_SUFFIXES = {".db", ".pyc", ".pyo", ".pyd", ".dll", ".exe", ".so"}
_BLOCKED_NAMES = {"lyrr_memory.db", ".env", "credentials.json"}
_BLOCKED_PARTS = {"__pycache__", ".git", "venv", ".venv", "node_modules"}

LIST_CODE_PATTERN = re.compile(
    r"\b(?:list\s+(?:your\s+)?(?:code|files|source)|show\s+(?:your\s+)?(?:file\s+)?(?:tree|structure)|"
    r"what\s+files\s+do\s+you\s+have)\b",
    re.I,
)
READ_CODE_PATTERN = re.compile(
    r"\b(?:(?:read|show|open)\s+(?:your\s+)?(?:code\s+(?:in|for)\s+|file\s+)?(?P<path>[\w./\\-]+\.py)|"
    r"what(?:'s|\s+is)\s+in\s+(?P<path2>[\w./\\-]+\.py))\b",
    re.I,
)
# "add a function to X" / "edit your code to ..." / "change your code so that ..."
EDIT_INTENT_PATTERN = re.compile(
    r"\b(?:edit\s+your\s+code|change\s+your\s+code|modify\s+your\s+code|"
    r"update\s+your\s+code|add\s+(?:a\s+)?(?:function|feature|command)|"
    r"write\s+(?:a\s+)?(?:function|code)|fix\s+your\s+code|"
    r"add\s+to\s+your\s+(?:code|source))\b",
    re.I,
)


def _is_safe_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = ROOT.resolve()
        if not str(resolved).startswith(str(root)):
            return False
        if resolved.name in _BLOCKED_NAMES:
            return False
        if resolved.suffix.lower() in _BLOCKED_SUFFIXES:
            return False
        for part in resolved.parts:
            if part in _BLOCKED_PARTS:
                return False
        return True
    except Exception:
        return False


def resolve_project_path(rel: str) -> Path | None:
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        return None
    # Allow "actions/apps.py" or just "apps.py" (search)
    candidate = ROOT / rel
    if candidate.exists() and _is_safe_path(candidate):
        return candidate
    # Search by name
    name = Path(rel).name
    for p in ROOT.rglob(name):
        if p.is_file() and _is_safe_path(p):
            return p
    return None


def list_source_files(max_files: int = 80) -> list[str]:
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        if not _is_safe_path(p):
            continue
        if p.suffix.lower() not in {".py", ".json", ".md", ".txt"}:
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        out.append(rel)
        if len(out) >= max_files:
            break
    return out


def read_file(rel: str, max_chars: int = 12000) -> str:
    path = resolve_project_path(rel)
    if not path:
        return f"I couldn't find “{rel}” in my project."
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n… [truncated, {len(text)} chars total]"
        rel_out = str(path.relative_to(ROOT)).replace("\\", "/")
        return f"// file: {rel_out}\n{text}"
    except Exception as e:
        return f"Couldn't read {rel}: {e}"


def write_file(rel: str, content: str, engine_context=None) -> str:
    """Write content to a project file after permission confirm. Creates .bak."""
    path = resolve_project_path(rel)
    if path is None:
        # Allow creating new .py under root / known folders only
        rel_clean = (rel or "").strip().replace("\\", "/").lstrip("/")
        if not rel_clean.endswith(".py") or ".." in rel_clean:
            return "I can only create .py files inside my project."
        path = ROOT / rel_clean
        if not _is_safe_path(path):
            return "That path isn't allowed."
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not _is_safe_path(path):
        return "That file is protected."

    from permissions import PermissionManager
    rel_out = str(path.relative_to(ROOT)).replace("\\", "/")
    allowed, reason = PermissionManager().check_permission(
        "self_code_write",
        f"modify my own file {rel_out}",
        engine_context,
    )
    if not allowed:
        return reason or "Okay, I won't change my code."

    try:
        if path.exists():
            bak = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
            shutil.copy2(path, bak)
        path.write_text(content, encoding="utf-8")
        # Syntax check
        if path.suffix == ".py":
            try:
                ast.parse(content)
            except SyntaxError as se:
                return (
                    f"Wrote {rel_out}, but Python syntax error at line {se.lineno}: {se.msg}. "
                    "You may want to fix it."
                )
        log.info("self_code wrote %s (%d bytes)", rel_out, len(content))
        return (
            f"Updated {rel_out}. A backup was saved. "
            "Restart me if the change is in a module that's already loaded."
        )
    except Exception as e:
        return f"Write failed: {e}"


def apply_patch(rel: str, old: str, new: str, engine_context=None) -> str:
    """Replace first occurrence of old with new in a file."""
    path = resolve_project_path(rel)
    if not path or not path.exists():
        return f"File “{rel}” not found."
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Couldn't read: {e}"
    if old not in text:
        return "I couldn't find that exact text to replace. Try a smaller unique snippet."
    content = text.replace(old, new, 1)
    return write_file(str(path.relative_to(ROOT)), content, engine_context=engine_context)


def summarize_project() -> str:
    files = list_source_files(40)
    return (
        f"I'm Lyra. My code lives in {ROOT.name}/ with {len(files)}+ source files. "
        f"Examples: {', '.join(files[:12])}…"
    )


def try_handle_self_code(command: str, engine_context=None) -> str | None:
    """Pattern entry for list/read. Edit intents return a guidance message
    (full AI edit goes through the coder agent)."""
    if LIST_CODE_PATTERN.search(command):
        files = list_source_files()
        return "My main files: " + ", ".join(files[:25]) + ("…" if len(files) > 25 else "")
    m = READ_CODE_PATTERN.search(command)
    if m:
        path = m.group("path") or m.group("path2")
        return read_file(path, max_chars=4000)
    if re.search(r"\bwhere\s+is\s+your\s+code\b", command, re.I):
        return summarize_project()
    return None
