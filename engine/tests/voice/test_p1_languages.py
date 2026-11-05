"""Phase 1 tests: language routing + hint / audio helpers. From the backend folder:

    python -m unittest tests.test_p1_languages -v

No models, no network, no microphone.
"""
import unittest

import numpy as np


from engine.voice.stt_engines import languages as L  # noqa: E402
from engine.voice.stt_engines import textutil as T  # noqa: E402


class LanguageTables(unittest.TestCase):
    def test_exactly_25_languages_and_no_arabic(self):
        self.assertEqual(len(L.PARAKEET_V3_LANGUAGES), 25)
        for code in ("ar", "he", "tr", "zh", "ja", "hi", "ko"):
            self.assertFalse(L.parakeet_supports(code), code)
        for code in ("en", "fr", "de", "es", "it", "pt", "nl", "pl", "ru", "uk", "bg", "el", "mt"):
            self.assertTrue(L.parakeet_supports(code), code)

    def test_normalize_language(self):
        self.assertEqual(L.normalize_language("en-US"), "en")
        self.assertEqual(L.normalize_language("FR_ca"), "fr")
        self.assertEqual(L.normalize_language(" de "), "de")
        for empty in (None, "", "auto", "AUTO", "und"):
            self.assertIsNone(L.normalize_language(empty))

    def test_unknown_language_is_not_supported(self):
        self.assertFalse(L.parakeet_supports(None))
        self.assertFalse(L.parakeet_supports("auto"))


class Routing(unittest.TestCase):
    def pick(self, mode, lang, ready=True):
        return L.choose_engine(mode, lang, parakeet_ready=ready)[0]

    def test_auto_supported_language_uses_parakeet(self):
        self.assertEqual(self.pick("auto", "en"), "parakeet")
        self.assertEqual(self.pick("auto", "fr-FR"), "parakeet")

    def test_arabic_always_goes_to_whisper_even_in_parakeet_mode(self):
        self.assertEqual(self.pick("auto", "ar"), "whisper")
        self.assertEqual(self.pick("parakeet", "ar"), "whisper")
        self.assertEqual(self.pick("parakeet", "ar-MA"), "whisper")

    def test_whisper_mode_never_uses_parakeet(self):
        self.assertEqual(self.pick("whisper", "en"), "whisper")
        self.assertEqual(self.pick("whisper", None), "whisper")

    def test_model_missing_falls_back_to_whisper(self):
        self.assertEqual(self.pick("auto", "en", ready=False), "whisper")
        self.assertEqual(self.pick("parakeet", "en", ready=False), "whisper")

    def test_unknown_language(self):
        self.assertEqual(self.pick("auto", None), "parakeet")                    # the model detects it

    def test_bad_mode_is_auto(self):
        self.assertEqual(L.normalize_mode("nonsense"), "auto")
        self.assertEqual(L.normalize_mode(None), "auto")
        self.assertEqual(L.normalize_mode(" Parakeet "), "parakeet")

    def test_reason_is_human_readable(self):
        self.assertIn("not supported", L.choose_engine("auto", "ar", parakeet_ready=True)[1])


class Hints(unittest.TestCase):
    def test_restores_spelling_ignoring_case(self):
        self.assertEqual(T.apply_hints("open spotify please", ["Spotify"]), "open Spotify please")

    def test_joins_split_words(self):
        self.assertEqual(T.apply_hints("open spot ify now", ["Spotify"]), "open Spotify now")
        self.assertEqual(T.apply_hints("a kubernetes cluster", ["Kuber netes"]), "a Kuber netes cluster")

    def test_keeps_punctuation(self):
        self.assertEqual(T.apply_hints("Open spotify, then discord.", ["Spotify", "Discord"]),
                         "Open Spotify, then Discord.")

    def test_no_fuzzy_by_default_discard_stays_discard(self):
        self.assertEqual(T.apply_hints("discard it", ["Discord"]), "discard it")
        self.assertEqual(T.apply_hints("discard it", ["Discord", "Spotify"], fuzzy=False), "discard it")

    def test_fuzzy_is_a_deliberate_opt_in(self):
        # one edit away from a 7-letter term: with fuzzy on it IS snapped (the user's own dictionary)
        self.assertEqual(T.apply_hints("discard it", ["Discord"], fuzzy=True), "Discord it")
        # short terms are never fuzzy-matched
        self.assertEqual(T.apply_hints("lira", ["Lyra"], fuzzy=True), "lira")

    def test_fuzzy_for_user_dictionary_long_terms(self):
        self.assertEqual(T.apply_hints("we use kubernetis", ["Kubernetes"], fuzzy=True), "we use Kubernetes")
        self.assertEqual(T.apply_hints("we use kubernetis", ["Kubernetes"]), "we use kubernetis")

    def test_empty_inputs_and_junk_never_raise(self):
        self.assertEqual(T.apply_hints("", ["x"]), "")
        self.assertEqual(T.apply_hints("hello", None), "hello")
        self.assertEqual(T.apply_hints("hello", [None, "", "  "]), "hello")
        self.assertEqual(T.apply_hints("hello", [object()]), "hello")

    def test_non_latin_text_untouched(self):
        self.assertEqual(T.apply_hints("привет мир", ["Spotify"]), "привет мир")


class AudioHelpers(unittest.TestCase):
    def test_prepare_audio_int16_and_nan(self):
        a = T.prepare_audio(np.array([0, 16384, -32768], dtype=np.int16))
        self.assertEqual(a.dtype, np.float32)
        self.assertAlmostEqual(float(a[1]), 0.5, places=3)
        b = T.prepare_audio(np.array([np.nan, np.inf, -np.inf, 2.0], dtype=np.float32))
        self.assertTrue(np.all(np.isfinite(b)))
        self.assertLessEqual(float(np.max(b)), 1.0)

    def test_prepare_audio_stereo_to_mono(self):
        st = np.stack([np.ones(100), np.zeros(100)], axis=1).astype(np.float32)
        m = T.prepare_audio(st)
        self.assertEqual(m.shape, (100,))
        self.assertAlmostEqual(float(m[0]), 0.5)

    def test_split_long_short_audio_untouched(self):
        a = np.zeros(16000 * 10, dtype=np.float32)
        self.assertEqual(len(T.split_long(a, 30)), 1)

    def test_split_long_cuts_in_the_quiet_part(self):
        sr = 16000
        rng = np.random.default_rng(0)
        a = (rng.standard_normal(sr * 70) * 0.2).astype(np.float32)           # constant "speech"
        gap = int(sr * 27.5)
        a[gap:gap + int(0.3 * sr)] = 0.0                                       # a pause near the 30 s mark
        pieces = T.split_long(a, 30)
        self.assertEqual(sum(len(p) for p in pieces), len(a))                  # nothing lost or duplicated
        self.assertTrue(all(len(p) <= 30 * sr for p in pieces))
        first_cut = len(pieces[0])
        self.assertTrue(gap <= first_cut <= gap + int(0.3 * sr), first_cut)    # cut inside the pause

    def test_split_long_without_pauses_still_terminates(self):
        a = np.ones(16000 * 95, dtype=np.float32)
        pieces = T.split_long(a, 30)
        self.assertEqual(sum(len(p) for p in pieces), len(a))
        self.assertGreaterEqual(len(pieces), 4)


if __name__ == "__main__":
    unittest.main()
