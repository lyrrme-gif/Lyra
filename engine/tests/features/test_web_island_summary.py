"""web_island.spoken_summary: with a knowledge card Lyra tells the first sentences about it; without one a short line."""
import unittest

from engine.features import web_island


class SpokenSummaryTests(unittest.TestCase):
    def test_card_is_read_out(self):
        card = {"title": "Tom Holland", "extract": "Thomas Stanley Holland is a British actor. He plays Spider-Man. Born in 1996."}
        self.assertEqual(web_island.spoken_summary("tom holland", card),
                         "Tom Holland. Thomas Stanley Holland is a British actor. He plays Spider-Man.")

    def test_a_shortened_half_sentence_is_dropped(self):
        card = {"title": "X", "extract": "X is a thing. It is known for being the highest-grossin\u2026"}
        self.assertEqual(web_island.spoken_summary("x", card), "X is a thing.")

    def test_no_card_keeps_the_short_line(self):
        self.assertEqual(web_island.spoken_summary("cats", None), "Searching the web for cats.")
        self.assertEqual(web_island.spoken_summary("cats", {"title": "Cats", "extract": ""}), "Searching the web for cats.")


if __name__ == "__main__":
    unittest.main()
