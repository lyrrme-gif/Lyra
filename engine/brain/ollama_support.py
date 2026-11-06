"""Ollama (local models) support for Lyra: run chat, routing and vision with no internet and no API key.

Ollama exposes an OpenAI-compatible API at <base>/v1, so the existing OpenAI client code works as is.
This module holds everything Ollama-specific so the shared files only need tiny hooks:

  * where Ollama lives (setting ollama_base_url, default http://localhost:11434)
  * which models are installed (GET /api/tags) and which one Lyra uses
  * (client, model) builders for chat (brain.py), routing (router.py) and vision (actions/apps.py)
  * the connection test and model list used by Settings -> AI
  * human error messages ("Ollama isn't running", "run: ollama pull <model>")

Settings stored in llm_keys.json via llm_store: ollama_model, ollama_vision_model, ollama_base_url,
ollama_timeout. "Local first, cloud if local fails or is too slow" = provider "Ollama" + a cloud
fallback provider (Settings -> AI -> Fallback); with a cloud fallback set, a local answer that takes
longer than ollama_timeout seconds is abandoned and the fallback answers instead.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from engine.bridge import ui

log = ui.log

DEFAULT_BASE = "http://localhost:11434"
SUGGESTED_MODEL = "llama3.2:3b"
SUGGESTED_VISION = "llava"                    # moondream is a smaller alternative
DEFAULT_FAST_TIMEOUT = 25                     # seconds, only used when a cloud fallback is configured
SLOW_TIMEOUT = 120                            # seconds, no fallback: local models can be slow on cold start
TAGS_TTL = 4.0                                # seconds the installed-model list is cached

# Models whose name suggests they can read images (used only when no vision model is chosen).
_VISION_HINT = re.compile(r"llava|moondream|vision|minicpm-v|bakllava|(^|[-_:])vl([-_:]|$)", re.I)
_MODEL_OK = re.compile(r"[\w./:@\-]{0,100}")

_lock = threading.Lock()
_tags_cache: dict[str, Any] = {}
_clients: dict[tuple, Any] = {}


# ── settings ─────────────────────────────────────────────────────────────────────────────────

def _cfg(key: str, default: Any = "") -> Any:
    try:
        from engine.brain import llm_store
        v = llm_store.get().get(key)
        return default if v in (None, "") else v
    except Exception:
        return default


def enabled() -> bool:
    """False only if the user switched "Local AI (Ollama)" off in Settings -> Features."""
    try:
        from engine.core import settings_store
        return settings_store.feature_on("ollama")
    except Exception:
        return True


def normalize_base(url: str) -> str:
    """'localhost:11434/v1/' -> 'http://localhost:11434'. Raises ValueError for anything that is not http(s)."""
    u = (url or "").strip()
    if not u:
        return DEFAULT_BASE
    if "://" not in u:
        u = "http://" + u
    p = urllib.parse.urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Ollama address must look like http://localhost:11434")
    path = p.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return f"{p.scheme}://{p.netloc}{path}".rstrip("/")


def configured() -> bool:
    """True when Ollama is switched on and either is the chosen provider or has a model picked.

    Settings uses this as "ready to use" (Ollama has no key to save)."""
    if not enabled():
        return False
    try:
        from engine.brain import llm_store
        d = llm_store.get()
        return str(d.get("provider") or "").lower() == "ollama" or bool(str(d.get("ollama_model") or "").strip())
    except Exception:
        return False


def in_use() -> bool:
    """True when the user has actually set local AI up (chosen it, picked a model, changed the address
    or made it the fallback). Until then Lyra never talks to port 11434 on her own: a PC without
    Ollama must not pay for a connection attempt every time Settings opens."""
    if not enabled():
        return False
    if configured():
        return True
    try:
        from engine.core import settings_store
        if str(settings_store.get("llm_fallback_provider") or "").lower() == "ollama":
            return True
    except Exception:
        pass
    if str(_cfg("ollama_vision_model", "")).strip():
        return True
    try:
        return normalize_base(str(_cfg("ollama_base_url", DEFAULT_BASE))) != DEFAULT_BASE
    except ValueError:
        return False


def base_url() -> str:
    try:
        return normalize_base(str(_cfg("ollama_base_url", DEFAULT_BASE)))
    except ValueError:
        return DEFAULT_BASE


def openai_base() -> str:
    return base_url() + "/v1"


def clean_updates(updates: dict) -> dict:
    """Validate ollama_* values before llm_store saves them (invalid ones are dropped, not stored)."""
    out = dict(updates)
    if "ollama_base_url" in out:
        try:
            out["ollama_base_url"] = normalize_base(str(out["ollama_base_url"]))
        except ValueError as e:
            log.warning("ollama: ignoring bad address (%s)", e)
            del out["ollama_base_url"]
    for k in ("ollama_model", "ollama_vision_model"):
        if k in out and not _MODEL_OK.fullmatch(str(out[k]).strip()):
            log.warning("ollama: ignoring bad model name for %s", k)
            del out[k]
        elif k in out:
            out[k] = str(out[k]).strip()
    if "ollama_keep_alive" in out:
        v = str(out["ollama_keep_alive"]).strip().lower()
        if _KEEP_OK.fullmatch(v):
            out["ollama_keep_alive"] = v
        else:
            log.warning("ollama: ignoring bad keep-alive %r", out["ollama_keep_alive"])
            del out["ollama_keep_alive"]
    if "ollama_timeout" in out:
        try:
            out["ollama_timeout"] = max(5, min(300, int(float(out["ollama_timeout"]))))
        except (TypeError, ValueError):
            del out["ollama_timeout"]
    return out


# ── talking to Ollama ────────────────────────────────────────────────────────────────────────

def _explain_connection(e: Exception) -> str:
    base = base_url()
    if isinstance(e, urllib.error.HTTPError):
        return f"Ollama at {base} answered with an error (HTTP {e.code})."
    if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
        return f"Ollama at {base} didn't answer in time. If it is busy or starting up, try again in a moment."
    return (f"Ollama isn't running (nothing answered at {base}). "
            "Start the Ollama app, or run: ollama serve")


def _get_json(path: str, timeout: float) -> dict:
    # No system proxy for a local server.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # "localhost" resolves to ::1 first on Windows; Ollama listens on IPv4, so every probe burned the
    # whole timeout on IPv6 before trying 127.0.0.1 (the 3 s /settings/ollama). Go straight to IPv4.
    url = (base_url() + path).replace("//localhost", "//127.0.0.1", 1)
    req = urllib.request.Request(url, headers={"User-Agent": "Lyra/1.0"})
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def installed(force: bool = False) -> tuple[list[str], str | None]:
    """(installed model names, error message or None). Cached for a few seconds."""
    now = time.time()
    base = base_url()
    with _lock:
        c = _tags_cache.get("v")
        # A server that is not running is remembered longer, so Settings does not re-probe it every few seconds.
        if c and not force and c[0] == base and now - c[1] < (TAGS_TTL if c[3] is None else 20.0):
            return c[2], c[3]
    try:
        raw = _get_json("/api/tags", 1.0)
        names = sorted({str(m.get("name") or m.get("model") or "") for m in raw.get("models", []) if isinstance(m, dict)} - {""})
        res: tuple[list[str], str | None] = (names, None)
    except Exception as e:  # noqa: BLE001
        res = ([], _explain_connection(e))
    with _lock:
        _tags_cache["v"] = (base, now, res[0], res[1])
    return res


def installed_soft() -> tuple[list[str], str | None]:
    """Like installed(), but never makes the caller wait on a dead server again: a stale answer is
    returned at once and refreshed on a background thread. Only the very first call may block."""
    base = base_url()
    with _lock:
        c = _tags_cache.get("v")
        busy = _tags_cache.get("refreshing")
    if not c or c[0] != base:
        return installed()
    ttl = TAGS_TTL if c[3] is None else 20.0
    if time.time() - c[1] >= ttl and not busy:
        with _lock:
            _tags_cache["refreshing"] = True

        def _go() -> None:
            try:
                installed(force=True)
            finally:
                with _lock:
                    _tags_cache["refreshing"] = False
        threading.Thread(target=_go, name="lyra-ollama-probe", daemon=True).start()
    return c[2], c[3]


def has_model(name: str, models: list[str]) -> bool:
    if not name:
        return False
    return name in models or (":" not in name and f"{name}:latest" in models)


def chosen_model() -> str:
    """The model Lyra chats with: the one picked in Settings, else the first installed model."""
    m = str(_cfg("ollama_model", "")).strip()
    if m:
        return m
    models, _ = installed()
    return models[0] if models else ""


def pull_command(model: str | None = None) -> str:
    return f"ollama pull {model or SUGGESTED_MODEL}"


# ── clients for brain / router / vision ──────────────────────────────────────────────────────

def _cloud_fallback_set() -> bool:
    try:
        from engine.core import settings_store
        fb = str(settings_store.get("llm_fallback_provider") or "").lower()
        return bool(fb) and fb != "ollama"
    except Exception:
        return False


def _timeout(primary: bool) -> float:
    if primary and _cloud_fallback_set():
        try:
            return float(max(5, min(300, int(float(_cfg("ollama_timeout", DEFAULT_FAST_TIMEOUT))))))
        except (TypeError, ValueError):
            return float(DEFAULT_FAST_TIMEOUT)
    return float(SLOW_TIMEOUT)


# ── Keeping the model in memory only as long as it is useful ────────────────────────────────
# Ollama keeps a model loaded for 5 minutes after EVERY request that arrives through its OpenAI-style API (and
# that API cannot change it), so a local model sat in video memory long after the last answer. Right after each
# answer, and after a pre-warm, Lyra now tells Ollama to use the keep-alive chosen in Settings instead.

DEFAULT_KEEP_ALIVE = "1m"
_KEEP_OK = re.compile(r"-1|0|\d{1,4}[smh]")
_arming: set[str] = set()
_arm_lock = threading.Lock()


def keep_alive_setting() -> str:
    v = str(_cfg("ollama_keep_alive", DEFAULT_KEEP_ALIVE) or DEFAULT_KEEP_ALIVE).strip().lower()
    return v if _KEEP_OK.fullmatch(v) else DEFAULT_KEEP_ALIVE


def _ka_value(v: str):
    """Ollama takes a duration string ("1m") or a number of seconds (0 = unload now, -1 = never)."""
    return int(v) if v in ("0", "-1") else v


def _post_native(path: str, payload: dict, timeout: float) -> None:
    req = urllib.request.Request(base_url() + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def preload(model: str) -> None:
    """Load the model into memory without generating a word (an empty prompt); it then expires after the keep-alive."""
    ka = keep_alive_setting()
    if ka == "0":
        ka = DEFAULT_KEEP_ALIVE                        # "unload after each answer": the answer that follows still gains from a pre-warm
    _post_native("/api/generate", {"model": model, "keep_alive": _ka_value(ka)}, 8.0)


def arm_keep_alive(model: str | None) -> None:
    """Call right AFTER an answer: sets how long the model stays loaded from now on (in the background)."""
    model = str(model or "").strip()
    if not model or keep_alive_setting() == "5m":      # 5 minutes is what Ollama does by itself
        return
    with _arm_lock:
        if model in _arming:
            return
        _arming.add(model)

    def run() -> None:
        try:
            _post_native("/api/generate", {"model": model, "keep_alive": _ka_value(keep_alive_setting())}, 5.0)
        except Exception as e:                         # noqa: BLE001 - housekeeping must never matter
            log.debug("ollama keep-alive not set: %s", e)
        finally:
            with _arm_lock:
                _arming.discard(model)

    threading.Thread(target=run, name="lyra-ollama-keepalive", daemon=True).start()


class _StreamProxy:
    """Hands the streamed chunks through unchanged; sets the keep-alive once the stream has ended (or was closed)."""

    def __init__(self, stream, model):
        self._s, self._m, self._done = stream, model, False

    def _finish(self) -> None:
        if not self._done:
            self._done = True
            arm_keep_alive(self._m)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._s)
        except BaseException:                          # StopIteration, errors, cancelled: the stream is over
            self._finish()
            raise

    def close(self) -> None:
        try:
            c = getattr(self._s, "close", None)
            if c:
                c()
        finally:
            self._finish()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._s, name)


def _wrap_create(comps) -> None:
    orig = comps.create

    def create(*args, **kw):
        model, stream = kw.get("model"), bool(kw.get("stream"))
        try:
            out = orig(*args, **kw)
        except Exception:
            arm_keep_alive(model)
            raise
        if stream:
            return _StreamProxy(out, model)
        arm_keep_alive(model)
        return out

    comps.create = create


def _make_client(timeout: float):
    from openai import OpenAI
    key = (openai_base(), timeout)
    c = _clients.get(key)
    if c is None:
        # max_retries=0: a timeout must hand over to the fallback at once, not be retried twice.
        c = OpenAI(base_url=key[0], api_key="ollama", timeout=timeout, max_retries=0)
        try:
            _wrap_create(c.chat.completions)
        except Exception:                              # noqa: BLE001 - never block a client over housekeeping
            pass
        _clients[key] = c
    return c


def client(primary: bool = True):
    """(OpenAI client, model) for chat, or (None, None) when Ollama has no usable model.

    primary=True: Ollama is the main provider (short timeout if a cloud fallback exists).
    primary=False: Ollama is the fallback, so it gets the patient timeout."""
    if not enabled():
        return None, None
    model = chosen_model()
    if not model:
        return None, None
    return _make_client(_timeout(primary)), model


def router_client():
    """(client, model) for command routing; short timeout, the routing pass is optional."""
    if not enabled():
        return None, None
    model = chosen_model()
    if not model:
        return None, None
    return _make_client(20.0), model


def vision_candidate():
    """(key, base_url, model) for actions/apps._vision_candidates, or None.

    Uses the vision model from Settings; without one, uses the chat model only if its name says
    it can read images (llava, moondream, ...)."""
    if not enabled():
        return None
    vm = str(_cfg("ollama_vision_model", "")).strip()
    if not vm:
        m = str(_cfg("ollama_model", "")).strip()
        if m and _VISION_HINT.search(m):
            vm = m
    if not vm:
        return None
    return ("ollama", openai_base(), vm)


def is_ollama_client(c: Any) -> bool:
    try:
        return str(c.base_url).rstrip("/") == openai_base().rstrip("/")
    except Exception:
        return False


# ── messages ─────────────────────────────────────────────────────────────────────────────────

def friendly_error(e: Exception, c: Any = None, model: str | None = None) -> str | None:
    """A clear sentence for an error raised by an Ollama client call, or None if it isn't one."""
    if c is None or not is_ollama_client(c):
        return None
    base = base_url()
    model = model or chosen_model() or SUGGESTED_MODEL
    text = str(e)
    low = text.lower()
    code = getattr(e, "status_code", None)
    try:
        import openai
        if isinstance(e, openai.APITimeoutError):
            return "Ollama is too slow. Try again."
        if isinstance(e, openai.APIConnectionError):
            return "Can't reach Ollama. Start it."
    except ImportError:
        pass
    if code == 404 or "not found" in low:
        return f"Model missing. Run: {pull_command(model)}"
    if "more system memory" in low or "out of memory" in low:
        return "Not enough memory. Pick a smaller model."
    if code == 400 and ("image" in low or "vision" in low or "multimodal" in low):
        return "This model can't read images."
    return None


def not_ready_message() -> str | None:
    """Why chat can't start while Ollama is the chosen provider; None if it isn't (or nothing is wrong)."""
    try:
        from engine.brain import llm_store
        if str(llm_store.get().get("provider") or "").lower() != "ollama":
            return None
    except Exception:
        return None
    if not enabled():
        return "Local models are off in Settings."
    models, err = installed(force=True)
    if err:
        return "Can't reach Ollama. Start it."
    if not models:
        return f"No local model. Run: {pull_command()}"
    return "Pick a model in Settings."


# ── Settings -> AI helpers ───────────────────────────────────────────────────────────────────

def list_models() -> dict[str, Any]:
    models, err = installed(force=True)
    if err:
        return {"ok": False, "provider": "ollama", "message": err, "models": []}
    if not models:
        return {"ok": True, "provider": "ollama", "models": [],
                "message": f"Ollama is running but has no models yet. Run: {pull_command()}"}
    return {"ok": True, "provider": "ollama", "models": models}


def status(probe: bool = False) -> dict[str, Any]:
    """Settings -> AI card. Local checks only unless Ollama is set up or the user pressed Refresh (probe=True).

    - switched off in Features: no network at all
    - never set up: no network at all, a calm "not set up" answer (running is None, not False)
    - set up: the cached list at once; a stale one is refreshed in the background
    """
    on = enabled()
    used = in_use()
    probed = on and (probe or used)
    if not on:
        models, err = [], "Local models (Ollama) are switched off in Settings -> Features."
    elif probe:
        models, err = installed(force=True)
    elif used:
        models, err = installed_soft()
    else:
        models, err = [], None
    model = str(_cfg("ollama_model", "")).strip()
    vm = str(_cfg("ollama_vision_model", "")).strip()
    out: dict[str, Any] = {
        "enabled": on,
        "in_use": used,
        "running": (err is None) if probed else None,
        "base_url": base_url(),
        "models": models,
        "vision_models": [m for m in models if _VISION_HINT.search(m)],
        "model": model,
        "vision_model": vm,
        "timeout": int(_cfg("ollama_timeout", DEFAULT_FAST_TIMEOUT) or DEFAULT_FAST_TIMEOUT),
        "keep_alive": keep_alive_setting(),
        "pull_command": pull_command(model or None),
        "vision_pull_command": pull_command(SUGGESTED_VISION),
    }
    if not on:
        out["message"] = err
    elif not probed:
        out["message"] = ("Not set up. You only need this to run AI on your own PC. "
                          "Install Ollama, then press Refresh.")
    elif err:
        out["message"] = err
    elif not models:
        out["message"] = f"Ollama is running but has no models yet. Run: {pull_command()}"
    elif model and not has_model(model, models):
        out["message"] = f"The model {model} isn't downloaded. Run: {pull_command(model)}"
    else:
        out["message"] = f"Ollama is running with {len(models)} model{'s' if len(models) != 1 else ''}."
    return out


def test_connection(timeout: float = 90.0) -> dict[str, Any]:
    """Real chat request to the local model. Reports the real reason when Ollama is off or the model is missing."""
    if not enabled():
        return {"ok": False, "message": "Local models (Ollama) are switched off in Settings -> Features.", "ms": None}
    models, err = installed(force=True)
    if err:
        return {"ok": False, "message": err, "ms": None}
    model = chosen_model()
    if not model:
        return {"ok": False, "message": f"Ollama is running but has no models yet. Run: {pull_command()}", "ms": None}
    if not has_model(model, models):
        return {"ok": False, "message": f"The model {model} isn't downloaded. Run: {pull_command(model)}",
                "model": model, "ms": None}
    t = time.time()
    try:
        c = _make_client(timeout)
        r = c.chat.completions.create(model=model, messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                                      max_tokens=8, temperature=0)
        reply = (r.choices[0].message.content or "").strip()
        ms = int((time.time() - t) * 1000)
        if reply:
            return {"ok": True, "message": f"Ollama model {model} responded.", "model": model, "ms": ms}
        return {"ok": False, "message": f"Ollama model {model} returned an empty response.", "model": model, "ms": ms}
    except Exception as e:  # noqa: BLE001
        ms = int((time.time() - t) * 1000)
        msg = friendly_error(e, _make_client(timeout), model) or f"Ollama model {model} failed: {str(e)[:240]}"
        return {"ok": False, "message": msg, "model": model, "ms": ms}
