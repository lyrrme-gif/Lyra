"""Lyra - which speech engine handles which language. Pure logic, no models, no I/O.

Parakeet TDT 0.6B v3 supports exactly these 25 European languages (NVIDIA model card,
checked for this build; it has NO Arabic, Darija, Hebrew, Turkish, Hindi, Chinese, Japanese...).
Anything else is routed to Whisper, which covers ~99 languages.
"""
from __future__ import annotations

PARAKEET_V3_LANGUAGES: dict[str, str] = {
    "bg": "Bulgarian", "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch",
    "en": "English", "et": "Estonian", "fi": "Finnish", "fr": "French", "de": "German",
    "el": "Greek", "hu": "Hungarian", "it": "Italian", "lv": "Latvian", "lt": "Lithuanian",
    "mt": "Maltese", "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "sk": "Slovak",
    "sl": "Slovenian", "es": "Spanish", "sv": "Swedish", "ru": "Russian", "uk": "Ukrainian",
}

MODES = ("auto", "parakeet", "whisper")


def normalize_language(language: str | None) -> str | None:
    """'en-US' / 'EN_gb' / ' fr ' -> 'en' / 'en' / 'fr'. Empty, None or 'auto' -> None (unknown)."""
    if not language:
        return None
    code = str(language).strip().lower().replace("_", "-").split("-")[0]
    if not code or code in ("auto", "und", "detect", "none"):
        return None
    return code


def parakeet_supports(language: str | None) -> bool:
    """True only for a KNOWN language that Parakeet v3 handles."""
    code = normalize_language(language)
    return code is not None and code in PARAKEET_V3_LANGUAGES


def normalize_mode(mode: str | None) -> str:
    m = str(mode or "auto").strip().lower()
    return m if m in MODES else "auto"


def choose_engine(mode: str | None, language: str | None, *, parakeet_ready: bool) -> tuple[str, str]:
    """Pick the engine for one utterance. Returns (engine, reason); engine is 'parakeet' | 'whisper'.

    mode            auto | parakeet | whisper (the user's setting)
    language        what the caller knows about the speech (None = unknown / auto-detect)
    parakeet_ready  the model is downloaded and loadable

    Rules, in order:
      whisper mode                          -> whisper
      language known and NOT in Parakeet's list -> whisper   (even in 'parakeet' mode: it cannot do it)
      Parakeet not downloaded               -> whisper (fallback)
      otherwise                             -> parakeet
    """
    mode = normalize_mode(mode)
    code = normalize_language(language)
    if mode == "whisper":
        return "whisper", "mode=whisper"
    if code is not None and code not in PARAKEET_V3_LANGUAGES:
        return "whisper", f"language '{code}' is not supported by Parakeet v3"
    if not parakeet_ready:
        return "whisper", "Parakeet model not downloaded"
    return "parakeet", "mode=" + mode + (f", language '{code}'" if code else ", language auto-detected by the model")
