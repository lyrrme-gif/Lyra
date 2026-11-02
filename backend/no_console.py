"""Keep console windows from flashing in the windowed (no-console) build.

lyra-backend.exe has no console of its own, so Windows gives every console program it starts
(powershell, shutdown, ffmpeg, taskkill...) a brand-new visible console window. A few of our own
calls forgot CREATE_NO_WINDOW, and libraries (pydub, screen-brightness, ...) never pass it.

Importing this module patches subprocess.Popen once, so EVERY child process Python starts is
created hidden unless the caller already chose its own creation flags.

Left alone on purpose: DETACHED_PROCESS / CREATE_NEW_CONSOLE callers, and interactive shells
(cmd, powershell, wsl...) started with no pipes - "open PowerShell" must still show a window.
"""
import ntpath
import os
import subprocess
import sys

_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_CONSOLE = 0x00000010

_INTERACTIVE = {"cmd", "powershell", "pwsh", "wsl", "bash", "ubuntu", "python", "py", "node", "ssh"}


def _exe_name(args, kw) -> str:
    if kw.get("shell"):
        return ""
    exe = kw.get("executable")
    if not exe:
        if isinstance(args, (list, tuple)) and args:
            exe = args[0]
        elif isinstance(args, (str, bytes, os.PathLike)):
            exe = os.fsdecode(args).strip().split(" ", 1)[0].strip('"')
    if not exe:
        return ""
    base = ntpath.basename(os.fsdecode(exe)).lower()
    return base[:-4] if base.endswith(".exe") else base


def flags_for(args, kw: dict) -> int:
    """creationflags to use for this call (pure function, easy to test)."""
    flags = int(kw.get("creationflags") or 0)
    if flags & (_CREATE_NO_WINDOW | _DETACHED_PROCESS | _CREATE_NEW_CONSOLE):
        return flags
    no_pipes = all(kw.get(k) is None for k in ("stdin", "stdout", "stderr"))
    if no_pipes and _exe_name(args, kw) in _INTERACTIVE:
        return flags
    return flags | _CREATE_NO_WINDOW


def install() -> None:
    if sys.platform != "win32" or getattr(subprocess.Popen, "_lyra_no_console", False):
        return
    original = subprocess.Popen.__init__

    def __init__(self, *args, **kw):  # noqa: N807
        # creationflags passed positionally (13th argument) is rare; then we leave the call alone.
        if len(args) < 13:
            kw["creationflags"] = flags_for(args[0] if args else kw.get("args"), kw)
        original(self, *args, **kw)

    subprocess.Popen.__init__ = __init__
    subprocess.Popen._lyra_no_console = True


install()
