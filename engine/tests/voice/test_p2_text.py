"""Phase 2: spoken phrases (dictionary replacements + snippets) in dictation_text.py."""
import unittest


from engine.voice.dictation_text import Literal, PhraseMap, WordStream, apply_phrases, norm_phrase  # noqa: E402


def dictate(sentence, phrases, pending=2):
    words, ws, out = sentence.split(), WordStream(pending, phrases), ""
    for i in range(1, len(words) + 1):
        out += ws.update(" ".join(words[:i]))
    return out + ws.update(sentence, flush=True) + ws.finish()


class PhraseMapTests(unittest.TestCase):
    def test_normalisation_ignores_case_and_punctuation(self):
        self.assertEqual(norm_phrase("  My, Email! "), ("my", "email"))

    def test_longest_trigger_wins(self):
        pm = PhraseMap({"my email": "a@x.com", "my email address": "long"})
        self.assertEqual(pm.match_at("my email address now".split(), 0), (3, "long"))
        self.assertEqual(pm.match_at("my email now".split(), 0), (2, "a@x.com"))

    def test_prefix_detection(self):
        pm = PhraseMap({"my email address": "x"})
        self.assertTrue(pm.could_continue(["my"]))
        self.assertTrue(pm.could_continue(["My", "email"]))
        self.assertFalse(pm.could_continue(["my", "email", "address"]))   # complete, not a prefix
        self.assertFalse(pm.could_continue(["email"]))

    def test_empty_map_is_falsy_and_ignores_blank_triggers(self):
        self.assertFalse(PhraseMap({"": "x", "  ": "y"}))


class WordStreamPhraseTests(unittest.TestCase):
    def test_replacement_is_typed_instead_of_the_words(self):
        pm = PhraseMap({"my email": "me@example.com"})
        self.assertEqual(dictate("send it to my email please", pm), "Send it to me@example.com please")

    def test_words_that_may_grow_into_a_trigger_are_held_back(self):
        pm = PhraseMap({"snippet signature": "Best regards,\nAnna"})
        ws = WordStream(0, pm)                      # no built-in hold-back: only the trigger logic holds
        self.assertEqual(ws.update("thanks snippet"), "Thanks")       # "snippet" may become "snippet signature"
        self.assertEqual(ws.update("thanks snippet signature"), " Best regards,\nAnna")

    def test_held_prefix_that_never_completes_is_typed_on_flush(self):
        pm = PhraseMap({"snippet signature": "X"})
        ws = WordStream(0, pm)
        ws.update("snippet")
        self.assertEqual(ws.update("snippet", flush=True), "Snippet")

    def test_replacement_text_is_not_recapitalised_but_capital_follows_a_full_stop(self):
        pm = PhraseMap({"iphone": "iPhone", "sign off": "Cheers."})
        self.assertEqual(dictate("my iphone is here", pm), "My iPhone is here")
        self.assertEqual(dictate("sign off see you", pm), "Cheers. See you")

    def test_note_list_still_works_next_to_phrases(self):
        pm = PhraseMap({"my email": "a@b.c"})
        self.assertEqual(dictate("note one my email next point call me", pm), "1. a@b.c\n2. Call me")

    def test_no_phrases_means_old_behaviour(self):
        self.assertEqual(dictate("hello world", PhraseMap()), "Hello world")


class ApplyPhrasesTests(unittest.TestCase):
    PM = PhraseMap({"my email": "me@x.com", "gonna": "going to"})

    def test_inside_text_with_punctuation_and_case(self):
        self.assertEqual(apply_phrases("Send it to My Email, thanks.", self.PM), "Send it to me@x.com, thanks.")

    def test_trailing_full_stop_after_a_closing_trigger_is_swallowed(self):
        self.assertEqual(apply_phrases("my email.", self.PM), "me@x.com")
        self.assertEqual(apply_phrases("It is my email!", self.PM), "It is me@x.com!")

    def test_whole_words_only(self):
        self.assertEqual(apply_phrases("a gonnabe thing", self.PM), "a gonnabe thing")

    def test_multiline_replacement_survives(self):
        pm = PhraseMap({"sign off": "Regards,\nAnna"})
        self.assertEqual(apply_phrases("Thanks. Sign off", pm), "Thanks. Regards,\nAnna")

    def test_accepts_plain_dict_and_empty_text(self):
        self.assertEqual(apply_phrases("gonna", {"gonna": "going to"}), "going to")
        self.assertEqual(apply_phrases("", self.PM), "")

    def test_literal_is_a_str(self):
        self.assertEqual(Literal("x") + "y", "xy")


if __name__ == "__main__":
    unittest.main()
