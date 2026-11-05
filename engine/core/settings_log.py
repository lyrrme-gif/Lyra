"""Lyra - the Settings log: what Settings is doing, what it is loading, and what that costs, tab by tab.

Two halves feed it:

* The Settings page (ui/src/settings/settingsLog.ts) POSTs a small snapshot to /settings-log every ~3 s while the
  window is on screen, and at once when something changes (tab, focus, visibility). A snapshot says: which tab is
  shown, whether the window has focus, which tabs are built / still waiting to be built, which card is being
  built right now, which cards are waiting for their tab (whenVisible), every request in flight and every request
  that just finished (with WHO started it: a tab build, a lazy card, a poll or the user), plus the page's own
  numbers (animation-frame rate, main-thread busy %, running animations by screen area, canvases).
* A sampler thread in here measures the machine side while Settings is open: CPU of the Lyra WebView2 processes
  (renderer / GPU process / browser), GPU engines per process (Windows counters, same source dbg.py uses), the
  backend's own CPU and RAM, and the whole PC.

Every sample is charged to the tab that was shown during it, so the end result is a table "tab -> CPU, GPU,
frames, animations, requests". The same lines go to the backend terminal / ui.log as "[trace] settings: ..." and to
a file of their own, settings-log.txt, next to ui.log. GET /settings-log returns the table and the numbers as JSON.

Cost: nothing runs while Settings is closed except one cheap look at the cached WebView2 processes every 15 s
(used to learn what "idle" looks like, so the Settings renderer can be recognised). While it is open the sampler
wakes every LYRA_SETTINGS_LOG_EVERY seconds (default 3) and starts the Windows GPU counter every
LYRA_SETTINGS_LOG_GPU seconds (default 6; it is a separate typeperf.exe process that takes about a second).

Switches: LYRA_TRACE=0 turns it off with the rest of the trace, LYRA_SETTINGS_LOG=0 turns off only this.
Privacy: only tab ids, endpoint paths (no query string, no bodies) and numbers are written.
"""
from __future__ import annotations

import collections
import csv
import logging
import os
import re
import threading
import time
from logging.handlers import RotatingFileHandler

_OFF = os.environ.get("LYRA_SETTINGS_LOG", "1").strip().lower() in ("0", "false", "off", "no")


def _envf(name: str, default: float) -> float:
    try:
        return max(0.5, float(os.environ.get(name, "") or default))
    except ValueError:
        return default


_EVERY = _envf("LYRA_SETTINGS_LOG_EVERY", 3.0)
_GPU_EVERY = _envf("LYRA_SETTINGS_LOG_GPU", 6.0)
_CLOSED_EVERY = 15.0
_OPEN_TTL = max(9.0, _EVERY * 3)        # no snapshot for this long = the window is gone / not showing
_SLOW_REQ_MS = 100

_ID = re.compile(r"[^a-z0-9_]")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

_lock = threading.RLock()
_started = False
_wake = threading.Event()               # set when Settings opens, so the sampler does not sleep through the first seconds

# --- state fed by the page -----------------------------------------------------------------------
_ui: dict = {}                         # the last snapshot
_ui_at = 0.0                           # monotonic time it arrived
_cur_tab = ""                          # tab shown now ("" until the first snapshot)
_cur_open = False                      # window on screen (the page says so)
_cur_focus = False
_last_acct = 0.0                       # monotonic time of the last time-accounting step
_slice: dict[str, float] = {}          # tab -> seconds shown since the last resource sample
_open_since = 0.0
_events: "collections.deque[str]" = collections.deque(maxlen=300)
_max_inflight = 0

# --- numbers per tab ------------------------------------------------------------------------------
_tabs: dict[str, dict] = {}


def _new_stat() -> dict:
    return {
        "secs": 0.0, "unfocused_secs": 0.0,
        "n": 0, "n_gpu": 0, "n_ui": 0,
        "cpu_r": 0.0, "n_r": 0,            # Settings renderer CPU (% of one core), samples where it was known
        "cpu_wv": 0.0,                     # all Lyra WebView2 processes together
        "gpu_r": 0.0, "gpu_wv": 0.0, "gpu_dwm": 0.0,
        "raf": 0.0, "main": 0.0, "anims": 0.0, "endless": 0.0,
        "req": 0, "req_ms": 0.0, "req_max": 0.0, "req_bg": 0, "req_build": 0, "req_lazy": 0, "req_poll": 0, "req_user": 0,
        "built_ms": None, "built_why": "",
    }


def _stat(tab: str) -> dict:
    s = _tabs.get(tab)
    if s is None:
        s = _tabs[tab] = _new_stat()
    return s


# --- helpers ------------------------------------------------------------------------------------
def on() -> bool:
    if _OFF:
        return False
    try:
        from engine.core import dbg
        return bool(dbg.on())
    except Exception:
        return False


def _clean(s: object, limit: int = 160) -> str:
    t = _CTRL.sub(" ", str(s))
    return t if len(t) <= limit else t[:limit] + "..."


def _tab_id(v: object) -> str:
    return _ID.sub("", str(v or "").lower())[:24]


def _num(v: object, default: float = 0.0, lo: float = 0.0, hi: float = 1e9) -> float:
    try:
        f = float(v)           # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if f != f:                 # NaN
        return default
    return min(hi, max(lo, f))


def _path(v: object) -> str:
    return _clean(str(v or "").split("?")[0], 60)


_file_log: logging.Logger | None = None


def _write(line: str) -> None:
    """One line into settings-log.txt (rotating, 1 MB x 2). Never raises."""
    global _file_log
    try:
        if _file_log is None:
            from engine.core import paths
            lg = logging.getLogger("lyra.settingslog")
            lg.setLevel(logging.INFO)
            lg.propagate = False
            if not lg.handlers:
                h = RotatingFileHandler(paths.data_path("settings-log.txt"), maxBytes=1_000_000, backupCount=1,
                                        encoding="utf-8", delay=True)
                h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", "%H:%M:%S"))
                lg.addHandler(h)
            _file_log = lg
        _file_log.info(line)
    except Exception:
        pass


def _say(tag: str, line: str, *, trace: bool = True) -> None:
    _events.append(f"{time.strftime('%H:%M:%S')} {tag}: {line}")
    _write(f"{tag}: {line}")
    if trace:
        try:
            from engine.core import dbg
            dbg.ev("settings", f"{tag}: {line}" if tag != "SAMPLE" else line)
        except Exception:
            pass


# --- time accounting --------------------------------------------------------------------------
def _account(now: float) -> None:
    """Charge the time since the last step to the tab that was shown (only while the window was on screen)."""
    global _last_acct
    dt = now - _last_acct if _last_acct else 0.0
    _last_acct = now
    if dt <= 0 or dt > 15 or not _cur_open or not _cur_tab:
        return
    st = _stat(_cur_tab)
    st["secs"] += dt
    if not _cur_focus:
        st["unfocused_secs"] += dt
    _slice[_cur_tab] = _slice.get(_cur_tab, 0.0) + dt


# --- the page -> backend half ---------------------------------------------------------------
def ingest(body: object) -> dict:
    """Handle one POST /settings-log. Returns the JSON answer ({"ok": True, "off": bool})."""
    global _ui, _ui_at, _cur_tab, _cur_open, _cur_focus, _open_since, _max_inflight
    if not on():
        return {"ok": True, "off": True}
    if not isinstance(body, dict):
        return {"ok": False, "off": False}
    _ensure()
    now = time.monotonic()
    with _lock:
        _account(now)
        tab = _tab_id(body.get("tab")) or _cur_tab
        was_open = _cur_open
        was_tab, was_focus = _cur_tab, _cur_focus
        _cur_open = bool(body.get("vis"))
        _cur_focus = bool(body.get("focus"))
        _cur_tab = tab
        _ui, _ui_at = body, now

        if _cur_open and not was_open:
            _open_since = now
            _slice.clear()
            _wake.set()
            _say("OPEN", f"Settings is on screen, tab={tab or '?'}, focus={'yes' if _cur_focus else 'no'}")
        elif was_open and not _cur_open:
            _say("CLOSE", "Settings left the screen (hidden / parked)")
            _write_table()
        elif _cur_open:
            if tab != was_tab and was_tab:
                _say("TAB", f"{was_tab} -> {tab}", trace=False)
            if _cur_focus != was_focus:
                _say("FOCUS", "window got focus" if _cur_focus else "window lost focus (animations should rest)", trace=False)

        # events the page collected since its last snapshot (builds, lazy cards, searches ...)
        evs = body.get("ev")
        if isinstance(evs, list):
            for e in evs[:60]:
                if isinstance(e, dict):
                    _say("UI " + _clean(e.get("k"), 12), _clean(e.get("m"), 200), trace=False)
                    if e.get("k") == "built":
                        st = _stat(_tab_id(e.get("tab")))
                        st["built_ms"] = round(_num(e.get("ms")), 1)
                        st["built_why"] = _clean(e.get("why"), 12)

        # requests that finished since the last snapshot
        done = body.get("done")
        if isinstance(done, list):
            for r in done[:80]:
                if not isinstance(r, dict):
                    continue
                rtab = _tab_id(r.get("tab")) or tab or "?"
                kind = _ID.sub("", str(r.get("kind") or "user").lower())[:8]
                ms = _num(r.get("ms"))
                st = _stat(rtab)
                st["req"] += 1
                st["req_ms"] += ms
                st["req_max"] = max(st["req_max"], ms)
                if r.get("bg"):
                    st["req_bg"] += 1
                key = "req_" + kind
                if key in st and key != "req_bg":
                    st[key] += 1
                if ms >= _SLOW_REQ_MS or str(r.get("st")) not in ("200", "204"):
                    _say("REQ", f"{_clean(r.get('m'), 6)} {_path(r.get('p'))} {ms:.0f} ms -> {_clean(r.get('st'), 6)}  "
                                f"[{kind}, tab {rtab}{', NOT the shown tab' if r.get('bg') else ''}, by {_clean(r.get('o'), 40) or '-'}]",
                         trace=False)
        _max_inflight = max(_max_inflight, int(_num(body.get("maxin"), 0, 0, 999)))

        # the page's own numbers: charged to the tab it reports (the shown one, unless the sample was mixed)
        perf = body.get("perf")
        if isinstance(perf, dict) and _cur_open and perf.get("ok"):
            st = _stat(_tab_id(perf.get("tab")) or tab or "?")
            st["n_ui"] += 1
            st["raf"] += _num(perf.get("raf"))
            st["main"] += _num(perf.get("main"), hi=100)
            st["anims"] += _num(perf.get("anims"))
            st["endless"] += _num(perf.get("endless"))
    return {"ok": True, "off": False}


# --- the machine half ------------------------------------------------------------------------
def parse_gpu_csv(text: str) -> dict[int, dict[str, float]]:
    """typeperf output for  \\GPU Engine(*)\\Utilization Percentage  ->  {pid: {engine type: percent}}.
    Pure function (tested without Windows)."""
    rows = list(csv.reader([ln for ln in text.splitlines() if ln.startswith('"')]))
    out: dict[int, dict[str, float]] = {}
    if len(rows) < 2:
        return out
    for col, val in zip(rows[0][1:], rows[1][1:]):
        m = re.search(r"pid_(\d+)_", col)
        if not m:
            continue
        try:
            v = float(val)
        except ValueError:
            continue
        if v <= 0:
            continue
        et = re.search(r"engtype_([A-Za-z0-9]+)", col)
        d = out.setdefault(int(m.group(1)), {})
        k = et.group(1) if et else "?"
        d[k] = d.get(k, 0.0) + v
    return out


def _gpu_by_pid() -> dict[int, dict[str, float]] | None:
    if os.name != "nt":
        return None
    import subprocess
    try:
        out = subprocess.run(["typeperf", r"\GPU Engine(*)\Utilization Percentage", "-sc", "1"],
                             capture_output=True, text=True, timeout=8, creationflags=0x08000000).stdout
        return parse_gpu_csv(out)
    except Exception:
        return None


def pick_settings_renderer(idle: dict[int, float], now: dict[int, float]) -> int | None:
    """Which WebView2 renderer is the Settings page? The one whose CPU rose most above its own idle level while
    Settings is on screen. Needs a clear winner (>= 8 points and at least twice the runner-up)."""
    gain = sorted(((now[p] - idle.get(p, 0.0), p) for p in now), reverse=True)
    if not gain or gain[0][0] < 8.0:
        return None
    if len(gain) > 1 and gain[1][0] > gain[0][0] / 2.0:
        return None
    return gain[0][1]


class _Procs:
    """The Lyra WebView2 processes, found once and then only polled (roles never change)."""

    def __init__(self, psutil):
        self.ps = psutil
        self.me = psutil.Process()
        self.me.cpu_percent(None)
        self.procs: dict[int, tuple] = {}       # pid -> (Process, kind)
        self.idle: dict[int, list[float]] = {}  # renderer pid -> CPU % samples taken while Settings was closed
        self.renderer: int | None = None
        self.how = ""
        self.closed_ticks = 0

    def discover(self) -> None:
        from engine.core import dbg
        ps = self.ps
        me_pid = os.getpid()
        seen = set()
        for pr in ps.process_iter(["pid", "name"]):
            try:
                if (pr.info.get("name") or "").lower() != "msedgewebview2.exe":
                    continue
                seen.add(pr.pid)
                if pr.pid in self.procs:
                    continue
                if "LYRA" not in dbg._owner(pr, me_pid):
                    continue
                kind = dbg._wv_role(pr).split(" ")[0].split("#")[0].lower()
                pr.cpu_percent(None)
                self.procs[pr.pid] = (pr, kind)
            except Exception:
                continue
        for pid in [p for p in self.procs if p not in seen]:
            self.procs.pop(pid, None)
            self.idle.pop(pid, None)
            if self.renderer == pid:
                self.renderer, self.how = None, ""

    def poll(self) -> dict[int, tuple[float, str]]:
        out: dict[int, tuple[float, str]] = {}
        for pid, (pr, kind) in list(self.procs.items()):
            try:
                out[pid] = (pr.cpu_percent(None), kind)
            except Exception:
                self.procs.pop(pid, None)
        return out

    def note_idle(self, cpu: dict[int, tuple[float, str]]) -> None:
        for pid, (v, kind) in cpu.items():
            if kind == "renderer":
                lst = self.idle.setdefault(pid, [])
                lst.append(v)
                del lst[:-6]

    def settle_renderer(self, cpu: dict[int, tuple[float, str]]) -> None:
        """Pick (or keep) the Settings renderer: dbg's pid map if it has one, else the CPU-rise heuristic."""
        try:
            from engine.core import dbg
            mapped = dbg._pidmap.get("settings")
        except Exception:
            mapped = None
        if mapped and mapped in cpu:
            self.renderer, self.how = mapped, "pid map (LYRA_TRACE_PIDMAP)"
            return
        if self.renderer in cpu and cpu[self.renderer][0] >= 3.0:
            return                                   # still busy: keep it
        idle = {p: (sorted(v)[len(v) // 2] if v else 0.0) for p, v in self.idle.items()}
        now = {p: v for p, (v, k) in cpu.items() if k == "renderer"}
        pick = pick_settings_renderer(idle, now)
        if pick is not None:
            self.renderer, self.how = pick, "CPU rise above its idle level"
        elif self.renderer not in cpu:
            self.renderer, self.how = None, ""


def _sample_line(s: dict) -> str:
    def pc(v):
        return "-" if v is None else f"{v:.0f}%"
    kinds = ", ".join(f"{k} {v:.0f}%" for k, v in sorted(s["by_kind"].items(), key=lambda kv: -kv[1]) if v >= 1) or "idle"
    r = f"Settings renderer {pc(s['cpu_r'])}" if s["cpu_r"] is not None else "Settings renderer not identified yet"
    gpu = "GPU not measured"
    if s["gpu"] is not None:
        g = s["gpu"]
        gpu = (f"GPU: WebView2 {g['wv']:.0f}% (Settings renderer {pc(g['r'])}), DWM {g['dwm']:.0f}%"
               + (f", other {g['other']:.0f}%" if g["other"] >= 1 else ""))
    return (f"tab={s['tab']}{' (mixed)' if s['mixed'] else ''} focus={'yes' if s['focus'] else 'NO'} | "
            f"CPU: {r}, WebView2 total {s['cpu_wv']:.0f}% ({kinds}) | {gpu} | "
            f"backend py {s['py']:.0f}% {s['rss']:.0f} MB | PC {s['pc']:.0f}%")


def _state_line(ui: dict) -> str:
    inflight = ui.get("inflight") if isinstance(ui.get("inflight"), list) else []
    inf = "; ".join(f"{_clean(r.get('m'), 5)} {_path(r.get('p'))} {_num(r.get('ms')):.0f}ms by {_clean(r.get('o'), 36) or '-'}"
                    for r in inflight[:6] if isinstance(r, dict))
    pending = ui.get("pending") if isinstance(ui.get("pending"), list) else []
    lazy = ui.get("lazy") if isinstance(ui.get("lazy"), dict) else {}
    lazy_s = ", ".join(f"{_tab_id(k)}:{int(_num(v))}" for k, v in sorted(lazy.items())[:12])
    perf = ui.get("perf") if isinstance(ui.get("perf"), dict) else {}
    where = perf.get("where") if isinstance(perf.get("where"), dict) else {}
    where_s = ", ".join(f"{_clean(k, 14)} {int(_num(v))}" for k, v in sorted(where.items(), key=lambda kv: -_num(kv[1]))[:5])
    cv = ui.get("canvas") if isinstance(ui.get("canvas"), list) else []
    cv_s = "; ".join(_clean(c, 70) for c in cv[:3])
    return (f"building now: {_clean(ui.get('building'), 24) or 'nothing'} | in flight {len(inflight)}"
            f"{' (' + inf + ')' if inf else ''} | tabs still to build {len(pending)}"
            f"{' [' + ','.join(_tab_id(x) for x in pending[:16]) + ']' if pending else ''} | cards waiting for their tab: "
            f"{lazy_s or 'none'} | focused element: {_clean(ui.get('focusel') or '', 40) or '-'}"
            + (f" | page: rAF {_num(perf.get('raf')):.0f}/s, main thread {_num(perf.get('main')):.0f}%, "
               f"{int(_num(perf.get('anims')))} animations ({int(_num(perf.get('endless')))} endless)" if perf.get("ok") else "")
            + (f" | animations by area: {where_s}" if where_s else "")
            + (f" | canvases: {cv_s}" if cv_s else ""))


def _sample(P: _Procs, gpu_state: dict, psutil) -> None:
    """One resource sample while Settings is open: measure, charge to the tab, write the lines."""
    global _renderer_hint
    cpu = P.poll()
    P.settle_renderer(cpu)
    _renderer_hint = (P.renderer, P.how)
    by_kind: dict[str, float] = {}
    for _pid, (v, kind) in cpu.items():
        by_kind[kind] = by_kind.get(kind, 0.0) + v
    cpu_wv = sum(by_kind.values())
    cpu_r = cpu[P.renderer][0] if P.renderer in cpu else None
    py = P.me.cpu_percent(None)
    rss = P.me.memory_info().rss / 1048576
    pc = psutil.cpu_percent(None)

    gpu = None
    g = gpu_state.get("data")
    if g is not None and time.monotonic() - gpu_state.get("at", 0.0) <= _GPU_EVERY * 2.5:
        wv_pids = set(cpu)
        wv = sum(sum(d.values()) for p, d in g.items() if p in wv_pids)
        dwm = 0.0
        other = 0.0
        for p, d in g.items():
            if p in wv_pids:
                continue
            try:
                nm = psutil.Process(p).name().lower()
            except Exception:
                nm = ""
            if nm == "dwm.exe":
                dwm += sum(d.values())
            else:
                other += sum(d.values())
        r = sum(g.get(P.renderer, {}).values()) if P.renderer is not None else None
        gpu = {"wv": wv, "dwm": dwm, "other": other, "r": r}

    with _lock:
        now = time.monotonic()
        _account(now)
        total = sum(_slice.values())
        tab = _cur_tab or "?"
        mixed = False
        if _slice:
            tab, secs = max(_slice.items(), key=lambda kv: kv[1])
            mixed = total > 0 and secs / total < 0.7
        _slice.clear()
        ui = _ui
        focus = _cur_focus
        sample = {"tab": tab, "mixed": mixed, "focus": focus, "cpu_r": cpu_r, "cpu_wv": cpu_wv, "by_kind": by_kind,
                  "gpu": gpu, "py": py, "rss": rss, "pc": pc}
        if not mixed:                                 # a sample that spans two tabs would blame the wrong one
            st = _stat(tab)
            st["n"] += 1
            st["cpu_wv"] += cpu_wv
            if cpu_r is not None:
                st["cpu_r"] += cpu_r
                st["n_r"] += 1
            if gpu is not None:
                st["n_gpu"] += 1
                st["gpu_wv"] += gpu["wv"]
                st["gpu_dwm"] += gpu["dwm"]
                st["gpu_r"] += gpu["r"] or 0.0
        _say("SAMPLE", _sample_line(sample))
        _write("STATE: " + _state_line(ui))
        try:
            from engine.core import dbg
            dbg.ev("settings", "state: " + _state_line(ui))
        except Exception:
            pass


def _loop() -> None:
    try:
        import psutil
    except Exception:
        _say("ERROR", "psutil is missing - no CPU/GPU numbers (pip install psutil)")
        return
    try:
        P = _Procs(psutil)
    except Exception as e:
        _say("ERROR", f"cannot start the sampler: {e}")
        return
    gpu_state: dict = {"data": None, "at": 0.0}
    psutil.cpu_percent(None)
    last_gpu = 0.0
    last_disc = 0.0
    while True:
        opened = False
        try:
            with _lock:
                opened = _cur_open and (time.monotonic() - _ui_at) < _OPEN_TTL
            if not opened:
                _wake.wait(_CLOSED_EVERY)
                if _wake.is_set():
                    _wake.clear()
                    continue                          # Settings just opened: go straight to sampling
                P.closed_ticks += 1
                if P.closed_ticks % 4 == 1:
                    P.discover()
                P.note_idle(P.poll())
                continue
            time.sleep(_EVERY)
            if not P.procs and time.monotonic() - last_disc >= 30.0:     # not found (yet): look again, but not every sample
                last_disc = time.monotonic()
                P.discover()
            if time.monotonic() - last_gpu >= _GPU_EVERY:
                last_gpu = time.monotonic()
                data = _gpu_by_pid()
                if data is not None:
                    gpu_state["data"], gpu_state["at"] = data, time.monotonic()
            _sample(P, gpu_state, psutil)
        except Exception as e:
            _say("ERROR", f"sampler: {e}", trace=False)
            time.sleep(5)


def _ensure() -> None:
    global _started
    if _started:
        return
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_loop, name="lyra-settings-log", daemon=True).start()
        _write("---- Settings log started (see settings_log.py; GET /settings-log for the table) ----")


# --- the report ------------------------------------------------------------------------------
def _avg(total: float, n: int) -> float | None:
    return total / n if n else None


def report() -> dict:
    with _lock:
        now = time.monotonic()
        _account(now)
        tabs = {}
        for tab, s in _tabs.items():
            tabs[tab] = {
                "shown_s": round(s["secs"], 1),
                "unfocused_s": round(s["unfocused_secs"], 1),
                "samples": s["n"],
                "cpu_settings_renderer_pct": _round(_avg(s["cpu_r"], s["n_r"])),
                "cpu_webview_total_pct": _round(_avg(s["cpu_wv"], s["n"])),
                "gpu_webview_pct": _round(_avg(s["gpu_wv"], s["n_gpu"])),
                "gpu_settings_renderer_pct": _round(_avg(s["gpu_r"], s["n_gpu"])),
                "gpu_dwm_pct": _round(_avg(s["gpu_dwm"], s["n_gpu"])),
                "raf_per_s": _round(_avg(s["raf"], s["n_ui"])),
                "main_thread_pct": _round(_avg(s["main"], s["n_ui"])),
                "animations": _round(_avg(s["anims"], s["n_ui"])),
                "endless_animations": _round(_avg(s["endless"], s["n_ui"])),
                "requests": s["req"],
                "requests_for_tabs_not_shown": s["req_bg"],
                "requests_by": {k[4:]: s[k] for k in ("req_build", "req_lazy", "req_poll", "req_user") if s[k]},
                "request_avg_ms": _round(_avg(s["req_ms"], s["req"])),
                "request_max_ms": round(s["req_max"]),
                "built_ms": s["built_ms"], "built_by": s["built_why"],
            }
        open_now = _cur_open and (now - _ui_at) < _OPEN_TTL
        return {
            "on": on(), "open": open_now, "shown_tab": _cur_tab, "focus": _cur_focus,
            "open_for_s": round(now - _open_since, 1) if open_now else 0,
            "max_requests_in_flight": _max_inflight,
            "last_state": _ui if isinstance(_ui, dict) else {},
            "tabs": tabs, "recent": list(_events)[-40:],
        }


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def table_text() -> str:
    """The per-tab table as plain text (what is written to the log when Settings closes)."""
    rep = report()
    head = (f"{'tab':<13}{'shown':>7}{'unfoc':>7}{'CPU rend':>9}{'CPU wv':>8}{'GPU wv':>8}{'GPU dwm':>8}"
            f"{'rAF/s':>7}{'main%':>7}{'anims':>7}{'reqs':>6}{'notshown':>9}{'slowest':>8}{'built':>8}")

    def f(v, w, unit=""):
        return f"{'-' if v is None else format(v, '.0f') + unit:>{w}}"
    lines = [head]
    for tab, t in sorted(rep["tabs"].items(), key=lambda kv: -kv[1]["shown_s"]):
        lines.append(f"{tab:<13}{t['shown_s']:>6.0f}s{t['unfocused_s']:>6.0f}s{f(t['cpu_settings_renderer_pct'], 9, '%')}"
                     f"{f(t['cpu_webview_total_pct'], 8, '%')}{f(t['gpu_webview_pct'], 8, '%')}{f(t['gpu_dwm_pct'], 8, '%')}"
                     f"{f(t['raf_per_s'], 7)}{f(t['main_thread_pct'], 7, '%')}{f(t['animations'], 7)}"
                     f"{t['requests']:>6}{t['requests_for_tabs_not_shown']:>9}{t['request_max_ms']:>6}ms"
                     f"{f(t['built_ms'], 6, 'ms')}")
    lines.append("CPU % = share of ONE core (100% = a whole core). 'notshown' = requests started for a tab the user had "
                 "not opened. Rows with fewer than 2 samples are not reliable. Max requests in flight at once: "
                 f"{rep['max_requests_in_flight']}.")
    return "\n".join(lines)


def _write_table() -> None:
    try:
        txt = table_text()
        _write("TABLE (per tab, averages while shown):\n" + txt)
        from engine.core import dbg
        for ln in txt.splitlines():
            dbg.ev("settings", "table: " + ln)
    except Exception:
        pass


def http_get() -> dict:
    """Answer of GET /settings-log."""
    if not on():
        return {"on": False, "note": "Settings log is off (LYRA_TRACE=0 or LYRA_SETTINGS_LOG=0)."}
    rep = report()
    rep["table"] = table_text()
    pid, how = _renderer_hint
    rep["settings_renderer_pid"], rep["settings_renderer_found_by"] = pid, how or "not identified"
    return rep


_renderer_hint: tuple = (None, "")


def reset() -> None:
    """Forget all numbers (tests, or to start a clean measurement)."""
    global _ui, _ui_at, _cur_tab, _cur_open, _cur_focus, _last_acct, _max_inflight, _open_since
    with _lock:
        _tabs.clear()
        _slice.clear()
        _events.clear()
        _ui, _ui_at, _cur_tab, _cur_open, _cur_focus, _last_acct, _max_inflight, _open_since = {}, 0.0, "", False, False, 0.0, 0, 0.0
