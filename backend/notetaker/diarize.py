"""Lyra notetaker - who spoke when (speaker diarization), as a SEPARATE optional download (~47 MB).

Models (run by sherpa-onnx, which Lyra already ships for streaming speech):
  pyannote-segmentation-3.0  (MIT)       from the k2-fsa/sherpa-onnx releases, int8 ONNX
  NeMo TitaNet-small speaker embeddings (CC-BY-4.0), same releases
Both files are pinned by SHA-256. Without the models or sherpa-onnx, transcripts simply label the
remote side "Remote" instead of "Speaker 1/2/..": nothing else changes."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tarfile
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import SR

try:
    import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.notetaker")

_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"


@dataclass(frozen=True)
class Item:
    url: str
    sha256: str
    save_as: str
    member: str = ""        # take this one file out of a .tar.bz2


ITEMS = (
    Item(_BASE + "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
         "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
         "segmentation.int8.onnx", "sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx"),
    Item(_BASE + "speaker-recongition-models/nemo_en_titanet_small.onnx",
         "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e", "embedding.onnx"),
)
APPROX_BYTES = 47_000_000


def models_dir() -> Path:
    import paths
    return paths.data_dir() / "models" / "diarization"


def sherpa_available() -> bool:
    try:
        import sherpa_onnx
        return hasattr(sherpa_onnx, "OfflineSpeakerDiarization")
    except Exception:
        return False


class ModelManager:
    """Download / delete the two files. `opener(url) -> file-like with read(n)` is injectable for tests."""

    def __init__(self, dest: Path | None = None, opener=None, items=ITEMS):
        self._dest = dest
        self._open = opener or (lambda url: urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "Lyra-notetaker"}), timeout=30))
        self.items = items
        self._lock = threading.Lock()
        self._t: threading.Thread | None = None
        self._cancel = threading.Event()
        self._s = {"state": "idle", "done": 0, "error": ""}

    @property
    def dest(self) -> Path:
        return self._dest or models_dir()

    def installed(self) -> bool:
        try:
            m = json.loads((self.dest / "installed.json").read_text(encoding="utf-8"))
            return all((self.dest / i.save_as).is_file() and (self.dest / i.save_as).stat().st_size == m["sizes"][i.save_as]
                       for i in self.items)
        except Exception:
            return False

    def paths(self) -> dict:
        return {"segmentation": str(self.dest / self.items[0].save_as), "embedding": str(self.dest / self.items[1].save_as)}

    def status(self) -> dict:
        with self._lock:
            s = dict(self._s)
        inst = self.installed()
        state = "ready" if inst and s["state"] != "downloading" else s["state"]
        return {"state": state, "installed": inst, "downloading": s["state"] == "downloading",
                "progress": 1.0 if inst else round(min(0.99, s["done"] / APPROX_BYTES), 3),
                "error": s["error"], "sherpa": sherpa_available(), "approx_mb": APPROX_BYTES // 1_000_000}

    def start(self) -> bool:
        with self._lock:
            if self._t is not None and self._t.is_alive():
                return False
            self._cancel.clear()
            self._s.update(state="downloading", done=0, error="")
            self._t = threading.Thread(target=self._run, name="lyra-diar-dl", daemon=True)
            self._t.start()
            return True

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout=20) -> None:
        if self._t:
            self._t.join(timeout)

    def delete(self) -> tuple[bool, str]:
        if self._t is not None and self._t.is_alive():
            return False, "A download is running. Cancel it first."
        import shutil
        try:
            _drop_cached()
            if self.dest.exists():
                shutil.rmtree(self.dest)
        except OSError as e:
            return False, f"Could not delete the models ({e})."
        with self._lock:
            self._s.update(state="idle", done=0, error="")
        return True, ""

    def _run(self):
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
            for it in self.items:
                self._fetch(it)
            sizes = {i.save_as: (self.dest / i.save_as).stat().st_size for i in self.items}
            tmp = self.dest / "installed.json.tmp"
            tmp.write_text(json.dumps({"sizes": sizes}), encoding="utf-8")
            os.replace(tmp, self.dest / "installed.json")
            with self._lock:
                self._s["state"] = "ready"
        except _Cancelled:
            with self._lock:
                self._s.update(state="cancelled", error="")
        except Exception as e:
            log.warning("diarization model download failed: %s", e)
            with self._lock:
                self._s.update(state="error", error=str(e) or e.__class__.__name__)

    def _fetch(self, it: Item):
        if not it.url.startswith("https://"):
            raise RuntimeError("Refusing a non-https download URL.")
        target = self.dest / it.save_as
        if target.is_file() and not it.member and _sha(target) == it.sha256:
            return
        with tempfile.NamedTemporaryFile(dir=self.dest, suffix=".part", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            h = hashlib.sha256()
            resp = self._open(it.url)
            try:
                while True:
                    if self._cancel.is_set():
                        raise _Cancelled()
                    b = resp.read(1 << 20)
                    if not b:
                        break
                    tmp.write(b)
                    h.update(b)
                    with self._lock:
                        self._s["done"] += len(b)
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
        try:
            if h.hexdigest() != it.sha256:
                raise RuntimeError(f"{it.save_as}: checksum mismatch (SHA-256); the download was discarded.")
            if it.member:
                with tarfile.open(tmp_path, "r:bz2") as tar:
                    f = tar.extractfile(it.member)          # exactly one named member; no path is ever written from the archive
                    if f is None:
                        raise RuntimeError(f"{it.member} not found in the archive")
                    out_tmp = self.dest / (it.save_as + ".tmp")
                    with open(out_tmp, "wb") as o:
                        while True:
                            b = f.read(1 << 20)
                            if not b:
                                break
                            o.write(b)
                os.replace(out_tmp, target)
            else:
                os.replace(tmp_path, target)
                tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


class _Cancelled(Exception):
    pass


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


_manager: ModelManager | None = None
_engine = {"sd": None, "key": None}
_lock = threading.Lock()


def manager() -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager


def _drop_cached():
    with _lock:
        _engine["sd"] = _engine["key"] = None


def available() -> bool:
    return sherpa_available() and manager().installed()


def diarize(audio: np.ndarray, num_speakers: int | None = None, mgr: ModelManager | None = None) -> list[tuple[float, float, int]]:
    """-> [(start_s, end_s, speaker_index)] for 16 kHz mono float32. Raises if unavailable."""
    import sherpa_onnx
    mgr = mgr or manager()
    if not mgr.installed():
        raise RuntimeError("diarization models are not downloaded")
    p = mgr.paths()
    key = (p["segmentation"], p["embedding"], num_speakers)
    with _lock:
        if _engine["sd"] is None or _engine["key"] != key:
            cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=p["segmentation"]),
                    num_threads=2),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=p["embedding"], num_threads=2),
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers or -1, threshold=0.5),
                min_duration_on=0.3, min_duration_off=0.5)
            if not cfg.validate():
                raise RuntimeError("diarization config is not valid")
            _engine["sd"], _engine["key"] = sherpa_onnx.OfflineSpeakerDiarization(cfg), key
        sd = _engine["sd"]
        res = sd.process(np.ascontiguousarray(audio, dtype=np.float32)).sort_by_start_time()
    return [(float(s.start), float(s.end), int(s.speaker)) for s in res]
