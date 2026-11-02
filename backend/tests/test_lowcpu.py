"""CPU savings: smaller/lighter Whisper that unloads when idle, no PowerShell for brightness, polite priority and
housekeeping that waits for a calm PC, and no browser-history copies when nothing changed."""
import sys
import threading
import time
import types
from pathlib import Path

import pytest


# ── a stand-in for faster-whisper so these tests need neither the package nor a model ──────────────
class FakeWhisper:
    made = []

    def __init__(self, path, **kw):
        self.path, self.kw = path, kw
        FakeWhisper.made.append(self)


@pytest.fixture
def stt(monkeypatch):
    if "faster_whisper" not in sys.modules:
        try:
            import faster_whisper  # noqa: F401
        except Exception:
            monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisper))
    import stt as _stt
    monkeypatch.setattr(_stt, "WhisperModel", FakeWhisper)
    monkeypatch.setitem(sys.modules, "model_setup", types.SimpleNamespace(ensure_whisper=lambda n: f"/models/{n}"))
    cfg = {}
    monkeypatch.setattr(_stt, "_setting", lambda k, d: cfg.get(k, d))
    monkeypatch.setitem(_stt._SHARED, "model", None)
    monkeypatch.setattr(_stt, "_unloader_started", True)          # no background thread in tests
    FakeWhisper.made.clear()
    _stt._cfg = cfg
    return _stt


def test_defaults_are_registered():
    import settings_store as ss
    d = ss._DEFAULTS
    assert d["whisper_model"] == "base" and d["whisper_threads"] == 0 and d["whisper_idle_unload_min"] == 10
    assert d["low_priority_backend"] is True
    assert {"whisper_model", "whisper_threads", "whisper_idle_unload_min", "low_priority_backend"} <= set(ss.PREF_GROUPS["voice"])


def test_model_name_and_threads(stt):
    assert stt._whisper_name() == stt.WHISPER_MODEL       # nothing chosen -> the config.py default
    stt._cfg["whisper_model"] = "tiny"
    assert stt._whisper_name() == "tiny"
    stt._cfg["whisper_model"] = "nonsense"
    assert stt._whisper_name() == stt.WHISPER_MODEL
    import os
    assert stt._whisper_threads() == max(2, (os.cpu_count() or 4) // 2)
    stt._cfg["whisper_threads"] = 3
    assert stt._whisper_threads() == 3


def test_load_uses_chosen_model_and_thread_cap(stt):
    stt._cfg["whisper_model"] = "base"
    stt._cfg["whisper_threads"] = 2
    m = stt._load_shared_whisper()
    assert m.path == "/models/base" and m.kw["cpu_threads"] == 2
    assert stt._load_shared_whisper() is m and len(FakeWhisper.made) == 1


def test_missing_small_model_falls_back_to_config_default(stt, monkeypatch):
    stt._cfg["whisper_model"] = "tiny"
    def ensure(n):
        if n == "tiny":
            raise OSError("offline")
        return f"/models/{n}"
    monkeypatch.setitem(sys.modules, "model_setup", types.SimpleNamespace(ensure_whisper=ensure))
    assert stt._load_shared_whisper().path == f"/models/{stt.WHISPER_MODEL}"


def test_idle_unload_and_reload(stt):
    stt._cfg["whisper_idle_unload_min"] = 10
    stt._load_shared_whisper()
    t0 = stt._LAST_USE[0]
    assert stt._unload_if_idle(now=t0 + 599) is False and stt._SHARED["model"] is not None
    assert stt._unload_if_idle(now=t0 + 601) is True and stt._SHARED["model"] is None
    r = stt.SpeechRecognizer()
    assert r.model is None                         # follows the shared copy
    r.load()
    assert r.model is not None and len(FakeWhisper.made) == 2


def test_idle_unload_off_and_minimum(stt):
    stt._cfg["whisper_idle_unload_min"] = 0
    stt._load_shared_whisper()
    assert stt._unload_if_idle(now=time.monotonic() + 10**6) is False
    stt._cfg["whisper_idle_unload_min"] = 0.1       # never less than a minute
    assert stt._idle_limit_s() == 60.0


def test_handing_out_the_model_postpones_unloading(stt):
    stt._cfg["whisper_idle_unload_min"] = 1
    stt._load_shared_whisper()
    stt._LAST_USE[0] -= 120
    stt._load_shared_whisper()                      # someone used it just now
    assert stt._unload_if_idle() is False


def test_tiny_noise_never_reaches_the_model(stt):
    r = stt.SpeechRecognizer()
    assert r.transcribe(b"\x10\x00" * 100) == ("unrecognized", "")     # 100 samples = 6 ms


# ── lowcpu ──────────────────────────────────────────────────────────────────────────────────────
def test_priority_only_on_windows_and_only_when_on(monkeypatch):
    import lowcpu
    monkeypatch.setattr(lowcpu.sys, "platform", "linux")
    assert lowcpu.apply_process_priority() is False
    monkeypatch.setattr(lowcpu.sys, "platform", "win32")
    monkeypatch.setattr(lowcpu, "_setting", lambda k, d: False)
    assert lowcpu.apply_process_priority() is False


def _fake_psutil(monkeypatch, cpu=5.0, plugged=True, pct=90):
    ps = types.SimpleNamespace(sensors_battery=lambda: types.SimpleNamespace(power_plugged=plugged, percent=pct),
                               cpu_percent=lambda interval=0: cpu)
    monkeypatch.setitem(sys.modules, "psutil", ps)


def test_calm_reasons(monkeypatch):
    import lowcpu
    monkeypatch.setitem(sys.modules, "game_mode", types.SimpleNamespace(is_active=lambda: False))
    _fake_psutil(monkeypatch)
    assert lowcpu.calm_reason() == ""
    _fake_psutil(monkeypatch, cpu=80); assert lowcpu.calm_reason() == "the PC is busy"
    _fake_psutil(monkeypatch, plugged=False, pct=20); assert lowcpu.calm_reason() == "the battery is low"
    _fake_psutil(monkeypatch, plugged=False, pct=80); assert lowcpu.calm_reason() == ""
    monkeypatch.setitem(sys.modules, "game_mode", types.SimpleNamespace(is_active=lambda: True))
    assert lowcpu.calm_reason() == "a game is running"


def test_wait_until_calm_goes_when_calm_stops_on_event_and_gives_up_waiting(monkeypatch):
    import lowcpu
    stop = threading.Event()
    monkeypatch.setattr(lowcpu, "calm_reason", lambda max_cpu=45.0: "")
    assert lowcpu.wait_until_calm(stop) is True
    # busy, then calm on the 3rd look
    seq = iter(["busy", "busy", ""]); waits = []
    monkeypatch.setattr(lowcpu, "calm_reason", lambda max_cpu=45.0: next(seq))
    stop2 = types.SimpleNamespace(is_set=lambda: False, wait=lambda s: waits.append(s) or False)
    assert lowcpu.wait_until_calm(stop2, every_s=60) is True and waits == [60, 60]
    # stopping while waiting
    monkeypatch.setattr(lowcpu, "calm_reason", lambda max_cpu=45.0: "busy")
    stop3 = types.SimpleNamespace(is_set=lambda: False, wait=lambda s: True)
    assert lowcpu.wait_until_calm(stop3) is False
    # busy for ever: goes ahead after max_wait_s
    t = [0.0]
    monkeypatch.setattr(lowcpu.time, "monotonic", lambda: t.__setitem__(0, t[0] + 100) or t[0])
    stop4 = types.SimpleNamespace(is_set=lambda: False, wait=lambda s: False)
    assert lowcpu.wait_until_calm(stop4, max_wait_s=250) is True


# ── brightness without PowerShell ───────────────────────────────────────────────────────────────
@pytest.fixture
def sw(monkeypatch):
    import status_watch as s
    monkeypatch.setattr(s, "_IS_WIN", True)
    monkeypatch.setattr(s, "_ioctl_state", None)
    calls = {"wmi": 0}
    s._calls = calls
    return s


def test_driver_value_is_trusted_once_it_matches_wmi(sw, monkeypatch):
    monkeypatch.setattr(sw, "_ioctl_brightness", lambda: 60)
    monkeypatch.setattr(sw, "_wmi_brightness", lambda: (sw._calls.__setitem__("wmi", sw._calls["wmi"] + 1), 61)[1])
    assert sw.read_brightness() == 60 and sw._ioctl_state is True
    monkeypatch.setattr(sw, "_ioctl_brightness", lambda: 45)
    assert sw.read_brightness() == 45
    assert sw._calls["wmi"] == 1, "after the first check no PowerShell is ever started"


def test_driver_value_that_disagrees_is_dropped(sw, monkeypatch):
    monkeypatch.setattr(sw, "_ioctl_brightness", lambda: 100)
    monkeypatch.setattr(sw, "_wmi_brightness", lambda: 40)
    assert sw.read_brightness() == 40 and sw._ioctl_state is False
    assert sw.read_brightness() == 40


def test_no_driver_support_falls_back_to_wmi(sw, monkeypatch):
    monkeypatch.setattr(sw, "_ioctl_brightness", lambda: None)
    monkeypatch.setattr(sw, "_wmi_brightness", lambda: 33)
    assert sw.read_brightness() == 33 and sw._ioctl_state is False


def test_driver_read_is_safe_off_windows():
    import status_watch as s
    assert s._ioctl_brightness() is None


# ── browser history: no copy when nothing changed ───────────────────────────────────────────────
def test_history_changed_only_when_files_change(tmp_path, monkeypatch):
    import browser_downloads_watch as b
    b._SIGS.clear()
    h = tmp_path / "History"; h.write_bytes(b"a" * 10)
    assert b._history_changed(h) is True           # first look
    assert b._history_changed(h) is False          # nothing happened
    (tmp_path / "History-wal").write_bytes(b"w")   # the browser wrote to its WAL
    assert b._history_changed(h) is True
    assert b._history_changed(h) is False
    h.write_bytes(b"b" * 20)
    assert b._history_changed(h) is True
    t = time.monotonic() + 31                       # safety net: read anyway every 30 s
    monkeypatch.setattr(b.time, "monotonic", lambda: t)
    assert b._history_changed(h) is True


def test_missing_history_file_is_handled(tmp_path):
    import browser_downloads_watch as b
    b._SIGS.clear()
    p = tmp_path / "nope" / "History"
    assert b._history_changed(p) is True and b._history_changed(p) is False


def test_file_index_priority_helper_is_harmless_off_windows():
    import file_index
    file_index._lower_priority()


def test_real_settings_make_base_the_default(stt, monkeypatch):
    import settings_store
    monkeypatch.setattr(stt, "_setting", lambda k, d: settings_store.get(k) if settings_store.get(k) is not None else d)
    assert stt._whisper_name() == "base" and stt._idle_limit_s() == 600.0
