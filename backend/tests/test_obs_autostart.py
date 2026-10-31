"""Starting a recording while OBS is closed: Lyra opens OBS hidden in the tray, waits for it, records, and leaves it there."""
import threading
from pathlib import Path

import pytest

import screen_record as sr
from obs_control import ObsError

UNREACHABLE = "OBS isn't running, or its WebSocket server is switched off (OBS: Tools → WebSocket Server Settings → Enable)"


class FakeProc:
    pid = 4242

    def __init__(self, dead=False):
        self._dead = dead

    def poll(self):
        return 1 if self._dead else None


class Env:
    """A pretend PC: OBS answers only after `ready_after` knocks once it was launched."""

    def __init__(self, monkeypatch, tmp_path, prefs=None, ready_after=3, installed=True, running=False, dead=False,
                 auth_error=False):
        self.prefs = {"obs_autostart": True, "obs_host": "127.0.0.1", "obs_quality": "obs", **(prefs or {})}
        self.launched, self.asked, self.hidden = [], [], []
        self.up, self.knocks, self.recording = False, 0, False
        self.ready_after, self.auth_error = ready_after, auth_error
        monkeypatch.setattr(sr, "_pref", lambda k, d: self.prefs.get(k, d))
        monkeypatch.setattr(sr, "out_dir", lambda: tmp_path)
        monkeypatch.setattr(sr, "_ask", self.ask)
        monkeypatch.setattr(sr, "_obs_exe", lambda: Path("C:/obs/bin/64bit/obs64.exe") if installed else None)
        monkeypatch.setattr(sr, "_obs_running", lambda: running)
        monkeypatch.setattr(sr, "_launch_obs", lambda exe: (self.launched.append(str(exe)), FakeProc(dead))[1])
        monkeypatch.setattr(sr, "_hide_obs_windows", lambda pid: (self.hidden.append(pid), 0)[1])
        monkeypatch.setattr(sr.time, "sleep", lambda s: None)
        monkeypatch.setattr(sr, "_OBS_WAIT_S", 25)
        for name, val in (("_live", False), ("_paused", False), ("_error", ""), ("_obs_msg", ""), ("_next_try", 0.0), ("_launching", False)):
            monkeypatch.setattr(sr, name, val)

    def ask(self, kind, data=None):
        self.asked.append(kind)
        if self.launched:
            self.knocks += 1
            if self.auth_error and self.knocks >= 2:
                raise ObsError("wrong password")
            if self.knocks >= self.ready_after:
                self.up = True
        if not self.up:
            raise ObsError(UNREACHABLE)
        if kind == "GetRecordStatus":
            return {"outputActive": self.recording}
        if kind == "StartRecord":
            self.recording = True
        return {}


def test_closed_obs_is_opened_hidden_then_recording_starts(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    st = sr.start()
    assert len(e.launched) == 1 and st["recording"] and not st["error"]
    assert "StartRecord" in e.asked and st["notice"] == ""
    assert e.hidden, "the hide-the-window fallback is tried once OBS answers"


def test_obs_is_left_running_after_the_recording(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    sr.start()
    assert not hasattr(sr, "_proc") and not any(k in ("StopRecord", "Quit", "Shutdown") for k in e.asked)
    e.recording = False
    sr.start()                                                             # next recording: OBS already there
    assert len(e.launched) == 1


def test_obs_already_open_is_not_launched_again(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    e.up = True
    assert sr.start()["recording"] and e.launched == []


def test_switch_off_keeps_the_old_message_and_opens_nothing(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, prefs={"obs_autostart": False})
    st = sr.start()
    assert e.launched == [] and not st["recording"] and "WebSocket" in st["error"]


def test_obs_open_but_websocket_off_is_explained_not_launched_twice(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, running=True)
    st = sr.start()
    assert e.launched == [] and "WebSocket" in st["error"] and "Enable" in st["error"]


def test_obs_not_installed_keeps_the_old_message(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, installed=False)
    st = sr.start()
    assert e.launched == [] and "Couldn't start recording" in st["error"] and "WebSocket" in st["error"]


def test_obs_on_another_pc_is_never_launched_here(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, prefs={"obs_host": "192.168.1.20"})
    st = sr.start()
    assert e.launched == [] and not st["recording"]


def test_wrong_password_stops_the_wait(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, auth_error=True, ready_after=99)
    st = sr.start()
    assert len(e.launched) == 1 and "wrong password" in st["error"] and not st["recording"]


def test_obs_that_closes_right_away(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, dead=True, ready_after=99)
    st = sr.start()
    assert "closed right after opening" in st["error"] and not st["recording"]


def test_obs_that_never_answers_times_out(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, ready_after=10**6)
    t = [0.0]
    monkeypatch.setattr(sr.time, "monotonic", lambda: t.__setitem__(0, t[0] + 1.0) or t[0])
    st = sr.start()
    assert "taking too long" in st["error"] and not st["recording"]
    assert sr._launching is False


def test_status_does_not_knock_while_obs_is_opening(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path)
    monkeypatch.setattr(sr, "_launching", True)
    sr.status()
    assert e.asked == []


def test_two_quick_requests_open_obs_once(monkeypatch, tmp_path):
    e = Env(monkeypatch, tmp_path, ready_after=6)
    out = []
    ts = [threading.Thread(target=lambda: out.append(sr.start())) for _ in range(2)]
    [t.start() for t in ts]; [t.join(10) for t in ts]
    assert len(e.launched) == 1 and len(out) == 2 and e.recording


def test_launch_uses_tray_flags_and_obs_folder(monkeypatch):
    seen = {}
    monkeypatch.setattr(sr.subprocess, "Popen", lambda args, **kw: seen.update(args=args, kw=kw) or FakeProc())
    sr._launch_obs(Path("/x/obs-studio/bin/64bit/obs64.exe"))
    assert "--minimize-to-tray" in seen["args"] and "--disable-shutdown-check" in seen["args"]
    assert seen["kw"]["cwd"].replace("\\", "/").endswith("bin/64bit")


def test_obs_location_from_settings(monkeypatch, tmp_path):
    exe = tmp_path / "obs-studio" / "bin" / "64bit" / "obs64.exe"
    exe.parent.mkdir(parents=True); exe.write_bytes(b"x")
    monkeypatch.setattr(sr, "_pref", lambda k, d: {"obs_path": str(tmp_path / "obs-studio")}.get(k, d))
    assert sr._obs_exe() == exe
    monkeypatch.setattr(sr, "_pref", lambda k, d: {"obs_path": str(exe)}.get(k, d))
    assert sr._obs_exe() == exe


def test_settings_keys():
    import settings_store as ss
    assert ss._DEFAULTS["obs_autostart"] is True and ss._DEFAULTS["obs_path"] == ""
    assert {"obs_autostart", "obs_path"} <= set(ss.PREF_GROUPS["recordings"])
