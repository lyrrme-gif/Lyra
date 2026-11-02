"""Lyra — central config. All constants live here so every other module
imports from one place instead of scattering magic numbers/strings around."""

import os

# ---------------------------------------------------------------------------
# Wake words / phrases
# ---------------------------------------------------------------------------
# Wake phrases from settings.json (wake_name). openWakeWord still uses
# hey_jarvis phonetically; STT maps transcripts to the configured name.
try:
    from settings_store import wake_phrases as _wake_phrases
    WAKE_WORDS = _wake_phrases()
except Exception:
    WAKE_WORDS = [
        "hey lyra", "hey lira", "hey lyra", "hey jarvis", "jarvis",
        "lyra", "lyra",
    ]
EXIT_PHRASES = ["shut down system", "power off", "goodbye forever"]
STANDBY_PHRASES = ["thank you", "stop listening", "go to sleep", "standby"]

# ---------------------------------------------------------------------------
# LLM backend — OpenRouter (recommended), Gemini, or Groq
# ---------------------------------------------------------------------------
# Provider: "openrouter" | "gemini" | "groq"
# Set via env LLM_PROVIDER or settings.json key "llm_provider".
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openrouter").lower().strip()

# API keys (env wins; settings UI can also store them later)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Models per provider (OpenRouter uses provider/model ids)
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-3.8-flash")
OPENROUTER_ROUTER_MODEL = os.environ.get("OPENROUTER_ROUTER_MODEL", "google/gemini-3.8-flash")
OPENROUTER_VISION_MODEL = os.environ.get("OPENROUTER_VISION_MODEL", "google/gemini-3.8-flash")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_ROUTER_MODEL = os.environ.get("GEMINI_ROUTER_MODEL", "gemini-3.8-flash")
GEMINI_VISION_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-3.8-flash")

GROQ_MODEL_NAME = os.environ.get("GROQ_MODEL_NAME", "openai/gpt-oss-120b")
GROQ_ROUTER_MODEL_NAME = os.environ.get("GROQ_ROUTER_MODEL_NAME", "openai/gpt-oss-20b")
GROQ_VISION_MODEL_NAME = os.environ.get("GROQ_VISION_MODEL_NAME", "qwen/qwen3.8-27b")

# Ollama (local models): no API key. Empty model = first model installed in Ollama (see ollama_support.py)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "")

# Resolved at import for modules that still import GROQ_* names
def _resolve_llm():
    p = LLM_PROVIDER
    if p == "openrouter":
        return (
            OPENROUTER_API_KEY,
            "https://openrouter.ai/api/v1",
            OPENROUTER_MODEL,
            OPENROUTER_ROUTER_MODEL,
            OPENROUTER_VISION_MODEL,
        )
    if p == "gemini":
        # Google AI Studio OpenAI-compatible endpoint
        return (
            GEMINI_API_KEY,
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            GEMINI_MODEL,
            GEMINI_ROUTER_MODEL,
            GEMINI_VISION_MODEL,
        )
    if p == "ollama":
        return (
            "ollama",   # placeholder key: the OpenAI client needs a non-empty one
            OLLAMA_BASE_URL.rstrip("/").removesuffix("/v1") + "/v1",
            OLLAMA_MODEL,
            OLLAMA_MODEL,
            OLLAMA_VISION_MODEL,
        )
    # default / groq
    return (
        GROQ_API_KEY,
        "https://api.groq.com/openai/v1",
        GROQ_MODEL_NAME,
        GROQ_ROUTER_MODEL_NAME,
        GROQ_VISION_MODEL_NAME,
    )

LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_ROUTER_MODEL, LLM_VISION_MODEL = _resolve_llm()

# Overlay keys saved from the Settings UI (llm_keys.json)
try:
    from llm_store import get as _llm_get
    _s = _llm_get()
    if _s.get("provider"):
        LLM_PROVIDER = str(_s["provider"]).lower().strip() or LLM_PROVIDER
    if _s.get("openrouter_api_key"):
        OPENROUTER_API_KEY = _s["openrouter_api_key"] or OPENROUTER_API_KEY
    if _s.get("gemini_api_key"):
        GEMINI_API_KEY = _s["gemini_api_key"] or GEMINI_API_KEY
    if _s.get("groq_api_key"):
        GROQ_API_KEY = _s["groq_api_key"] or GROQ_API_KEY
    if _s.get("openrouter_model"):
        OPENROUTER_MODEL = _s["openrouter_model"] or OPENROUTER_MODEL
    if _s.get("gemini_model"):
        GEMINI_MODEL = _s["gemini_model"] or GEMINI_MODEL
    if _s.get("groq_model"):
        GROQ_MODEL_NAME = _s["groq_model"] or GROQ_MODEL_NAME
    OLLAMA_BASE_URL = _s.get("ollama_base_url") or OLLAMA_BASE_URL
    OLLAMA_MODEL = _s.get("ollama_model") or OLLAMA_MODEL
    OLLAMA_VISION_MODEL = _s.get("ollama_vision_model") or OLLAMA_VISION_MODEL
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_ROUTER_MODEL, LLM_VISION_MODEL = _resolve_llm()
    GROQ_API_KEY = LLM_API_KEY or GROQ_API_KEY
    GROQ_MODEL_NAME = LLM_MODEL
    GROQ_ROUTER_MODEL_NAME = LLM_ROUTER_MODEL
    GROQ_VISION_MODEL_NAME = LLM_VISION_MODEL
except Exception:
    pass


# Back-compat aliases used by older modules
GROQ_API_KEY = LLM_API_KEY or GROQ_API_KEY
GROQ_MODEL_NAME = LLM_MODEL
GROQ_ROUTER_MODEL_NAME = LLM_ROUTER_MODEL
GROQ_VISION_MODEL_NAME = LLM_VISION_MODEL

# ---------------------------------------------------------------------------
# Screen watch (Phase 1 live capture buffer)
# ---------------------------------------------------------------------------
# Captures stay local; Groq vision is only called when the user asks to
# describe or click. Interval is how often we refresh the focused window.
SCREEN_WATCH_INTERVAL = 1.0       # seconds between local captures while watching
SCREEN_WATCH_MAX_AGE = 2.5        # max age (s) before a buffered frame is stale

CONVO_MEMORY_TURNS = 6
CONVO_HISTORY_HARD_CAP = 100   # absolute max; oldest entries dropped beyond this
CONVO_IDLE_RESET_SECONDS = 180  # nobody talked to Lyra for this long: the next question starts a fresh chat (0 = never)
CONVERSATION_TIMEOUT = 30
PAUSE_THRESHOLD = 1.2
PHRASE_TIME_LIMIT = 15
# Lyra should not transcribe her own TTS playback.  One-shot voice turns return
# to wake-word standby after the response, so barge-in is disabled by default.
# Set LYRA_BARGE_IN=1 if explicit speech interruption is wanted later.
BARGE_IN_ENABLED = os.environ.get("LYRA_BARGE_IN", "0").strip().lower() in ("1", "true", "yes", "on")
BARGE_IN_CHECK_TIMEOUT = 1
STT_LANGUAGE = "en-US"         # set to e.g. "en-GB" or "fr-FR" if you want a different default
SEARCH_MAX_RESULTS = 5
SEARCH_TIMELIMIT = None         # None, or "d"/"w"/"m"/"y"
DEBUG_SEARCH = True

# Get a free key at https://tavily.com (1,000 req/month). On Windows PowerShell:
#   setx TAVILY_API_KEY "your-key-here"
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# Google results. Google has closed its own web-search API to new customers, so Lyra asks Serper.dev,
# which returns the real Google results (free starter key at https://serper.dev). PowerShell:
#   setx SERPER_API_KEY "your-key-here"
# Without a key Lyra falls back to Tavily, then DuckDuckGo.
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")

# Posters, cast, social links and "where to watch" for films, series, anime and actors.
# Free key at https://www.themoviedb.org/settings/api (the "API Key" or the "Read Access Token" both work):
#   setx TMDB_API_KEY "your-key-here"
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
# Country for "where to watch" (two letters: MA, US, FR ...). Empty = take it from Windows.
WATCH_REGION = os.environ.get("LYRA_REGION", "")

# LLM-first routing. True: after exact shortcuts, EVERY request is understood by the LLM
# (it picks tools, answers chat, or hands unsupported commands to the local command
# handlers). False: the old fast path (regex commands first, LLM only as fallback).
# Costs one extra LLM call per request when True; set False to go back instantly.
LLM_FIRST = True

# Phase 3 (speed).
# TIMING_LOGS: one line per turn with the time of every stage (speech end, STT, LLM routing, action,
#   TTS) in the backend log and in %TEMP%\lyra-timing.jsonl. `python perf.py` prints the averages.
# FAST_PATH: plain volume / play / pause / next / previous / mute commands skip the LLM entirely.
# STREAM_TTS: a chat answer is spoken sentence by sentence while the model is still writing it.
TIMING_LOGS = True
FAST_PATH = True
STREAM_TTS = True

# >>> phase-L begin
# CHAT_PREGATE: exact small-talk / joke / thanks phrases go straight to the chat model, skipping the
#   planner round trip (config.LLM_FIRST would otherwise spend a whole LLM call just to say "chat").
# LLM_*_TIMEOUT_S / LLM_MAX_RETRIES: the OpenAI client default is a 10 minute timeout and 2 retries,
#   so one stalled request froze a turn. These make a stalled request fail fast (and fall back).
CHAT_PREGATE = True
LLM_CONNECT_TIMEOUT_S = 5.0
LLM_READ_TIMEOUT_S = 30.0
LLM_MAX_RETRIES = 0      # 429s fail fast and use the fallback provider (the SDK would sleep the 13-33 s Retry-After)
# <<< phase-L end

# Personality lives in persona.py (kept importable from here for back-compat).
from persona import SYSTEM_INSTRUCTIONS  # noqa: E402,F401

# ---------------------------------------------------------------------------

# Voice engine
# ---------------------------------------------------------------------------
TTS_VOICE = "en-US-JennyNeural"  # edge-tts neural voice. Alternatives worth trying:
                                  # "en-US-AriaNeural" (the old default), "en-US-AvaNeural" (newer, very natural)
                                  # Full list: `edge-tts --list-voices` after installing the package.
TTS_RATE_PCT = "+0%"             # edge-tts speed adjustment, e.g. "+15%" faster, "-10%" slower

# Barge-in / self-echo knobs — a cheap heuristic, NOT a real fix. Headphones
# (or physically lowering speaker volume / mic gain) always work better.
BARGE_IN_START_DELAY = 0.6
ECHO_THRESHOLD_MULTIPLIER = 1.8

# Delay (seconds) before a shutdown/restart actually executes — gives Lyra
# time to finish speaking and gives the user a window to say "cancel shutdown".
POWER_ACTION_DELAY = 8

# ---------------------------------------------------------------------------
# Phase 0: Wake word detection (openWakeWord)
# ---------------------------------------------------------------------------
# Pre-trained models: alexa, hey_mycroft, hey_jarvis, etc. No native "hey_lyra"
# model, so hey_jarvis is the phonetic detector; STT maps transcripts to "lyra".
WAKE_MODEL = "hey_jarvis"
# Lower = more sensitive. 0.3 works when saying "hey Lyra" into hey_jarvis.
WAKE_THRESHOLD = 0.25
WAKE_CHUNK_SIZE = 1280      # audio chunk size for wake word detection (80ms at 16kHz)
WAKE_SAMPLE_RATE = 16000    # sample rate for wake word audio

# Utterance capture while in standby (see wake.py). Lyra only acts on speech that
# contains the wake phrase; everything else is ignored.
WAKE_MIN_SPEECH_RMS = 350   # mic level that counts as speech. Lower it if Lyra misses quiet voices,
                            # raise it if background noise keeps triggering transcription.
WAKE_END_SILENCE = 0.9      # seconds of silence that end an utterance
WAKE_MAX_UTTERANCE = 12     # hard cap on one utterance (seconds)
WAKE_PREROLL_CHUNKS = 8     # audio kept from BEFORE speech starts (8 x 80ms) so "hey" isn't clipped
# If you say only "Hey Lyra" and pause, wait this long (seconds) for the command that
# belongs to THAT wake. After the command runs Lyra always returns to standby.
# Set to 0 to require the command in the same breath.
WAKE_FOLLOWUP_GRACE = 4.0

# ---------------------------------------------------------------------------
# Phase 0: Speech-to-text (faster-whisper)
# ---------------------------------------------------------------------------
# "base" is fast but mishears a lot. "small" is the best accuracy/speed tradeoff
# on CPU (~2-3x slower than base, much more accurate). Use "medium" if you have
# a GPU (set WHISPER_DEVICE = "cuda") or a strong CPU and want max accuracy.
WHISPER_MODEL = "small"          # tiny | base | small | medium | large-v3
WHISPER_DEVICE = "cpu"           # cpu or cuda
WHISPER_COMPUTE_TYPE = "int8"    # int8 (CPU), float16 (GPU)
WHISPER_LANGUAGE = "en"          # language code for transcription
WHISPER_BEAM_SIZE = 1            # decoding effort: 1 fastest, 5+ slower and only slightly more accurate
# Bias Whisper toward Lyra command vocabulary (helps short commands a lot)
WHISPER_INITIAL_PROMPT = (
    "Hey Lyra. Open Spotify. Open Discord. Open Chrome. Volume up. Volume down. "
    "Take a screenshot. Lock the screen. What time is it. Switch to text mode. "
    "Go to sleep. Focus mode. Gaming mode. Get me ready for work."
)

# ---------------------------------------------------------------------------
# Phase 4: streaming speech recognition (sherpa-onnx) + dictation
# ---------------------------------------------------------------------------
# A small streaming model gives partial text while you are still talking. It is used to
# (1) end a spoken turn sooner and pre-warm the LLM, and (2) type dictation word by word.
# Commands still run only on the FINAL Whisper transcript, never on a partial.
# The default model is English only; any sherpa-onnx streaming zipformer/transducer
# release (same file layout) can be used by changing the two values below.
STREAMING_ENABLED = True
# Light model (default). A bigger one, "sherpa-onnx-streaming-zipformer-en-2023-06-26", hears better but uses
# about twice the CPU: too much for a PC that already runs hot. Same name inside the URL when changing it.
STREAMING_MODEL_NAME = "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"
STREAMING_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2"
)
STREAMING_THREADS = 2
# Wake / PTT turns: end the turn after this much silence (instead of WAKE_END_SILENCE)
# once the partial text has stopped changing for STREAMING_STABLE_S.
STREAMING_EARLY_END = True
STREAMING_END_SILENCE = 0.5
STREAMING_STABLE_S = 0.3
# Dictation: the last words of a partial can still change, so they are typed later.
DICTATION_PENDING_WORDS = 2     # words held back while you are still speaking (1-2)
DICTATION_PAUSE_FLUSH_S = 0.7   # a pause this long types the held-back words too
DICTATION_MAX_POLISH_CHARS = 3000
