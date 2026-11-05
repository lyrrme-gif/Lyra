"""Phase 7 tests: the notetaker. From the backend folder:

    python -m unittest tests.test_p7_notetaker -v

Everything is FAKE: audio sources produce synthetic noise bursts, the transcriber / diarizer / LLM are
stand-ins, downloads come from an in-memory server, data goes to a temp folder. No microphone, no model,
no network.
"""
import bz2
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import unittest
import wave
from pathlib import Path

import numpy as np

_TMP = tempfile.mkdtemp(prefix="p7data_")
os.environ["LYRA_DATA_DIR"] = _TMP

from engine.core import settings_store  # noqa: E402
from engine.notetaker import audio, diarize, merge, pipeline, recorder, segment, service, settings, store  # noqa: E402
from engine.notetaker import summarize as summ  # noqa: E402
from engine.bridge import api_p7  # noqa: E402

RNG = np.random.default_rng(1)


def bursts(spec, total=None, amp=0.3):
    """spec = [(start_s, end_s)] -> float32 noise bursts on silence."""
    total = total or max(e for _, e in spec) + 1.0
    a = np.zeros(int(total * 16000), dtype=np.float32)
    for s, e in spec:
        a[int(s * 16000):int(e * 16000)] = (RNG.standard_normal(int((e - s) * 16000)) * amp).astype(np.float32)
    return a


def pcm(a):
    return (np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes()


class Env(unittest.TestCase):
    def setUp(self):
        self.mem = {}
        self._o = (settings_store.get, settings_store.set_many)
        settings_store.get = lambda k, d=None: self.mem.get(k, d)
        settings_store.set_many = lambda u: self.mem.update(u)
        for d in ("notetaker",):
            shutil.rmtree(Path(_TMP) / d, ignore_errors=True)
        service._queue.clear()

    def tearDown(self):
        settings_store.get, settings_store.set_many = self._o


# ── settings ────────────────────────────────────────────────────────────────────────────────────────────
class Settings(Env):
    def test_defaults_are_private(self):
        s = settings.snapshot()
        self.assertIs(s["p7_summarize"], False)               # text-only LLM is OFF by default
        self.assertEqual(s["p7_summary_provider"], "local")
        self.assertEqual(s["p7_retention_days"], 30)

    def test_validation(self):
        self.assertEqual(settings.update({"p7_retention_days": 7})["p7_retention_days"], 7)
        for bad in ({"p7_summarize": "yes"}, {"p7_retention_days": -1}, {"p7_retention_days": True},
                    {"p7_summary_provider": "cloud"}, {"p7_max_hours": 99}):
            with self.assertRaises(ValueError):
                settings.update(bad)
        settings.update({"nonsense": 1})
        self.assertNotIn("nonsense", self.mem)


# ── audio helpers ───────────────────────────────────────────────────────────────────────────────────────
class AudioTests(Env):
    def test_resampler_48k_stereo(self):
        r = audio.Resampler(48000, 2)
        out = b"".join(r.feed(np.zeros(4800 * 2, np.int16).tobytes()) for _ in range(10))
        self.assertEqual(len(out) // 2, 16000)

    def test_resampler_44k_keeps_tone_and_length(self):
        r = audio.Resampler(44100, 1)
        t = np.arange(44100) / 44100
        x = (np.sin(2 * np.pi * 440 * t) * 20000).astype(np.int16)
        out = b"".join(r.feed(x[i:i + 1000].tobytes()) for i in range(0, 44100, 1000))
        y = np.frombuffer(out, np.int16)
        self.assertLess(abs(len(y) - 16000), 20)
        self.assertGreater(np.abs(y).max(), 15000)

    def test_track_fills_silence_gaps_and_pads(self):
        now = [0.0]
        tr = audio.Track(Path(_TMP) / "t.wav", clock=lambda: now[0], t0=0.0)
        tr.push(bytes(3200))                                   # 0.1 s at t=0.1
        now[0] = 0.1
        now[0] = 5.0                                           # 4.9 s of nothing, then audio
        tr.push(pcm(np.full(1600, 0.1)))
        tr.finish(6.0)
        with wave.open(str(tr.path)) as w:
            self.assertAlmostEqual(w.getnframes() / 16000, 6.0, delta=0.1)

    def test_segments_find_speech_only(self):
        a = bursts([(1.0, 3.0), (6.0, 8.5)], total=10)
        segs = segment.segments(a)
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0][0], 1.0, delta=0.4)
        self.assertAlmostEqual(segs[1][1], 8.5, delta=0.4)
        self.assertEqual(segment.segments(np.zeros(16000 * 5, np.float32)), [])

    def test_segments_merge_short_pauses_and_split_long_speech(self):
        self.assertEqual(len(segment.segments(bursts([(1, 2), (2.4, 3.5)], total=5))), 1)
        long = segment.segments(bursts([(0.5, 70)], total=71))
        self.assertGreaterEqual(len(long), 3)
        self.assertTrue(all(e - s <= 28.5 for s, e in long))


# ── merge ───────────────────────────────────────────────────────────────────────────────────────────────
class Merge(Env):
    def seg(self, a, b, t, spk=None):
        return {"start": a, "end": b, "text": t, "spk": spk}

    def test_assign_by_max_overlap(self):
        segs = [self.seg(0, 4, "a"), self.seg(5, 8, "b")]
        merge.assign_speakers(segs, [(0, 3, 1), (3, 5, 0), (5, 9, 0)])
        self.assertEqual([s["spk"] for s in segs], [1, 0])

    def test_labels_you_and_speakers_in_order_of_appearance(self):
        mic = [self.seg(10, 12, "sounds good")]
        sysr = [self.seg(0, 4, "hello team", 7), self.seg(5, 8, "agenda first", 2), self.seg(14, 16, "bye", 7)]
        lines, speakers = merge.build_lines(mic, sysr, diarized=True)
        self.assertEqual(speakers, ["Speaker 1", "Speaker 2", "You"])
        self.assertEqual([l["speaker"] for l in lines], ["Speaker 1", "Speaker 2", "You", "Speaker 1"])

    def test_without_diarization_remote(self):
        lines, speakers = merge.build_lines([], [self.seg(0, 2, "hi")], diarized=False)
        self.assertEqual(speakers, ["Remote"])

    def test_mic_echo_of_speakers_is_dropped(self):
        sysr = [self.seg(0, 4, "let us review the quarterly numbers today")]
        mic = [self.seg(0.2, 4.1, "Let us review the quarterly numbers today."), self.seg(5, 6, "yes please")]
        lines, _ = merge.build_lines(mic, sysr, diarized=False)
        self.assertEqual([l["speaker"] for l in lines], ["Remote", "You"])

    def test_real_overlapping_speech_is_kept(self):
        sysr = [self.seg(0, 4, "we should ship friday")]
        mic = [self.seg(1, 3, "I disagree completely")]
        lines, _ = merge.build_lines(mic, sysr, diarized=False)
        self.assertEqual(len(lines), 2)

    def test_same_speaker_neighbours_are_joined(self):
        lines, _ = merge.build_lines([self.seg(0, 2, "one"), self.seg(2.5, 4, "two"), self.seg(20, 21, "three")], [], diarized=False)
        self.assertEqual([l["text"] for l in lines], ["one two", "three"])


# ── store ───────────────────────────────────────────────────────────────────────────────────────────────
def doc(sid="20260101-100000-abcd", started=None, **kw):
    started = started or time.time()
    d = {"id": sid, "title": "Weekly", "started": started, "ended": started + 60, "duration": 60,
         "speakers": ["You", "Remote"], "diarized": False, "summary": None,
         "lines": [{"start": 0, "end": 2, "speaker": "You", "text": "hello"},
                   {"start": 3, "end": 5, "speaker": "Remote", "text": "hi there"}]}
    d.update(kw)
    return d


class Store(Env):
    def test_roundtrip_list_and_no_traversal(self):
        store.save(doc())
        self.assertEqual(store.get("20260101-100000-abcd")["title"], "Weekly")
        self.assertEqual(store.list_sessions()[0]["preview"], "hello hi there")
        self.assertIsNone(store.get("../../etc/passwd"))
        self.assertFalse(store.valid_id("../x"))
        with self.assertRaises(ValueError):
            store.save(doc(sid="../evil"))

    def test_retention(self):
        now = time.time()
        store.save(doc("20250101-100000-aaaa", started=now - 40 * 86400))
        store.save(doc("20260101-100000-bbbb", started=now - 5 * 86400))
        self.assertEqual(store.purge_expired(30, now), 1)
        self.assertEqual([s["id"] for s in store.list_sessions()], ["20260101-100000-bbbb"])
        self.assertEqual(store.purge_expired(0, now), 0)       # 0 = keep forever

    def test_rename_speaker_export_delete(self):
        store.save(doc(summary={"text": "We said hi.", "todos": [{"text": "Send notes", "owner": "Ana"}]}))
        d = store.rename_speaker("20260101-100000-abcd", "Remote", "Ana")
        self.assertEqual(d["speakers"], ["You", "Ana"])
        md = store.export_markdown(d)
        self.assertIn("**Ana**", md)
        self.assertIn("- [ ] Send notes (Ana)", md)
        self.assertIsNone(store.rename_speaker("20260101-100000-abcd", "Nobody", "X"))
        self.assertTrue(store.delete("20260101-100000-abcd"))
        self.assertFalse(store.delete("20260101-100000-abcd"))


# ── recorder ────────────────────────────────────────────────────────────────────────────────────────────
class FakeSource:
    def __init__(self, name, audio_arr=None, fail=None):
        self.name, self.audio, self.fail, self.stopped = name, audio_arr, fail, False

    def start(self, on_chunk):
        if self.fail:
            raise self.fail
        if self.audio is not None:
            b = pcm(self.audio)
            for i in range(0, len(b), 3200):
                on_chunk(b[i:i + 3200])

    def stop(self):
        self.stopped = True


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Recording(Env):
    def mk(self, sources, clock=None, max_s=lambda: 99999):
        return recorder.Recorder(lambda: sources, clock or FakeClock(), time.time, max_s)

    def test_refuses_without_acknowledgement(self):
        r = self.mk({"mic": FakeSource("mic")})
        for ack in (False, None, "true", 1):
            with self.assertRaises(recorder.NotAcknowledged):
                r.start("x", ack)
        self.assertFalse(r.active)
        self.assertEqual(list(pipeline.tmp_dir().glob("*")), [])       # nothing was created

    def test_start_stop_writes_aligned_tracks_and_marker(self):
        clk = FakeClock()
        mic, sysn = FakeSource("mic", bursts([(0.5, 1.5)], 3)), FakeSource("system", bursts([(1.0, 2.0)], 3))
        r = self.mk({"mic": mic, "system": sysn}, clk)
        st = r.start("Standup", True, "en")
        self.assertTrue(st["recording"] and st["system_audio"])
        clk.t = 3.0
        mk = r.stop()
        self.assertTrue(mic.stopped and sysn.stopped)
        self.assertEqual(mk["state"], "pending")
        for fn in mk["tracks"].values():
            with wave.open(str(pipeline.tmp_dir() / fn)) as w:
                self.assertAlmostEqual(w.getnframes() / 16000, 3.0, delta=0.1)
        self.assertEqual(pipeline.read_marker(mk["id"])["title"], "Standup")
        self.assertIsNone(r.stop())

    def test_system_audio_unavailable_still_records_mic(self):
        r = self.mk({"mic": FakeSource("mic"), "system": FakeSource("system", fail=ImportError("pyaudiowpatch"))})
        st = r.start("x", True)
        self.assertFalse(st["system_audio"])
        self.assertIn("needs Windows", " ".join(st["warnings"]))
        r.stop()

    def test_no_input_at_all_is_an_error_and_leaves_nothing(self):
        r = self.mk({"mic": FakeSource("mic", fail=OSError("no device"))})
        with self.assertRaises(RuntimeError):
            r.start("x", True)
        self.assertEqual(list(pipeline.tmp_dir().glob("*.wav")), [])

    def test_second_start_refused(self):
        r = self.mk({"mic": FakeSource("mic")})
        r.start("a", True)
        with self.assertRaises(RuntimeError):
            r.start("b", True)
        r.stop()


# ── pipeline ────────────────────────────────────────────────────────────────────────────────────────────
def make_session(mic_spec, sys_spec, total=12.0, sid="20260105-120000-0001"):
    mk = {"id": sid, "title": "Call", "started": time.time() - 100, "language": "en", "state": "pending",
          "duration": total, "system_audio": True, "tracks": {}}
    for name, spec in (("mic", mic_spec), ("system", sys_spec)):
        fn = f"{sid}-{name}.wav"
        a = bursts(spec, total) if spec else np.zeros(int(total * 16000), np.float32)
        with wave.open(str(pipeline.tmp_dir() / fn), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
            w.writeframes(pcm(a))
        mk["tracks"][name] = fn
    pipeline.write_marker(mk)
    return mk


def fake_tr(audio_arr, lang, hints):
    # "recognise" by loudness segment: a deterministic stand-in for the speech engine
    return f"words{int(len(audio_arr) / 16000 * 10)}"


class PipelineTests(Env):
    def audio_files(self):
        return list(pipeline.tmp_dir().glob("*"))

    def test_transcript_saved_then_audio_deleted(self):
        mk = make_session([(1, 3)], [(5, 8)])
        doc_ = pipeline.Pipeline(fake_tr, "fake").process(mk["id"])
        self.assertTrue(store.exists(mk["id"]))
        self.assertEqual([l["speaker"] for l in doc_["lines"]], ["You", "Remote"])
        self.assertEqual(self.audio_files(), [])                       # every wav AND the marker are gone
        self.assertEqual(doc_["engine"], "fake")

    def test_audio_is_deleted_only_after_the_transcript_exists(self):
        mk = make_session([(1, 3)], [(5, 8)])
        seen = []
        real_wipe = pipeline.wipe_audio
        pipeline.wipe_audio = lambda sid: (seen.append(store.exists(sid)), real_wipe(sid))[1]
        try:
            pipeline.Pipeline(fake_tr, "f").process(mk["id"])
        finally:
            pipeline.wipe_audio = real_wipe
        self.assertEqual(seen, [True])

    def test_engine_failure_keeps_audio_and_marks_failed(self):
        mk = make_session([(1, 3)], [(5, 8)])

        def boom(a, l, h):
            raise RuntimeError("no engine")
        with self.assertRaises(pipeline.PipelineError):
            pipeline.Pipeline(boom, "x").process(mk["id"])
        self.assertFalse(store.exists(mk["id"]))
        self.assertEqual(len(list(pipeline.tmp_dir().glob("*.wav"))), 2)
        self.assertEqual(pipeline.read_marker(mk["id"])["state"], "failed")
        self.assertIn(mk["id"], pipeline.pending())
        pipeline.Pipeline(fake_tr, "x").process(mk["id"])              # a retry finishes the job
        self.assertEqual(self.audio_files(), [])

    def test_silent_call_still_stores_an_empty_transcript_and_deletes_audio(self):
        mk = make_session(None, None)
        d = pipeline.Pipeline(fake_tr, "x").process(mk["id"])
        self.assertEqual(d["lines"], [])
        self.assertEqual(self.audio_files(), [])

    def test_diarization_labels_and_graceful_failure(self):
        d_ok = diarize.available
        diarize.available = lambda: True
        try:
            mk = make_session([(1, 2)], [(4, 6), (8, 10)], sid="20260105-120000-0002")
            turns = lambda a: [(3.5, 7, 0), (7.5, 11, 1)]
            d = pipeline.Pipeline(fake_tr, "x", diarizer=turns).process(mk["id"])
            self.assertTrue(d["diarized"])
            self.assertEqual(d["speakers"], ["You", "Speaker 1", "Speaker 2"])
            mk = make_session([], [(4, 6)], sid="20260105-120000-0003")

            def bad(a):
                raise RuntimeError("model broke")
            d = pipeline.Pipeline(fake_tr, "x", diarizer=bad).process(mk["id"])
            self.assertFalse(d["diarized"])
            self.assertEqual(d["speakers"], ["Remote"])                # transcript still produced
        finally:
            diarize.available = d_ok

    def test_diarization_off_in_settings(self):
        self.mem["p7_diarize"] = False
        called = []
        d_ok = diarize.available
        diarize.available = lambda: True
        try:
            mk = make_session([], [(4, 6)])
            pipeline.Pipeline(fake_tr, "x", diarizer=lambda a: called.append(1) or []).process(mk["id"])
        finally:
            diarize.available = d_ok
        self.assertEqual(called, [])

    def test_summary_off_by_default_and_failure_keeps_transcript(self):
        calls = []
        mk = make_session([(1, 3)], [])
        pipeline.Pipeline(fake_tr, "x", summarizer=lambda l, p: calls.append(p) or {}).process(mk["id"])
        self.assertEqual(calls, [])
        self.mem["p7_summarize"] = True

        def bad(l, p):
            raise RuntimeError("provider down")
        mk = make_session([(1, 3)], [], sid="20260105-120000-0004")
        p = pipeline.Pipeline(fake_tr, "x", summarizer=bad)
        d = p.process(mk["id"])
        self.assertTrue(store.exists(mk["id"]))
        self.assertIsNone(d["summary"])
        self.assertIn("Summary failed", p.state["error"])
        self.assertEqual(self.audio_files(), [])

    def test_summary_saved_when_on(self):
        self.mem["p7_summarize"] = True
        mk = make_session([(1, 3)], [])
        p = pipeline.Pipeline(fake_tr, "x", summarizer=lambda l, prov: {"text": "S", "todos": [], "provider": prov})
        d = p.process(mk["id"])
        self.assertEqual(d["summary"]["text"], "S")
        self.assertEqual(d["summary"]["provider"], "local")

    def test_sweep(self):
        # transcript exists but audio was left behind (delete failed): swept
        mk = make_session([(1, 3)], [])
        store.save(doc(sid=mk["id"]))
        # orphan wav without marker
        (pipeline.tmp_dir() / "20260106-120000-9999-mic.wav").write_bytes(b"x")
        # stale unprocessed session
        old = make_session([(1, 3)], [], sid="20250101-000000-0000")
        past = time.time() - 9 * 86400
        os.utime(pipeline.marker_path(old["id"]), (past, past))
        pipeline.sweep()
        self.assertEqual(list(pipeline.tmp_dir().glob("*")), [])

    def test_sweep_keeps_fresh_pending_session(self):
        mk = make_session([(1, 3)], [])
        pipeline.sweep()
        self.assertEqual(len(list(pipeline.tmp_dir().glob("*.wav"))), 2)
        self.assertEqual(pipeline.pending(), [mk["id"]])


# ── summarize ───────────────────────────────────────────────────────────────────────────────────────────
class Summaries(Env):
    LINES = [{"start": 0, "end": 2, "speaker": "You", "text": "Please send the deck by Friday"},
             {"start": 3, "end": 4, "speaker": "Ana", "text": "I will do it"}]

    def test_parse_variants(self):
        r = summ.parse('Sure! {"summary": "Deck due.", "todos": [{"text": "Send deck", "owner": "Ana"}, "Book room"]} bye')
        self.assertEqual(r["text"], "Deck due.")
        self.assertEqual([t["text"] for t in r["todos"]], ["Send deck", "Book room"])
        self.assertEqual(summ.parse("just prose")["text"], "just prose")
        self.assertEqual(summ.parse("")["todos"], [])
        self.assertEqual(summ.parse("{broken json}")["text"], "{broken json}")

    def test_only_text_is_sent(self):
        sent = []

        def fake(provider, messages):
            sent.append((provider, messages))
            return '{"summary": "ok", "todos": []}'
        out = summ.summarize(self.LINES, "key", fake)
        self.assertEqual(out["provider"], "key")
        for _, msgs in sent:
            for m in msgs:
                self.assertIsInstance(m["content"], str)
        self.assertIn("Ana: I will do it", sent[0][1][1]["content"])

    def test_long_transcript_is_split_and_merged(self):
        lines = [{"start": i, "end": i + 1, "speaker": "A", "text": "word " * 300} for i in range(40)]
        calls = []

        def fake(p, msgs):
            calls.append(msgs[1]["content"][:20])
            return '{"summary": "part", "todos": [{"text": "Do X", "owner": ""}]}'
        out = summ.summarize(lines, "local", fake)
        self.assertGreater(len(calls), 2)
        self.assertEqual(out["text"], "part")
        self.assertEqual([t["text"] for t in out["todos"]], ["Do X"])

    def test_empty_transcript_makes_no_llm_call(self):
        def never(*a):
            raise AssertionError("must not be called")
        self.assertEqual(summ.summarize([], "local", never)["text"], "")

    def test_complete_without_provider_is_a_clear_error(self):
        import types
        fake = types.ModuleType("engine.brain.brain")
        fake._get_client = lambda: (None, None)
        saved = sys.modules.get("engine.brain.brain")
        sys.modules["engine.brain.brain"] = fake                      # the real brain needs the openai package
        try:
            with self.assertRaises(RuntimeError) as cm:
                summ.complete("key", [])
            self.assertIn("API key", str(cm.exception))
        finally:
            if saved is not None:
                sys.modules["engine.brain.brain"] = saved
            else:
                del sys.modules["engine.brain.brain"]


# ── diarization model download ─────────────────────────────────────────────────────────────────────────
def tarball(member, data):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:bz2") as t:
        ti = tarfile.TarInfo(member)
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    return raw.getvalue()


class FakeNet:
    def __init__(self, files):
        self.files = files
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return io.BytesIO(self.files[url])


class ModelDownload(Env):
    def items(self, corrupt=False, scheme="https"):
        seg = tarball("pkg/model.int8.onnx", b"SEGMENT" * 100)
        emb = b"EMBED" * 100
        its = (diarize.Item(f"{scheme}://x.test/seg.tar.bz2", hashlib.sha256(seg).hexdigest(), "segmentation.int8.onnx", "pkg/model.int8.onnx"),
               diarize.Item(f"{scheme}://x.test/emb.onnx", hashlib.sha256(emb).hexdigest(), "embedding.onnx"))
        if corrupt:
            emb = emb[:-1] + b"!"
        return its, FakeNet({its[0].url: seg, its[1].url: emb})

    def test_download_install_delete(self):
        its, net = self.items()
        dest = Path(_TMP) / "diar"
        m = diarize.ModelManager(dest, net, its)
        self.assertFalse(m.installed())
        self.assertTrue(m.start())
        m.wait()
        self.assertTrue(m.installed(), m.status())
        self.assertEqual((dest / "segmentation.int8.onnx").read_bytes(), b"SEGMENT" * 100)
        self.assertEqual(m.status()["state"], "ready")
        ok, _ = m.delete()
        self.assertTrue(ok)
        self.assertFalse(dest.exists())

    def test_checksum_mismatch_installs_nothing(self):
        its, net = self.items(corrupt=True)
        m = diarize.ModelManager(Path(_TMP) / "diar2", net, its)
        m.start()
        m.wait()
        self.assertFalse(m.installed())
        self.assertEqual(m.status()["state"], "error")
        self.assertIn("checksum", m.status()["error"])
        self.assertFalse(list((Path(_TMP) / "diar2").glob("*.part")))

    def test_non_https_refused(self):
        its, net = self.items(scheme="http")
        m = diarize.ModelManager(Path(_TMP) / "diar3", net, its)
        m.start()
        m.wait()
        self.assertEqual(m.status()["state"], "error")
        self.assertEqual(net.urls, [])

    def test_unavailable_models_raise_for_diarize(self):
        its, net = self.items()
        with self.assertRaises(Exception):
            diarize.diarize(np.zeros(16000, np.float32), mgr=diarize.ModelManager(Path(_TMP) / "none", net, its))

    def test_pinned_manifest_shape(self):
        for it in diarize.ITEMS:
            self.assertTrue(it.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/"))
            self.assertRegex(it.sha256, r"^[0-9a-f]{64}$")


# ── API ─────────────────────────────────────────────────────────────────────────────────────────────────
class ApiTests(Env):
    def test_foreign_paths_ignored(self):
        self.assertIsNone(api_p7.dispatch("GET", "/health"))

    def test_start_requires_acknowledgement_and_returns_reminder(self):
        code, obj = api_p7.dispatch("POST", api_p7.PREFIX + "start", None, {"title": "x"})
        self.assertEqual(code, 409)
        self.assertEqual(obj["error"], "acknowledge_required")
        self.assertIn("recording", obj["reminder"].lower())
        self.assertFalse(service.recorder.active)

    def test_status_has_reminder_and_settings(self):
        code, obj = api_p7.dispatch("GET", api_p7.PREFIX + "status")
        self.assertEqual(code, 200)
        self.assertIn("recording", obj["reminder"].lower())
        self.assertFalse(obj["settings"]["p7_summarize"])

    def test_sessions_get_export_rename_delete(self):
        store.save(doc())
        sid = "20260101-100000-abcd"
        self.assertEqual(api_p7.dispatch("GET", api_p7.PREFIX + "sessions")[1]["sessions"][0]["id"], sid)
        self.assertEqual(api_p7.dispatch("GET", api_p7.PREFIX + "session", {"id": [sid]})[0], 200)
        self.assertIn("# Weekly", api_p7.dispatch("GET", api_p7.PREFIX + "export", {"id": [sid]})[1]["markdown"])
        self.assertEqual(api_p7.dispatch("GET", api_p7.PREFIX + "session", {"id": ["../x"]})[0], 404)
        self.assertEqual(api_p7.dispatch("POST", api_p7.PREFIX + "rename", None, {"id": sid, "title": "New"})[0], 200)
        self.assertEqual(store.get(sid)["title"], "New")
        self.assertEqual(api_p7.dispatch("POST", api_p7.PREFIX + "delete", None, {"id": "bad"})[0], 400)
        self.assertEqual(api_p7.dispatch("POST", api_p7.PREFIX + "delete", None, {"id": sid})[0], 200)

    def test_summarize_forbidden_until_enabled(self):
        store.save(doc())
        code, obj = api_p7.dispatch("POST", api_p7.PREFIX + "summarize", None, {"id": "20260101-100000-abcd"})
        self.assertEqual(code, 403)

    def test_settings_route(self):
        self.assertEqual(api_p7.dispatch("POST", api_p7.PREFIX + "settings", None, {"p7_retention_days": 14})[1]["settings"]["p7_retention_days"], 14)
        self.assertEqual(api_p7.dispatch("POST", api_p7.PREFIX + "settings", None, {"p7_summarize": "x"})[0], 400)

    def test_register_wraps_and_blocks_foreign_origin(self):
        class H:
            def __init__(s, path, origin=None):
                s.path, s.headers, s.out = path, ({"Origin": origin} if origin else {}), None

            def _json(s, c, o):
                s.out = (c, o)

            def do_GET(s):
                s.out = ("orig", s.path)

            def do_POST(s):
                s.out = ("orig", s.path)
        api_p7.register(H)
        api_p7.register(H)
        h = H("/status")
        h.do_GET()
        self.assertEqual(h.out, ("orig", "/status"))
        h = H("/p7/notetaker/sessions")
        h.do_GET()
        self.assertEqual(h.out[0], 200)
        h = H("/p7/notetaker/sessions", origin="https://evil.example")
        h.do_GET()
        self.assertEqual(h.out[0], 403)
        h = H("/p7/notetaker/delete-all", origin="https://evil.example")
        h.do_POST()
        self.assertEqual(h.out[0], 403)


class Startup(Env):
    def test_startup_purges_sweeps_and_queues(self):
        now = time.time()
        store.save(doc("20240101-100000-aaaa", started=now - 90 * 86400))
        mk = make_session([(1, 3)], [])
        service._started = False
        queued = []
        orig = service.enqueue
        service.enqueue = queued.append
        try:
            service.startup()
        finally:
            service.enqueue = orig
        self.assertFalse(store.exists("20240101-100000-aaaa"))
        self.assertEqual(queued, [mk["id"]])


if __name__ == "__main__":
    unittest.main()
