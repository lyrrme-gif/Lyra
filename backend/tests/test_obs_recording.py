"""Screen recording through OBS Studio: the WebSocket client (handshake, password, requests) against a fake OBS server, and
screen_record's start / pause / resume / stop / status / list on top of it."""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("ui", types.SimpleNamespace(log=__import__("logging").getLogger("t")))
import obs_control as oc  # noqa: E402
import screen_record as sr  # noqa: E402

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SALT, CHALLENGE = "salty", "challenging"


def _auth(pw):
    secret = base64.b64encode(hashlib.sha256((pw + SALT).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + CHALLENGE).encode()).digest()).decode()


class FakeObs:
    """Just enough of OBS's WebSocket v5 server."""

    def __init__(self, folder, password="", refuse_dir=False, own_dir=None):
        self.password, self.refuse_dir = password, refuse_dir
        self.directory = str(own_dir or folder)
        self.recording = self.paused = False
        self.refuse_video = False
        self.video = {"baseWidth": 2560, "baseHeight": 1440, "outputWidth": 2560, "outputHeight": 1440,
                      "fpsNumerator": 30, "fpsDenominator": 1}
        self.requests, self.pings = [], 0
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.alive = False
        self.srv.close()

    def _accept(self):
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    @staticmethod
    def _frame(sock, text, opcode=1):
        d = text.encode() if isinstance(text, str) else text
        head = bytes([0x80 | opcode])
        head += bytes([len(d)]) if len(d) < 126 else bytes([126]) + struct.pack(">H", len(d))
        sock.sendall(head + d)

    @staticmethod
    def _read(sock, n):
        b = b""
        while len(b) < n:
            c = sock.recv(n - len(b))
            if not c:
                raise OSError("closed")
            b += c
        return b

    def _recv(self, sock):
        b1, b2 = self._read(sock, 2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(sock, 2))[0]
        mask = self._read(sock, 4)
        data = bytes(x ^ mask[i % 4] for i, x in enumerate(self._read(sock, n)))
        return b1 & 0x0F, data

    def _serve(self, c):
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                head += c.recv(4096)
            key = [l.split(":", 1)[1].strip() for l in head.decode().split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
            acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            c.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {acc}\r\n\r\n".encode())
            hello = {"op": 0, "d": {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}}
            if self.password:
                hello["d"]["authentication"] = {"challenge": CHALLENGE, "salt": SALT}
            self._frame(c, json.dumps(hello))
            _, data = self._recv(c)
            ident = json.loads(data)["d"]
            if self.password and ident.get("authentication") != _auth(self.password):
                self._frame(c, struct.pack(">H", 4009), 8)
                return
            self._frame(c, json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
            while True:
                op, data = self._recv(c)
                if op == 8:
                    return
                if op == 0xA:
                    self.pings += 1
                    continue
                d = json.loads(data)["d"]
                self._frame(c, "", 9)                                            # a ping in between: the client must pong and carry on
                self._frame(c, json.dumps({"op": 7, "d": self._answer(d)}))
        except OSError:
            pass
        finally:
            c.close()

    def _answer(self, d):
        kind, rid, args = d["requestType"], d["requestId"], d.get("requestData") or {}
        self.requests.append((kind, args))
        ok, body, comment = True, {}, ""
        if kind == "GetVersion":
            body = {"obsVersion": "33.0.0", "obsWebSocketVersion": "5.5.0"}
        elif kind == "GetRecordStatus":
            body = {"outputActive": self.recording, "outputPaused": self.paused, "outputDuration": 7000 if self.recording else 0,
                    "outputBytes": 123456 if self.recording else 0}
        elif kind == "GetVideoSettings":
            body = dict(self.video)
        elif kind == "SetVideoSettings":
            if self.refuse_video:
                ok, comment = False, "Video settings are in use"
            else:
                self.video.update(args)
        elif kind == "SetRecordDirectory":
            if self.refuse_dir:
                ok, comment = False, "Unknown request type"
            else:
                self.directory = args["recordDirectory"]
        elif kind == "StartRecord":
            self.recording = True
        elif kind == "PauseRecord":
            self.paused = True
        elif kind == "ResumeRecord":
            self.paused = False
        elif kind == "StopRecord":
            self.recording = self.paused = False
            path = os.path.join(self.directory, "2026-10-31 07-34-12.mp4")
            open(path, "wb").write(b"video" * 100)
            body = {"outputPath": path}
        return {"requestType": kind, "requestId": rid, "requestStatus": {"result": ok, "code": 100 if ok else 204, "comment": comment}, "responseData": body}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "out_dir", lambda: tmp_path)
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    sr._client = None
    sr._last = sr._path = sr._error = sr._obs_msg = ""
    sr._live = sr._paused = sr._stopping = False
    sr._saving, sr._secs, sr._size, sr._next_try = 0, 0, 0, 0.0
    yield
    if sr._client is not None:
        sr._client.close()


def _use(monkeypatch, srv, password="", quality="obs"):
    vals = {"obs_host": "127.0.0.1", "obs_port": srv.port, "obs_password": password, "obs_quality": quality}
    monkeypatch.setattr(sr, "_pref", lambda k, d: vals.get(k, d))


# ── the WebSocket client ──────────────────────────────────────────────────────────────────────────

def test_the_password_hash_is_the_documented_double_sha256():
    expected = base64.b64encode(hashlib.sha256((base64.b64encode(hashlib.sha256(b"secretsalty").digest()).decode() + "challenging").encode()).digest()).decode()
    assert oc.auth_string("secret", "salty", "challenging") == expected


def test_it_connects_without_a_password_and_survives_pings(tmp_path):
    srv = FakeObs(tmp_path)
    c = oc.ObsClient("127.0.0.1", srv.port)
    assert c.request("GetVersion")["obsVersion"] == "33.0.0"
    assert c.request("GetRecordStatus")["outputActive"] is False          # a ping arrived in between: answered, nothing lost
    c.close(); srv.close()


def test_it_connects_with_the_right_password_and_refuses_a_wrong_one(tmp_path):
    srv = FakeObs(tmp_path, password="hunter2")
    assert oc.ObsClient("127.0.0.1", srv.port, "hunter2").request("GetVersion")["obsVersion"] == "33.0.0"
    with pytest.raises(oc.ObsError, match="wrong password"):
        oc.ObsClient("127.0.0.1", srv.port, "nope").request("GetVersion")
    with pytest.raises(oc.ObsError, match="asks for a password"):
        oc.ObsClient("127.0.0.1", srv.port, "").request("GetVersion")
    srv.close()


def test_obs_not_running_gives_a_sentence_not_a_traceback():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()    # a port nobody listens on
    with pytest.raises(oc.ObsError, match="isn't running"):
        oc.ObsClient("127.0.0.1", port).request("GetVersion")


def test_a_request_obs_refuses_is_reported_and_the_connection_stays(tmp_path):
    srv = FakeObs(tmp_path, refuse_dir=True)
    c = oc.ObsClient("127.0.0.1", srv.port)
    with pytest.raises(oc.ObsRefused):
        c.request("SetRecordDirectory", {"recordDirectory": "x"})
    assert c.request("GetVersion")                                         # still usable
    c.close(); srv.close()


def test_a_dropped_connection_is_reopened(tmp_path):
    srv = FakeObs(tmp_path)
    c = oc.ObsClient("127.0.0.1", srv.port)
    c.request("GetVersion")
    c._ws.sock.close()                                                     # the connection dies behind our back
    assert c.request("GetVersion")["obsVersion"] == "33.0.0"
    c.close(); srv.close()


# ── Lyra's recorder on top of it ──────────────────────────────────────────────────────────────────

def test_start_pause_resume_stop_save_into_lyras_folder(tmp_path, monkeypatch):
    srv = FakeObs(tmp_path / "obs_default"); os.makedirs(srv.directory)
    _use(monkeypatch, srv)
    s = sr.start()
    assert s["recording"] and not s["error"] and s["capture"] == "OBS"
    assert ("SetRecordDirectory", {"recordDirectory": str(tmp_path)}) in srv.requests      # OBS records straight into Lyra's folder
    assert sr.pause()["paused"] and sr.resume()["recording"] and not sr.status()["paused"]
    done = sr.stop()
    assert not done["recording"] and done["saved"] and done["path"].startswith(str(tmp_path)) and done["path"].endswith(".mp4")
    assert os.path.getsize(done["path"]) > 0
    srv.close()


def test_an_old_obs_that_cannot_change_folder_has_its_file_moved(tmp_path, monkeypatch):
    own = tmp_path / "obs_own"; own.mkdir()
    lyra = tmp_path / "lyra"; lyra.mkdir()
    monkeypatch.setattr(sr, "out_dir", lambda: lyra)
    srv = FakeObs(own, refuse_dir=True)
    _use(monkeypatch, srv)
    assert sr.start()["recording"]
    done = sr.stop()
    assert done["saved"] and os.path.dirname(done["path"]) == str(lyra) and not list(own.iterdir())
    srv.close()


def test_status_when_obs_is_not_running_is_quiet_and_does_not_hammer_it(tmp_path, monkeypatch):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    monkeypatch.setattr(sr, "_pref", lambda k, d: {"obs_port": port}.get(k, d))
    st = sr.status()
    assert not st["recording"] and not st["obs_ok"] and "isn't running" in st["obs_message"] and not st["error"]
    assert sr._next_try > time.monotonic()                                 # the next few seconds do not knock again


def test_starting_without_obs_says_what_to_do(tmp_path, monkeypatch):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    monkeypatch.setattr(sr, "_pref", lambda k, d: {"obs_port": port}.get(k, d))
    st = sr.start()
    assert not st["recording"] and "Couldn't start recording" in st["error"] and "WebSocket" in st["error"]


def test_a_recording_started_inside_obs_shows_up_and_the_island_is_told(tmp_path, monkeypatch):
    import ui_bridge
    seen = []
    monkeypatch.setattr(ui_bridge, "push_record", lambda rec, at=0, **kw: seen.append((rec, at, kw)))
    srv = FakeObs(tmp_path); _use(monkeypatch, srv)
    srv.recording = True                                                   # somebody pressed Record in OBS
    st = sr.status()
    assert st["recording"] and st["seconds"] == 7 and st["size"] == 123456
    assert seen and seen[-1][0] is True and seen[-1][1] > 0
    srv.recording = False
    assert not sr.status()["recording"] and seen[-1][:2] == (False, 0)
    srv.close()


def test_paused_state_reaches_the_island(tmp_path, monkeypatch):
    import ui_bridge
    seen = []
    monkeypatch.setattr(ui_bridge, "push_record", lambda rec, at=0, **kw: seen.append((rec, at, kw)))
    srv = FakeObs(tmp_path); _use(monkeypatch, srv)
    sr.start(); sr.pause()
    assert seen[-1][2].get("paused") is True and seen[-1][2]["elapsed_ms"] == 7000
    srv.close()


def test_toggle_and_pause_toggle_are_what_the_shortcuts_call(tmp_path, monkeypatch):
    srv = FakeObs(tmp_path); _use(monkeypatch, srv)
    assert sr.toggle()["recording"]
    assert sr.toggle_pause()["paused"]
    assert not sr.toggle_pause()["paused"]
    assert not sr.toggle()["recording"]
    assert not sr.toggle_pause()["recording"]
    srv.close()


def test_voice_commands(tmp_path, monkeypatch):
    srv = FakeObs(tmp_path); _use(monkeypatch, srv)
    assert "not recording" in sr.try_command("stop the recording")
    assert "Recording your screen" in sr.try_command("start recording")
    assert "paused" in sr.try_command("pause recording").lower()
    assert "again" in sr.try_command("resume the recording").lower()
    assert "saved" in sr.try_command("stop the recording").lower()
    assert sr.try_command("what's the weather") is None
    srv.close()


def test_the_connection_test_for_settings(tmp_path, monkeypatch):
    srv = FakeObs(tmp_path, password="pw"); _use(monkeypatch, srv, "pw")
    ok = sr.obs_test()
    assert ok["ok"] and "OBS 33.0.0" in ok["message"]
    _use(monkeypatch, srv, "wrong"); sr._client = None
    bad = sr.obs_test()
    assert not bad["ok"] and "password" in bad["message"]
    srv.close()


def test_the_list_shows_mp4_and_mkv_and_marks_the_file_being_recorded(tmp_path, monkeypatch):
    (tmp_path / "old.mp4").write_bytes(b"x"); (tmp_path / "b.mkv").write_bytes(b"x")
    os.utime(tmp_path / "old.mp4", (1000, 1000)); os.utime(tmp_path / "b.mkv", (2000, 2000))
    r = sr.list_recordings()
    assert [i["name"] for i in r["items"]] == ["b.mkv", "old.mp4"] and not any(i["current"] for i in r["items"])
    assert sr.delete_recording("old.mp4")["ok"] and not (tmp_path / "old.mp4").exists()
    srv = FakeObs(tmp_path); _use(monkeypatch, srv)
    (tmp_path / "new.mp4").write_bytes(b"x")                              # OBS is writing it right now
    sr.start()
    assert [i["name"] for i in sr.list_recordings()["items"] if i["current"]] == ["new.mp4"]
    sr.stop(); srv.close()


def test_settings_know_the_obs_keys_and_the_old_recorder_is_gone():
    import settings_store as ss
    assert ss._DEFAULTS["obs_port"] == 4455 and ss._DEFAULTS["obs_host"] == "127.0.0.1" and ss._DEFAULTS["obs_password"] == ""
    assert {"obs_host", "obs_port", "obs_password"} <= set(ss.PREF_GROUPS["recordings"])
    assert not [k for k in ss._DEFAULTS if k in ("record_quality", "record_encoder", "record_fps", "record_cpu", "record_capture")]
    for gone in ("record_audio.py", "record_diag.py"):
        assert not os.path.exists(os.path.join(os.path.dirname(__file__), "..", gone))


# ── recording quality ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("quality,size,fps", [("high", (1920, 1080), 60), ("medium", (1280, 720), 30), ("low", (852, 480), 30)])
def test_quality_presets_set_obs_output_size_and_fps(tmp_path, monkeypatch, quality, size, fps):
    srv = FakeObs(tmp_path); _use(monkeypatch, srv, quality=quality)
    assert sr.start()["recording"]
    assert (srv.video["outputWidth"], srv.video["outputHeight"]) == size and srv.video["fpsNumerator"] == fps
    assert srv.video["baseWidth"] == 2560                                    # the capture itself is never touched
    kinds = [k for k, _ in srv.requests]
    assert kinds.index("SetVideoSettings") < kinds.index("StartRecord")      # set before the recording starts
    srv.close()


def test_obs_default_quality_leaves_video_settings_alone(tmp_path, monkeypatch):
    srv = FakeObs(tmp_path); _use(monkeypatch, srv)
    sr.start()
    assert "SetVideoSettings" not in [k for k, _ in srv.requests] and "GetVideoSettings" not in [k for k, _ in srv.requests]
    srv.close()


def test_a_small_canvas_is_never_scaled_up(tmp_path, monkeypatch):
    srv = FakeObs(tmp_path); _use(monkeypatch, srv, quality="high")
    srv.video.update(baseWidth=1280, baseHeight=720, outputWidth=1280, outputHeight=720)
    sr.start()
    assert (srv.video["outputWidth"], srv.video["outputHeight"]) == (1280, 720)
    srv.close()


def test_obs_refusing_video_settings_still_records(tmp_path, monkeypatch):
    srv = FakeObs(tmp_path); _use(monkeypatch, srv, quality="high")
    srv.refuse_video = True
    st = sr.start()
    assert st["recording"] and not st["error"]
    srv.close()
