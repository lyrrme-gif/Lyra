"""Lyra - live trace for debugging. Prints what happens (keys, clicks, dictation steps, loops) to the
backend terminal AND ui.log, as lines starting with "[trace]".

* ON when running from source, OFF in the built app. LYRA_TRACE=0 / 1 forces it either way.
* Privacy: ordinary character keys are NEVER logged by name - only "key". Modifiers and special keys
  (Ctrl, Alt, Shift, Win, Esc, Enter, Space, arrows, F-keys...) are named. Dictated words ARE logged
  (cut to 80 characters) so a dictation problem can be diagnosed; look before you share a log.
* ev() only puts a line on a queue, so it is safe to call from inside the keyboard/mouse hooks.
"""
from __future__ import annotations

import os
import queue
import threading
import time

import ui

# On when running from source (that is where it is read), OFF in the built app: its CPU/GPU sampler runs nvidia-smi and
# polls every 5 s for nothing anyone reads. Set LYRA_TRACE=1 to switch it on in the exe when diagnosing something.
_DEFAULT_ON = "0" if getattr(__import__("sys"), "frozen", False) else "1"
_ON = os.environ.get("LYRA_TRACE", _DEFAULT_ON).strip().lower() not in ("0", "false", "off", "no")
_q: "queue.SimpleQueue[tuple[str, str]]" = queue.SimpleQueue()
_lock = threading.Lock()
_started = False
_down: set[int] = set()
_last_loop: dict[str, float] = {}

_KEYS = {
    0x11: "Ctrl", 0xA2: "Ctrl", 0xA3: "Ctrl", 0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt",
    0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift", 0x5B: "Win", 0x5C: "Win",
    0x1B: "Esc", 0x0D: "Enter", 0x09: "Tab", 0x20: "Space", 0x08: "Backspace", 0x2E: "Delete",
    0x14: "CapsLock", 0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    **{0x70 + i: f"F{i + 1}" for i in range(12)},
}
_MOUSE = {0x201: "left", 0x204: "right", 0x207: "middle", 0x20B: "side"}


def on() -> bool:
    return _ON


def _writer() -> None:
    while True:
        tag, msg = _q.get()
        try:
            ui.log.info("[trace] %s: %s", tag, msg)
        except Exception:
            pass


def _heartbeat() -> None:
    while True:
        time.sleep(30)
        names = sorted(t.name for t in threading.enumerate())
        ev("loop", f"heartbeat: {len(names)} threads alive: {', '.join(names)}")


# --------------------------------------------------------------------------------------------
# CPU / RAM / GPU monitor: what is Lyra using, which of its threads, and what else is busy.
# Every LYRA_TRACE_RES seconds (default 5; 0 = off).
# --------------------------------------------------------------------------------------------
_RES_EVERY = float(os.environ.get("LYRA_TRACE_RES", "5") or 0)
# Mapping "window -> renderer process" makes each window busy-spin 250 ms in turn (see pidprobe below), which
# showed up as a 250 ms UI freeze in every window shortly after launch and whenever a window opened. It is a
# diagnostic nicety only, so it is off unless asked for: LYRA_TRACE_PIDMAP=1.
_PIDMAP = os.environ.get("LYRA_TRACE_PIDMAP", "0").strip().lower() in ("1", "true", "on", "yes")


def _gpu_nvidia() -> str | None:
    import shutil
    import subprocess
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4,
                             creationflags=0x08000000 if os.name == "nt" else 0).stdout.strip().splitlines()
        if not out:
            return None
        name, util, used, total, temp = [x.strip() for x in out[0].split(",")[:5]]
        return f"{name}: {util}% busy, VRAM {used}/{total} MB, {temp} C"
    except Exception:
        return None


def _gpu_windows(psutil) -> str | None:
    """Per-process GPU use from the Windows performance counters (works for NVIDIA, AMD and Intel)."""
    if os.name != "nt":
        return None
    import csv
    import re
    import subprocess
    try:
        out = subprocess.run(["typeperf", r"\GPU Engine(*)\Utilization Percentage", "-sc", "1"],
                             capture_output=True, text=True, timeout=8, creationflags=0x08000000).stdout
        rows = list(csv.reader([ln for ln in out.splitlines() if ln.startswith('"')]))
        if len(rows) < 2:
            return None
        per_pid: dict[int, float] = {}
        per_type: dict[int, dict[str, float]] = {}      # pid -> {engine type (3D, Copy, VideoDecode...): %}
        total = 0.0
        for col, val in zip(rows[0][1:], rows[1][1:]):
            m = re.search(r"pid_(\d+)_", col)
            et = re.search(r"engtype_([A-Za-z0-9]+)", col)
            try:
                v = float(val)
            except ValueError:
                continue
            if m and v > 0:
                per_pid[int(m.group(1))] = per_pid.get(int(m.group(1)), 0.0) + v
                if et:
                    d = per_type.setdefault(int(m.group(1)), {})
                    d[et.group(1)] = d.get(et.group(1), 0.0) + v
                total += v
        if not per_pid:
            return "GPU idle (no process above 0%)"
        top = sorted(per_pid.items(), key=lambda kv: -kv[1])[:5]
        names = []
        for pid, v in top:
            try:
                nm = psutil.Process(pid).name()
            except Exception:
                nm = "?"
            mark = " <-- LYRA backend" if pid == os.getpid() else ""
            try:
                o = _owner(psutil.Process(pid), os.getpid())
                if o:
                    mark += f" ({o})"
            except Exception:
                pass
            kinds = ", ".join(f"{k} {x:.0f}%" for k, x in sorted(per_type.get(pid, {}).items(), key=lambda kv: -kv[1]) if x >= 1)
            who = next((w for w, p in _pidmap.items() if p == pid), "")
            names.append(f"{who + '=' if who else ''}{nm}[{pid}] {v:.0f}%{' {' + kinds + '}' if kinds else ''}{mark}")
        return "GPU engines by process: " + ", ".join(names) + " | windows: " + (_windows_label() or "unmapped")
    except Exception:
        return None


def _where(idents: list[int]) -> dict[int, str]:
    """Which Python code each thread is running right now: sample its stack 8 times, report the most common
    spot (file:line function <- caller <- caller). A thread inside a native library shows the line that called it."""
    import collections
    import sys
    counts = {i: collections.Counter() for i in idents}
    for _ in range(8):
        frames = sys._current_frames()
        for ident, cnt in counts.items():
            f, parts = frames.get(ident), []
            while f is not None and len(parts) < 3:
                parts.append(f"{os.path.basename(f.f_code.co_filename)}:{f.f_lineno} {f.f_code.co_name}")
                f = f.f_back
            if parts:
                cnt[" <- ".join(parts)] += 1
        time.sleep(0.03)
    return {i: c.most_common(1)[0][0] for i, c in counts.items() if c}


def _owner(pr, me_pid: int) -> str:
    """Whose program is this? WebView2 helpers name their host app on the command line; others: parent."""
    try:
        for a in pr.cmdline():
            if a.startswith("--webview-exe-name="):
                host = a.split("=", 1)[1]
                return f"UI of {host}" + (" = LYRA" if host.lower() == "lyra.exe" else "")
        par = pr.parent()
        chain = []
        while par is not None and len(chain) < 3:
            chain.append(par.name())
            par = par.parent()
        mark = " = LYRA" if any(n.lower() in ("lyra.exe",) for n in chain) or me_pid in [p.pid for p in pr.parents()] else ""
        return "child of " + " > ".join(chain) + mark if chain else ""
    except Exception:
        return ""


def _wv_role(pr) -> str:
    """What is this msedgewebview2.exe process FOR? Read from its command line (--type=...)."""
    try:
        args = pr.cmdline()
    except Exception:
        return "unknown role"
    typ = next((a.split("=", 1)[1] for a in args if a.startswith("--type=")), "")
    sub = next((a.split("=", 1)[1] for a in args if a.startswith("--utility-sub-type=")), "")
    if not typ:
        return "BROWSER process (the WebView2 manager: input, networking glue, window embedding)"
    if typ == "renderer":
        cid = next((a.split("=", 1)[1] for a in args if a.startswith("--renderer-client-id=")), "?")
        return f"RENDERER #{cid} (runs ONE page: its JavaScript, CSS animations, layout, paint)"
    if typ == "gpu-process":
        return "GPU process (draws and composites EVERY WebView2 window: blur, shadows, animations, video)"
    if typ == "utility":
        return f"utility ({sub.split('.')[-1] or 'service'})"
    return typ


def _wv_threads(pr, secs: float = 0.5, role_hint: str = "RENDERER") -> str:
    """Which threads of this process are busy right now. Chromium threads have no names here, so they are
    classified: the lowest thread id is the main thread (JavaScript + style/layout), the rest are the
    compositor / raster / GPU / worker threads."""
    try:
        t1 = {t.id: t.user_time + t.system_time for t in pr.threads()}
        order = sorted(t1)
        time.sleep(secs)
        t2 = {t.id: t.user_time + t.system_time for t in pr.threads()}
    except Exception:
        return ""
    kind_main = {"BROWSER": "browser UI thread", "GPU": "GPU main thread"}.get(role_hint, "MAIN thread (JS/style/layout)")
    kind_other = {"BROWSER": "browser worker thread", "GPU": "GPU raster/compositor thread"}.get(role_hint, "compositor/raster/worker thread")
    rows = []
    for tid, cur in t2.items():
        pct = (cur - t1.get(tid, cur)) / secs * 100.0
        if pct >= 3:
            rows.append((pct, tid))
    rows.sort(reverse=True)
    parts = []
    for pct, tid in rows[:5]:
        kind = kind_main if order and tid == order[0] else kind_other
        parts.append(f"{kind} {pct:.0f}%")
    return "; ".join(parts) if parts else "no single thread above 3%"


def _webview_report(psutil, me_pid: int) -> None:
    """Break the WebView2 load down: per role (renderer / GPU / browser / utility) and, for the busy ones,
    per thread. The page-by-page answer (which animation, timer or script) comes from the UI itself:
    lines tagged  ui:<window>:perf, perf-anim, perf-timer, perf-heavy  printed right next to these."""
    try:
        procs = []
        for pr in psutil.process_iter(["pid", "name"]):
            if (pr.info.get("name") or "").lower() != "msedgewebview2.exe":
                continue
            if "LYRA" not in _owner(pr, me_pid):
                continue
            procs.append(pr)
        if not procs:
            return
        for pr in procs:
            try:
                pr.cpu_percent(None)
            except Exception:
                pass
        time.sleep(1.0)
        rows = []
        for pr in procs:
            try:
                rows.append((pr.cpu_percent(None), pr, _wv_role(pr)))
            except Exception:
                continue
        by_kind: dict[str, float] = {}
        for v, _pr, role in rows:
            k = role.split(" ")[0].split("#")[0]
            by_kind[k] = by_kind.get(k, 0.0) + v
        total = sum(by_kind.values())
        ev("webview", f"{len(rows)} WebView2 processes use {total:.0f}% of one core in total: " + ", ".join(
            f"{k} {v:.0f}%" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]) if v >= 1))
        for v, pr, role in sorted(rows, key=lambda r: -r[0])[:4]:
            if v < 10:
                break
            try:
                mem = pr.memory_info().rss / 1048576
                ev("webview", f"[{pr.pid}] {v:.0f}% CPU, {mem:.0f} MB, {pr.num_threads()} threads - {role} - busy threads: {_wv_threads(pr, 0.5, role.split(' ')[0].split('#')[0])}")
            except Exception:
                continue
        gpu_pid_hint = next((pr.pid for v, pr, role in rows if role.startswith("GPU")), None)
        if gpu_pid_hint:
            ev("webview", f"(the GPU process is [{gpu_pid_hint}]; if GPU % in the 'GPU engines by process' line is on a renderer instead, "
                          "that page is drawing directly - see its ui:<window>:perf-heavy line)")
    except Exception as e:
        ev("res", f"webview report error: {e}")


def _resources() -> None:
    try:
        import psutil
    except Exception:
        ev("res", "psutil is missing - no CPU/RAM/GPU numbers (pip install psutil)")
        return
    me = psutil.Process()
    ncpu = psutil.cpu_count() or 1
    me.cpu_percent(None)
    psutil.cpu_percent(None)
    prev: dict[int, float] = {}
    for t in me.threads():
        prev[t.id] = t.user_time + t.system_time
    others: dict[int, "psutil.Process"] = {}
    last = time.monotonic()
    n = 0
    while True:
        time.sleep(_RES_EVERY)
        n += 1
        try:
            now = time.monotonic()
            dt = max(0.001, now - last)
            last = now
            tinfo = {t.native_id: t for t in threading.enumerate() if getattr(t, "native_id", None)}
            busy = []
            for t in me.threads():
                cur = t.user_time + t.system_time
                pct = (cur - prev.get(t.id, cur)) / dt * 100.0
                prev[t.id] = cur
                if pct >= 2.0:
                    busy.append((pct, t.id))
            busy.sort(reverse=True)
            kids = 0.0
            for c in me.children(recursive=True):
                try:
                    kids += c.cpu_percent(None)
                except Exception:
                    pass
            lyra = me.cpu_percent(None)
            rss = me.memory_info().rss / 1048576
            vm = psutil.virtual_memory()
            ev("cpu", f"Lyra backend (python) {lyra:.0f}% of one core ({lyra / ncpu:.0f}% of the whole CPU), RAM {rss:.0f} MB, "
                      f"{me.num_threads()} threads"
                      + (f", child processes {kids:.0f}%" if kids >= 1 else "")
                      + f" | whole PC: CPU {psutil.cpu_percent(None):.0f}%, RAM {vm.percent:.0f}%")
            if busy:
                where = _where([tinfo[i].ident for _, i in busy[:4] if i in tinfo and tinfo[i].ident]) if busy[0][0] >= 5 else {}
                parts = []
                for pct, tid in busy[:6]:
                    th = tinfo.get(tid)
                    nm = th.name if th else f"native-thread-{tid} (model/audio library, no Python code)"
                    loc = where.get(th.ident) if th else None
                    parts.append(f"{nm} {pct:.0f}%" + (f"  in  {loc}" if loc else ""))
                ev("cpu", "busiest backend threads: " + " || ".join(parts))
            if n % 3 == 0:                    # scanning every process is costly: every 3rd round only
                top = []
                for pr in psutil.process_iter(["pid", "name"]):
                    pid = pr.info["pid"]
                    if pid == 0 or pid == me.pid:
                        continue
                    if pid not in others:
                        others[pid] = pr
                        try:
                            pr.cpu_percent(None)
                        except Exception:
                            pass
                        continue
                    try:
                        v = others[pid].cpu_percent(None)
                    except Exception:
                        continue
                    if v >= 5:
                        top.append((v, pr))
                for pid in [p for p in others if not psutil.pid_exists(p)]:
                    others.pop(pid, None)
                top.sort(key=lambda x: -x[0])
                if top:
                    ev("cpu", "other busy programs (100% = one core): " + ", ".join(
                        f"{pr.info['name']}[{pr.pid}] {v:.0f}%" + (f" ({o})" if (o := _owner(pr, me.pid)) else "")
                        for v, pr in top[:5]))
                if any((pr.info.get("name") or "").lower() == "msedgewebview2.exe" and v >= 15 for v, pr in top):
                    _webview_report(psutil, me.pid)          # which role / thread inside the UI is the busy one
            if n % 2 == 0:                     # GPU every 2nd round (the Windows counter takes ~1 s)
                gpu = _gpu_nvidia()
                if gpu:
                    ev("gpu", gpu)
                g2 = _gpu_windows(psutil)
                if g2 or not gpu:
                    ev("gpu", g2 or "no GPU numbers available (no nvidia-smi, no Windows GPU counters)")
        except Exception as e:
            ev("res", f"monitor error: {e}")


# ── window <-> renderer PID map ─────────────────────────────────────────────────────────────────────
# WebView2 gives no "this window = that process" API, so each window is asked in turn (GET /pidprobe, see
# ui/src/core/perfProbe.ts) to busy-spin ~450 ms; the renderer that jumps in CPU is that window's.
_WINS = ("island", "caption", "mascot", "wheel", "settings")
_pidmap: dict[str, int] = {}
_probe: dict = {"win": "", "handed": 0.0}
_probe_lock = threading.Lock()


def pidprobe(win: str) -> dict:
    """Answer of GET /pidprobe?win=X: how long X should spin now, and when it should ask again."""
    with _probe_lock:
        if _probe["win"] and _probe["win"] == win and not _probe["handed"]:
            _probe["handed"] = time.monotonic()
            return {"spin": 250, "next": 1500}
        return {"spin": 0, "next": 1500 if _probe["win"] else 10000}


def _lyra_renderers(psutil, me_pid: int) -> list:
    out = []
    for pr in psutil.process_iter(["pid", "name"]):
        try:
            if (pr.info.get("name") or "").lower() != "msedgewebview2.exe" or "--type=renderer" not in pr.cmdline():
                continue
            if "LYRA" in _owner(pr, me_pid):
                out.append(pr)
        except Exception:
            continue
    return out


def _map_windows(psutil, me_pid: int) -> None:
    rend = _lyra_renderers(psutil, me_pid)
    live = {pr.pid for pr in rend}
    for w in [w for w, p in _pidmap.items() if p not in live]:
        _pidmap.pop(w, None)                          # that window (or its process) is gone
    todo = [w for w in _WINS if w not in _pidmap]     # already-known windows are never spun again
    if not todo:
        return
    for win in todo:
        with _probe_lock:
            _probe["win"], _probe["handed"] = win, 0.0
        end = time.monotonic() + 12.0
        while time.monotonic() < end and not _probe["handed"]:
            time.sleep(0.05)
        if not _probe["handed"]:
            _pidmap.pop(win, None)                    # window not open (or not answering)
            continue
        for pr in rend:
            try: pr.cpu_percent(None)
            except Exception: pass
        time.sleep(0.3)
        res = []
        for pr in rend:
            try: res.append((pr.cpu_percent(None), pr.pid))
            except Exception: pass
        res.sort(reverse=True)
        if res and res[0][0] >= 30 and (len(res) < 2 or res[0][0] > 2 * res[1][0]):
            _pidmap[win] = res[0][1]
        else:
            _pidmap.pop(win, None)
    with _probe_lock:
        _probe["win"] = ""
    ev("webview", "windows: " + (_windows_label() or "none identified"))


def _windows_label() -> str:
    return " ".join(f"{w}={p}" for w, p in _pidmap.items())


def _pidmap_loop() -> None:
    try:
        import psutil
    except Exception:
        return
    me = os.getpid()
    time.sleep(20)
    seen: frozenset = frozenset()
    while True:
        try:
            cur = frozenset(pr.pid for pr in _lyra_renderers(psutil, me))
            if cur - seen:                            # a NEW renderer appeared (startup, or Settings opened)
                time.sleep(2)                         # let it finish loading
                _map_windows(psutil, me)
                seen = frozenset(pr.pid for pr in _lyra_renderers(psutil, me))
            elif seen - cur:                          # a window closed: just forget it, no probing, no spin
                seen = cur
                for w in [w for w, p in _pidmap.items() if p not in cur]:
                    _pidmap.pop(w, None)
        except Exception:
            pass
        time.sleep(30)


def _ensure() -> None:
    global _started
    if _started:
        return
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_writer, name="lyra-trace", daemon=True).start()
        threading.Thread(target=_heartbeat, name="lyra-trace-heartbeat", daemon=True).start()
        if _RES_EVERY > 0:
            threading.Thread(target=_resources, name="lyra-trace-resources", daemon=True).start()
            if _PIDMAP:
                threading.Thread(target=_pidmap_loop, name="lyra-trace-pidmap", daemon=True).start()
        ui.log.info("[trace] live trace is ON (set LYRA_TRACE=0 to turn it off)")


def ev(tag: str, msg: str = "") -> None:
    """Record one event. Never raises, never blocks."""
    if not _ON:
        return
    try:
        _ensure()
        _q.put((tag, msg))
    except Exception:
        pass


def key(down: bool, vk: int) -> None:
    """A physical key went down/up (auto-repeat is skipped). Character keys are logged anonymously."""
    if not _ON:
        return
    if down:
        if vk in _down:
            return
        _down.add(vk)
    else:
        _down.discard(vk)
    ev("key", f"{_KEYS.get(vk, 'key')} {'down' if down else 'up'}")


def click(msg: int, x: int, y: int, injected: bool) -> None:
    ev("click", f"{_MOUSE.get(msg, 'button')} at ({x},{y}){' [sent by a program]' if injected else ''}")


def loop(name: str, every: float = 0.0, msg: str = "tick") -> None:
    """A background loop ran once. `every` (seconds) rate-limits the line; 0 = every time."""
    if not _ON:
        return
    now = time.monotonic()
    if every and now - _last_loop.get(name, 0.0) < every:
        return
    _last_loop[name] = now
    ev("loop", f"{name} {msg}")
