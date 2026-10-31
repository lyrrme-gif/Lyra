"""Lyra — persistent user settings stored in settings.json next to this package.

Provides a simple key/value API with in-memory caching and automatic
fallback to hard-coded defaults when the JSON file is missing or corrupt.

Other modules should import the helper functions (get, set_many, wake_name,
wake_phrases) rather than reading the JSON file themselves.
"""

from __future__ import annotations  # Enables modern type hints

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import paths
import ui

log = ui.log  # Shared logger

# Persistent settings live outside the project so replacing/updating the Lyra ZIP
# cannot reset the user's choices. In source/dev mode we still keep a one-time
# migration path from backend/settings.json; frozen builds already use this folder.
def _persistent_settings_path() -> Path:
    if getattr(paths, "FROZEN", False):
        return paths.data_path("settings.json")
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    p = Path(base) / "Lyra" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    legacy = paths.data_path("settings.json")
    if not p.exists() and legacy.exists():
        try:
            shutil.copy2(legacy, p)
            log.info("Migrated settings.json to persistent user data: %s", p)
        except Exception as e:
            log.warning("Could not migrate legacy settings.json: %s", e)
    return p


SETTINGS_PATH = _persistent_settings_path()

# Programs that switch game mode on (the editable list in Settings -> Features -> Game mode).
GAME_MODE_APPS = (
    "valorant-win64-shipping.exe", "valorant.exe", "cs2.exe", "fortniteclient-win64-shipping.exe",
    "r5apex.exe", "apexlegends.exe", "overwatch.exe", "dota2.exe", "league of legends.exe",
    "gta5.exe", "eldenring.exe", "rocketleague.exe",
)

# ---------------------------------------------------------------------------
# Default values used when the JSON file is absent or a key is missing
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {
    # >>> phase-1 begin
    "p1_stt_engine": "auto",        # auto | parakeet | whisper (stt_engines)
    # <<< phase-1 end
    # >>> phase-7 begin
    "p7_retention_days": 30,        # notetaker: delete transcripts older than this (0 = keep)
    "p7_diarize": True,             # tell remote speakers apart when the optional model is installed
    "p7_summarize": False,          # notetaker summary + to-dos from transcript TEXT; off by default
    "p7_summary_provider": "local", # local | key
    "p7_max_hours": 4,              # recording auto-stops after this many hours
    # <<< phase-7 end
    "wake_name": "Lyra",
    "silent_startup": True,
    "tts_voice": "en-US-AriaNeural",
    "tts_engine": "edge",                   # edge (Microsoft, default) | offline (Kokoro, optional download)
    "tts_auto_language": True,              # speak replies with a voice matching their language
    "always_on_top": True,
    "auto_collapse": True,
    "audio_input": "Default Microphone",   # input device NAME ("Default Microphone" = system default)
    "audio_output": "Default Speaker",     # output device name for Lyra's voice
    "voice_model_note": "Local STT (faster-whisper) + OpenRouter/Gemini/Groq",
    "screen_watch_enabled": False,  # legacy compatibility
    "screen_watch_mode": "off",      # off | ask | always
    "screen_watch_interval": 1.0,
    "screen_watch_change_threshold": 3.0,
    "confirm_before_click": False,
    # LLM
    "llm_provider": "openrouter",           # openrouter | gemini | groq
    "llm_model": "google/gemini-2.5-flash",
    "llm_fallback_provider": "",            # "" = none | openrouter | gemini | groq
    "reply_style": "balanced",              # short | balanced | detailed
    # Voice
    "wake_sensitivity": 3,                  # 1 (needs a loud voice) .. 5 (picks up quiet speech)
    "voice_activation_mode": "ptt",        # always push-to-talk (Right Ctrl): there is no wake word any more
    # Dictation (Phase 4): hold or toggle a shortcut and Lyra types what you say into the focused window.
    "dictation_enabled": True,
    "dictation_hotkey": "ctrl+alt",         # see dictation_hotkey.HOTKEYS
    "dictation_hotkey_mode": "hold",        # hold | toggle
    "dictation_polish": True,               # LLM clean-up pass when you stop
    "dictation_refine_early": False,        # also decode each sentence while you speak (faster end, hotter CPU)
    "dictation_refine": True,               # when you stop, re-check live typing with Parakeet/Whisper and fix it
    "mochi_sound_mode": "mochi",            # speak | mochi | both: how short acknowledgements ("Done", "Opening ...") are given
    # >>> phase-6 begin
    "p6_startup_anim": True,                # play the ~1.5 s start-up animation when Lyra launches
    # <<< phase-6 end
    # Notifications (island toasts)
    "notify_toast_ms": 4000,
    "notify_discord": True,
    "notify_whatsapp": True,
    "notify_telegram": True,
    "notify_reminders": True,
    "notify_calls_only": False,             # chat apps: only calls get through
    "notify_quiet_enabled": False,
    "notify_quiet_start": "22:00",
    "notify_quiet_end": "07:00",
    "timer_alarm_sound": True,           # play the finish chime for timers/reminders
    "music_recognition_mode": "manual",  # off | manual | automatic
    "audd_api_token": "",
    # Feature switches (Settings -> Features). Each one starts/stops a background watcher;
    # the island hides the matching tab on its own. Applied when the backend (re)starts.
    "feature_notifications": True,          # mirror Discord / WhatsApp / Telegram toasts into the island
    "feature_clipboard": True,              # clipboard session watcher (Clipboard tab)
    "feature_downloads": True,              # file + browser download progress in the island
    "feature_volume": True,                 # per-app volume / ducking watcher (Sounds tab)
    "feature_status": True,                 # battery, Wi-Fi, device and brightness watcher
    "feature_light_mode": True,             # Light mode: heavy models (offline voice, Parakeet) load only when needed; idle watchers poll slower
    "shortcut_wheel_combo": "ctrl+capslock",   # Settings -> Shortcuts: hold to open the tab wheel ("" = off)
    "shortcut_circle_combo": "lctrl",          # hold to circle something on screen ("" = off)
    "shortcut_record_combo": "ctrl+alt+r",     # start / stop a screen recording ("" = off)
    "shortcut_pause_combo": "ctrl+alt+p",      # pause / resume the recording ("" = off)
    "shortcut_typing_combo": "ctrl+meta",  # Settings -> Shortcuts -> "Switch to typing" (opens the chat bar from anywhere)
    "shortcut_typing_enabled": True,
    "feature_circle_ask": True,             # Ctrl+Shift + circle something on screen to ask about it
    "feature_tab_wheel": True,              # hold Ctrl+CapsLock: the island's tab wheel
    "proactive_enabled": True,              # low-battery and long-session heads-ups
    "feature_game_mode": True,              # while a game runs, remove Right Ctrl / Ctrl+Win / Ctrl+Shift / Ctrl+CapsLock listeners
    "game_mode_fullscreen": True,           # ... also when any full-screen program (not a browser/player) is in front
    "game_mode_apps": ", ".join(GAME_MODE_APPS),
    "feature_macros": True,                 # macro recorder: record clicks/keys once, replay by voice
    "feature_ollama": True,
    "feature_plugins": True,
    "plugins_disabled": [],
    "feature_proactive_extra": True,
    "proactive_deadlines": True,
    "proactive_break": True,
    "proactive_hydration": False,
    "proactive_bedtime": False,
    "proactive_resources": True,
    "proactive_break_minutes": 50,
    "proactive_hydration_minutes": 90,
    "proactive_bedtime_time": "23:00",
    "proactive_cpu_percent": 90,
    "proactive_ram_percent": 90,
    "proactive_disk_percent": 92,
    "proactive_debug_seconds": 0,
    "feature_study": True,
    "study_focus_mutes": True,
    "study_distractors": "",
    "pomo_focus_min": 25,
    "pomo_break_min": 5,
    "pomo_long_break_min": 15,
    "feature_file_search": True,
    "index_folders": None,
    "index_exclude": [],
    "feature_dev": True,
    "feature_translate": True,
    "feature_mascot": False,
    "mascot_size": "medium",
    "feature_mascot_stay": False,
    "mascot_shape": "cloud",
    "record_folder": "",              # where screen recordings are saved: "" = Videos\Lyra Recordings, else a folder the user chose (Settings -> Recording -> Save location)
    "obs_host": "127.0.0.1",          # screen recording goes through OBS Studio's WebSocket server (see obs_control.py)
    "obs_port": 4455,
    "obs_password": "",               # only when OBS asks for one (Tools -> WebSocket Server Settings)
    "feature_guide": True,
    "guide_level": "look",            # screen guide: look (point only) | step (ask before each click) | auto
    "feature_planner": True,
    "feature_google": True,
    "feature_smarthome": False,
    "ha_url": "",
    "ha_token": "",
    "ha_aliases": "",
    "feature_phone_push": False,
    "ntfy_url": "",
    "ntfy_token": "",
    # Advanced
    "log_level": "INFO",                    # WARNING | INFO | DEBUG (applies to Lyra's own log, live)
}

# edge-tts voices offered in Settings ; "" = built-in default.
TTS_VOICES: tuple[str, ...] = (
    "en-US-AriaNeural", "en-US-JennyNeural", "en-US-AvaNeural", "en-US-GuyNeural",
    "en-US-ChristopherNeural", "en-GB-SoniaNeural", "en-GB-RyanNeural",
    "en-US-AvaMultilingualNeural", "en-US-AndrewMultilingualNeural",
    "fr-FR-DeniseNeural", "fr-FR-EloiseNeural", "fr-FR-HenriNeural",
    "fr-FR-VivienneMultilingualNeural", "fr-FR-RemyMultilingualNeural",
    "es-ES-ElviraNeural", "es-ES-AlvaroNeural",
    "de-DE-KatjaNeural", "de-DE-ConradNeural",
    "it-IT-ElsaNeural", "it-IT-DiegoNeural",
    "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural",
    "ar-SA-ZariyahNeural", "ar-SA-HamedNeural",
)

# Settings groups the Settings window edits over HTTP: key -> ("bool"|"time"),
# ("int", lo, hi), ("choice", options) or ("text", max_length).
PREF_GROUPS: dict[str, dict[str, tuple]] = {
    "recordings": {
        "obs_host": ("text", 120),
        "obs_port": ("int", 1, 65535),
        "obs_password": ("text", 200),
    },
    "notifications": {
        "notify_toast_ms": ("int", 1500, 15000),
        "notify_discord": ("bool",),
        "notify_whatsapp": ("bool",),
        "notify_telegram": ("bool",),
        "notify_reminders": ("bool",),
        "notify_calls_only": ("bool",),
        "notify_quiet_enabled": ("bool",),
        "notify_quiet_start": ("time",),
        "notify_quiet_end": ("time",),
    },
    "ai": {
        "llm_fallback_provider": ("choice", ("", "openrouter", "gemini", "groq", "ollama")),
        "reply_style": ("choice", ("short", "balanced", "detailed")),
    },
    "privacy": {
        "confirm_before_click": ("bool",),
        "screen_watch_mode": ("choice", ("off", "ask", "always")),
        "screen_watch_interval": ("int", 1, 10),
        "screen_watch_change_threshold": ("int", 1, 30),
    },
    "features": {
        # >>> phase-6 begin
        "p6_startup_anim": ("bool",),
        # <<< phase-6 end
        "feature_notifications": ("bool",),
        "feature_clipboard": ("bool",),
        "feature_downloads": ("bool",),
        "feature_volume": ("bool",),
        "feature_status": ("bool",),
        "feature_light_mode": ("bool",),
        "shortcut_wheel_combo": ("text", 40), "shortcut_circle_combo": ("text", 40),
        "shortcut_record_combo": ("text", 40), "shortcut_pause_combo": ("text", 40),
        "shortcut_typing_combo": ("text", 40), "shortcut_typing_enabled": ("bool",),
        "feature_circle_ask": ("bool",),
        "feature_tab_wheel": ("bool",),
        "proactive_enabled": ("bool",),
        "feature_game_mode": ("bool",), "game_mode_fullscreen": ("bool",), "game_mode_apps": ("text", 1000),
        "feature_macros": ("bool",),
        "feature_ollama": ("bool",),
        "feature_plugins": ("bool",),
        "feature_proactive_extra": ("bool",),
        "proactive_deadlines": ("bool",), "proactive_break": ("bool",),
        "proactive_hydration": ("bool",), "proactive_bedtime": ("bool",),
        "proactive_resources": ("bool",),
        "proactive_break_minutes": ("int", 10, 240),
        "proactive_hydration_minutes": ("int", 20, 240),
        "proactive_bedtime_time": ("time",),
        "proactive_cpu_percent": ("int", 50, 100),
        "proactive_ram_percent": ("int", 50, 100),
        "proactive_disk_percent": ("int", 50, 100),
        "proactive_debug_seconds": ("int", 0, 3600),
        "feature_study": ("bool",),
        "study_focus_mutes": ("bool",), "study_distractors": ("text", 300),
        "pomo_focus_min": ("int", 1, 180), "pomo_break_min": ("int", 1, 60),
        "pomo_long_break_min": ("int", 1, 120),
        "feature_file_search": ("bool",), "feature_dev": ("bool",),
        "feature_translate": ("bool",), "feature_mascot": ("bool",),
        "mascot_size": ("choice", ("small", "medium", "large")),
        "feature_mascot_stay": ("bool",),
                "mascot_shape": ("choice", ("cloud", "fox", "cat", "bunny", "panda", "knight")),
        "feature_guide": ("bool",), "guide_level": ("choice", ("look", "step", "auto")), "feature_planner": ("bool",),
        "feature_google": ("bool",), "feature_smarthome": ("bool",),
        "ha_url": ("text", 500), "ha_token": ("text", 500), "ha_aliases": ("text", 3000),
        "feature_phone_push": ("bool",), "ntfy_url": ("text", 500), "ntfy_token": ("text", 500),
    },
    "advanced": {
        "log_level": ("choice", ("WARNING", "INFO", "DEBUG")),
    },
    "music": {
        "music_recognition_mode": ("choice", ("off", "manual", "automatic")),
        "audd_api_token": ("text", 200),
    },
    "voice": {
        "audio_input": ("text", 200),
        "audio_output": ("text", 200),
        "wake_sensitivity": ("int", 1, 5),
        "voice_activation_mode": ("choice", ("ptt", "wake", "both")),
        "tts_voice": ("choice", ("",) + TTS_VOICES),
        "tts_engine": ("choice", ("edge", "offline")),
        "tts_auto_language": ("bool",),
        "dictation_enabled": ("bool",),
        "dictation_hotkey": ("choice", ("ctrl+alt", "ctrl+alt+d", "ctrl+shift+d", "ctrl+alt+space", "ctrl+shift+space")),
        "dictation_hotkey_mode": ("choice", ("hold", "toggle")),
        "dictation_polish": ("bool",),
        "dictation_refine": ("bool",),
        "dictation_refine_early": ("bool",),
        "mochi_sound_mode": ("choice", ("speak", "mochi", "both")),
    },
}


def _coerce(spec: tuple, value: Any) -> Any:
    kind = spec[0]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError("expected true/false")
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("expected a number")
        return max(spec[1], min(spec[2], int(round(value))))
    if kind == "time":
        text = str(value).strip()
        h, _, m = text.partition(":")
        if h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60:
            return f"{int(h):02d}:{int(m):02d}"
        raise ValueError("expected HH:MM")
    if kind == "choice":
        if isinstance(value, str) and value in spec[1]:
            return value
        raise ValueError("not an allowed choice")
    if kind == "text":
        if isinstance(value, str) and len(value.strip()) <= spec[1]:
            return value.strip()
        raise ValueError("expected short text")
    raise ValueError("unknown setting type")


def feature_on(name: str) -> bool:
    """True unless the user switched the feature off in Settings -> Features.
    `name` is the part after "feature_" (e.g. "clipboard"). Missing/odd values mean on."""
    try:
        return get(f"feature_{name}") is not False
    except Exception:
        return True


def get_group(group: str) -> dict[str, Any]:
    """Current values of one settings group (see PREF_GROUPS)."""
    return {k: get(k) for k in PREF_GROUPS[group]}


def update_group(group: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Validate and save the known keys of `updates`; unknown keys are ignored.
    Raises ValueError (with the key name) on a bad value."""
    spec = PREF_GROUPS[group]
    clean: dict[str, Any] = {}
    for key, value in updates.items():
        if key in spec:
            try:
                clean[key] = _coerce(spec[key], value)
            except ValueError as e:
                raise ValueError(f"{key}: {e}") from None
    if clean:
        set_many(clean)
        if "log_level" in clean:
            apply_log_level()
    return get_group(group)


def apply_log_level() -> None:
    """Make Lyra's own logger (ui.log) use the saved level. Safe to call at any time."""
    level = getattr(logging, str(get("log_level") or "INFO").upper(), logging.INFO)
    log.setLevel(level)


def reset_prefs() -> int:
    """Put every Settings-window preference back to its default (the groups in PREF_GROUPS).
    Leaves API keys, mods, shortcuts, memory and other settings.json keys (wake name,
    screen watch...) alone. Returns how many keys were reset."""
    keys = {k for spec in PREF_GROUPS.values() for k in spec}
    set_many({k: _DEFAULTS[k] for k in keys})
    apply_log_level()
    return len(keys)

# In-memory cache so we do not re-read the file on every get()
_cache: dict[str, Any] | None = None



def _migrate(data: dict, raw: dict) -> None:
    """Old saved values that no longer exist are mapped to their closest survivor (never an error)."""
    if "shortcut_record_combo" not in raw and raw.get("record_hotkeys") is not True:
        # The recording shortcuts used to be off unless switched on: keep them off for people who never did.
        data["shortcut_record_combo"] = ""
        data["shortcut_pause_combo"] = ""

def _ensure_loaded() -> dict[str, Any]:
    """Load settings from disk (once) and return the cached dict.

    Always starts from a copy of _DEFAULTS, then overlays any keys that
    actually exist in the JSON file. Corrupt / missing files are logged
    but never raise — the defaults are used instead.
    """
    global _cache
    if _cache is not None:
        return _cache  # Already loaded this process
    data = dict(_DEFAULTS)  # Start with a fresh copy of the defaults
    needs_write = not SETTINGS_PATH.exists()
    if SETTINGS_PATH.exists():
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                # Only accept keys that we know about (ignore unknown keys).
                # p2_* (dictation preferences) are not in _DEFAULTS but must survive a restart.
                data.update({k: raw[k] for k in raw if k in _DEFAULTS or str(k).startswith("p2_")})
                # Persist newly introduced defaults too. This prevents a new
                # preference such as voice_activation_mode from reverting to
                # its default after a restart/update.
                needs_write = any(k not in raw for k in _DEFAULTS)
                _migrate(data, raw)
        except Exception as e:
            log.warning("Could not read settings.json: %s", e)
            needs_write = True
    _cache = data
    if needs_write:
        try:
            SETTINGS_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            log.info("Initialized persistent settings at %s", SETTINGS_PATH)
        except Exception as e:
            log.error("Failed to initialize settings.json: %s", e)
    # >>> phase-0 begin
    try:
        import secrets_store
        secrets_store.settings_migrate(_cache, SETTINGS_PATH)   # tokens: settings.json -> OS keychain (one time)
    except Exception:
        pass
    # <<< phase-0 end
    return _cache


def get(key: str, default: Any = None) -> Any:
    """Return the value for *key*, falling back to the supplied default
    or the hard-coded default from _DEFAULTS.
    """
    data = _ensure_loaded()
    # >>> phase-0 begin
    try:
        import secrets_store
        _v = secrets_store.settings_get(key)        # ha_token / ntfy_token / audd_api_token come from the keychain
        if _v is not None:
            return _v
    except Exception:
        pass
    # <<< phase-0 end
    return data.get(key, default if default is not None else _DEFAULTS.get(key))


def get_all() -> dict[str, Any]:
    """Return a shallow copy of the entire settings dictionary."""
    return dict(_ensure_loaded())


def set_many(updates: dict[str, Any]) -> None:
    """Update one or more settings and immediately write them to disk.

    The in-memory cache is updated first so subsequent get() calls see
    the new values even if the disk write fails.
    """
    global _cache
    data = _ensure_loaded()
    data.update(updates)
    # >>> phase-0 begin
    try:
        import secrets_store
        secrets_store.settings_set_many(data, updates)  # secret keys go to the keychain, blank in the file
    except Exception:
        pass
    # <<< phase-0 end
    _cache = data
    try:
        SETTINGS_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.info("Settings saved to %s", SETTINGS_PATH)
    except Exception as e:
        log.error("Failed to save settings.json: %s", e)


def wake_name() -> str:
    """Return the configured display name of the assistant (never empty)."""
    name = str(get("wake_name") or "Lyra").strip()
    return name or "Lyra"


def wake_phrases() -> list[str]:
    """Build the list of spoken wake phrases from the configured name.

    Always includes a set of Jarvis / Lyra phonetic variants so that the
    openWakeWord "hey_jarvis" model still triggers even when the user has
    renamed the assistant.
    """
    name = wake_name()
    low = name.lower().strip()
    phrases = [
        f"hey {low}",                                    # "hey lyra"
        low,                                             # bare name
        f"hey {low}a" if not low.endswith("a") else f"hey {low}",  # phonetic padding
    ]
    # Always keep jarvis variants for openWakeWord phonetic hits
    phrases += [
        "hey jarvis", "hey jarviss", "hey jarves", "jarvis",
        "hey lyra", "lyra", "hey lyra", "lyra",
    ]
    # De-duplicate while preserving order
    seen = set()
    out = []
    for p in phrases:
        p = p.strip().lower()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


apply_log_level()  # honour the saved level from the first moment settings are loaded
