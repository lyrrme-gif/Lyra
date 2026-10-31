"""Screen recording: the ffmpeg command, start/stop/status with a fake process, and the voice commands."""
import io
import os
import shutil
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.modules.setdefault("ui", types.SimpleNamespace(log=__import__("logging").getLogger("t")))
import screen_record as sr  # noqa: E402


class FakeProc:
    def __init__(self, out, die_at_once=False):
        self.out, self.stdin, self.stderr = out, io.BytesIO(), io.BytesIO(b"boom: bad device\n")
        self.returncode = 1 if die_at_once else None
        self.sent = b""

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        with open(self.out, "wb") as f:
            f.write(b"mp4data")

    def terminate(self):
        self.returncode = 0


def fake_final(cmd, timeout):
    """Stands in for the final ffmpeg pass (these tests replace Popen, which subprocess.run needs): copies the first input."""
    shutil.copy(cmd[cmd.index("-i") + 1], cmd[-1])
    return True, ""


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(sr.sys, "platform", "win32")
    monkeypatch.setattr(sr, "out_dir", lambda: tmp_path)
    monkeypatch.setattr(sr, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (1920, 1080))
    monkeypatch.setattr(sr, "_probe_ddagrab", lambda exe, idx=0: False)      # never try the Desktop Duplication path here
    monkeypatch.setattr(sr, "_run_low", fake_final)
    monkeypatch.setattr(sr, "recover_async", lambda force=False: None)
    sr._plan_cache.clear()
    sr._plan_info = ""
    sr._proc, sr._path, sr._last, sr._error, sr._started = None, "", "", "", 0.0
    sr._segments, sr._paused, sr._paused_total, sr._stopping, sr._saving = [], False, 0.0, False, 0
    sr._stopped.set()
    yield


def test_command_line_is_a_gdigrab_mp4():
    cmd = sr.build_cmd("ffmpeg", "out.mp4", 24)
    assert cmd[0] == "ffmpeg" and "gdigrab" in cmd and cmd[cmd.index("-framerate") + 1] == "24"
    assert "libx264" in cmd and cmd[-1] == "out.mp4" and any("pad=ceil(iw/2)*2:ceil(ih/2)*2" in a for a in cmd)


def test_start_then_stop_saves_a_file(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1]))
    s = sr.start()
    assert s["recording"] and not s["error"] and s["path"].endswith(".mp4")
    s = sr.stop()
    assert not s["recording"] and s["saved"] and os.path.exists(s["path"])


def test_start_twice_does_not_start_two(monkeypatch):
    n = []
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: n.append(1) or FakeProc(cmd[-1]))
    sr.start(); sr.start()
    assert len(n) == 1


def test_ffmpeg_dying_at_once_reports_the_error(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1], die_at_once=True))
    s = sr.start()
    assert not s["recording"] and "bad device" in s["error"]


def test_missing_ffmpeg_and_other_platforms(monkeypatch):
    monkeypatch.setattr(sr, "_ffmpeg", lambda: None)
    assert "ffmpeg" in sr.start()["error"]
    monkeypatch.setattr(sr.sys, "platform", "linux")
    assert "Windows" in sr.start()["error"]


def test_forgotten_recording_stops_itself(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1]))
    sr.start()
    sr._started -= sr.MAX_SECONDS + 5
    assert not sr.status()["recording"] and sr.status()["saved"]


@pytest.mark.parametrize("text", ["start recording", "Hey Lyra, start a screen recording", "record my screen", "please record the screen"])
def test_voice_start(text, monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1]))
    monkeypatch.setitem(sys.modules, "ui_bridge", types.SimpleNamespace(push_open_view=lambda v: None))
    assert "Recording" in sr.try_command(text)


def test_voice_stop_and_not_recording(monkeypatch):
    assert sr.try_command("stop recording") == "I'm not recording right now."
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1]))
    sr.start()
    assert "saved" in sr.try_command("stop the recording")


@pytest.mark.parametrize("text", ["what is the weather", "record a voice memo", "stop the music", "play recording of my song"])
def test_other_commands_are_left_alone(text):
    assert sr.try_command(text) is None


def test_island_is_told_when_recording_starts_and_stops(monkeypatch):
    pushed = []
    monkeypatch.setitem(sys.modules, "ui_bridge", types.SimpleNamespace(push_record=lambda rec, at=0: pushed.append((rec, at))))
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1]))
    sr.start()
    assert pushed and pushed[-1][0] is True and pushed[-1][1] > 0
    sr.stop()
    assert pushed[-1] == (False, 0)


def test_a_broken_ui_never_breaks_recording(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ui down")
    monkeypatch.setitem(sys.modules, "ui_bridge", types.SimpleNamespace(push_record=boom))
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1]))
    assert sr.start()["recording"] and sr.stop()["saved"]


def test_quality_presets_change_fps_crf_and_size():
    low = sr.build_cmd("ffmpeg", "o.mp4", level="low")
    high = sr.build_cmd("ffmpeg", "o.mp4", level="high")
    assert low[low.index("-framerate") + 1] == "30" and low[low.index("-crf") + 1] == "28"
    assert "scale=-2:'min(480,ih)',pad=ceil(iw/2)*2:ceil(ih/2)*2" in low                      # Low: 480p, 30 fps
    assert high[high.index("-framerate") + 1] == "60" and high[high.index("-crf") + 1] == "20"
    assert "scale=-2:'min(1080,ih)',pad=ceil(iw/2)*2:ceil(ih/2)*2" in high                    # High: 1080p, 60 fps
    med = sr.build_cmd("ffmpeg", "o.mp4", level="medium")
    assert med[med.index("-framerate") + 1] == "30" and "scale=-2:'min(720,ih)',pad=ceil(iw/2)*2:ceil(ih/2)*2" in med   # Medium: 720p, 30 fps


def test_start_uses_the_saved_quality(monkeypatch):
    seen = []
    monkeypatch.setattr(sr, "quality", lambda: "low")
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: seen.append(cmd) or FakeProc(cmd[-1]))
    sr.start()
    assert seen and seen[0][seen[0].index("-framerate") + 1] == "30"
    sr.stop()


def test_list_open_and_delete_only_touch_mp4s_in_the_folder(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x" * 10)
    (tmp_path / "notes.txt").write_text("keep")
    names = [i["name"] for i in sr.list_recordings()["items"]]
    assert names == ["a.mp4"]
    assert sr.delete_recording("../a.mp4")["ok"] is False
    assert sr.delete_recording("notes.txt")["ok"] is False
    assert sr.delete_recording("a.mp4")["ok"] is True
    assert (tmp_path / "notes.txt").exists() and not (tmp_path / "a.mp4").exists()
    assert sr.delete_recording("a.mp4")["ok"] is False


def test_the_file_being_recorded_cannot_be_deleted(monkeypatch):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1]))
    s = sr.start()
    name = os.path.basename(s["path"])
    assert sr.delete_recording(name)["ok"] is False
    sr.stop()
    assert sr.delete_recording(name)["ok"] is True


# ── Custom quality, pointer, auto-stop, previews ─────────────────────────────

def _prefs(monkeypatch, **values):
    import settings_store
    monkeypatch.setattr(settings_store, "get", lambda key: values.get(key))


def test_presets_keep_their_numbers(monkeypatch):
    _prefs(monkeypatch, record_quality="low")
    p = sr.profile()
    assert (p["fps"], p["max_h"]) == (30, 480)
    _prefs(monkeypatch, record_quality="medium")
    assert (sr.profile()["fps"], sr.profile()["max_h"]) == (30, 720)
    _prefs(monkeypatch, record_quality="high")
    assert (sr.profile()["fps"], sr.profile()["max_h"]) == (60, 1080)
    _prefs(monkeypatch, record_quality="smooth")                 # the old "Smooth 60" preset is High now
    assert (sr.profile()["fps"], sr.profile()["max_h"]) == (60, 1080)


def test_encoder_choice(monkeypatch):
    for enc, quality, want in [("cpu", "high", False), ("gpu", "low", True), ("auto", "low", False), ("auto", "medium", False), ("auto", "high", True)]:
        _prefs(monkeypatch, record_quality=quality, record_encoder=enc)
        assert sr.wants_gpu(sr.profile(region=False)) is want, (enc, quality)


def test_custom_quality_reads_the_individual_settings(monkeypatch):
    _prefs(monkeypatch, record_quality="custom", record_fps=60, record_crf=18, record_height=1080, record_mouse=False, record_max_minutes=10)
    p = sr.profile()
    assert (p["fps"], p["crf"], p["max_h"], p["mouse"], p["max_seconds"]) == (60, 18, 1080, False, 600)
    assert p["preset"] == "ultrafast"                       # 60 fps: the grabber must keep up
    cmd = sr.build_cmd("ffmpeg", "o.mp4", prof=p)
    assert cmd[cmd.index("-framerate") + 1] == "60" and cmd[cmd.index("-draw_mouse") + 1] == "0"
    assert "scale=-2:'min(1080,ih)'" in cmd[cmd.index("-vf") + 1]


def test_custom_values_are_clamped(monkeypatch):
    _prefs(monkeypatch, record_quality="custom", record_fps=500, record_crf=1, record_max_minutes=-5)
    p = sr.profile()
    assert p["fps"] == 60 and p["crf"] == 12 and p["max_seconds"] == 60


def test_no_limit_option(monkeypatch):
    _prefs(monkeypatch, record_quality="custom", record_max_minutes=0)
    assert sr.profile()["max_seconds"] == sr.NO_LIMIT_SECONDS


def test_unknown_quality_falls_back_to_medium(monkeypatch):
    _prefs(monkeypatch, record_quality="ultra")
    assert sr.quality() == "medium"


def test_recording_stops_after_the_chosen_limit(monkeypatch):
    _prefs(monkeypatch, record_quality="custom", record_max_minutes=1)
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: FakeProc(cmd[-1]))
    sr.start()
    assert sr._limit == 60
    sr._started -= 61
    assert sr.status()["recording"] is False


def test_list_carries_previews_and_delete_removes_them(tmp_path, monkeypatch):
    f = tmp_path / "Lyra-2026-10-22-185004.mp4"
    f.write_bytes(b"mp4data")

    def fake_run(cmd, **kw):
        if "-frames:v" in cmd:
            open(cmd[-1], "wb").write(b"\xff\xd8jpeg")
            return types.SimpleNamespace(returncode=0, stderr=b"")
        banner = b"  Duration: 00:00:12.40, start: 0\n  Stream #0:0: Video: h264, yuv420p, 1920x1080, 60 fps, 60 tbr\n"
        return types.SimpleNamespace(returncode=1, stderr=banner)

    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    items = sr.list_recordings()["items"]
    assert items[0]["thumb"] is True and items[0]["duration"] == 12.4
    assert (items[0]["width"], items[0]["height"], items[0]["fps"]) == (1920, 1080, 60)
    assert sr.thumbnail(f.name).startswith(b"\xff\xd8")
    assert sr.delete_recording(f.name)["ok"]
    assert not list((tmp_path / ".thumbs").iterdir())


def test_thumbnail_refuses_paths_and_the_file_being_recorded():
    assert sr.thumbnail("../secret.mp4") is None
    assert sr.thumbnail("nothing.mp4") is None


# ── which screen is recorded ──────────────────────────────────────────────────

import screen_capture as sc  # noqa: E402

_TWO = [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
        {"index": 1, "left": 1920, "top": 0, "width": 1920, "height": 1080}]
_LEFT = [{"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
         {"index": 1, "left": -1920, "top": -200, "width": 1920, "height": 1080}]


def _screens(monkeypatch, mons, cursor=None):
    monkeypatch.setattr(sc, "monitors", lambda: mons)
    l = min(m["left"] for m in mons); t = min(m["top"] for m in mons)
    monkeypatch.setattr(sc, "virtual_bounds", lambda: (l, t, max(m["left"] + m["width"] for m in mons) - l, max(m["top"] + m["height"] for m in mons) - t))


def test_all_screens_has_no_crop(monkeypatch):
    _screens(monkeypatch, _TWO)
    assert sr.region_for("all") is None
    assert not any(a.startswith("crop=") for a in sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["medium"], "region": None}))


def test_one_screen_alone_never_crops(monkeypatch):
    _screens(monkeypatch, _TWO[:1])
    assert sr.region_for("primary") is None and sr.region_for("2") is None


def test_main_screen_is_cropped_out_of_the_desktop(monkeypatch):
    _screens(monkeypatch, _TWO)
    r = sr.region_for("primary")
    assert (r["l"], r["t"], r["w"], r["h"], r["vw"], r["vh"]) == (0, 0, 1920, 1080, 3840, 1080)
    vf = sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["medium"], "region": r})
    vf = vf[vf.index("-vf") + 1]
    assert vf.startswith("crop=w='iw*1920/3840':h='ih*1080/1080':x='iw*0/3840':y='ih*0/1080',") and vf.endswith("pad=ceil(iw/2)*2:ceil(ih/2)*2")


def test_second_screen_and_negative_origin(monkeypatch):
    _screens(monkeypatch, _TWO)
    assert sr.region_for("2")["l"] == 1920
    _screens(monkeypatch, _LEFT)                     # a screen left of and above the main one: the desktop starts at (-1920, -200)
    r = sr.region_for("2")
    assert (r["vl"], r["vt"], r["vw"], r["vh"]) == (-1920, -200, 3840, 1280)
    vf = sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["medium"], "region": r})
    vf = vf[vf.index("-vf") + 1]
    assert "x='iw*0/3840'" in vf and "y='ih*0/1280'" in vf          # the left screen starts at the desktop's left/top edge
    cmd = sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["medium"], "region": sr.region_for("primary")})
    assert "x='iw*1920/3840'" in cmd[cmd.index("-vf") + 1] and "y='ih*200/1280'" in cmd[cmd.index("-vf") + 1]   # main screen: 1920 px / 200 px in from the desktop's corner


def test_old_cursor_choice_reads_as_main_screen_and_unplugged_screen(monkeypatch):
    _screens(monkeypatch, _TWO, cursor=1)
    assert sr.region_for("cursor")["l"] == 0         # "Under cursor" was removed: it behaves like the main screen
    assert sr.region_for("4")["l"] == 0              # screen 4 is not there: the main screen, not a broken command


def test_scaled_down_picture_is_cropped_before_it_is_scaled(monkeypatch):
    _screens(monkeypatch, _TWO)
    vf = sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["low"], "region": sr.region_for("1")})
    vf = vf[vf.index("-vf") + 1]
    assert vf.index("crop=") < vf.index("scale=-2")


def test_layout_sentence_and_labels(monkeypatch):
    monkeypatch.setattr(sc, "monitors", lambda: _TWO)
    assert sc.screen_label(_TWO[0], _TWO) == "Screen 1 (main)"
    assert sc.screen_label(_TWO[1], _TWO) == "Screen 2 (right of the main screen)"
    assert "2 screens" in sc.describe_layout()
    monkeypatch.setattr(sc, "monitors", lambda: _TWO[:1])
    assert sc.describe_layout() == ""


def test_dpi_layer_is_used_only_when_it_shows_a_bigger_screen(monkeypatch):
    # The old rule (kept for when the real desktop size is unknown).
    # Scaled screen: unaware ffmpeg sees 1280x720, DPI-aware ffmpeg sees the real 1920x1080.
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (1920, 1080) if env else (1280, 720))
    assert sr.capture_env("ffmpeg") == {"__COMPAT_LAYER": "HighDpiAware"}
    # 100 % scaling: both see the same, so ffmpeg is left alone.
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (1920, 1080))
    assert sr.capture_env("ffmpeg") == {}
    # Probe failed: leave ffmpeg alone too.
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (0, 0))
    assert sr.capture_env("ffmpeg") == {}


def test_start_passes_the_dpi_environment(monkeypatch):
    seen = {}
    monkeypatch.setattr(sr, "plan_capture", lambda exe, region: {"env": {"__COMPAT_LAYER": "HighDpiAware"}, "input": [], "crop": None, "verified": True, "info": "t"})
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: seen.update(kw) or FakeProc(cmd[-1]))
    assert sr.start()["recording"]
    assert seen["env"]["__COMPAT_LAYER"] == "HighDpiAware"


# ── the "only a square of the screen is recorded" fix: real pixels + a verified capture plan ──────────────

def _desktop(monkeypatch, mons):
    _screens(monkeypatch, mons)


def _probe(seeing, region_size=None, aware=("HighDpiAware",)):
    """A fake gdigrab: sees `seeing` unless its environment holds one of the DPI layer spellings, then the real size.
    With offset / size options it gives `region_size` (or nothing when that is None)."""
    def probe(exe, env, extra=()):
        layer = (env or {}).get("__COMPAT_LAYER")
        size = seeing["real"] if layer in aware else seeing["plain"]
        if extra:
            return region_size or (0, 0)
        return size
    return probe


def test_real_pixels_does_nothing_off_windows():
    with sc.real_pixels():
        pass


def test_scaled_screen_gets_the_dpi_layer_and_the_whole_desktop(monkeypatch):
    _desktop(monkeypatch, _TWO[:1])
    monkeypatch.setattr(sr, "_probe_capture", _probe({"plain": (1536, 864), "real": (1920, 1080)}))
    plan = sr.plan_capture("ffmpeg", None)
    assert plan["verified"] and plan["env"] == {"__COMPAT_LAYER": "HighDpiAware"} and plan["input"] == [] and plan["crop"] is None
    cmd = sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["medium"], "region": None}, plan=plan)
    assert "-offset_x" not in cmd and not any(a.startswith("crop") for a in cmd)


def test_unscaled_screen_needs_no_layer(monkeypatch):
    _desktop(monkeypatch, _TWO[:1])
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (1920, 1080))
    plan = sr.plan_capture("ffmpeg", None)
    assert plan["verified"] and plan["env"] == {}


def test_the_other_spelling_of_the_layer_is_tried(monkeypatch):
    _desktop(monkeypatch, _TWO[:1])
    monkeypatch.setattr(sr, "_probe_capture", _probe({"plain": (1536, 864), "real": (1920, 1080)}, aware=("~ HIGHDPIAWARE",)))
    assert sr.plan_capture("ffmpeg", None)["env"] == {"__COMPAT_LAYER": "~ HIGHDPIAWARE"}


def test_main_screen_of_two_is_grabbed_with_an_exact_offset_and_size(monkeypatch):
    _desktop(monkeypatch, _TWO)
    monkeypatch.setattr(sr, "_probe_capture", _probe({"plain": (3072, 864), "real": (3840, 1080)}, region_size=(1920, 1080)))
    plan = sr.plan_capture("ffmpeg", sr.region_for("primary"))
    assert plan["verified"] and plan["input"] == ["-offset_x", "0", "-offset_y", "0", "-video_size", "1920x1080"]
    cmd = sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["medium"], "region": sr.region_for("primary")}, plan=plan)
    assert cmd[cmd.index("-offset_x") + 1] == "0" and cmd.index("-offset_x") < cmd.index("-i")
    assert not any(a.startswith("crop") for a in cmd[cmd.index("-vf") + 1].split(","))


def test_a_screen_left_of_the_main_one_uses_negative_offsets(monkeypatch):
    _desktop(monkeypatch, _LEFT)
    monkeypatch.setattr(sr, "_probe_capture", _probe({"plain": (3072, 1024), "real": (3840, 1280)}, region_size=(1920, 1080)))
    plan = sr.plan_capture("ffmpeg", sr.region_for("2"))
    assert plan["input"] == ["-offset_x", "-1920", "-offset_y", "-200", "-video_size", "1920x1080"]


def test_when_ffmpeg_refuses_the_offset_the_screen_is_cropped_pixel_for_pixel(monkeypatch):
    _desktop(monkeypatch, _LEFT)
    monkeypatch.setattr(sr, "_probe_capture", _probe({"plain": (3072, 1024), "real": (3840, 1280)}, region_size=None))
    plan = sr.plan_capture("ffmpeg", sr.region_for("primary"))
    assert plan["verified"] and plan["input"] == [] and plan["crop"] == "crop=1920:1080:1920:200"
    vf = sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["low"], "region": sr.region_for("primary")}, plan=plan)
    vf = vf[vf.index("-vf") + 1]
    assert vf.startswith("crop=1920:1080:1920:200,scale=-2:")        # cropped before it is scaled down


def test_if_nothing_makes_ffmpeg_see_real_pixels_the_old_ratio_crop_stays(monkeypatch):
    _desktop(monkeypatch, _TWO)
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (3072, 864))
    r = sr.region_for("primary")
    plan = sr.plan_capture("ffmpeg", r)
    assert not plan["verified"] and "cropping by ratio" in plan["info"]
    vf = sr.build_cmd("ffmpeg", "o.mp4", prof={**sr.QUALITY["medium"], "region": r}, plan=plan)
    assert vf[vf.index("-vf") + 1].startswith("crop=w='iw*1920/3840'")
    # a plan that did not check out is remembered only briefly (re-probing it on every start cost ~8 s)
    assert all(not p["verified"] and p.get("_until") for p in sr._plan_cache.values())


def test_a_good_plan_is_worked_out_once(monkeypatch):
    _desktop(monkeypatch, _TWO[:1])
    n = []
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): n.append(1) or (1920, 1080))
    sr.plan_capture("ffmpeg", None); sr.plan_capture("ffmpeg", None)
    assert len(n) == 1


def test_unknown_desktop_size_keeps_the_old_rule(monkeypatch):
    monkeypatch.setattr(sc, "virtual_bounds", lambda: (0, 0, 0, 0))
    monkeypatch.setattr(sr, "_probe_capture", lambda exe, env, extra=(): (1920, 1080) if env else (1280, 720))
    plan = sr.plan_capture("ffmpeg", None)
    assert not plan["verified"] and plan["env"] == {"__COMPAT_LAYER": "HighDpiAware"}


def test_start_records_with_the_plan_and_reports_it(monkeypatch):
    _desktop(monkeypatch, _TWO)
    monkeypatch.setattr(sr, "_probe_capture", _probe({"plain": (3072, 864), "real": (3840, 1080)}, region_size=(1920, 1080)))
    seen = {}
    monkeypatch.setattr(sr.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, env=kw["env"]) or FakeProc(cmd[-1]))
    s = sr.start()
    assert s["recording"] and "one screen 1920x1080" in s["capture"]
    assert seen["env"]["__COMPAT_LAYER"] == "HighDpiAware" and "-video_size" in seen["cmd"]
