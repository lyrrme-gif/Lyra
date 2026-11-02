"""Pick a TTS voice that matches the language of the text Lyra is about to speak.

No extra dependencies: Arabic is detected by script, Latin-script languages by
common-word counts. If the chosen voice already speaks the detected language
(or detection is unsure), the chosen voice is kept.
"""
from __future__ import annotations
import re

_WORDS = {
    "fr": "le la les un une des et est je tu il elle nous vous ils de du que qui pour dans sur avec pas ce cette sont suis très bien merci bonjour salut oui non au aux mon ma mes ton ça c'est j'ai l'",
    "es": "el la los las un una y es yo tú usted nosotros de del que para en con no por esto esta son soy muy bien gracias hola sí al mi su qué cómo está",
    "de": "der die das ein eine und ist ich du er sie wir ihr von zu mit nicht für auf auch sehr gut danke hallo ja nein im dem den was wie",
    "it": "il lo la gli le un una e è io tu lui lei noi voi di del che per in con non questo questa sono molto bene grazie ciao sì al mi cosa come",
    "pt": "o a os as um uma e é eu tu ele ela nós de do que para em com não isto esta são sou muito bem obrigado olá sim ao meu seu como",
    "en": "the a an and is are i you he she we they of to that for in on with not this it be was very good thanks hello yes no what how my your",
}
_SETS = {k: set(v.split()) for k, v in _WORDS.items()}
_ACCENTS = {"fr": "àâçéèêëîïôùûœ", "es": "ñ¿¡áíóú", "de": "äöüß", "pt": "ãõ", "it": "ì"}

# Default voice per language: (female, male)
_VOICES = {
    "en": ("en-US-JennyNeural", "en-US-GuyNeural"),
    "fr": ("fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    "es": ("es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
    "de": ("de-DE-KatjaNeural", "de-DE-ConradNeural"),
    "it": ("it-IT-ElsaNeural", "it-IT-DiegoNeural"),
    "pt": ("pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"),
    "ar": ("ar-SA-ZariyahNeural", "ar-SA-HamedNeural"),
}
_MALE = ("Guy", "Christopher", "Ryan", "Henri", "Remy", "Alvaro", "Conrad", "Diego", "Antonio", "Hamed", "Jamal", "Mounir")


# Translate mode knows which language a reply is in; a one-word answer like "Merci" is too short for
# word-count detection, so it hints it here. Small and bounded: the oldest hint is dropped first.
_HINTS: dict[str, str] = {}
_HINT_MAX = 64


def _hint_key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def hint(text: str, lang: str) -> None:
    """Remember that `text` is in `lang` so detect()/pick_voice() pick the right voice for it."""
    key = _hint_key(text)
    if not key or not lang:
        return
    _HINTS.pop(key, None)
    while len(_HINTS) >= _HINT_MAX:
        _HINTS.pop(next(iter(_HINTS)))
    _HINTS[key] = lang


def detect(text: str) -> str:
    """Return a language code ('fr', 'es', 'ar', ...) or '' when unsure."""
    hinted = _HINTS.get(_hint_key(text))
    if hinted:
        return hinted
    t = (text or "").lower()
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return ""
    if sum("\u0600" <= c <= "\u06ff" for c in letters) > len(letters) * 0.4:
        return "ar"
    words = re.findall(r"[\wàâçéèêëîïôùûœñáíóúäöüßãõì']+", t)
    scores = {}
    for lang, vocab in _SETS.items():
        s = sum(w in vocab or w.split("'")[-1] in vocab for w in words)
        s += sum(c in _ACCENTS.get(lang, "") for c in t) * 0.5
        scores[lang] = s
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 and scores[best] > scores.get("en", 0) else ("en" if scores["en"] >= 2 else "")


def pick_voice(text: str, chosen: str) -> str:
    """Keep `chosen` if it already speaks the text's language, else a matching voice."""
    lang = detect(text)
    if not lang or lang not in _VOICES:
        return chosen
    if chosen and chosen.lower().startswith(lang + "-"):
        return chosen
    if chosen and "multilingual" in chosen.lower():
        return chosen
    male = any(m in (chosen or "") for m in _MALE)
    return _VOICES[lang][1 if male else 0]
