"""Lyra - load speech models without making the PC stutter.

Loading an ONNX speech model (Parakeet ~650 MB, the offline voice ~350 MB) is the heaviest thing Lyra does, and it
used to freeze the mouse for a few seconds because:

  * ONNX Runtime's worker threads BUSY-WAIT ("spin") between jobs, so a few threads kept whole cores at 100 %;
  * those threads run at normal priority (lowering the priority of the Python thread that starts the load changes
    nothing for them: they are created by ONNX Runtime, not by us);
  * right after a download the speech model and the offline voice were both loaded at the same moment, on every core.

What this module does about it (nothing here changes what the models say, only how nicely they are loaded):

  session_options()   ONNX session options with spinning switched off (idle threads sleep instead of burning a core)
  gentle()            context manager for BACKGROUND loads: one heavy load at a time, wait until the PC is quiet,
                      and run the whole load + warm-up at below-normal priority (Windows), then put it back
  wait_for_quiet()    the quiet-PC wait on its own

Loads that someone is waiting for (the first sentence after the idle unloader freed the model) do not use gentle();
they only get the no-spin session options.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time

_gate = threading.Lock()                 # one heavy model load / warm-up at a time
_BELOW_NORMAL = 0x00004000               # BELOW_NORMAL_PRIORITY_CLASS


def session_options(threads: int | None = None):
    """onnxruntime.SessionOptions with spinning off. `threads` caps the intra-op threads (None = onnxruntime's default)."""
    import onnxruntime as ort
    so = ort.SessionOptions()
    if threads:
        so.intra_op_num_threads = int(threads)
        so.inter_op_num_threads = 1
    for key in ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning"):
        try:
            so.add_session_config_entry(key, "0")      # older onnxruntime builds do not know the key: harmless
        except Exception:                              # noqa: BLE001
            pass
    return so


def wait_for_quiet(max_s: float = 20.0, below: float = 50.0) -> None:
    """Returns once the CPU is below `below` percent (or after max_s). Never raises."""
    try:
        import psutil
    except Exception:                                  # noqa: BLE001
        return
    end = time.monotonic() + max(0.0, max_s)
    try:
        while time.monotonic() < end:
            if psutil.cpu_percent(interval=1.0) < below:
                return
    except Exception:                                  # noqa: BLE001
        return


def _process_priority(new_class: int | None) -> int | None:
    """Windows: set this process's priority class and return the old one (None when not on Windows / on failure).
    Pass None to only read it."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetPriorityClass.argtypes = [wintypes.HANDLE]
        k.GetPriorityClass.restype = wintypes.DWORD
        k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.SetPriorityClass.restype = wintypes.BOOL
        h = k.GetCurrentProcess()
        old = int(k.GetPriorityClass(h)) or None
        if new_class is not None and old is not None:
            k.SetPriorityClass(h, new_class)
        return old
    except Exception:                                  # noqa: BLE001
        return None


@contextlib.contextmanager
def gentle(quiet_wait_s: float = 20.0):
    """Use around a background model load + warm-up: `with gentle(): engine.warmup()`."""
    with _gate:
        wait_for_quiet(quiet_wait_s)
        old = _process_priority(_BELOW_NORMAL)
        try:
            yield
        finally:
            if old is not None:
                _process_priority(old)
