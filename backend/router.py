"""Lyra — the router: a lightweight LLM pass that runs ONLY when the fast,
free, local matching (regex action patterns + fuzzy_match_target) already
missed. It does two things in one call:

1. Classifies the command as "action" (do something on the computer),
   "research" (needs a factual/current-info lookup), or "chat" (casual
   conversation) — used by brain.py to decide whether to bother searching.
2. Cleans up obvious STT noise in the wording ("open my spitofy playlist"
   -> "open my spotify playlist") so a second pass through the action
   router has a better shot at matching a known target.

Deliberately NOT run on every command — that would put an API round-trip
(roughly half a second to a couple of seconds on Groq) in front of things
that already work instantly, like "what time is it" or "open notepad".
It only fires once the cheap stuff has already failed, so the added
latency only shows up on the commands that would otherwise have gone
nowhere anyway.

Local apps are not limited to KNOWN_APP_KEYS anymore. The action layer
also maintains a Windows installed-application index (Start Menu, packaged
apps, registry App Paths/uninstall metadata, and PATH executables), so
"corrected" app names can be resolved even when the app was never hard-coded.
For websites, the existing known-site/learned-URL behavior still applies.
"""

import json
import re

from lazy_imports import LazyClient, OpenAI     # the openai package is imported on first use

import ui
import perf
from config import LLM_API_KEY, LLM_BASE_URL, LLM_ROUTER_MODEL, GROQ_API_KEY, GROQ_ROUTER_MODEL_NAME

trace = ui.trace

# Shared client pointed at Groq; None when the key is missing
_router_client = LazyClient(lambda: OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=0, timeout=12.0)) if LLM_API_KEY else None

_ROUTER_PROVIDERS = {
    # provider -> (key field in llm_keys.json, base url, config attr with the router model)
    "openrouter": ("openrouter_api_key", "https://openrouter.ai/api/v1", "OPENROUTER_ROUTER_MODEL"),
    "gemini": ("gemini_api_key", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_ROUTER_MODEL"),
    "groq": ("groq_api_key", "https://api.groq.com/openai/v1", "GROQ_ROUTER_MODEL_NAME"),
    "ollama": ("", "http://localhost:11434/v1", "OLLAMA_MODEL"),  # no key; handled in _live_router
}
_live_cache: dict = {}


def _live_router():
    """(client, model) from the key saved in Settings *now*, so a key added after the
    backend started (first run of the installed app) works without a restart.
    Falls back to the client built at import time."""
    try:
        import config as _cfg
        from llm_store import get as _llm_get
        s = _llm_get()
        name = str(s.get("provider") or "openrouter").lower()
        if name == "ollama":
            import ollama_support
            return ollama_support.router_client()   # (None, None) when no local model -> router says "unknown"
        key_f, base, model_attr = _ROUTER_PROVIDERS.get(name, _ROUTER_PROVIDERS["groq"])
        key = s.get(key_f) or ""
        model = getattr(_cfg, model_attr, "") or LLM_ROUTER_MODEL
        if key:
            ck = (base, key)
            if _live_cache.get("k") != ck:
                _live_cache.update(k=ck, c=OpenAI(base_url=base, api_key=key, max_retries=0, timeout=12.0))
            return _live_cache["c"], model
    except Exception:
        pass
    return _router_client, LLM_ROUTER_MODEL

# ---------------------------------------------------------------------------
# System prompt that forces the model to return strict JSON
# ---------------------------------------------------------------------------
ROUTER_SYSTEM_PROMPT = (
    "You clean up short voice-transcribed commands before they're routed to "
    "the right handler. Output ONLY compact JSON, nothing else, in this exact "
    'shape: {"intent": "action", "corrected": "open spotify", "guessed_url": null}\n\n'
    "intent must be one of:\n"
    '- "action": the user wants something done on their computer — open an '
    "app or website, file/folder operations, system control (volume, lock, "
    "screenshot, shutdown), or media control.\n"
    '- "research": a factual or lookup question that likely needs current '
    "information (news, prices, facts, \"what is/who is/when did\").\n"
    '- "chat": casual conversation, opinions, small talk, or anything not '
    "needing a search.\n\n"
    "corrected: the same command with obvious speech-to-text mistakes fixed "
    "(garbled app/site names, mangled words) — but do NOT change the meaning, "
    "add words that weren't implied, or rephrase things that are already clear.\n\n"
    "For local apps, preserve the user's intended application name even if it "
    "is not in a predefined list; the Windows app scanner will resolve installed "
    "applications after this router pass.\n\n"
    "guessed_url: ONLY when intent is \"action\" and the user is asking to open "
    "a specific, well-known WEBSITE (not a local desktop app) that you are "
    "genuinely confident about the real URL of — e.g. \"open notion\" -> "
    "\"https://www.notion.so\". Set it to null if you're not certain, if it's "
    "a local app rather than a website, or if it's a generic/ambiguous name. "
    "A wrong guess is worse than no guess, so only fill this in when you'd "
    "bet on it."
)


def classify(command: str) -> dict:
    """Classify and optionally correct a command that local matching missed.

    Returns a dict with keys:
      intent      – "action" | "research" | "chat" | "unknown"
      corrected   – cleaned version of the original command
      guessed_url – absolute http(s) URL or None

    "unknown" intent means the router could not weigh in (no API key or
    request error). Callers should fall back to their own heuristics
    rather than trusting a default blindly.
    """
    # No key → cannot call the model; return a neutral result
    _client, _model = _live_router()
    if _client is None:
        return {"intent": "unknown", "corrected": command, "guessed_url": None}

    try:
        with perf.span("llm_route"):
            response = _client.chat.completions.create(
                model=_model,  # Lighter model is fine for classification
                messages=[
                    {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": command},
                ],
                temperature=0,          # Deterministic classification
                max_tokens=150,         # JSON response is tiny
                # Note: some Groq models reject response_format=json_object and
                # return 400 json_validate_failed — parse robustly instead.
            )
        content = response.choices[0].message.content
        if not isinstance(content, str):
            raise ValueError("Router returned an empty or invalid response")
        content = content.strip()
        # Strip markdown fences if the model ignores "JSON only"
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I | re.M).strip()
        # Extract object if prose wrapped it
        brace = re.search(r"\{.*\}", content, re.S)
        if brace:
            content = brace.group(0)
        data = json.loads(content)

        # Validate the intent field; fall back to "unknown" on garbage
        intent = data.get("intent") if data.get("intent") in ("action", "research", "chat") else "unknown"
        corrected = (data.get("corrected") or command).strip() or command

        # Only accept a guessed_url that looks like a real absolute URL
        guessed_url = data.get("guessed_url")
        if not (isinstance(guessed_url, str) and guessed_url.startswith(("http://", "https://"))):
            guessed_url = None  # Guard against the model returning junk/relative text

        # Emit a short trace line for debugging
        note = "" if corrected.lower() == command.lower() else f", corrected from '{command}'"
        url_note = f", guessed_url={guessed_url}" if guessed_url else ""
        trace(f"router: intent={intent}{note}{url_note}")

        return {"intent": intent, "corrected": corrected, "guessed_url": guessed_url}
    except Exception as e:
        # Network / JSON / model errors → fall back to the raw command
        trace(f"router: error, falling back to raw command ({e})")
        return {"intent": "unknown", "corrected": command, "guessed_url": None}
