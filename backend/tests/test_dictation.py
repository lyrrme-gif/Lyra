"""Tests for dictation_text.py (Phase 4). From the backend folder:  python -m unittest tests.test_dictation -v

Pure text logic only: which words are typed yet, capitals, spoken lists, polish sanity check.
Typing, hotkeys and the recognizer need Windows / a microphone and are not covered here."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dictation_text import WordStream, accept_polish  # noqa: E402


def dictate(sentence: str, pending: int = 2) -> str:
    """Feed `sentence` one word at a time, like growing partials, then flush at the end."""
    words, ws, out = sentence.split(), WordStream(pending), ""
    for i in range(1, len(words) + 1):
        out += ws.update(" ".join(words[:i]))
    return out + ws.update(sentence, flush=True) + ws.finish()


class WordStreamTests(unittest.TestCase):
    def test_last_words_stay_pending(self):
        ws = WordStream(2)
        self.assertEqual(ws.update("hello world how"), "Hello")
        self.assertEqual(ws.update("hello world how are you"), " world how")
        self.assertEqual(ws.update("hello world how are you", flush=True), " are you")

    def test_shorter_guess_types_nothing(self):
        ws = WordStream(1)
        ws.update("one two three four")
        self.assertEqual(ws.update("one two"), "")

    def test_new_segment_starts_over_but_keeps_spacing(self):
        ws = WordStream(1)
        first = ws.update("good morning", flush=True)
        ws.end_segment()
        self.assertEqual(first + ws.update("how are you", flush=True), "Good morning how are you")

    def test_capital_i_and_fillers(self):
        self.assertEqual(dictate("i think um i'm done"), "I think I'm done")


class ListTests(unittest.TestCase):
    def test_spoken_list(self):
        self.assertEqual(dictate("note one open the door next point close the door"),
                         "1. Open the door\n2. Close the door")

    def test_numbered_notes_and_digits(self):
        self.assertEqual(dictate("note 1 milk note 2 eggs note three bread"), "1. Milk\n2. Eggs\n3. Bread")

    def test_text_before_list_goes_on_its_own_line(self):
        self.assertEqual(dictate("shopping note one milk next point eggs"), "Shopping\n1. Milk\n2. Eggs")

    def test_ordinary_sentences_are_left_alone(self):
        self.assertEqual(dictate("please take note 5 minutes ago"), "Please take note 5 minutes ago")
        self.assertEqual(dictate("what is the next point"), "What is the next point")
        self.assertEqual(dictate("take note"), "Take note")

    def test_list_must_start_at_one(self):
        self.assertEqual(dictate("note two apples"), "Note two apples")


class PolishTests(unittest.TestCase):
    def test_accepts_a_cleanup(self):
        self.assertEqual(accept_polish("hello world", "Hello, world."), "Hello, world.")

    def test_strips_wrapping_quotes_and_fences(self):
        self.assertEqual(accept_polish("hello there my friend", '"Hello there, my friend."'), "Hello there, my friend.")
        self.assertEqual(accept_polish("hello there", "```\nHello there.\n```"), "Hello there.")

    def test_rejects_unchanged_empty_or_runaway(self):
        self.assertIsNone(accept_polish("hello", "hello"))
        self.assertIsNone(accept_polish("hello", ""))
        self.assertIsNone(accept_polish("hello", None))
        self.assertIsNone(accept_polish("hi there", "Sure! Here is a much longer answer than anything you dictated, " * 3))
        self.assertIsNone(accept_polish("a fairly long dictated sentence about nothing much at all", "ok"))

    def test_keeps_numbered_lines(self):
        raw = "1. Open the door\n2. Close the door"
        self.assertEqual(accept_polish(raw, "1. Open the door.\n2. Close the door."), "1. Open the door.\n2. Close the door.")


if __name__ == "__main__":
    unittest.main()
