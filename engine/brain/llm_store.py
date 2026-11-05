"""Persistent LLM provider keys + mod slot names for Lyra.

Settings (provider, models, URLs) are stored as llm_keys.json in the data folder (local only).
The secrets (API keys, Spotify client secret) live in the OS keychain through secrets_store
(Phase 0) and are blank in that file; if no keychain is usable the file holds them exactly as
before. The public API (load / save / get / list_models / test_connection) is unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from engine.core import paths

PATH = paths.data_path("llm_keys.json")

_DEFAULTS: dict[str, Any] = {
    "provider": "openrouter",  # openrouter | gemini | groq
    "openrouter_api_key": "",
    "gemini_api_key": "",
    "groq_api_key": "",
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "openrouter_model": "google/gemini-3.8-flash",
    "gemini_model": "gemini-3.8-flash",
    "groq_model": "openai/gpt-oss-120b",
    "ollama_model": "",                       # "" = first model installed in Ollama
    "ollama_vision_model": "",                # optional, e.g. llava or moondream
    "ollama_base_url": "http://localhost:11434",
    "ollama_timeout": 25,                     # seconds; only used when a cloud fallback provider is set
    "ollama_keep_alive": "1m",                # how long the local model stays in memory after an answer: 30s / 1m / 5m / -1 (always)
}

_cache: dict[str, Any] | None = None

# >>> phase-0 begin
_SECRET_FIELDS = ("openrouter_api_key", "gemini_api_key", "groq_api_key", "spotify_client_secret")


def _keychain():
    """secrets_store when the OS keychain is usable, else None (old plain-file behaviour)."""
    try:
        from engine.core import secrets_store
        return secrets_store if secrets_store.keyring_available() else None
    except Exception:
        return None
# <<< phase-0 end


def load() -> dict[str, Any]:
    global _cache
    data = dict(_DEFAULTS)
    if PATH.exists():
        try:
            raw = json.loads(PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in _DEFAULTS:
                    if k in raw:
                        data[k] = raw[k]
        except Exception:
            pass
    # >>> phase-0 begin
    ss = _keychain()
    if ss is not None:
        try:
            if any(isinstance(data.get(f), str) and data[f].strip() for f in _SECRET_FIELDS):
                ss.migrate_json_file("llm", PATH, _SECRET_FIELDS)     # one time; verified before the file changes
            for f in _SECRET_FIELDS:
                v = ss.get(f)
                if v:
                    data[f] = v                                       # else the file value (if any) stays
        except Exception:
            pass
    # <<< phase-0 end
    _cache = data
    return data


def save(updates: dict[str, Any]) -> dict[str, Any]:
    try:
        from engine.brain import ollama_support
        updates = ollama_support.clean_updates(updates)
    except Exception:
        pass
    data = load()
    changed = [k for k, v in updates.items() if k in _DEFAULTS and data.get(k) != v]
    for k, v in updates.items():
        if k in _DEFAULTS:
            data[k] = v
    # >>> phase-0 begin
    to_file = dict(data)
    ss = _keychain()
    if ss is not None:
        for f in _SECRET_FIELDS:
            val = str(data.get(f) or "").strip()
            if f in changed:
                stored = ss.set(f, val) if val else ss.delete(f)
                to_file[f] = "" if stored else data.get(f, "")    # refused everywhere: keep the old file behaviour
            elif val and not ss.has(f):
                to_file[f] = "" if ss.set(f, val) else val        # never drop a key that is only in the file
            else:
                to_file[f] = ""
        try:
            ss.forget_backup_fields("llm", [f for f in changed if f in _SECRET_FIELDS])
        except Exception:
            pass
    # <<< phase-0 end
    PATH.write_text(json.dumps(to_file, indent=2), encoding="utf-8")
    global _cache
    _cache = data
    return data


def get() -> dict[str, Any]:
    global _cache
    if _cache is None:
        return load()
    return _cache


_TEST_URLS = {
    # OpenRouter's /models list is public, so its key-check endpoint is used instead.
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1/auth/key", "OPENROUTER_API_KEY"),
    "gemini": ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/models", "GEMINI_API_KEY"),
    "groq": ("Groq", "https://api.groq.com/openai/v1/models", "GROQ_API_KEY"),
}


def _provider_info(prov: str):
    return {
        "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1/models", "openrouter_api_key", "openrouter_model"),
        "gemini": ("Gemini", "https://generativelanguage.googleapis.com/v1beta/openai/models", "gemini_api_key", "gemini_model"),
        "groq": ("Groq", "https://api.groq.com/openai/v1/models", "groq_api_key", "groq_model"),
        "ollama": ("Ollama", "http://localhost:11434/api/tags", "", "ollama_model"),  # no key; see ollama_support
    }.get(prov)

def list_models(provider: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
    import urllib.request, urllib.error
    data=load(); prov=str(provider or data.get("provider") or "openrouter").lower()
    if prov=="ollama":
        from engine.brain import ollama_support
        return ollama_support.list_models()
    info=_provider_info(prov)
    if not info: return {"ok":False,"message":"Unknown provider.","models":[]}
    label,url,key_field,model_field=info; key=data.get(key_field) or ""
    if not key: return {"ok":False,"message":f"No {label} key saved yet.","models":[]}
    req=urllib.request.Request(url,headers={"Authorization":f"Bearer {key}","User-Agent":"Lyra/1.0"})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as resp: raw=json.loads(resp.read().decode("utf-8"))
        models=[]
        for x in raw.get("data",[]):
            mid=x.get("id") if isinstance(x,dict) else None
            if mid: models.append(mid)
        return {"ok":True,"provider":prov,"models":sorted(set(models))}
    except Exception as e:
        return {"ok":False,"message":f"Could not list {label} models: {e}","models":[]}

def test_connection(provider: str | None = None, timeout: float = 15.0) -> dict[str, Any]:
    """Real chat-model test, never returns the key."""
    import time
    from openai import OpenAI
    data=load(); prov=str(provider or data.get("provider") or "openrouter").lower()
    if prov=="ollama":
        from engine.brain import ollama_support
        return ollama_support.test_connection()
    info=_provider_info(prov)
    if not info: return {"ok":False,"message":"Unknown provider.","ms":None}
    label,_,key_field,model_field=info; key=data.get(key_field) or ""; model=data.get(model_field) or ""
    if not key: return {"ok":False,"message":f"No {label} key saved yet.","ms":None}
    bases={"openrouter":"https://openrouter.ai/api/v1","gemini":"https://generativelanguage.googleapis.com/v1beta/openai/","groq":"https://api.groq.com/openai/v1"}
    if not model: return {"ok":False,"message":f"No {label} model selected.","ms":None}
    t=time.time()
    try:
        client=OpenAI(base_url=bases[prov],api_key=key,timeout=timeout)
        r=client.chat.completions.create(model=model,messages=[{"role":"user","content":"Reply with exactly: OK"}],max_tokens=4,temperature=0)
        reply=(r.choices[0].message.content or '').strip()
        return {"ok":bool(reply),"message":f"{label} chat model {model} responded." if reply else f"{label} returned an empty response.","model":model,"ms":int((time.time()-t)*1000)}
    except Exception as e:
        msg=str(e)
        code=getattr(e,"status_code",None)
        return {"ok":False,"message":f"{label} model {model} failed" + (f" (HTTP {code})" if code else "") + f": {msg[:240]}","model":model,"ms":int((time.time()-t)*1000)}
