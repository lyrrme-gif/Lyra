"""Lyra — the "brain": local shortcuts first, then Groq for plain chat.

This is the only module that talks to the Groq chat-completions API.
Everything else (STT, TTS, actions, search) is backend-agnostic.

Flow for every user utterance that reaches ask_brain():
  1. try_knowledge_shortcut()  → date / time / math (instant, no API)
  2. system-status / current-window shortcuts (psutil / pygetwindow)
  3. Optionally fetch live web-search context (Tavily / DDGS)
  4. Build a proper chat-completions message list (system + history + user)
  5. Call Groq, append the reply to history, return the text
"""

import re
import threading
import time
from openai.types.chat import ChatCompletionMessageParam

from openai import OpenAI  # Groq is OpenAI-compatible; we just change the base_url

import ui
import perf
import memory
from config import (
    SYSTEM_INSTRUCTIONS, CONVO_MEMORY_TURNS, CONVO_HISTORY_HARD_CAP,
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
    GROQ_API_KEY, GROQ_MODEL_NAME,  # aliases
)
from knowledge import try_knowledge_shortcut
from actions import get_system_status, get_current_window
from voice import SMALL_TALK_PATTERN

trace = ui.trace  # Lightweight debug helper used throughout the engine

# Without a cap OpenRouter reserves the model's maximum (65536) and answers 402 on a small balance.
_MAX_REPLY_TOKENS = 2048

def _live_search(prompt: str) -> str:
    """Search once, show the results (with the picture/links card when it is about a known
    person, place...) on the Island, and return LLM context."""
    try:
        import web_island
        rows, card = web_island.gather(prompt)
    except Exception as e:
        ui.log.debug("search failed: %s", e)
        import search
        rows, card = search.web_search_results(prompt), None
    if rows or card:
        try:
            import ui_bridge
            ui_bridge.push_search_results(prompt, rows, rows[0].get("provider", "") if rows else "", card=card)
        except Exception as e:
            ui.log.debug("search result UI publish failed: %s", e)
    lines = [f"- {r['title']} | {r['source']} | {r['url']}\n  {r['description']}" for r in rows]
    if card:  # the card's text is a reliable summary: give it to the model too
        lines.insert(0, f"- {card['title']} | wikipedia.org | {card.get('url','')}\n  {card.get('extract','')}")
    return "\n".join(lines)

# Create a single shared OpenAI client pointed at Groq's endpoint.
# If GROQ_API_KEY is missing the client is None and we return a helpful error.
_PROVIDERS = {
    # provider -> (key field in llm_keys.json, model field, base url, env-derived key attr in config)
    "openrouter": ("openrouter_api_key", "openrouter_model", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "gemini": ("gemini_api_key", "gemini_model", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    "groq": ("groq_api_key", "groq_model", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
}


# >>> phase-L begin
_CLIENT_CACHE: dict = {}
_CLIENT_LOCK = threading.Lock()


def _shared_client(base_url: str, api_key: str):
    """One OpenAI client per (base_url, key), reused by every call.

    _get_client() used to build a brand-new client on every call, which threw away the connection
    pool each time: every planner call, chat call and pre-warm paid DNS + TCP + TLS again (and the
    pre-warm's connection was never the one the real request used). A key change in Settings makes a
    new cache entry, so "key changes apply without restart" still holds."""
    ck = (base_url, api_key)
    c = _CLIENT_CACHE.get(ck)
    if c is not None:
        return c
    with _CLIENT_LOCK:
        c = _CLIENT_CACHE.get(ck)
        if c is None:
            kwargs = {}
            try:
                import config as _c
                import httpx
                kwargs["timeout"] = httpx.Timeout(float(getattr(_c, "LLM_READ_TIMEOUT_S", 30.0)),
                                                  connect=float(getattr(_c, "LLM_CONNECT_TIMEOUT_S", 5.0)))
                kwargs["max_retries"] = int(getattr(_c, "LLM_MAX_RETRIES", 0))
            except Exception:
                pass            # library defaults: still correct, just slower to give up
            c = OpenAI(base_url=base_url, api_key=api_key, **kwargs)
            if len(_CLIENT_CACHE) >= 8:      # keys edited many times: do not grow forever
                _CLIENT_CACHE.pop(next(iter(_CLIENT_CACHE)))
            _CLIENT_CACHE[ck] = c
    return c
# <<< phase-L end


def _get_client(provider=None):
    """Rebuild client so Settings UI key changes apply without restart.

    `provider=None` means the provider chosen in Settings. Passing a provider name
    (used for the fallback) never borrows another provider's key."""
    try:
        from llm_store import get as _llm_get
        import config as _cfg
        s = _llm_get()
        explicit = provider is not None
        name = str(provider or s.get("provider") or "openrouter").lower()
        if name == "ollama":
            import ollama_support
            return ollama_support.client(primary=not explicit)   # no key; (None, None) if no local model
        key_f, model_f, base, env_attr = _PROVIDERS.get(name, _PROVIDERS["groq"])
        if explicit:
            key = s.get(key_f) or getattr(_cfg, env_attr, "")
            model = s.get(model_f)
        else:
            key = s.get(key_f) or LLM_API_KEY
            model = s.get(model_f) or LLM_MODEL
        if not key or not model:
            return None, None
        return _shared_client(base, key), model
    except Exception:
        if provider is not None or not LLM_API_KEY:
            return None, None
        return _shared_client(LLM_BASE_URL, LLM_API_KEY), LLM_MODEL


_STYLE_HINTS = {
    "short": "Reply style: keep every answer very short — one or two sentences — unless the user asks for detail.",
    "detailed": "Reply style: give fuller, well-explained answers (steps or examples when they help), while staying clear.",
}


def _style_hint() -> str:
    try:
        import settings_store
        return _STYLE_HINTS.get(str(settings_store.get("reply_style") or "balanced"), "")
    except Exception:
        return ""


def _fallback_client():
    """(client, model) for the fallback provider chosen in Settings, or None."""
    try:
        import settings_store
        from llm_store import get as _llm_get
        fb = str(settings_store.get("llm_fallback_provider") or "").lower()
        if not fb or fb == str(_llm_get().get("provider") or "openrouter").lower():
            return None
        client, model = _get_client(fb)
        return (client, model) if client else None
    except Exception:
        return None

_llm_client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY) if LLM_API_KEY else None
_groq_client = _llm_client  # back-compat


# Words that signal the answer depends on fresh information.
_NEEDS_FRESH = re.compile(
    r"\b(latest|news|today|tonight|tomorrow|yesterday|current(?:ly)?|right now|recent(?:ly)?|"
    r"this (?:week|month|year)|score|scores|price|prices|weather|release[ds]?|update[ds]?)\b",
    re.IGNORECASE,
)
# Lyra's previous reply offered to look something up online.
_OFFERED_LOOKUP = re.compile(
    r"\b(look(?:ing)?\s+(?:it|that|this)\s+up|search(?:\s+for)?|check\s+online|look\s+online)\b",
    re.IGNORECASE,
)


# "Do something" requests start like a command; a question starts with one of these. The polite
# lead-in ("can you ...") is not a question.
_POLITE_LEAD = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?", re.I)
_QUESTION_LEAD = re.compile(
    r"^(?:what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|does|did|can|could|should|would|am)\b", re.I)
CANT_DO_YET = "I can't do that yet."


def _is_unsupported_action(prompt: str, intent: str) -> bool:
    """The router says the user wants something DONE and no handler took it: say so instead of
    googling it (a search can never set an alarm, move a window or send a file)."""
    if intent != "action":
        return False
    rest = _POLITE_LEAD.sub("", (prompt or "").strip())
    return bool(rest) and not _QUESTION_LEAD.match(rest)


def _is_rate_limit(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 429 or "429" in str(e) or "rate limit" in str(e).lower()


def _retry_after(e: Exception) -> float:
    """Seconds the provider asked us to wait (header), 2 s when unknown. Callers cap it."""
    try:
        h = getattr(getattr(e, "response", None), "headers", None) or {}
        return float(h.get("retry-after") or h.get("Retry-After") or 2.0)
    except Exception:
        return 2.0


def _chat_completion(client, model_name, messages):
    """One chat call. Some models (e.g. gpt-oss on Groq) occasionally try to call
    a built-in tool such as web.run, which the API rejects with 400 tool_use_failed
    because we don't offer tools. Retry once with an explicit no-tools nudge."""
    try:
        return client.chat.completions.create(model=model_name, messages=messages, temperature=0.2, max_tokens=_MAX_REPLY_TOKENS)
    except Exception as e:
        msg = str(e)
        if "tool_use_failed" not in msg and "called a tool" not in msg:
            raise
        ui.log.warning("brain: model tried to call a tool; retrying without. (%s)", msg[:200])
        nudged = messages + [{
            "role": "system",
            "content": "You have NO tools and cannot browse. Do not call any tool or function. "
                       "Answer in plain text now, using only the Live Search Data above if any, "
                       "otherwise your own knowledge, and say briefly if you are unsure.",
        }]
        return client.chat.completions.create(model=model_name, messages=nudged, temperature=0.2, max_tokens=_MAX_REPLY_TOKENS)


def _stream_reply(client, model_name, messages, on_text) -> str:
    """Same request as _chat_completion, but streamed: every piece of text goes to `on_text` as it
    arrives (the voice starts on the first sentence). Raises only if the stream fails BEFORE any text
    came, so the caller can retry normally with nothing said yet; a failure half way keeps what was said."""
    stream = client.chat.completions.create(model=model_name, messages=messages, temperature=0.2, stream=True, max_tokens=_MAX_REPLY_TOKENS)
    parts: list = []
    try:
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content or ""
            except (AttributeError, IndexError):
                delta = ""
            if not delta:
                continue
            if not parts:
                perf.mark("llm_first_token")
            parts.append(delta)
            on_text(delta)
    except Exception as e:
        if not parts:
            raise
        ui.log.warning("brain: stream broke after %d pieces (%s); keeping what was said.", len(parts), str(e)[:160])
    return "".join(parts).strip()


def ask_brain(prompt: str, history: list, intent: str = "unknown", on_text=None) -> str:
    """Main entry point called by the command-processing chain.

    Args:
        prompt:  The user's latest utterance (already cleaned by the router).
        history: Mutable list of {"user": …, "lyra": …} dicts for this session.
        intent:  Classification from router.py ("action"|"research"|"chat"|"unknown").
                 Used only to decide whether a web search is worthwhile.
        on_text: Optional callback. When given, the answer is streamed and every piece of text is passed
                 to it as it arrives (voice.SpeechStream.feed). The full reply is still returned.

    Returns:
        A plain-text reply ready to be spoken / printed.
    """
    try:
        # ------------------------------------------------------------------
        # 1. Cheap local shortcuts — never hit the network
        # ------------------------------------------------------------------
        shortcut = try_knowledge_shortcut(prompt)
        if shortcut:
            trace("path: knowledge shortcut (date/time/math — no LLM call)")
            history.append({"user": prompt, "lyra": shortcut})
            return shortcut

        # System-status questions also get a fast path via psutil
        low = prompt.lower()
        if any(k in low for k in ("how's my computer", "system status", "cpu usage", "ram usage", "memory usage")):
            trace("path: system status shortcut (psutil — no LLM call)")
            reply = get_system_status()
            history.append({"user": prompt, "lyra": reply})
            return reply
        if any(k in low for k in ("what am i looking at", "what window", "current window", "what's open")):
            trace("path: current window shortcut (no LLM call)")
            reply = get_current_window()
            history.append({"user": prompt, "lyra": reply})
            return reply

        # If Lyra just offered to look something up and the user said yes, the
        # request comes back unchanged: search for it this time.
        _force_search = bool(
            history
            and str(history[-1].get("user", "")).strip().lower() == prompt.strip().lower()
            and _OFFERED_LOOKUP.search(str(history[-1].get("lyra", "")))
        )

        # An action nothing could carry out is not a search query.
        if not _force_search and _is_unsupported_action(prompt, intent):
            trace("path: action with no matching tool - saying so, not searching")
            history.append({"user": prompt, "lyra": CANT_DO_YET})
            return CANT_DO_YET

        # ------------------------------------------------------------------
        # 2. Guard against missing API key
        # ------------------------------------------------------------------
        client, model_name = _get_client()
        if client is None:
            try:
                import ollama_support
                _local_msg = ollama_support.not_ready_message()   # only set when Ollama is the chosen provider
            except Exception:
                _local_msg = None
            if _local_msg:
                return _local_msg
            trace("error: LLM API key not set (OPENROUTER_API_KEY / GEMINI_API_KEY / GROQ_API_KEY)")
            return "I don't have a API key configured yet, sir — set OPENROUTER_API_KEY (or GEMINI_API_KEY / GROQ_API_KEY) and restart me."

        # ------------------------------------------------------------------
        # 3. Decide whether to spend a web-search round-trip
        # ------------------------------------------------------------------
        # Trust the router's classification when it actually weighed in.
        # Never auto-search bare short phrases — that turns single words
        # into encyclopedia dumps. Only search when intent is clearly
        # research or the user asked an explicit question.
        _explicit_q = bool(re.search(
            r"\b(what|who|when|where|why|how|define|explain|meaning)\b",
            prompt, re.IGNORECASE,
        ))
        _fresh = bool(_NEEDS_FRESH.search(prompt))
        if _force_search:
            search_context = _live_search(prompt)
        elif intent == "chat":
            search_context = ""
        elif intent == "research" and (_explicit_q or _fresh or len(prompt.split()) >= 3):
            search_context = _live_search(prompt)
        elif intent == "research":
            search_context = ""
        else:
            # Fallback heuristic when the router returned "unknown"
            search_context = (
                "" if SMALL_TALK_PATTERN.search(prompt.strip()) or not (_explicit_q or _fresh)
                else _live_search(prompt)
            )
        if not search_context and intent != "chat" and not SMALL_TALK_PATTERN.search(prompt.strip()):
            trace("web search: no results / no API configured")
        # Research/current requests must not be answered as if a live search happened.
        # This keeps the rich-search contract honest: no invented search results.
        if (intent == "research" or _fresh or _force_search) and not search_context:
            return "I couldn't reach a live web search provider right now, so I won't invent results."

        # ------------------------------------------------------------------
        # 4. Build the user message content (prompt + optional search + session)
        # ------------------------------------------------------------------
        user_content = prompt
        if search_context:
            user_content = f"Live Search Data:\n{search_context}\n\nUser Question: {prompt}"
        try:
            import session_context
            sctx = session_context.get().summary_for_brain()
            if sctx:
                user_content = f"{sctx}\n\n{user_content}"
        except Exception:
            pass  # Session context is optional; never fail the whole request

        # ------------------------------------------------------------------
        # 5. Inject remembered facts into the system prompt
        # ------------------------------------------------------------------
        # Deliberately "dump everything" rather than search_facts() —
        # a handful of facts costs almost nothing in tokens. Switch to
        # search_facts(prompt) once the list grows large enough that
        # relevance filtering becomes necessary.
        facts = memory.all_facts()
        system_content = SYSTEM_INSTRUCTIONS
        _style = _style_hint()  # Settings > AI > reply style (balanced adds nothing)
        if _style:
            system_content += "\n\n" + _style
        if facts:
            facts_block = "\n".join(f"- {key}: {value}" for key, value in facts)
            system_content += f"\n\nThings you've been told to remember about the user:\n{facts_block}"

        # ------------------------------------------------------------------
        # 6. Assemble the OpenAI-style messages list
        # ------------------------------------------------------------------
        # Groq is a chat-completions endpoint, so history becomes proper
        # alternating user/assistant turns instead of one flattened string.
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_content}
        ]
        for turn in history[-CONVO_MEMORY_TURNS:]:
            messages.append({"role": "user", "content": turn["user"]})
            messages.append({"role": "assistant", "content": turn["lyra"]})
        messages.append({"role": "user", "content": user_content})

        # ------------------------------------------------------------------
        # 7. Call Groq and measure latency
        # ------------------------------------------------------------------
        ui.set_state("thinking")  # Lets the island show the "thinking" state
        t0 = time.time()
        reply = ""
        usage = None
        perf.count("llm_calls")
        perf.count("llm_prompt_chars", sum(len(str(m.get("content", ""))) for m in messages))
        with perf.span("llm_chat"):
            _stream_rl = None
            if on_text is not None:
                try:
                    reply = _stream_reply(client, model_name, messages, on_text)
                except Exception as stream_err:
                    # Nothing was said yet: fall through to the normal request below.
                    ui.log.warning("brain: streaming failed (%s); using a normal request.", str(stream_err)[:160])
                    if _is_rate_limit(stream_err):
                        _stream_rl = stream_err        # do not hit the same limited provider a second time
            if not reply:
                try:
                    if _stream_rl is not None:
                        raise _stream_rl
                    response = _chat_completion(client, model_name, messages)
                except Exception as primary_err:
                    fb = _fallback_client()  # Settings > AI > fallback provider
                    if fb is not None:
                        ui.log.warning("brain: %s failed (%s); trying fallback provider.", model_name, str(primary_err)[:160])
                        client, model_name = fb
                        response = _chat_completion(client, model_name, messages)
                    elif _is_rate_limit(primary_err):
                        # No fallback set: wait the shortest sensible time (never the provider's 13-33 s) and retry once.
                        wait_s = min(_retry_after(primary_err), 4.0)
                        ui.log.warning("brain: rate limited; retrying once in %.1fs.", wait_s)
                        time.sleep(wait_s)
                        response = _chat_completion(client, model_name, messages)
                    else:
                        raise
                reply = (response.choices[0].message.content or "").strip()
                usage = getattr(response, "usage", None)
        elapsed = time.time() - t0
        tps = f", {usage.completion_tokens / elapsed:.1f} tok/s" if usage and elapsed > 0 else ""
        trace(f"path: LLM ({model_name}), {elapsed:.1f}s{tps}, search_context={'yes' if search_context else 'no'}")

        # ------------------------------------------------------------------
        # 8. Update conversation history (with hard size cap)
        # ------------------------------------------------------------------
        history.append({"user": prompt, "lyra": reply})
        if len(history) > CONVO_HISTORY_HARD_CAP:
            del history[: len(history) - CONVO_HISTORY_HARD_CAP]
        return reply

    except Exception as e:
        trace(f"error: {e}")
        ui.log.error("brain: request failed: %s", e)
        try:
            import ollama_support
            _local_err = ollama_support.friendly_error(e, locals().get("client"), locals().get("model_name"))
        except Exception:
            _local_err = None
        if _local_err:
            return _local_err
        msg=str(e)
        code=getattr(e,"status_code",None)
        if code == 404 or "404" in msg or "not found" in msg.lower():
            return f"The selected model isn't available: {model_name}. Check Settings and choose another model."
        if code == 429 or "429" in msg or "rate limit" in msg.lower():
            return "The LLM provider is rate-limiting me right now. I couldn't complete that request."
        if code and int(code) >= 500 or any(x in msg for x in ("500","502","503","504")):
            return "The LLM provider is having a server error right now. I couldn't complete that request."
        if "api key" in msg.lower() or "authentication" in msg.lower() or "401" in msg or "403" in msg:
            return "The LLM provider rejected the saved API key. Check the provider and key in Settings."
        return f"I couldn't process that because the LLM request failed: {msg[:180]}"
