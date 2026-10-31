"""'play X on Spotify' plays straight away (no cards); 'search X on Spotify' shows the cards."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import spotify_island as si  # noqa: E402

ROW = {"title": "Forgive Me", "channel": "Jools Holland", "url": "spotify:track:" + "a" * 22,
       "track_id": "a" * 22, "thumbnail": "", "provider": "Spotify"}


class Intent(unittest.TestCase):
    def test_play_verbs(self):
        for cmd in ("play forgive me on spotify", "Please play forgive me on Spotify.",
                    "open spotify and play forgive me", "launch spotify play forgive me"):
            self.assertEqual(si.parse_intent(cmd), ("play", "forgive me"), cmd)

    def test_search_verbs(self):
        for cmd in ("search forgive me on spotify", "search spotify for forgive me", "find forgive me on spotify",
                    "look up forgive me on spotify", "show me forgive me on spotify", "open spotify and search forgive me"):
            self.assertEqual(si.parse_intent(cmd), ("search", "forgive me"), cmd)

    def test_not_spotify(self):
        self.assertEqual(si.parse_intent("play forgive me on youtube"), ("", ""))
        self.assertEqual(si.parse_intent("what time is it"), ("", ""))

    def test_intent_of_for_action_path(self):
        self.assertEqual(si.intent_of("play it might be painful on spotify"), "play")
        self.assertEqual(si.intent_of("search spotify for it might be painful"), "search")


class PlayQuery(unittest.TestCase):
    def test_plays_best_match_without_cards(self):
        with mock.patch.object(si, "search", return_value=[ROW, dict(ROW, title="Other")]) as s, \
             mock.patch.object(si, "play", return_value="Playing Forgive Me on Spotify.") as p, \
             mock.patch.object(si, "show") as show:
            self.assertEqual(si.play_query("forgive me"), "Playing Forgive Me on Spotify.")
            p.assert_called_once_with(ROW["url"])
            show.assert_not_called()
            self.assertIn(ROW["track_id"], si._last)   # the banner needs the title / art

    def test_nothing_found_returns_empty_so_caller_falls_back(self):
        with mock.patch.object(si, "search", return_value=[]), mock.patch.object(si, "play") as p:
            self.assertEqual(si.play_query("zzzz"), "")
            p.assert_not_called()

    def test_play_shows_banner_not_results(self):
        bridge = types.SimpleNamespace(push_search_dismiss=mock.Mock(), push_activity=mock.Mock(), push_search_results=mock.Mock())
        with mock.patch.dict(sys.modules, {"ui_bridge": bridge}), \
             mock.patch.object(si, "search", return_value=[ROW]), \
             mock.patch.object(si, "_can_verify", return_value=False), \
             mock.patch.object(os, "startfile", create=True) as start:
            reply = si.play_query("forgive me")
        start.assert_called_once_with("spotify:track:" + "a" * 22 + ":play")
        self.assertEqual(bridge.push_activity.call_args[0][0], "song_play")       # the "Playing ..." animation
        bridge.push_search_results.assert_not_called()                            # no result cards
        self.assertIn("Forgive Me", reply)


class SearchStillShowsCards(unittest.TestCase):
    def test_show_pushes_results(self):
        bridge = types.SimpleNamespace(push_search_dismiss=mock.Mock(), push_activity=mock.Mock(), push_search_results=mock.Mock())
        with mock.patch.dict(sys.modules, {"ui_bridge": bridge}), \
             mock.patch.object(si, "search", return_value=[ROW]), mock.patch.object(si, "more_by", return_value=[]):
            self.assertTrue(si.show("forgive me"))
        bridge.push_search_results.assert_called_once()
        bridge.push_activity.assert_not_called()


class _Clock:
    """A fake clock so the polling loops run instantly."""
    def __init__(self):
        self.t = 0.0
        self.monotonic = lambda: self.t
        self.sleep = lambda s: setattr(self, "t", self.t + s)


class ConfirmPlaying(unittest.TestCase):
    """Spotify sometimes only opens the album and highlights the row. play() must notice and recover."""

    def setUp(self):
        self.bridge = types.SimpleNamespace(push_search_dismiss=mock.Mock(), push_activity=mock.Mock(), push_search_results=mock.Mock())
        self.row = dict(ROW, title="hate that i made you love me", channel="Ariana Grande")
        si._last[ROW["track_id"]] = self.row
        self.clock = _Clock()
        self.start = mock.patch.object(os, "startfile", create=True).start()
        mock.patch.dict(sys.modules, {"ui_bridge": self.bridge}).start()
        mock.patch.object(si, "time", self.clock).start()
        mock.patch.object(si, "_can_verify", return_value=True).start()
        mock.patch.object(si, "_art_data_url", return_value=None).start()
        mock.patch.object(si, "_spotify_titles", return_value=["Spotify Premium"]).start()   # up, idle
        self.addCleanup(mock.patch.stopall)

    def _titles(self, *seq):
        """Window titles over time; the last one repeats."""
        it = iter(seq)
        last = [seq[-1]]
        def f():
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            return last[0]
        si._spotify_titles.side_effect = f

    def test_plays_first_time_no_retry(self):
        si._spotify_titles.return_value = ["Ariana Grande - hate that i made you love me"]
        reply = si.play(ROW["url"])
        self.assertEqual(self.start.call_count, 1)
        self.assertEqual(self.bridge.push_activity.call_args[0][0], "song_play")
        self.assertIn("Playing", reply)

    def test_not_started_resends_link_then_plays(self):
        # another song keeps playing for a while, then ours starts after the second link
        calls = {"n": 0}
        def titles():
            return ["Tuyu - It Might Be Painful"] if self.start.call_count < 2 else ["Ariana Grande - hate that i made you love me"]
        si._spotify_titles.side_effect = titles
        with mock.patch.object(si, "_double_click_row") as dbl:
            reply = si.play(ROW["url"])
        self.assertEqual(self.start.call_count, 2)
        dbl.assert_not_called()
        self.assertEqual(self.bridge.push_activity.call_args[0][0], "song_play")
        self.assertIn("Playing", reply)

    def test_falls_back_to_double_click(self):
        state = {"clicked": False}
        def click(*a):
            state["clicked"] = True
            return True
        si._spotify_titles.side_effect = lambda: ["Ariana Grande - hate that i made you love me"] if state["clicked"] else ["Spotify Premium"]
        with mock.patch.object(si, "_double_click_row", side_effect=click) as dbl:
            reply = si.play(ROW["url"])
        dbl.assert_called_once_with("hate that i made you love me", "Ariana Grande")
        self.assertEqual(self.bridge.push_activity.call_args[0][0], "song_play")
        self.assertIn("Playing", reply)

    def test_never_starts_is_honest_no_playing_banner(self):
        with mock.patch.object(si, "_double_click_row", return_value=True):
            reply = si.play(ROW["url"])
        kinds = [c[0][0] for c in self.bridge.push_activity.call_args_list]
        self.assertNotIn("song_play", kinds)
        self.assertIn("didn't start", reply)

    def test_cold_start_waits_for_spotify_without_resending(self):
        # Spotify is not running: no windows, then the app opens straight on our song
        self._titles([], [], [], ["Spotify Premium"], ["Ariana Grande - hate that i made you love me"])
        reply = si.play(ROW["url"])
        self.assertEqual(self.start.call_count, 1)
        self.assertIn("Playing", reply)

    def test_unreadable_titles_assume_it_worked(self):
        si._spotify_titles.return_value = None
        reply = si.play(ROW["url"])
        self.assertEqual(self.start.call_count, 1)
        self.assertEqual(self.bridge.push_activity.call_args[0][0], "song_play")
        self.assertIn("Playing", reply)


class SameSong(unittest.TestCase):
    def test_matching(self):
        self.assertTrue(si._same_song("hate that i made you love me", "Ariana Grande - hate that i made you love me"))
        self.assertTrue(si._same_song("Forgive Me (feat. Tom Jones)", "Jools Holland - Forgive Me"))
        self.assertTrue(si._same_song("It Might Be Painful, But I Still Love It", "Tuyu - It Might Be Painful, But I Still Love It"))
        self.assertFalse(si._same_song("hate that i made you love me", "Tuyu - It Might Be Painful, But I Still Love It"))
        self.assertFalse(si._same_song("kiss me", "Spotify Premium"))
        self.assertFalse(si._same_song("", "anything"))


if __name__ == "__main__":
    unittest.main()
