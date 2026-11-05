"""Tests for reply_style (Settings -> AI -> Reply length & style).
From the backend folder:  python -m unittest tests.test_reply_style -v   (offline, no LLM)"""
import unittest


import engine.brain.reply_style as rs  # noqa: E402
from engine.brain.persona import SYSTEM_INSTRUCTIONS  # noqa: E402


class PromptTests(unittest.TestCase):
    def test_base_prompt_has_fixed_length_wording(self):
        # The reason Balanced / Detailed never worked: this wording was always in the system prompt.
        self.assertIn(rs._BASE_INTRO, SYSTEM_INSTRUCTIONS)
        self.assertIn(rs._BASE_RULE, SYSTEM_INSTRUCTIONS)

    def test_each_style_replaces_it(self):
        for style in rs.STYLES:
            p = rs.system_prompt(SYSTEM_INSTRUCTIONS, style)
            self.assertNotIn(rs._BASE_INTRO, p, style)
            self.assertNotIn(rs._BASE_RULE, p, style)
        self.assertIn("one or two short sentences", rs.system_prompt(SYSTEM_INSTRUCTIONS, "short"))
        self.assertIn("fuller, well-explained", rs.system_prompt(SYSTEM_INSTRUCTIONS, "detailed"))
        self.assertIn("two to four sentences", rs.system_prompt(SYSTEM_INSTRUCTIONS, "balanced"))

    def test_note_and_limits(self):
        self.assertTrue(rs.user_note("short"))
        self.assertEqual(rs.user_note("balanced"), "")
        self.assertEqual(rs.max_sentences("short"), 2)
        self.assertEqual(rs.max_sentences("detailed"), 0)
        self.assertLess(rs.max_tokens("short"), rs.max_tokens("balanced"))
        self.assertLess(rs.max_tokens("balanced"), rs.max_tokens("detailed"))


class CutTests(unittest.TestCase):
    def test_cut(self):
        t = "Paris is the capital. It sits on the Seine. It has 2.1 million people."
        self.assertEqual(rs.cut_sentences(t, 2), "Paris is the capital. It sits on the Seine.")
        self.assertEqual(rs.cut_sentences("One only.", 2), "One only.")
        self.assertEqual(rs.cut_sentences(t, 0), t)

    def test_decimals_and_abbreviations_are_not_sentence_ends(self):
        self.assertEqual(rs.cut_sentences("It costs 3.5 dollars, e.g. a coffee. Yes. No more.", 2), "It costs 3.5 dollars, e.g. a coffee. Yes.")

    def test_stream_limiter_stops_after_second_sentence(self):
        lim = rs.SentenceLimiter(2)
        out = []
        for piece in ["Sure", " thing. It is ", "done! And here", " is a third. And a fourth."]:
            out.append(lim.feed(piece))
            if lim.done:
                break
        self.assertTrue(lim.done)
        self.assertEqual("".join(out), "Sure thing. It is done!")

    def test_stream_limiter_uncapped(self):
        lim = rs.SentenceLimiter(0)
        self.assertEqual(lim.feed("a. b. c. d."), "a. b. c. d.")
        self.assertFalse(lim.done)


if __name__ == "__main__":
    unittest.main(verbosity=2)
