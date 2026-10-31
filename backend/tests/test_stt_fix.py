"""stt_fix: misheard names are repaired, normal sentences are left alone. Pure logic, no network."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stt_fix

T = {n: n for n in ("anogashi", "mosaab", "spotify", "discord", "valorant", "chrome")}


def test_names_are_repaired():
    assert stt_fix.correct_names("send a message to anagash on whatsapp", T) == "send a message to anogashi on whatsapp"
    assert stt_fix.correct_names("text mosab hello", T) == "text mosaab hello"
    assert stt_fix.correct_names("open spotifi", T) == "open spotify"
    assert stt_fix.correct_names("open valerant", T) == "open valorant"
    assert stt_fix.correct_names("tell me about chroma", T) == "tell me about chrome"


def test_normal_sentences_untouched():
    for s in ("open the door please", "what is the weather today", "volume up", "play some music", "set a timer for ten minutes"):
        assert stt_fix.correct_names(s, T) == s


def test_known_names_and_short_words_untouched():
    assert stt_fix.correct_names("open discord", T) == "open discord"
    assert stt_fix.correct_names("go to the app", T) == "go to the app"


def test_fix_never_raises_and_keeps_empty():
    assert stt_fix.fix("") == ""
    assert stt_fix.fix("   ") == "   "
