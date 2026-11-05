"""Lyra - one-click model download: progress, resume, SHA-256 verification, delete.

Everything lives under the data folder (paths.data_path), never in the repo or the installer.

Safety properties
  * Only https URLs. The files come from a PINNED Hugging Face revision, so the bytes cannot change
    under us, and every large file is checked against a pinned SHA-256 before it is accepted.
  * A file is written as <name>.part and renamed only after it verified. A crash, a lost
    connection or "Cancel" leaves the .part behind and the next start resumes it (HTTP Range).
  * A checksum mismatch deletes the partial file and downloads that file once more from byte 0;
    a second mismatch is an error (nothing broken is ever kept or loaded).
  * `installed.json` is written last. Without it the model counts as not installed.
The network layer is injectable (`opener`) so the whole thing is tested offline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

try:                                    # same logger as the rest of Lyra when available
    from engine.bridge import ui
    log = ui.log
except Exception:                       # tests / tools without the full backend
    log = logging.getLogger("lyra.stt_engines")

CHUNK = 1024 * 1024
USER_AGENT = "Lyra-voice-model-downloader/1"


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str | None = None      # None only for tiny text files that have no published digest
    size: int | None = None        # exact bytes if pinned; otherwise taken from the server
    approx: int = 0                # rough size, only used to make the progress bar sensible early on


@dataclass(frozen=True)
class ModelSpec:
    id: str
    title: str
    revision: str
    files: tuple[ModelFile, ...]
    approx_total: int = field(default=0)


_HF = "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/"
_REV = "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"

# NVIDIA Parakeet TDT 0.6B v3, ONNX export by istupakov (CC-BY-4.0), int8 files only.
# onnx-asr ships its own feature extractor, so the repo's nemo128.onnx is not needed.
# SHA-256 of the two large files copied from the Hugging Face file pages at this revision.
# config.json (97 B) and vocab.txt (94 kB) are plain git files without a published digest; they are
# size-checked and parsed before use.
PARAKEET_V3_INT8 = ModelSpec(
    id="parakeet-tdt-0.6b-v3-int8",
    title="Parakeet v3 (int8)",
    revision=_REV,
    files=(
        ModelFile("config.json", f"{_HF}{_REV}/config.json", None, None, 97),
        ModelFile("vocab.txt", f"{_HF}{_REV}/vocab.txt", None, None, 93_900),
        ModelFile("decoder_joint-model.int8.onnx", f"{_HF}{_REV}/decoder_joint-model.int8.onnx",
                  "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70", None, 18_200_000),
        ModelFile("encoder-model.int8.onnx", f"{_HF}{_REV}/encoder-model.int8.onnx",
                  "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09", None, 652_000_000),
    ),
    approx_total=670_300_000,
)

MARKER = "installed.json"


class DownloadError(Exception):
    pass


class _Cancelled(Exception):
    pass


# -- network layer ------------------------------------------------------------------------------
class _Resp:
    """The tiny response interface the downloader needs (urllib's response has it too)."""
    status: int
    headers: dict

    def read(self, n: int) -> bytes: ...
    def close(self) -> None: ...


def default_opener(url: str, headers: dict) -> _Resp:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        return urllib.request.urlopen(req, timeout=30)          # follows redirects, keeps Range
    except urllib.error.HTTPError as e:
        if e.code == 416:                                       # "range not satisfiable": handled by caller
            return e                                            # HTTPError is itself a response object
        raise


def _hdr(resp, name: str) -> str:
    h = getattr(resp, "headers", None) or {}
    try:
        v = h.get(name)
        if v is None:
            for k in h:
                if str(k).lower() == name.lower():
                    v = h[k]
                    break
    except Exception:
        v = None
    return str(v) if v is not None else ""


def sha256_file(path: Path, progress: Callable[[int], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancelled and cancelled():
                raise _Cancelled()
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            if progress:
                progress(len(b))
    return h.hexdigest()


# -- manager --------------------------------------------------------------------------------------
class ModelDownloader:
    def __init__(self, spec: ModelSpec, dest: Path, *, opener: Callable[[str, dict], _Resp] | None = None,
                 sleep: Callable[[float], None] = time.sleep, retries: int = 5,
                 on_done: Callable[[bool, str], None] | None = None,
                 before_delete: Callable[[], None] | None = None):
        self.spec = spec
        self.dest = Path(dest)
        self._open = opener or default_opener
        self._sleep = sleep
        self._retries = max(1, retries)
        self._on_done = on_done
        self._before_delete = before_delete
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._s = {"state": "idle", "file": "", "done": 0, "total": 0, "error": "", "phase": ""}
        self._known: dict[str, int] = {}          # file name -> real total bytes once known
        self._file_done: dict[str, int] = {}      # file name -> bytes present so far
        if self.is_installed():
            self._s["state"] = "ready"

    # ---- queries -------------------------------------------------------------------------------
    def is_installed(self) -> bool:
        """Marker present, same revision, every file there with the size recorded at install time."""
        try:
            m = json.loads((self.dest / MARKER).read_text(encoding="utf-8"))
            if m.get("revision") != self.spec.revision or m.get("id") != self.spec.id:
                return False
            for f in self.spec.files:
                p = self.dest / f.name
                if not p.is_file() or p.stat().st_size != int(m["sizes"][f.name]):
                    return False
            return True
        except Exception:
            return False

    def installed_bytes(self) -> int:
        try:
            return sum((self.dest / f.name).stat().st_size for f in self.spec.files)
        except Exception:
            return 0

    def status(self) -> dict:
        with self._lock:
            s = dict(self._s)
            total = self._total_locked()
            done = sum(self._file_done.get(f.name, 0) for f in self.spec.files)
        if s["state"] == "ready":
            progress = 1.0
        elif total > 0:
            progress = max(0.0, min(0.999, done / total))
        else:
            progress = 0.0
        return {
            "state": s["state"],                         # idle | downloading | verifying | ready | error | cancelled
            "downloading": s["state"] in ("downloading", "verifying"),
            "progress": round(progress, 4),
            "bytes_done": done if s["state"] != "ready" else self.installed_bytes(),
            "bytes_total": total,
            "file": s["file"],
            "error": s["error"],
            "installed": s["state"] == "ready",
            "resumable": self._has_partial(),
            "size_bytes": self.installed_bytes() if s["state"] == "ready" else 0,
        }

    def _total_locked(self) -> int:
        return sum(self._known.get(f.name) or f.size or f.approx for f in self.spec.files)

    def _has_partial(self) -> bool:
        try:
            return any((self.dest / (f.name + ".part")).is_file() for f in self.spec.files)
        except Exception:
            return False

    # ---- control -------------------------------------------------------------------------------
    def start(self) -> bool:
        """Begin (or resume) the download in the background. False if one is already running or done."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._s["state"] == "ready" and self.is_installed():
                return False
            self._cancel.clear()
            self._s.update(state="downloading", error="", file="")
            self._thread = threading.Thread(target=self._run, name="lyra-voice-model-dl", daemon=True)
            self._thread.start()
            return True

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: float | None = None) -> bool:
        t = self._thread
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True

    def delete(self) -> tuple[bool, str]:
        """Remove the model (and any half-finished download). Refused while a download runs."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False, "A download is running. Cancel it first."
        if self._before_delete:
            try:
                self._before_delete()       # drop the loaded ONNX session so Windows lets go of the file
            except Exception as e:
                log.debug("before_delete hook failed: %s", e)
        try:
            if self.dest.exists():
                shutil.rmtree(self.dest)
        except OSError as e:
            return False, f"Could not delete the model files ({e}). Close dictation and try again."
        with self._lock:
            self._s.update(state="idle", error="", file="")
            self._known.clear()
            self._file_done.clear()
        return True, ""

    def verify_installed(self) -> tuple[bool, str]:
        """Full re-hash of every pinned file (slow: a few seconds). For a manual 'check files' action."""
        for f in self.spec.files:
            p = self.dest / f.name
            if not p.is_file():
                return False, f"{f.name} is missing"
            if f.sha256:
                if sha256_file(p) != f.sha256:
                    return False, f"{f.name} does not match its checksum"
        return True, ""

    # ---- worker --------------------------------------------------------------------------------
    def _run(self) -> None:
        ok, msg = False, ""
        try:
            self.dest.mkdir(parents=True, exist_ok=True)
            self._check_space()
            for f in self.spec.files:
                if self._cancel.is_set():
                    raise _Cancelled()
                self._fetch(f)
            self._write_marker()
            with self._lock:
                self._s.update(state="ready", file="", error="")
            ok = True
        except _Cancelled:
            msg = "Download cancelled. It will resume where it stopped."
            with self._lock:
                self._s.update(state="cancelled", error="", file="")
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            log.warning("Voice model download failed: %s", msg)
            with self._lock:
                self._s.update(state="error", error=msg, file="")
        finally:
            if self._on_done:
                try:
                    self._on_done(ok, msg)
                except Exception:
                    pass

    def _check_space(self) -> None:
        need = max(0, self.spec.approx_total - self.installed_bytes() - sum(
            p.stat().st_size for p in self.dest.glob("*.part")))
        try:
            free = shutil.disk_usage(self.dest).free
        except OSError:
            return
        if need and free < int(need * 1.1) + 50_000_000:
            raise DownloadError(
                f"Not enough free disk space: the voice model needs about {need // 1_000_000} MB "
                f"and only {free // 1_000_000} MB is free.")

    def _fetch(self, f: ModelFile) -> None:
        final = self.dest / f.name
        part = self.dest / (f.name + ".part")
        with self._lock:
            self._s.update(state="downloading", file=f.name)
        if final.is_file() and self._file_ok(final, f, quiet=True):
            with self._lock:
                self._file_done[f.name] = final.stat().st_size
                self._known[f.name] = final.stat().st_size
            return
        restarts = 0
        attempt = 0
        while True:
            try:
                self._download_to_part(f, part)
                self._verify_part(f, part)
                os.replace(part, final)
                with self._lock:
                    self._file_done[f.name] = final.stat().st_size
                return
            except _Cancelled:
                raise
            except DownloadError as e:
                if "checksum" in str(e):
                    self._rm(part)                               # never keep (or resume) corrupt bytes
                    with self._lock:
                        self._file_done[f.name] = 0
                    if restarts < 1:                             # start this file over, once
                        restarts += 1
                        log.warning("Checksum mismatch for %s - downloading it again from the start.", f.name)
                        continue
                raise
            except (urllib.error.URLError, OSError, TimeoutError, ConnectionError) as e:
                attempt += 1
                if attempt >= self._retries:
                    raise DownloadError(f"Network problem while downloading {f.name}: {e}. "
                                        "Check your connection and press Download again to resume.") from e
                if self._cancel.is_set():
                    raise _Cancelled()
                self._sleep(min(30.0, 1.5 ** attempt))

    def _download_to_part(self, f: ModelFile, part: Path) -> None:
        if not f.url.lower().startswith("https://"):
            raise DownloadError("Refusing a non-https download URL.")
        have = part.stat().st_size if part.is_file() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        resp = self._open(f.url, headers)
        try:
            status = int(getattr(resp, "status", None) or getattr(resp, "code", 200))
            if status == 416:                                   # we already hold >= everything, or garbage
                total = _total_from_content_range(_hdr(resp, "Content-Range"))
                if total is not None and have == total:
                    with self._lock:
                        self._known[f.name] = total
                        self._file_done[f.name] = have
                    return                                       # complete: the checksum step decides
                self._rm(part)
                with self._lock:
                    self._file_done[f.name] = 0
                raise OSError("stale partial file discarded, retrying from the start")
            if status not in (200, 206):
                raise OSError(f"server answered HTTP {status}")
            resumed = status == 206 and have > 0
            if not resumed and have:
                self._rm(part)                                   # server ignored Range: start over
                have = 0
            length = _hdr(resp, "Content-Length")
            if length.isdigit():
                total = have + int(length)
                if f.size and total != f.size:
                    raise DownloadError(f"{f.name} has an unexpected size ({total} bytes).")
                with self._lock:
                    self._known[f.name] = total
            with self._lock:
                self._file_done[f.name] = have
            with open(part, "ab" if resumed else "wb") as out:
                while True:
                    if self._cancel.is_set():
                        raise _Cancelled()
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    have += len(chunk)
                    with self._lock:
                        self._file_done[f.name] = have
            known = self._known.get(f.name)
            if known and have < known:
                raise OSError(f"connection closed early ({have} of {known} bytes)")   # -> resume
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _verify_part(self, f: ModelFile, part: Path) -> None:
        with self._lock:
            self._s.update(state="verifying", file=f.name)
        size = part.stat().st_size
        if size == 0:
            raise DownloadError(f"{f.name} downloaded empty.")
        if f.size and size != f.size:
            raise DownloadError(f"{f.name} checksum/size check failed (size {size}).")
        if f.sha256:
            got = sha256_file(part, cancelled=self._cancel.is_set)
            if got != f.sha256:
                raise DownloadError(f"{f.name} failed its checksum (SHA-256 mismatch).")
        else:
            self._sanity_small(f, part)
        with self._lock:
            self._s.update(state="downloading")

    @staticmethod
    def _sanity_small(f: ModelFile, part: Path) -> None:
        if f.name.endswith(".json"):
            try:
                json.loads(part.read_text(encoding="utf-8"))
            except Exception:
                raise DownloadError(f"{f.name} is not valid JSON (checksum-style sanity check failed).")
        elif f.name.endswith(".txt"):
            try:
                part.read_text(encoding="utf-8")
            except Exception:
                raise DownloadError(f"{f.name} is not valid text (checksum-style sanity check failed).")

    def _file_ok(self, p: Path, f: ModelFile, quiet: bool = False) -> bool:
        try:
            if f.size and p.stat().st_size != f.size:
                return False
            if f.sha256:
                return sha256_file(p, cancelled=self._cancel.is_set) == f.sha256
            return p.stat().st_size > 0
        except _Cancelled:
            raise
        except Exception:
            return False

    def _write_marker(self) -> None:
        sizes = {f.name: (self.dest / f.name).stat().st_size for f in self.spec.files}
        data = {"id": self.spec.id, "revision": self.spec.revision, "sizes": sizes,
                "sha256": {f.name: f.sha256 for f in self.spec.files if f.sha256}, "ts": int(time.time())}
        tmp = self.dest / (MARKER + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.dest / MARKER)

    @staticmethod
    def _rm(p: Path) -> None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _total_from_content_range(value: str) -> int | None:
    """'bytes */12345' or 'bytes 0-9/12345' -> 12345."""
    try:
        return int(value.rsplit("/", 1)[1])
    except Exception:
        return None
