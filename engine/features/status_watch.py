"""Lyra — system status feed for the island: battery/charging and microphone use.

Pure standard library (ctypes + winreg), so nothing to install. One small thread polls Windows
and hands changes to the UI bridge, which shows them in the notch like the volume HUD.

  battery:  plugged in / unplugged / low (20 %, 10 %)  -> {"type":"status","kind":"battery",...}
  bluetooth: audio device (headphones, speaker...) connects / disconnects
            -> {"type":"status","kind":"bluetooth","text":"WH-1000XM4 connected"}
  privacy:  Lyra microphone in use                     -> {"type":"privacy","mic":bool}

The first reading is only a baseline, so nothing pops up at start-up.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from typing import Callable

from engine.bridge import ui

log = ui.log

POLL_S = 2.0
LOW_LEVELS = (20, 10)   # percent; each warns once per discharge

_state: dict = {"battery": None, "mic": False}
_lock = threading.Lock()
_started = False
_push: Callable[[dict], None] | None = None


class _PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def read_battery() -> dict | None:
    """{"percent": 0-100, "charging": bool} or None on a desktop PC / unknown."""
    st = _PowerStatus()
    try:
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):
            return None
    except Exception:
        return None
    if st.BatteryFlag & 128 or st.BatteryLifePercent > 100:   # no battery / unknown
        return None
    return {"percent": int(st.BatteryLifePercent), "charging": st.ACLineStatus == 1}


class _BtSearch(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong), ("fReturnAuthenticated", ctypes.c_int),
        ("fReturnRemembered", ctypes.c_int), ("fReturnUnknown", ctypes.c_int),
        ("fReturnConnected", ctypes.c_int), ("fIssueInquiry", ctypes.c_int),
        ("cTimeoutMultiplier", ctypes.c_ubyte), ("hRadio", ctypes.c_void_p),
    ]


class _SysTime(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ushort) for n in
                ("year", "month", "dow", "day", "hour", "minute", "second", "ms")]


class _BtDevice(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong), ("Address", ctypes.c_ulonglong),
        ("ulClassofDevice", ctypes.c_ulong), ("fConnected", ctypes.c_int),
        ("fRemembered", ctypes.c_int), ("fAuthenticated", ctypes.c_int),
        ("stLastSeen", _SysTime), ("stLastUsed", _SysTime), ("szName", ctypes.c_wchar * 248),
    ]


def read_bluetooth_audio() -> dict[int, str] | None:
    """Connected Bluetooth audio devices as {address: name}; None when Bluetooth is unavailable.
    Uses the paired-device list (no scanning, no radio traffic). Audio = major device class 4."""
    try:
        bth = ctypes.windll.LoadLibrary("bthprops.cpl")
    except Exception:
        return None
    params = _BtSearch()
    params.dwSize = ctypes.sizeof(_BtSearch)
    params.fReturnAuthenticated = params.fReturnRemembered = params.fReturnConnected = 1
    dev = _BtDevice()
    dev.dwSize = ctypes.sizeof(_BtDevice)
    bth.BluetoothFindFirstDevice.restype = ctypes.c_void_p
    bth.BluetoothFindFirstDevice.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    bth.BluetoothFindNextDevice.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    bth.BluetoothFindDeviceClose.argtypes = [ctypes.c_void_p]
    handle = bth.BluetoothFindFirstDevice(ctypes.byref(params), ctypes.byref(dev))
    if not handle:
        return {}   # no paired devices, or no Bluetooth radio
    found: dict[int, str] = {}
    try:
        while True:
            if dev.fConnected and ((dev.ulClassofDevice >> 8) & 0x1F) == 4:
                found[int(dev.Address)] = dev.szName.strip() or "Bluetooth audio"
            if not bth.BluetoothFindNextDevice(handle, ctypes.byref(dev)):
                break
    finally:
        bth.BluetoothFindDeviceClose(handle)
    return found


_CONSENT = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"


def snapshot() -> dict:
    """Current state, for GET /status (the island asks once when it starts)."""
    with _lock:
        return {"battery": _state["battery"], "mic": _state["mic"]}


def set_mic_active(active: bool) -> None:
    """Set Lyra's own microphone privacy state.

    The orange indicator must describe Lyra's microphone ownership, not whether
    some unrelated Windows app has touched the microphone capability registry.
    PTT/wake call this when they actually open/close Lyra's audio stream.
    """
    active = bool(active)
    with _lock:
        changed = _state["mic"] != active
        _state["mic"] = active
        push = _push
    if changed and push:
        push({"type": "privacy", "mic": active})


_sbc_ok = True
_IS_WIN = os.name == "nt"

# ── Brightness without starting PowerShell ───────────────────────────────────────────────────
# Every read used to start a PowerShell process (hundreds of ms of CPU each, every 1-3 s, all day). A laptop panel
# answers the same question straight from the display driver in microseconds, with no process at all.
_IOCTL_QUERY_BRIGHTNESS = 0x230414     # IOCTL_VIDEO_QUERY_DISPLAY_BRIGHTNESS
_ioctl_state: bool | None = None       # None = not checked yet, True = agrees with WMI (use it), False = unusable here


def _ioctl_brightness() -> int | None:
    """Brightness 0-100 of the laptop panel from the display driver, or None."""
    try:
        k32 = ctypes.windll.kernel32                                   # type: ignore[attr-defined]
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
                                    ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        k32.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        bad = ctypes.c_void_p(-1).value
        h = None
        for access in (0xC0000000, 0x80000000):                        # read+write, then read only
            h = k32.CreateFileW("\\\\.\\LCD", access, 3, None, 3, 0, None)
            if h not in (None, 0, bad):
                break
            h = None
        if h is None:
            return None
        try:
            buf = (ctypes.c_ubyte * 3)()                               # DISPLAY_BRIGHTNESS: policy, on-AC level, on-battery level
            got = ctypes.c_ulong(0)
            if not k32.DeviceIoControl(h, _IOCTL_QUERY_BRIGHTNESS, None, 0, ctypes.byref(buf), 3, ctypes.byref(got), None):
                return None
            st = _PowerStatus()
            on_ac = bool(k32.GetSystemPowerStatus(ctypes.byref(st))) and st.ACLineStatus == 1
            v = int(buf[1] if on_ac else buf[2])
            return v if 0 <= v <= 100 else None
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _wmi_brightness() -> int | None:
    """Fallback without extra packages: Windows WMI (laptop panels)."""
    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1).CurrentBrightness"],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000,
        ).stdout.strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None


def read_brightness() -> int | None:
    """Display brightness 0-100, or None when the display doesn't report it.
    Prefers the optional `screen-brightness-control` package, falls back to WMI."""
    global _sbc_ok
    # The Windows provider probes DDC/VCP displays as well as laptop panels.
    # On this machine that probe retries a malformed EDID dozens of times and
    # logs before it raises. WMI reads the laptop panel directly and avoids it.
    if _IS_WIN:
        global _ioctl_state
        if _ioctl_state is not False:
            v = _ioctl_brightness()
            if v is not None:
                if _ioctl_state is True:
                    return v
                w = _wmi_brightness()                  # first read: trust the driver only if WMI says the same
                _ioctl_state = w is not None and abs(w - v) <= 3
                log.info("Brightness: %s", "read from the display driver (no PowerShell)" if _ioctl_state
                         else "driver value differs from WMI, keeping WMI")
                return v if _ioctl_state else w
            if _ioctl_state is None:
                _ioctl_state = False
        return _wmi_brightness()
    if _sbc_ok:
        try:
            import screen_brightness_control as sbc  # type: ignore
            vals = sbc.get_brightness()
            if vals:
                return int(vals[0])
        except ImportError:
            _sbc_ok = False
        except Exception as e:
            # Some laptop/display drivers make screen-brightness-control retry
            # the same unsupported EDID query dozens of times per call. Disable
            # that provider after its first failure and use the quieter WMI
            # fallback for the rest of this process.
            _sbc_ok = False
            log.info("Brightness provider unavailable; switching to WMI: %s", e)
    return _wmi_brightness()


_BRIGHTNESS_IDLE_S = 3.0     # light mode: PowerShell is started for every read on Windows, so look less often ...
_BRIGHTNESS_ACTIVE_S = 12.0  # ... and fast again (the old rate) for this long after the brightness last changed


def _light_mode() -> bool:
    try:
        from engine.core import settings_store
        return settings_store.feature_on("light_mode")
    except Exception:
        return False


def _brightness_loop(push: Callable[[dict], None]) -> None:
    last: int | None = None
    misses = 0
    changed_at = 0.0
    light = _light_mode()
    while True:
        v = read_brightness()
        if v is None:
            misses += 1
            if misses >= 3:        # no supported display: stop polling quietly
                return
        else:
            misses = 0
            if last is not None and abs(v - last) >= 1:
                changed_at = time.monotonic()
                push({"type": "status", "kind": "brightness", "percent": v, "ts": int(time.time() * 1000)})
            last = v
        fast = 0.8 if _sbc_ok else 1.2
        if _ioctl_state is True:
            time.sleep(1.0)                    # a driver read costs nothing: always look once a second
            continue
        if light and time.monotonic() - changed_at > _BRIGHTNESS_ACTIVE_S:
            time.sleep(_BRIGHTNESS_IDLE_S)
        else:
            time.sleep(fast)


def start(push: Callable[[dict], None]) -> None:
    """Start the polling thread. `push` sends an event dict to the island."""
    global _started, _push
    if _started:
        return
    _started = True
    _push = push

    # Seed the state once before starting the watcher. This is not a second
    # polling loop; it prevents the Island from briefly showing "unknown" on
    # startup while still keeping the normal watcher at its low 2s cadence.
    try:
        initial_bat = read_battery()
        with _lock:
            _state["battery"] = initial_bat
    except Exception:
        pass

    def loop() -> None:
        warned: set[int] = set()
        first = True
        bt_prev: dict[int, str] | None = None
        bt_last: dict[int, float] = {}   # address -> last time we announced it (anti-flap)
        while True:
            try:
                bat = read_battery()
                # Microphone is deliberately NOT inferred from Windows capability
                # timestamps; they can represent another app and stay stale. Lyra's
                # voice modules explicitly set the mic state when they own it.
                with _lock:
                    prev = dict(_state)
                    mic = bool(_state["mic"])
                    _state.update(battery=bat)

                if bat and prev["battery"] and not first:
                    pct, charging = bat["percent"], bat["charging"]
                    prev_pct = prev["battery"]["percent"]
                    if charging != prev["battery"]["charging"]:
                        warned.clear()
                        push({"type": "status", "kind": "battery", "percent": pct, "charging": charging,
                              "text": "Charging" if charging else "On battery", "ts": int(time.time() * 1000)})
                    elif not charging:
                        low_announced = False
                        for level in LOW_LEVELS:
                            if pct <= level and level not in warned:
                                warned.update(l for l in LOW_LEVELS if l >= level)
                                push({"type": "status", "kind": "battery", "percent": pct, "charging": False,
                                      "text": "Low battery", "ts": int(time.time() * 1000)})
                                try:
                                    from engine.bridge import ui_bridge
                                    ui_bridge.push_mascot_emote("lowbat", 8000)
                                except Exception:
                                    pass
                                low_announced = True
                                break
                        # Soft percent updates keep the expanded chip current without
                        # producing a compact HUD notification for every percent drift.
                        if not low_announced and abs(pct - prev_pct) >= 1:
                            push({"type": "status", "kind": "battery", "percent": pct, "charging": False,
                                  "text": "On battery", "ts": int(time.time() * 1000), "soft": True})
                    elif charging and abs(pct - prev_pct) >= 1:
                        push({"type": "status", "kind": "battery", "percent": pct, "charging": True,
                              "text": "Charging", "ts": int(time.time() * 1000), "soft": True})
                elif not bat and prev["battery"] is not None:
                    # A battery can disappear on desktops, docking changes, or Windows
                    # power-service transitions. Tell the UI explicitly so it never
                    # leaves stale laptop state visible on a desktop.
                    push({"type": "status", "kind": "battery", "available": False,
                          "ts": int(time.time() * 1000), "soft": True})
                if bat and bat["charging"]:
                    warned.clear()

                bt = read_bluetooth_audio()
                if bt is not None:
                    if bt_prev is not None:
                        now = time.time()
                        for addr, name in bt.items():
                            if addr not in bt_prev and now - bt_last.get(addr, 0) > 15:
                                bt_last[addr] = now
                                push({"type": "status", "kind": "bluetooth", "text": f"{name[:26]} connected",
                                      "ts": int(now * 1000)})
                        for addr, name in bt_prev.items():
                            if addr not in bt and now - bt_last.get(addr, 0) > 15:
                                bt_last[addr] = now
                                push({"type": "status", "kind": "bluetooth", "text": f"{name[:22]} disconnected",
                                      "ts": int(now * 1000)})
                    bt_prev = bt

                if first or mic != prev["mic"]:
                    push({"type": "privacy", "mic": mic})
                first = False
            except Exception as e:  # never let a Windows hiccup kill the thread
                log.debug("status watch: %s", e)
            time.sleep(POLL_S)

    threading.Thread(target=loop, daemon=True, name="lyra-status-watch").start()
    threading.Thread(target=_brightness_loop, args=(push,), daemon=True, name="lyra-brightness").start()
