"""Lyra — Controls + Power tab on the island.

Quick switches (Windows, no extra installs): dark mode, night light, Wi-Fi, Bluetooth,
master volume + mute, and a power row (lock, sleep, restart, shut down).

  snapshot()                 -> what the tab shows (None = not available on this PC)
  toggle(name, on)           -> flip one switch, returns the new snapshot
  set_volume(pct) / set_mute(on)
  power(action)              -> lock | sleep | restart | shutdown | cancel
  handle_command(text)       -> voice: "turn off bluetooth", "open the controls", ...

Everything fails soft: if a switch can't be read or changed on this PC it shows as
unavailable instead of raising.
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import sys
import threading
import time

import ui

log = ui.log

_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_NIGHT_KEY = (r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
              r"\default$windows.data.bluelightreduction.bluelightreductionstate"
              r"\windows.data.bluelightreduction.bluelightreductionstate")
_NO_WINDOW = 0x08000000


def _winreg():
    import winreg
    return winreg


# ── dark mode ────────────────────────────────────────────────────────────────────

def get_dark() -> bool | None:
    try:
        wr = _winreg()
        with wr.OpenKey(wr.HKEY_CURRENT_USER, _PERSONALIZE) as k:
            return wr.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return None


def set_dark(on: bool) -> bool:
    try:
        wr = _winreg()
        with wr.OpenKey(wr.HKEY_CURRENT_USER, _PERSONALIZE, 0, wr.KEY_SET_VALUE) as k:
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                wr.SetValueEx(k, name, 0, wr.REG_DWORD, 0 if on else 1)
        out = ctypes.c_ulong()                       # tell running apps (WM_SETTINGCHANGE)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, ctypes.c_wchar_p("ImmersiveColorSet"),
                                                 2, 2000, ctypes.byref(out))
        return True
    except Exception as e:
        log.warning("dark mode: %s", e)
        return False


# ── night light ──────────────────────────────────────────────────────────────────
# Windows keeps Night light as a small binary blob: 41 bytes when off, 43 when on
# (byte 18 is 0x13 / 0x15, and on adds the two bytes 0x10 0x00 at index 23).

def night_is_on(data: bytes) -> bool | None:
    if len(data) < 41 or data[18] not in (0x13, 0x15):
        return None
    return data[18] == 0x15


def night_with_state(data: bytes, on: bool) -> bytes | None:
    cur = night_is_on(data)
    if cur is None:
        return None
    if cur == on:
        return bytes(data)
    b = bytearray(data)
    if on:
        b[18] = 0x15
        b[23:23] = b"\x10\x00"
    else:
        b[18] = 0x13
        del b[23:25]
    return bytes(b)


def _night_blob() -> bytes | None:
    try:
        wr = _winreg()
        with wr.OpenKey(wr.HKEY_CURRENT_USER, _NIGHT_KEY) as k:
            return bytes(wr.QueryValueEx(k, "Data")[0])
    except Exception:
        return None


def get_night() -> bool | None:
    blob = _night_blob()
    return night_is_on(blob) if blob else None


def set_night(on: bool) -> bool:
    blob = _night_blob()
    new = night_with_state(blob, on) if blob else None
    if new is None:
        return False
    try:
        wr = _winreg()
        with wr.OpenKey(wr.HKEY_CURRENT_USER, _NIGHT_KEY, 0, wr.KEY_SET_VALUE) as k:
            wr.SetValueEx(k, "Data", 0, wr.REG_BINARY, new)
        return True
    except Exception as e:
        log.warning("night light: %s", e)
        return False


# ── Wi-Fi + Bluetooth (Windows radio API, via Windows PowerShell) ────────────────

_RADIO_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$kind = '__KIND__'
$set = '__SET__'
foreach ($r in $radios) {
  if ($set -ne '' -and $r.Kind.ToString() -eq $kind) {
    Await ($r.SetStateAsync($set)) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
  }
}
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
foreach ($r in $radios) { Write-Output ('{0}={1}' -f $r.Kind, $r.State) }
"""

_radio_lock = threading.Lock()
_radio_cache: dict = {"at": 0.0, "data": {}, "busy": False}
RADIO_TTL = 8.0


def parse_radios(text: str) -> dict:
    """'WiFi=On\\nBluetooth=Off' -> {'wifi': True, 'bluetooth': False}"""
    out: dict = {}
    for line in (text or "").splitlines():
        m = re.match(r"\s*(WiFi|Bluetooth)\s*=\s*(\w+)", line, re.I)
        if m:
            out[m.group(1).lower()] = m.group(2).lower() == "on"
    return out


def _run_radios(kind: str = "", state: str = "") -> dict:
    script = _RADIO_PS.replace("__KIND__", kind).replace("__SET__", state)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", script], capture_output=True, text=True, timeout=20,
                           creationflags=_NO_WINDOW if sys.platform == "win32" else 0)
        data = parse_radios(r.stdout)
        if not data and r.stderr:
            log.warning("radio query: %s", r.stderr.strip()[:200])
        return data
    except Exception as e:
        log.warning("radio query failed: %s", e)
        return {}


def _refresh_radios() -> None:
    data = _run_radios()
    with _radio_lock:
        if data:
            _radio_cache["data"] = data
        _radio_cache["at"] = time.time()
        _radio_cache["busy"] = False


def get_radios() -> dict:
    """Cached Wi-Fi/Bluetooth state; refreshed in the background so the tab never waits."""
    with _radio_lock:
        first = not _radio_cache["data"] and _radio_cache["at"] == 0.0
        stale = time.time() - _radio_cache["at"] > RADIO_TTL
        start = stale and not _radio_cache["busy"] and not first
        if first or start:
            _radio_cache["busy"] = True
    if first:
        _refresh_radios()                                   # very first call: wait once
    elif start:
        threading.Thread(target=_refresh_radios, daemon=True, name="lyra-radios").start()
    with _radio_lock:
        return dict(_radio_cache["data"])


def set_radio(kind: str, on: bool) -> bool:
    data = _run_radios("WiFi" if kind == "wifi" else "Bluetooth", "On" if on else "Off")
    with _radio_lock:
        if data:
            _radio_cache["data"] = data
            _radio_cache["at"] = time.time()
    return bool(data) and data.get(kind) == on


# ── volume (master) ──────────────────────────────────────────────────────────────

_vol_error = ""


def _endpoint():
    """The default speakers' volume control. Works with old pycaw (device.Activate) and
    new pycaw (device.EndpointVolume)."""
    from ctypes import POINTER, cast
    import comtypes
    comtypes.CoInitialize()                       # every request thread needs COM once
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    ev = getattr(dev, "EndpointVolume", None)     # newer pycaw wraps the device
    if ev is not None:
        return ev
    iface = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def _note_volume_error(e: Exception) -> None:
    global _vol_error
    msg = f"{type(e).__name__}: {e}"[:160]
    if msg != _vol_error:
        _vol_error = msg
        log.warning("Controls volume unavailable: %s (python: %s)", msg, sys.executable)


def get_volume() -> tuple[int | None, bool | None]:
    global _vol_error
    try:
        ep = _endpoint()
        out = int(round(ep.GetMasterVolumeLevelScalar() * 100)), bool(ep.GetMute())
        _vol_error = ""
        return out
    except Exception as e:
        _note_volume_error(e)
        return None, None


def set_volume(pct: int) -> bool:
    try:
        _endpoint().SetMasterVolumeLevelScalar(max(0, min(100, int(pct))) / 100.0, None)
        return True
    except Exception as e:
        _note_volume_error(e)
        return False


def set_mute(on: bool) -> bool:
    try:
        _endpoint().SetMute(1 if on else 0, None)
        return True
    except Exception as e:
        _note_volume_error(e)
        return False


# ── the tab ──────────────────────────────────────────────────────────────────────

NAMES = {"dark": "Dark mode", "night": "Night light", "wifi": "Wi-Fi", "bluetooth": "Bluetooth"}


def snapshot() -> dict:
    radios = get_radios()
    vol, muted = get_volume()
    return {"ok": True, "dark": get_dark(), "night": get_night(), "wifi": radios.get("wifi"),
            "bluetooth": radios.get("bluetooth"), "volume": vol, "muted": muted,
            "volume_error": _vol_error if vol is None else ""}


def toggle(name: str, on: bool | None = None) -> dict:
    """Flip (or set) one switch. Returns {"ok": bool, **snapshot}."""
    cur = {"dark": get_dark, "night": get_night, "wifi": lambda: get_radios().get("wifi"),
           "bluetooth": lambda: get_radios().get("bluetooth")}.get(name)
    if cur is None:
        return {**snapshot(), "ok": False, "error": "unknown switch"}
    if on is None:
        now = cur()
        if now is None:
            return {**snapshot(), "ok": False, "error": f"{NAMES[name]} isn't available on this PC"}
        on = not now
    ok = {"dark": set_dark, "night": set_night,
          "wifi": lambda v: set_radio("wifi", v), "bluetooth": lambda v: set_radio("bluetooth", v)}[name](bool(on))
    snap = snapshot()
    return {**snap, "ok": bool(ok), **({} if ok else {"error": f"Couldn't change {NAMES[name]}"})}


def power(action: str) -> dict:
    """lock / sleep act now; restart / shutdown are delayed (cancelable) like the voice commands."""
    from actions import system
    action = (action or "").lower()
    try:
        if action == "lock":
            system.lock_screen()
            return {"ok": True, "message": "Locked."}
        if action == "sleep":
            def _sleep():
                time.sleep(0.6)                          # let the reply reach the island first
                ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
            threading.Thread(target=_sleep, daemon=True).start()
            return {"ok": True, "message": "Sleeping…"}
        if action in ("restart", "shutdown"):
            ok, msg = system.system_power_action(action)
            import config
            return {"ok": ok, "message": msg, "delay": int(getattr(config, "POWER_ACTION_DELAY", 8))}
        if action == "cancel":
            ok, msg = system.system_power_cancel()
            return {"ok": ok, "message": msg}
    except Exception as e:
        log.warning("power %s: %s", action, e)
        return {"ok": False, "message": str(e)}
    return {"ok": False, "message": "unknown action"}


# ── voice ────────────────────────────────────────────────────────────────────────

_SW = r"(dark\s+mode|night\s*light|night\s+mode|bluetooth|wi-?\s?fi)"
_KEYS = {"darkmode": "dark", "nightlight": "night", "nightmode": "night", "bluetooth": "bluetooth", "wifi": "wifi"}
_TOGGLE = re.compile(rf"^(?:please\s+)?(?:(?:turn|switch)\s+(?P<a>on|off)\s+(?:the\s+|my\s+)?{_SW}"
                     rf"|(?:turn|switch)\s+(?:the\s+|my\s+)?{_SW}\s+(?P<b>on|off)"
                     rf"|(?P<c>enable|disable)\s+(?:the\s+|my\s+)?{_SW}"
                     rf"|toggle\s+(?:the\s+|my\s+)?{_SW})[.!?]*$", re.I)
_OPEN = re.compile(r"^(?:please\s+)?(?:open|show|bring\s+up|go\s+to)\s+(?:me\s+)?(?:the\s+|my\s+)?"
                   r"(?:controls?(?:\s+tab)?|quick\s+(?:settings|controls)|power\s+(?:menu|tab|controls?))[.!?]*$", re.I)


def parse_command(text: str):
    """-> ("open", None, None) | ("toggle", key, True/False/None) | None"""
    t = (text or "").strip()
    if _OPEN.match(t):
        return ("open", None, None)
    m = _TOGGLE.match(t)
    if not m:
        return None
    key = _KEYS[re.sub(r"[\s-]+", "", re.search(_SW, t, re.I).group(1).lower())]
    state = m.group("a") or m.group("b")
    if m.group("c"):
        state = "on" if m.group("c").lower() == "enable" else "off"
    return ("toggle", key, None if state is None else state.lower() == "on")


def handle_command(text: str) -> str | None:
    """Voice commands for the Controls tab. Returns the reply, or None if it isn't one."""
    cmd = parse_command(text)
    if not cmd:
        return None
    kind, key, on = cmd
    try:
        import ui_bridge
        if kind == "open":
            ui_bridge.push_controls(True)
            return "Here are your controls."
        res = toggle(key, on)
        ui_bridge.push_controls(False)
        name = NAMES.get(key, key)
        if res.get("ok"):
            state = res.get(key)
            return f"{name} is {'on' if state else 'off'}."
        return res.get("error") or f"I couldn't change {name}."
    except Exception as e:
        log.warning("controls command: %s", e)
        return None
