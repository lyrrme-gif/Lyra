"""Lyra — "Test recording performance" (Settings -> Recordings).

Answers the question a video cannot: does RECORDING slow the rest of the PC down? For each way Lyra can capture the
screen it runs a short real capture (nothing is saved) and, at the same time, measures how fast Windows' own
compositor (DWM) keeps producing frames, with DwmFlush(). DWM's frame rate IS the PC's animation frame rate: windows,
menus and animations all go through it. The report compares it with the rate measured before any capture started.
It also says how many frames the capture itself delivered, and WHY a faster method was not used (the exact text ffmpeg
printed). While each trial runs, a monitor samples once a second: CPU and RAM of the whole PC, which programs use the CPU
(ffmpeg, dwm, Lyra, the island's webview...), and the graphics card (3D load, encoder load, memory; needs nvidia-smi).
The last trial is a REAL Lyra recording (sound, island pill, watchdog and all), deleted afterwards: it is the only one
that includes everything Lyra itself does. Meant to be pasted to whoever is debugging: it contains no personal data.

DWM only produces a frame when something on screen changes, so keep something moving (a video, testufo.com) while it runs.
Windows only. Never runs while a recording is going.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import screen_record as sr

SECONDS = 6


def summarize(ms: list[float]) -> dict:
    """Frame intervals (ms) -> {fps, p95_ms, slow_pct (share of frames later than 25 ms = below 40 fps)}. {} = too few."""
    if len(ms) < 8:
        return {}
    s = sorted(ms)
    return {"fps": round(1000.0 * len(ms) / sum(ms), 1), "p95_ms": round(s[int(0.95 * (len(s) - 1))], 1),
            "slow_pct": round(100.0 * sum(1 for x in ms if x > 25.0) / len(ms))}


def parse_gpu_line(line: str) -> dict:
    """One line of `nvidia-smi --query-gpu=utilization.gpu,utilization.encoder,memory.used --format=csv,noheader,nounits`."""
    try:
        a = [float(x) for x in line.replace("%", "").split(",")]
        return {"gpu": a[0], "enc": a[1], "mem": a[2]} if len(a) >= 3 else {}
    except ValueError:
        return {}


def summarize_samples(cpu: list[float], ram: list[float], ram_mb: list[float], gpu: list[dict], procs: dict[str, list[float]]) -> dict:
    """Lists of once-a-second samples -> averages / maxima, and the busiest programs by average CPU (percent of the whole PC)."""
    def avg(x): return round(sum(x) / len(x), 1) if x else None
    out: dict = {"cpu_avg": avg(cpu), "cpu_max": round(max(cpu), 1) if cpu else None, "ram_pct": avg(ram), "ram_mb": round(avg(ram_mb) or 0)}
    if gpu:
        out["gpu_avg"] = avg([g["gpu"] for g in gpu]); out["gpu_max"] = max(g["gpu"] for g in gpu)
        out["enc_avg"] = avg([g["enc"] for g in gpu]); out["gpu_mem"] = round(avg([g["mem"] for g in gpu]) or 0)
    top = sorted(((n, sum(v) / len(v)) for n, v in procs.items() if v), key=lambda t: -t[1])[:6]
    out["top"] = [(n, round(v, 1)) for n, v in top if v >= 0.3]
    return out


class Monitor:
    """Samples the PC once a second until stop(): CPU, RAM, per-program CPU (psutil) and the graphics card (nvidia-smi,
    when it is installed). Costs a little itself, so it only runs during the test."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._cpu: list[float] = []; self._ram: list[float] = []; self._ram_mb: list[float] = []
        self._gpu: list[dict] = []; self._procs: dict[str, list[float]] = {}
        self._smi = None
        self._threads: list[threading.Thread] = []

    def start(self) -> "Monitor":
        try:
            import psutil
            psutil.cpu_percent(None)
            self._cores = psutil.cpu_count() or 1
            self._threads.append(threading.Thread(target=self._loop, daemon=True))
        except Exception:  # noqa: BLE001 - no psutil: no numbers, the test still works
            pass
        try:
            import shutil
            exe = shutil.which("nvidia-smi")
            if exe:
                self._smi = subprocess.Popen([exe, "--query-gpu=utilization.gpu,utilization.encoder,memory.used",
                                              "--format=csv,noheader,nounits", "-l", "1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), text=True)
                self._threads.append(threading.Thread(target=self._read_smi, daemon=True))
        except Exception:  # noqa: BLE001
            self._smi = None
        for t in self._threads:
            t.start()
        return self

    def _loop(self) -> None:
        import psutil
        while not self._stop.wait(1.0):
            self._cpu.append(psutil.cpu_percent(None))
            vm = psutil.virtual_memory()
            self._ram.append(vm.percent); self._ram_mb.append(vm.used / (1 << 20))
            now: dict[str, float] = {}
            for p in psutil.process_iter(["name"]):
                try:
                    n = str(p.info.get("name") or "?").lower().removesuffix(".exe")
                    now[n] = now.get(n, 0.0) + p.cpu_percent(None) / self._cores
                except Exception:  # noqa: BLE001
                    continue
            for n, v in now.items():
                self._procs.setdefault(n, []).append(v)

    def _read_smi(self) -> None:
        try:
            for line in self._smi.stdout:                                        # type: ignore[union-attr]
                g = parse_gpu_line(line)
                if g:
                    self._gpu.append(g)
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> dict:
        self._stop.set()
        if self._smi is not None:
            try:
                self._smi.terminate()
            except Exception:  # noqa: BLE001
                pass
        for t in self._threads:
            t.join(2.0)
        procs = {n: v[1:] for n, v in self._procs.items()}                       # the first reading of a program is always 0
        return summarize_samples(self._cpu, self._ram, self._ram_mb, self._gpu, procs)


def measure_dwm(seconds: float) -> dict:
    """How fast Windows composes frames right now (blocks for `seconds`)."""
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        flush = ctypes.WinDLL("dwmapi").DwmFlush
    except Exception:  # noqa: BLE001
        return {}
    ms: list[float] = []
    end = time.perf_counter() + seconds
    last = None
    while time.perf_counter() < end:
        flush()
        now = time.perf_counter()
        if last is not None:
            ms.append((now - last) * 1000.0)
        last = now
    return summarize(ms)


def parse_progress(text: str) -> dict:
    """The last values of ffmpeg's `-progress` lines (frame, dup_frames, drop_frames, speed) and its last error line."""
    out: dict = {}
    err = ""
    for line in text.splitlines():
        k, eq, v = line.strip().partition("=")
        if eq and k in ("frame", "dup_frames", "drop_frames"):
            try:
                out[k] = int(v)
            except ValueError:
                pass
        elif eq and k == "speed":
            try:
                out["speed"] = float(v.rstrip("x "))
            except ValueError:
                pass
        elif line.strip() and not (eq and k.isidentifier()):
            err = line.strip()[:200]
    if err:
        out["error"] = err
    return out


def _trial(exe: str, prof: dict, plan: dict, seconds: int, out_fps: int = 0) -> dict:
    """One real capture (the same command a recording would use, written to nowhere) with the DWM and the PC measured
    meanwhile. out_fps: capture at prof's rate but write fewer frames per second (the frame-rate drop happens in ffmpeg)."""
    cmd = sr.build_cmd(exe, "trial.null", prof=prof, plan=plan)[:-1] + ["-t", str(seconds), "-f", "null", "-"]
    if out_fps:
        i = cmd.index("-vf")
        cmd[i + 1] = f"fps={int(out_fps)}," + cmd[i + 1]
    box: dict = {}
    th = threading.Thread(target=lambda: box.update(dwm=measure_dwm(seconds - 1.5)), daemon=True)
    try:
        import os
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             env={**os.environ, **(plan.get("env") or {})}, creationflags=sr._proc_flags(prof))
        sr._lower_gpu_priority(p)                             # the same as a real recording
        time.sleep(1.2)                                       # let the grabber start before measuring
        mon = Monitor().start()
        th.start()
        _o, e = p.communicate(timeout=seconds + 30)
        box["pc"] = mon.stop()
    except Exception as ex:  # noqa: BLE001
        return {"error": str(ex)[:160]}
    th.join(seconds)
    res = parse_progress((e or b"").decode("utf-8", "replace"))
    res["dwm"] = box.get("dwm") or {}
    res["pc"] = box.get("pc") or {}
    res["returncode"] = p.returncode
    if res.get("frame"):
        res["capture_fps"] = round(res["frame"] / seconds, 1)
    return res


def _real_trial(seconds: int) -> dict:
    """A real Lyra recording with everything it does (sound, island pill, watchdog, status), measured the same way, then deleted."""
    import os
    s = sr.start()
    if not s.get("recording"):
        return {"error": s.get("error") or "the recording did not start"}
    res: dict = {}
    try:
        time.sleep(1.5)
        mon = Monitor().start()
        res["dwm"] = measure_dwm(seconds - 1.5)
        res["pc"] = mon.stop()
        h = sr.status().get("health") or {}
        res.update({"frame": h.get("frames", 0), "dup_frames": h.get("dup", 0), "drop_frames": h.get("drop", 0), "speed": h.get("speed", "?")})
        res["capture_fps"] = h.get("fps") or None
        res["how"] = sr.status().get("capture", "")
    finally:
        done = sr.stop()
        try:
            if done.get("path"):
                sr.delete_recording(os.path.basename(done["path"]))
        except Exception:  # noqa: BLE001
            pass
    return res


def _pc(d: dict) -> list[str]:
    if not d:
        return ["  PC load: could not measure"]
    out = [f"  PC load: CPU {d.get('cpu_avg')}% average (max {d.get('cpu_max')}%), RAM {d.get('ram_pct')}% ({d.get('ram_mb')} MB in use)"]
    if "gpu_avg" in d:
        out.append(f"  Graphics card: 3D {d['gpu_avg']}% average (max {d['gpu_max']}%), video encoder {d['enc_avg']}%, memory {d['gpu_mem']} MB")
    else:
        out.append("  Graphics card: not measured (nvidia-smi not found)")
    if d.get("top"):
        out.append("  Busiest programs (share of the whole CPU): " + ", ".join(f"{n} {v}%" for n, v in d["top"]))
    return out


def format_report(info: dict) -> str:
    def dwm(d: dict) -> str:
        return f"{d['fps']} fps (slowest 5%: {d['p95_ms']} ms, {d['slow_pct']}% of frames under 40 fps)" if d else "could not measure"
    lines = ["Lyra recording performance test", f"ffmpeg: {info.get('ffmpeg', '?')}", f"settings: {info.get('settings', '?')}",
             f"screens: {info.get('screens', '?')}  (first graphics adapter outputs: {info.get('outputs', '?')})", "",
             f"Windows animation rate with NO capture: {dwm(info.get('baseline') or {})}", *_pc(info.get("baseline_pc") or {}), ""]
    for t in info.get("trials", []):
        lines.append(f"[{t['name']}]")
        if t.get("skipped"):
            lines.append(f"  not tested: {t['skipped']}")
        else:
            r = t["result"]
            if r.get("error") and not r.get("frame"):
                lines.append(f"  FAILED: {r['error']}")
            else:
                lines.append(f"  capture delivered {r.get('capture_fps', '?')} fps (repeated {r.get('dup_frames', 0)}, dropped {r.get('drop_frames', 0)}, speed {r.get('speed', '?')}x)")
                lines.append(f"  Windows animation rate DURING capture: {dwm(r.get('dwm') or {})}")
                lines.extend(_pc(r.get("pc") or {}))
                if r.get("how"):
                    lines.append(f"  (Lyra said: {r['how']})")
        lines.append("")
    for k, v in (info.get("why") or {}).items():
        lines.append(f"why {k} was not usable: {v}")
    return "\n".join(lines).strip()


def run(seconds: int = SECONDS) -> dict:
    """-> {ok, report}. Takes about 30 seconds."""
    if sys.platform != "win32":
        return {"ok": False, "report": "The performance test only works on Windows."}
    if sr._recording_dir_busy():
        return {"ok": False, "report": "Stop the recording first, then run the test."}
    exe = sr._ffmpeg()
    if not exe:
        return {"ok": False, "report": "ffmpeg wasn't found."}
    prof = sr.profile()
    prof["sync"] = sr._sync_args(exe)
    if prof.get("encoder") == "auto":
        prof["gpu"] = sr._gpu_preset(exe)
    prof.update(sr._tuning(exe, prof))
    region = prof.get("region")
    info: dict = {"ffmpeg": " ".join(str(x) for x in sr._ffmpeg_version(exe)), "trials": [],
                  "settings": f"{prof['fps']} fps, crf {prof['crf']}, encoder {'NVENC ' + str(prof['gpu']) if prof.get('gpu') else 'x264'}, cpu {prof.get('cpu')}, capture {prof.get('capture')}",
                  "screens": sr._monitor_count(), "outputs": len(sr._dxgi_outputs())}
    mon0 = Monitor().start()
    info["baseline"] = measure_dwm(4.0)
    info["baseline_pc"] = mon0.stop()
    gdi = sr.plan_capture(exe, region, grab="gdigrab")
    info["trials"].append({"name": "A  old GDI grabber (gdigrab)", "result": _trial(exe, prof, gdi, seconds)})
    idx = sr._dd_output(exe, region)
    if idx is None:
        info["trials"].append({"name": "B  Desktop Duplication, frames through memory", "skipped": sr.probe_why.get("ddagrab") or "not available for this screen setup"})
        info["trials"].append({"name": "C  Desktop Duplication, zero-copy to the graphics card encoder", "skipped": "needs B"})
    else:
        dd = {"env": {}, "input": [], "crop": None, "verified": True, "grab": "ddagrab", "output_idx": idx, "info": ""}
        info["trials"].append({"name": "B  Desktop Duplication, frames through memory", "result": _trial(exe, prof, dd, seconds)})
        if prof.get("gpu") and sr._probe_nvenc_bgra(exe, idx, int(prof["fps"]), str(prof["gpu"])):
            info["trials"].append({"name": "B2 Desktop Duplication, raw colours straight into the graphics card encoder (no CPU colour conversion)",
                                   "result": _trial(exe, prof, {**dd, "raw_rgb": True}, seconds)})
        if prof.get("gpu") and not prof.get("max_h"):
            ok = sr._probe_zero_copy(exe, idx, int(prof["fps"]), str(prof["gpu"]))
            if ok:
                info["trials"].append({"name": "C  Desktop Duplication, zero-copy to the graphics card encoder", "result": _trial(exe, prof, {**dd, "zero_copy": True}, seconds)})
            else:
                info["trials"].append({"name": "C  Desktop Duplication, zero-copy to the graphics card encoder", "skipped": "the test encode failed"})
        else:
            info["trials"].append({"name": "C  Desktop Duplication, zero-copy to the graphics card encoder", "skipped": "needs the graphics-card encoder (Encoder: Auto) and the screen's own size"})
    if idx is not None and int(prof["fps"]) < 60:
        dd60 = {"env": {}, "input": [], "crop": None, "verified": True, "grab": "ddagrab", "output_idx": idx, "info": ""}
        info["trials"].append({"name": f"D  Desktop Duplication captured at 60 fps, written as {int(prof['fps'])} fps",
                               "result": _trial(exe, {**prof, "fps": 60}, dd60, seconds, out_fps=int(prof["fps"]))})
    info["trials"].append({"name": "E  a REAL Lyra recording (sound, island pill, everything; deleted afterwards)", "result": _real_trial(seconds + 2)})
    info["why"] = dict(sr.probe_why)
    return {"ok": True, "report": format_report(info)}
