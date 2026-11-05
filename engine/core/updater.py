"""Lyra - verified updates (phase 8).

What this module does, and what it refuses to do
  * Looks at the GitHub Releases of ONE repository the user typed in ("owner/repo"). Nothing is contacted
    until the user turned updates on (or pressed "Check now"). No telemetry: the only requests are
    api.github.com (release list) and the release's own installer / checksum files on github.com.
  * Never downloads or installs by itself. Download starts on a click; install is a second click.
  * An installer is accepted ONLY if its SHA-256 equals a checksum the release publishes (a SHA256SUMS file or a
    <installer>.sha256 file, plus GitHub's own asset digest when present; if two sources exist they must agree).
    No published checksum = "update available" but not installable. Mismatch = the file is deleted.
  * Safe by construction: https only, a host allow-list that is re-checked on every redirect, a size cap, a disk
    space check, resumable downloads, a "looks like a Windows executable" check, and a re-hash right before the
    installer is handed to Windows (the Rust side hashes it once more).
  * Offline, rate limits (ETag cache + Retry-After / X-RateLimit-Reset), private or missing repos, 5xx and
    broken JSON all end as a calm, specific message - never a traceback and never a half-installed update.

Stdlib only (no new dependency). Every network call goes through _open(), which tests replace.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

API_ROOT = "https://api.github.com"
API_HOSTS = ("api.github.com",)
# Where a release asset may be served from. Re-checked on EVERY redirect hop.
DOWNLOAD_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")

MAX_INSTALLER_BYTES = 600 * 1024 * 1024
MAX_API_BYTES = 2 * 1024 * 1024
MAX_CHECKSUM_BYTES = 256 * 1024
AUTO_CHECK_INTERVAL_S = 12 * 3600          # background check at most twice a day
MANUAL_CHECK_COOLDOWN_S = 20               # "Check now" cannot be hammered
HTTP_TIMEOUT_S = 15
CHUNK = 256 * 1024
NOTES_MAX_CHARS = 600

# Tests only. Real builds must keep these False: they would allow non-https / non-GitHub hosts.
_ALLOW_INSECURE_FOR_TESTS = False
_EXTRA_TEST_HOSTS: tuple = ()

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,39}/[A-Za-z0-9_.-]{1,100}$")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


class _Short(Exception):
    """The stream ended before the announced size (connection dropped). Resumable, not fatal."""


class UpdateError(Exception):
    """A problem with a user-facing code. `code` is stable (the UI switches on it)."""

    def __init__(self, code: str, message: str, retry_at: float = 0.0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_at = retry_at

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "retry_at": self.retry_at}


# --------------------------------------------------------------------------- versions

def _pre_key(pre: Optional[str]):
    if pre is None:
        return (1, ())                      # a release is newer than any of its prereleases
    ids = []
    for part in pre.split("."):
        ids.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return (0, tuple(ids))


def parse_version(text) -> Optional[tuple]:
    """'v1.2.3', '1.2.3-beta.1+build5' -> a sortable tuple. None if it is not a version."""
    m = _SEMVER.match(str(text or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), _pre_key(m.group(4)))


def is_newer(candidate, current) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def clean_version(text) -> str:
    return str(text or "").strip().lstrip("vV")


# --------------------------------------------------------------------------- repo / state

def normalize_repo(text) -> str:
    """Accepts 'owner/repo' or a github.com URL; returns 'owner/repo' or ''. Never raises."""
    s = str(text or "").strip()
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s?#]+)", s, re.I)
    if m:
        s = f"{m.group(1)}/{m.group(2)}"
    if s.lower().endswith(".git"):
        s = s[:-4]
    s = s.strip("/")
    if not _REPO_RE.match(s) or ".." in s or s.startswith(".") or "/." in s:
        return ""
    return s


def _data_path(*parts: str) -> Path:
    try:
        from engine.core import paths
        return paths.data_path(*parts)
    except Exception:
        p = Path(tempfile.gettempdir(), "lyra-data").joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def updates_dir() -> Path:
    d = _data_path("updates", ".keep").parent
    return d


_STATE_LOCK = threading.RLock()
_DEFAULT_STATE = {"enabled": False, "repo": "", "prereleases": False, "skipped": "",
                  "last_check": 0.0, "next_allowed": 0.0, "etags": {}, "cached": {}}


def load_state() -> dict:
    with _STATE_LOCK:
        st = dict(_DEFAULT_STATE, etags={}, cached={})
        try:
            raw = json.loads(_data_path("updater.json").read_text("utf-8"))
            if isinstance(raw, dict):
                for k in _DEFAULT_STATE:
                    if k in raw and type(raw[k]) is type(_DEFAULT_STATE[k]):
                        st[k] = raw[k]
        except (OSError, ValueError):
            pass
        st["repo"] = normalize_repo(st["repo"])
        return st


def save_state(st: dict) -> None:
    with _STATE_LOCK:
        p = _data_path("updater.json")
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(st, ensure_ascii=False), "utf-8")
            os.replace(tmp, p)
        except OSError:
            pass                             # state is a convenience; never break an update over it


def update_state(**changes) -> dict:
    with _STATE_LOCK:
        st = load_state()
        st.update(changes)
        save_state(st)
        return st


# --------------------------------------------------------------------------- HTTP (single choke point)

def _host_ok(url: str, hosts: tuple) -> bool:
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if _ALLOW_INSECURE_FOR_TESTS and host in _EXTRA_TEST_HOSTS:
        return u.scheme in ("http", "https")
    return u.scheme == "https" and host in hosts and not u.username and not u.password


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _host_ok(newurl, self.hosts):
            raise UpdateError("insecure_url", "The download tried to redirect somewhere that is not GitHub. Stopped.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(req: urllib.request.Request, timeout: float, hosts: tuple):
    """The only place that touches the network. Replaced in tests."""
    if not _host_ok(req.full_url, hosts):
        raise UpdateError("insecure_url", "Refusing a non-https or non-GitHub address.")
    opener = urllib.request.build_opener(_Redirects(hosts))
    return opener.open(req, timeout=timeout)


def _ua(version: str = "") -> str:
    return f"Lyra-Updater/{clean_version(version) or '0'}"


def _read_capped(resp, cap: int) -> bytes:
    data = resp.read(cap + 1)
    if len(data) > cap:
        raise UpdateError("bad_response", "GitHub sent an unexpectedly large reply. Stopped.")
    return data


def _classify_http_error(e: urllib.error.HTTPError) -> UpdateError:
    code = e.code
    h = e.headers or {}
    now = time.time()
    if code in (403, 429):
        retry = 0.0
        ra = h.get("Retry-After")
        if ra and str(ra).strip().isdigit():
            retry = now + int(ra)
        elif str(h.get("X-RateLimit-Remaining", "")).strip() == "0":
            reset = str(h.get("X-RateLimit-Reset", "")).strip()
            retry = float(reset) if reset.isdigit() else now + 3600
        if retry or code == 429:
            retry = retry or now + 3600
            mins = max(1, int((retry - now) / 60))
            return UpdateError("rate_limited", f"GitHub is limiting requests right now. Try again in about {mins} min.", retry)
        return UpdateError("forbidden", "GitHub refused the request (is the repository private?).")
    if code == 404:
        return UpdateError("not_found", "That repository or its releases were not found. Private repositories are not supported.")
    if 500 <= code < 600:
        return UpdateError("github_error", f"GitHub had a problem (HTTP {code}). Try again later.")
    return UpdateError("http_error", f"GitHub answered HTTP {code}.")


def _network_error(e: BaseException) -> UpdateError:
    if isinstance(e, UpdateError):
        return e
    if isinstance(e, urllib.error.HTTPError):
        return _classify_http_error(e)
    if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError)):
        return UpdateError("offline", "Can't reach GitHub. Check your internet connection.")
    return UpdateError("network", "The update check failed.")


# --------------------------------------------------------------------------- release parsing

def _trim_release(rel: dict) -> dict:
    assets = []
    for a in (rel.get("assets") or [])[:60]:
        if not isinstance(a, dict):
            continue
        assets.append({"name": str(a.get("name") or "")[:200], "size": int(a.get("size") or 0),
                       "url": str(a.get("browser_download_url") or ""), "digest": str(a.get("digest") or "")})
    return {"tag": str(rel.get("tag_name") or ""), "name": str(rel.get("name") or "")[:200],
            "body": str(rel.get("body") or "")[:4000], "published_at": str(rel.get("published_at") or ""),
            "html_url": str(rel.get("html_url") or ""), "prerelease": bool(rel.get("prerelease")),
            "draft": bool(rel.get("draft")), "assets": assets}


def notes_excerpt(body: str) -> str:
    """Plain text only (the UI never renders release notes as HTML)."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(body or ""))
    text = re.sub(r"<[^>]{0,200}>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= NOTES_MAX_CHARS else text[:NOTES_MAX_CHARS].rstrip() + "..."


_SETUP_RE = re.compile(r"(?:-|_|\.)setup\.exe$|setup.*\.exe$", re.I)


def pick_installer(assets: list) -> Optional[dict]:
    cands = [a for a in assets if _SETUP_RE.search(a["name"]) and not a["name"].lower().endswith(".blockmap")]
    if not cands:
        return None
    cands.sort(key=lambda a: (("x64" not in a["name"].lower()), ("arm64" in a["name"].lower()), a["name"]))
    return cands[0]


def _belongs(url: str, repo: str) -> bool:
    """True if `url` is a download link of this repository (GitHub treats owner/repo case-insensitively)."""
    return str(url).lower().startswith(f"https://github.com/{repo}/releases/download/".lower())


def _norm_name(name: str) -> str:
    # GitHub turns spaces in uploaded file names into dots; checksum files usually list the original name.
    return str(name).strip().lstrip("*./\\").replace(" ", ".").lower()


def parse_checksum_text(text: str, filename: str) -> Optional[str]:
    """Hash for `filename` from sha256sum / BSD-style lines, or a bare one-hash file. None if not found."""
    want = _norm_name(os.path.basename(filename.replace("\\", "/")))
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) == 1 and len(lines[0].split()) == 1 and _HEX64.match(lines[0]):
        return lines[0].lower()
    for ln in lines:
        digest = name = None
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", ln)                          # sha256sum
        if m:
            digest, name = m.group(1), m.group(2)
        else:
            m = re.match(r"^SHA256\s*\((.+)\)\s*=\s*([0-9a-fA-F]{64})$", ln, re.I)   # BSD
            if m:
                name, digest = m.group(1), m.group(2)
        if digest and _norm_name(os.path.basename(name.replace("\\", "/"))) == want:
            return digest.lower()
    return None


def _checksum_assets(assets: list, installer: dict) -> list:
    out = []
    for a in assets:
        n = a["name"].lower()
        if n in ("sha256sums", "sha256sums.txt", "checksums.txt", "checksums.sha256", "sha256sum.txt"):
            out.append(a)
        elif n == installer["name"].lower() + ".sha256" or n == installer["name"].lower() + ".sha256sum":
            out.append(a)
    return out


def build_update(rel: dict, repo: str) -> dict:
    """From a trimmed release -> the update description the UI shows."""
    assets = rel["assets"]
    inst = pick_installer(assets)
    version = clean_version(rel["tag"])
    upd = {"version": version, "name": rel["name"] or rel["tag"], "published_at": rel["published_at"],
           "prerelease": rel["prerelease"], "notes": notes_excerpt(rel["body"]),
           "html_url": rel["html_url"] if rel["html_url"].lower().startswith(f"https://github.com/{repo}/".lower()) else "",
           "installer": None, "size": 0, "checksum_sources": [], "checksum_assets": [], "verifiable": False,
           "problem": ""}
    if inst is None:
        upd["problem"] = "This release has no Windows installer (a *-setup.exe file)."
        return upd
    if not _belongs(inst["url"], repo):
        upd["problem"] = "The installer link does not belong to this repository. Not trusted."
        return upd
    if not (0 < inst["size"] <= MAX_INSTALLER_BYTES):
        upd["problem"] = "The installer size is missing or unreasonable. Not trusted."
        return upd
    upd["installer"] = inst
    upd["size"] = inst["size"]
    srcs = []
    for a in _checksum_assets(assets, inst):
        if _belongs(a["url"], repo):
            srcs.append("checksum file: " + a["name"])
            upd["checksum_assets"].append({"name": a["name"], "url": a["url"]})
    if re.match(r"^sha256:[0-9a-fA-F]{64}$", inst["digest"] or ""):
        srcs.append("GitHub asset digest")
    upd["checksum_sources"] = srcs
    upd["verifiable"] = bool(srcs)
    if not srcs:
        upd["problem"] = "The release publishes no SHA-256 checksum, so Lyra will not install it."
    return upd


# --------------------------------------------------------------------------- check

def _fetch_json(url: str, etag: str, version: str):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": _ua(version)}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with _open(req, HTTP_TIMEOUT_S, API_HOSTS) as resp:
            data = _read_capped(resp, MAX_API_BYTES)
            new_etag = resp.headers.get("ETag", "") if getattr(resp, "headers", None) else ""
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        raise _classify_http_error(e)
    except UpdateError:
        raise
    except Exception as e:                                           # noqa: BLE001
        raise _network_error(e)
    try:
        return json.loads(data.decode("utf-8")), new_etag
    except (ValueError, UnicodeDecodeError):
        raise UpdateError("bad_response", "GitHub sent something Lyra could not read.")


def check(repo: str, current: str, prereleases: bool = False) -> dict:
    """One check. Returns {"status": "available"|"up_to_date", "update": {...}|None}; raises UpdateError."""
    repo = normalize_repo(repo)
    if not repo:
        raise UpdateError("bad_repo", "Type the repository as owner/repo, for example acme/lyra.")
    if parse_version(current) is None:
        raise UpdateError("bad_version", "Lyra could not read its own version number.")
    url = f"{API_ROOT}/repos/{repo}/releases" + ("?per_page=15" if prereleases else "/latest")
    st = load_state()
    etag = st["etags"].get(url, "")
    cached = st["cached"].get(url)
    payload, new_etag = _fetch_json(url, etag if cached else "", current)
    if payload is None:                                              # 304: nothing changed since last time
        rel_list = cached if isinstance(cached, list) else ([cached] if cached else [])
    else:
        items = payload if isinstance(payload, list) else [payload]
        rel_list = [_trim_release(r) for r in items if isinstance(r, dict)]
        st["etags"] = {url: new_etag}                                # one URL is ever cached
        st["cached"] = {url: rel_list if prereleases else (rel_list[0] if rel_list else None)}
        save_state(st)
    best = None
    for rel in rel_list:
        if not rel or rel["draft"] or (rel["prerelease"] and not prereleases):
            continue
        v = parse_version(rel["tag"])
        if v is not None and (best is None or v > best[0]):
            best = (v, rel)
    if best is None or not is_newer(best[1]["tag"], current):
        return {"status": "up_to_date", "update": None}
    return {"status": "available", "update": build_update(best[1], repo)}


# --------------------------------------------------------------------------- checksums + download

def sha256_file(path: Path, cancel: Optional[threading.Event] = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise UpdateError("cancelled", "Cancelled.")
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def resolve_expected(update: dict, repo: str, current: str) -> list:
    """[(source, hex)] for every published checksum of this installer. All of them must agree."""
    inst = update["installer"]
    out = []
    if re.match(r"^sha256:[0-9a-fA-F]{64}$", inst.get("digest") or ""):
        out.append(("GitHub asset digest", inst["digest"].split(":", 1)[1].lower()))
    for a in update.get("checksum_assets", []):
        if not _belongs(a["url"], repo):
            continue
        req = urllib.request.Request(a["url"], headers={"User-Agent": _ua(current)})
        try:
            with _open(req, HTTP_TIMEOUT_S, DOWNLOAD_HOSTS) as resp:
                text = _read_capped(resp, MAX_CHECKSUM_BYTES).decode("utf-8", "replace")
        except UpdateError:
            raise
        except Exception as e:                                       # noqa: BLE001
            raise _network_error(e)
        got = parse_checksum_text(text, inst["name"])
        if got is None:
            raise UpdateError("no_checksum", f"{a['name']} does not list the installer. Not installing.")
        out.append(("checksum file: " + a["name"], got))
    if not out:
        raise UpdateError("no_checksum", "The release publishes no SHA-256 checksum. Not installing.")
    if len({h for _, h in out}) != 1:
        raise UpdateError("checksum_mismatch", "The release's own checksums disagree with each other. Not installing.")
    return out


def _safe_name(version: str) -> str:
    return "Lyra-" + re.sub(r"[^0-9A-Za-z.+-]", "_", clean_version(version))[:40] + "-setup.exe"


def ready_info() -> Optional[dict]:
    try:
        d = json.loads((updates_dir() / "ready.json").read_text("utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def clear_downloads(keep_ready: bool = False) -> None:
    d = updates_dir()
    ri = ready_info() if keep_ready else None
    for p in d.glob("*"):
        if p.name == ".keep" or (ri and p.name in (ri.get("file"), "ready.json")):
            continue
        try:
            p.unlink()
        except OSError:
            pass


def download(update: dict, repo: str, current: str,
             progress: Optional[Callable[[int, int], None]] = None,
             cancel: Optional[threading.Event] = None,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """Download -> verify -> write ready.json. Returns the ready record; raises UpdateError."""
    cancel = cancel or threading.Event()
    inst = update.get("installer")
    if not inst or not update.get("verifiable"):
        raise UpdateError("no_checksum", update.get("problem") or "This update cannot be verified. Not installing.")
    expected = resolve_expected(update, repo, current)
    want_hash = expected[0][1]
    total = int(inst["size"])
    d = updates_dir()
    if shutil.disk_usage(d).free < total * 1.3:
        raise UpdateError("disk_full", "Not enough free disk space for the update.")
    final = d / _safe_name(update["version"])
    part = final.with_suffix(".part")
    (d / "ready.json").unlink(missing_ok=True)
    final.unlink(missing_ok=True)

    attempts = 0
    while True:
        done = part.stat().st_size if part.exists() else 0
        if done > total:
            part.unlink(missing_ok=True)
            done = 0
        try:
            headers = {"User-Agent": _ua(current)}
            if done:
                headers["Range"] = f"bytes={done}-"
            req = urllib.request.Request(inst["url"], headers=headers)
            with _open(req, HTTP_TIMEOUT_S, DOWNLOAD_HOSTS) as resp:
                status = getattr(resp, "status", 200)
                if done and status != 206:                           # server ignored Range: start over
                    done = 0
                mode = "ab" if done else "wb"
                with open(part, mode) as out:
                    got = done
                    while True:
                        if cancel.is_set():
                            raise UpdateError("cancelled", "Cancelled.")
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        got += len(chunk)
                        if got > total:
                            raise UpdateError("too_large", "The download is larger than the release says. Stopped.")
                        out.write(chunk)
                        if progress:
                            progress(got, total)
            if part.stat().st_size < total:
                raise _Short()
            break
        except urllib.error.HTTPError as e:
            if e.code == 416:                                        # our partial file is not valid any more
                part.unlink(missing_ok=True)
            err = _classify_http_error(e)
            if err.code in ("rate_limited", "not_found", "forbidden") or attempts >= 3:
                raise err
        except UpdateError as e:
            if e.code in ("too_large", "insecure_url"):
                part.unlink(missing_ok=True)
            raise
        except _Short:
            if attempts >= 3:
                raise UpdateError("bad_response", "The download keeps ending early. Try again later.")
        except Exception as e:                                       # noqa: BLE001
            if attempts >= 3:
                raise _network_error(e)
        attempts += 1
        sleep(min(8.0, 1.0 + attempts * 2))

    if part.stat().st_size != total:
        part.unlink(missing_ok=True)
        raise UpdateError("bad_response", "The download ended early. Try again.")
    with open(part, "rb") as f:
        if f.read(2) != b"MZ":
            part.unlink(missing_ok=True)
            raise UpdateError("bad_response", "The downloaded file is not a Windows program. Deleted.")
    got_hash = sha256_file(part, cancel)
    if got_hash != want_hash:
        part.unlink(missing_ok=True)
        raise UpdateError("checksum_mismatch", "The download does not match the published SHA-256. It was deleted.")
    os.replace(part, final)
    rec = {"version": clean_version(update["version"]), "file": final.name, "sha256": got_hash, "size": total,
           "repo": repo, "verified_against": [s for s, _ in expected], "verified_at": time.time()}
    (d / "ready.json").write_text(json.dumps(rec), "utf-8")
    return rec


def verify_ready(current: str) -> dict:
    """Re-hash the downloaded installer right before it is handed to Windows."""
    rec = ready_info()
    if not rec:
        raise UpdateError("not_ready", "No verified update is waiting.")
    if not is_newer(rec.get("version", ""), current):
        raise UpdateError("not_newer", "That update is not newer than the version you are running.")
    p = updates_dir() / str(rec.get("file", ""))
    if p.parent != updates_dir() or not p.is_file():
        raise UpdateError("not_ready", "The downloaded installer is gone. Download it again.")
    if p.stat().st_size != int(rec.get("size", -1)):
        raise UpdateError("checksum_mismatch", "The installer changed on disk. Download it again.")
    if not _HEX64.match(str(rec.get("sha256", ""))) or sha256_file(p) != rec["sha256"]:
        raise UpdateError("checksum_mismatch", "The installer no longer matches its checksum. Download it again.")
    return dict(rec, path=str(p))


def cleanup_stale(current: str, max_age_days: int = 7) -> None:
    """Drop leftovers: an installer that is not newer than us, or anything older than a week."""
    try:
        rec = ready_info()
        if rec and not is_newer(rec.get("version", ""), current):
            clear_downloads()
            return
        cutoff = time.time() - max_age_days * 86400
        for p in updates_dir().glob("*"):
            if p.name != ".keep" and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass
