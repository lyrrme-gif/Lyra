"""Task replies are Mochi sounds + a subtitle; real answers and questions to the user stay spoken."""
import unittest
from unittest import mock


import engine.voice.mochi_sounds as ms  # noqa: E402


class TaskReplies(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(ms, "mode", return_value="mochi")
        self.p.start()
        ms._info.clear()
        ms._recent.clear()

    def tearDown(self):
        self.p.stop()

    def test_stock_acknowledgements_keep_their_kinds(self):
        self.assertEqual(ms.plan("Opening Spotify, sir.")["kind"], "opened")
        self.assertEqual(ms.plan("Done.")["kind"], "done")

    def test_a_task_result_with_content_is_a_sound_with_a_subtitle(self):
        p = ms.plan("Timer set for 5 minutes. I'll tell you when it ends.")
        self.assertEqual((p["kind"], p["silent"]), ("info", True))
        self.assertGreaterEqual(p["ms"], 2800)
        self.assertTrue(ms.silences("Timer set for 5 minutes. I'll tell you when it ends."))

    def test_failures_are_errors(self):
        self.assertEqual(ms.plan("I couldn't find that file. Try another name.")["kind"], "error")

    def test_offers_and_questions_are_still_spoken(self):
        self.assertIsNone(ms.plan("Do you want me to delete it?"))
        self.assertIsNone(ms.plan("Which of the three files did you mean, the quarterly report or the invoice or the meeting notes from Tuesday?"))

    def test_long_or_multiline_text_is_still_spoken(self):
        self.assertIsNone(ms.plan("x" * 300))
        self.assertIsNone(ms.plan("line one\nline two"))

    def test_real_answers_are_spoken_unless_they_are_a_stock_acknowledgement(self):
        ms.mark_info("Paris is the capital of France.")
        self.assertIsNone(ms.plan("Paris is the capital of France."))
        self.assertFalse(ms.silences("Paris is the capital of France."))
        ms.mark_info("You're welcome, sir.")
        self.assertEqual(ms.plan("You're welcome, sir.")["kind"], "happy")

    def test_speak_mode_never_silences(self):
        with mock.patch.object(ms, "mode", return_value="speak"):
            self.assertIsNone(ms.plan("Volume is at 40%."))
            self.assertFalse(ms.silences("Volume is at 40%."))

    def test_speech_follows_the_decision_made_for_the_chat_line(self):
        ms.plan("Volume is at 40%.")
        self.assertTrue(ms.silences("Volume is at 40%."))
        with mock.patch.object(ms, "mode", return_value="both"):       # the setting changed in between
            self.assertFalse(ms.silences("Volume is at 40%."))


if __name__ == "__main__":
    unittest.main()
