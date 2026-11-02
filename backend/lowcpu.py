"""Lyra — keep the background work polite.

* Lyra's engine runs at BELOW-NORMAL priority (Windows), so a game, a video call or a build always wins the CPU
  and Lyra only uses what is left over. Voice stays quick: it only ever waits when the PC is already saturated.
* Heavy housekeeping (the file index) waits until the PC is calm: no game running, CPU not busy, and not on a
  battery that is running low. It never waits forever: after `max_wait_s` it goes ahead (at low priority).

Setting: low_priority_backend (default on).
"""

from __future__ import annotations

import logging
import sys
import threading
import time

log = logging.getLogger("lyra")

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def _setting(key: str, default):
    try:
        import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:
        return default


def apply_process_priority() -> bool:
    """Run this process below normal priority. Returns True when it was changed."""
    if sys.platform != "win32" or not _setting("low_priority_backend", True):
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32                                         # type: ignore[attr-defined]
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        ok = bool(k.SetPriorityClass(k.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))
        if ok:
            log.info("Lyra's engine now runs at below-normal priority (games and apps come first).")
        return ok
    except Exception as e:                                                 # noqa: BLE001
        log.debug("could not lower the process priority: %s", e)
        return False


def calm_reason(max_cpu: float = 45.0) -> str:
    """'' when the PC is calm enough for heavy background work, else a short reason why not."""
    try:
        import game_mode
        if game_mode.is_active():
            return "a game is running"
    except Exception:                                                      # noqa: BLE001
        pass
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is not None and not b.power_plugged and b.percent < 40:
            return "the battery is low"
        if psutil.cpu_percent(interval=0.5) > max_cpu:
            return "the PC is busy"
    except Exception:                                                      # noqa: BLE001
        pass
    return ""


def wait_until_calm(stop: threading.Event, max_wait_s: float = 1200.0, every_s: float = 60.0) -> bool:
    """Block until the PC is calm (True), `stop` is set, or `max_wait_s` has passed (then also True: go ahead)."""
    end = time.monotonic() + max_wait_s
    while not stop.is_set():
        why = calm_reason()
        if not why:
            return True
        if time.monotonic() >= end:
            log.info("Background work goes ahead after waiting (%s).", why)
            return True
        log.debug("Background work waits: %s.", why)
        if stop.wait(every_s):
            return False
    return False
