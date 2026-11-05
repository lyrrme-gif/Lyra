"""Lyra — live PC stats for the island's Stats tab.

snapshot() is cheap enough to poll every couple of seconds. CPU / RAM / disk / network /
uptime / top apps come from psutil; the GPU comes from nvidia-smi when an NVIDIA card is
present (otherwise "gpu" is None and the tab shows "no GPU data"). The optional speed
test downloads / uploads a little data from Cloudflare's speed endpoint when asked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

try:
    import psutil
except Exception:  # pragma: no cover - psutil is a project requirement
    psutil = None

_lock = threading.Lock()
_GB = 1024 ** 3
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ── CPU load ─────────────────────────────────────────────────────────────────────────────────
# psutil.cpu_percent(None) compares against ONE process-wide baseline that every caller shares
# (the voice "how's my PC" action, the stats poll, ...). If anything else reads it in between,
# or two polls land close together, the window is ~0 and it reports a flat 0 %. So we keep our
# own baseline in a tiny sampler thread and snapshot() just reads the latest number.
_cpu_state: dict = {"pct": None}
_cpu_thread_started = False
_last_cpu_request = 0.0
_SAMPLER_IDLE_S = 30.0          # nobody asked for the CPU number for this long: the sampler thread ends (no 1 Hz timer for nothing)


def _busy_idle(t) -> tuple[float, float]:
    idle = t.idle + getattr(t, "iowait", 0.0)
    total = sum(t) - getattr(t, "guest", 0.0) - getattr(t, "guest_nice", 0.0)
    return total, idle


def _cpu_sampler() -> None:
    global _cpu_thread_started
    prev = None
    while True:
        if time.monotonic() - _last_cpu_request > _SAMPLER_IDLE_S:
            _cpu_state["pct"] = None          # stale after a pause: the next request measures afresh
            _cpu_thread_started = False
            return
        try:
            cur = _busy_idle(psutil.cpu_times())
            if prev is not None:
                d_total, d_idle = cur[0] - prev[0], cur[1] - prev[1]
                if d_total > 0:
                    _cpu_state["pct"] = max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))
            prev = cur
        except Exception:
            pass
        time.sleep(1.0)


def _ensure_cpu_sampler() -> None:
    global _cpu_thread_started
    if _cpu_thread_started or psutil is None:
        return
    _cpu_thread_started = True
    threading.Thread(target=_cpu_sampler, name="lyra-cpu-sampler", daemon=True).start()


def _cpu_pct() -> int:
    global _last_cpu_request
    _last_cpu_request = time.monotonic()
    _ensure_cpu_sampler()
    if _cpu_state["pct"] is None:  # first poll, sampler hasn't got two readings yet
        try:
            return round(psutil.cpu_percent(interval=0.25))
        except Exception:
            return 0
    return round(_cpu_state["pct"])


# (The sampler is started by the first _cpu_pct() call, not at import: the Stats tab may never be opened.)

# ── static facts, looked up once ─────────────────────────────────────────────────────────────
_cpu_name = ""


def _get_cpu_name() -> str:
    global _cpu_name
    if _cpu_name:
        return _cpu_name
    name = ""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = str(winreg.QueryValueEx(k, "ProcessorNameString")[0])
        except Exception:
            name = ""
    if not name:
        try:
            import platform
            name = platform.processor() or ""
        except Exception:
            name = ""
    for junk in ("(R)", "(TM)", "(tm)", "CPU", "Processor", "with Radeon Graphics"):
        name = name.replace(junk, "")
    _cpu_name = " ".join(name.split()) or "Processor"
    return _cpu_name


# ── GPU (NVIDIA via nvidia-smi) ──────────────────────────────────────────────────────────────
_gpu_cache: dict = {"at": 0.0, "val": None}
_gpu_absent_until = 0.0


def _gpu() -> dict | None:
    global _gpu_absent_until
    now = time.time()
    if now < _gpu_absent_until:
        return None
    if now - _gpu_cache["at"] < 1.5:
        return _gpu_cache["val"]
    exe = shutil.which("nvidia-smi")
    if not exe:
        _gpu_absent_until = now + 120
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW,
        ).stdout.strip().splitlines()
        name, util, used, total = [p.strip() for p in out[0].split(",")[:4]]
        val = {
            "name": name.replace("NVIDIA ", "", 1),
            "pct": float(util),
            "mem_used_gb": round(float(used) / 1024, 1),
            "mem_total_gb": round(float(total) / 1024, 1),
        }
    except Exception:
        _gpu_absent_until = now + 60
        return None
    _gpu_cache.update(at=now, val=val)
    return val


# ── network rate ─────────────────────────────────────────────────────────────────────────────
_net = {"t": 0.0, "rx": 0, "tx": 0, "down": 0.0, "up": 0.0}


def _network() -> dict:
    now = time.time()
    try:
        c = psutil.net_io_counters()
    except Exception:
        return {"down_bps": 0, "up_bps": 0}
    if _net["t"] and now - _net["t"] >= 0.5:
        dt = now - _net["t"]
        _net["down"] = max(0.0, (c.bytes_recv - _net["rx"]) / dt)
        _net["up"] = max(0.0, (c.bytes_sent - _net["tx"]) / dt)
    if not _net["t"] or now - _net["t"] >= 0.5:
        _net.update(t=now, rx=c.bytes_recv, tx=c.bytes_sent)
    return {"down_bps": int(_net["down"]), "up_bps": int(_net["up"])}


# ── top apps (grouped by program name: Chrome is one entry, not forty processes) ──────────────
_FRIENDLY = {
    "chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox", "brave": "Brave",
    "code": "Visual Studio Code", "spotify": "Spotify", "discord": "Discord", "steam": "Steam",
    "explorer": "File Explorer", "python": "Python", "pythonw": "Python", "unity": "Unity",
    "unityhub": "Unity Hub", "obs64": "OBS Studio", "whatsapp": "WhatsApp", "telegram": "Telegram",
    "teams": "Microsoft Teams", "ms-teams": "Microsoft Teams", "slack": "Slack", "notion": "Notion",
    "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "lyra": "Lyra", "node": "Node.js",
}
_SKIP = {"system idle process", "system", "registry", "memcompression", "memory compression", "idle"}
_apps_cache: dict = {"at": 0.0, "val": {"ram": [], "cpu": []}}


def _pretty(name: str) -> str:
    base = name[:-4] if name.lower().endswith(".exe") else name
    return _FRIENDLY.get(base.lower(), base[:1].upper() + base[1:] if base else name)


def _top_apps() -> dict:
    now = time.time()
    if now - _apps_cache["at"] < 3.0:
        return _apps_cache["val"]
    ram: dict[str, float] = {}
    cpu: dict[str, float] = {}
    cores = psutil.cpu_count() or 1
    try:
        for p in psutil.process_iter(["name", "memory_info", "cpu_percent"]):
            try:
                nm = (p.info.get("name") or "").strip()
                if not nm or nm.lower() in _SKIP:
                    continue
                key = _pretty(nm)
                mi = p.info.get("memory_info")
                if mi:
                    ram[key] = ram.get(key, 0.0) + mi.rss
                cpu[key] = cpu.get(key, 0.0) + (p.info.get("cpu_percent") or 0.0) / cores
            except Exception:
                continue
    except Exception:
        pass
    val = {
        "ram": [{"name": k, "mb": int(v / 1024 ** 2)} for k, v in sorted(ram.items(), key=lambda kv: -kv[1])[:4]],
        "cpu": [{"name": k, "pct": round(v, 1)} for k, v in sorted(cpu.items(), key=lambda kv: -kv[1])[:4]],
    }
    _apps_cache.update(at=now, val=val)
    return val


# ── public ───────────────────────────────────────────────────────────────────────────────────
def snapshot() -> dict:
    """Everything the Stats tab shows, in one small dict."""
    if psutil is None:
        return {"ok": False, "error": "psutil is not installed"}
    with _lock:
        out: dict = {"ok": True}
        try:
            freq = psutil.cpu_freq()
            out["cpu"] = {
                "pct": _cpu_pct(),
                "name": _get_cpu_name(),
                "ghz": round(freq.current / 1000, 1) if freq and freq.current else None,
                "procs": len(psutil.pids()),
            }
        except Exception:
            out["cpu"] = {"pct": 0, "name": _get_cpu_name(), "ghz": None, "procs": 0}
        out["gpu"] = _gpu()
        try:
            vm = psutil.virtual_memory()
            out["ram"] = {"pct": round(vm.percent), "used_gb": round((vm.total - vm.available) / _GB, 1),
                          "total_gb": round(vm.total / _GB, 1), "free_gb": round(vm.available / _GB, 1)}
        except Exception:
            out["ram"] = None
        try:
            drive = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
            du = shutil.disk_usage(drive)
            out["disk"] = {"label": drive.rstrip("\\") or "/", "used_gb": round(du.used / _GB), "total_gb": round(du.total / _GB)}
        except Exception:
            out["disk"] = None
        out["net"] = _network()
        try:
            out["uptime_s"] = int(time.time() - psutil.boot_time())
        except Exception:
            out["uptime_s"] = 0
        out["top"] = _top_apps()
        out["speedtest"] = speedtest_state()
        return out


# ── optional speed test ──────────────────────────────────────────────────────────────────────
_speed: dict = {"running": False, "down_mbps": None, "up_mbps": None, "at": None, "where": "", "error": ""}
_speed_lock = threading.Lock()


def speedtest_state() -> dict:
    with _speed_lock:
        return dict(_speed)


def _run_speedtest() -> None:
    import requests
    base = "https://speed.cloudflare.com"
    down = up = None
    where = ""
    err = ""
    try:
        t0 = time.time()
        got = 0
        with requests.get(f"{base}/__down?bytes=25000000", stream=True, timeout=10) as r:
            where = r.headers.get("cf-meta-city") or r.headers.get("cf-meta-colo") or ""
            for chunk in r.iter_content(chunk_size=65536):
                got += len(chunk)
                if time.time() - t0 > 8:
                    break
        dt = max(time.time() - t0, 0.001)
        down = round(got * 8 / dt / 1e6)
        payload = os.urandom(6_000_000)
        t1 = time.time()
        requests.post(f"{base}/__up", data=payload, timeout=15)
        up = round(len(payload) * 8 / max(time.time() - t1, 0.001) / 1e6)
    except Exception as e:
        err = "No connection" if down is None else "Upload test failed"
        try:
            from engine.bridge import ui
            ui.log.warning("speed test failed: %s", e)
        except Exception:
            pass
    with _speed_lock:
        _speed.update(running=False, down_mbps=down, up_mbps=up, where=where, error=err,
                      at=int(time.time() * 1000) if down is not None else _speed["at"])


def start_speedtest() -> bool:
    """Start a speed test in the background. False if one is already running."""
    with _speed_lock:
        if _speed["running"]:
            return False
        _speed.update(running=True, error="")
    threading.Thread(target=_run_speedtest, name="lyra-speedtest", daemon=True).start()
    return True
