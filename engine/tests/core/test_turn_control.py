"""Stopping speech (new message) and cancelling a turn (Esc), plus the very short LLM error texts."""
import threading

from engine.core import turn_control as tc


def test_stop_speech_moves_the_generation():
    g = tc.speech_gen()
    assert not tc.speech_stopped_since(g)
    tc.stop_speech()
    assert tc.speech_stopped_since(g)
    assert not tc.speech_stopped_since(tc.speech_gen())      # speech that starts AFTER the stop is fine


def test_cancel_turn_silences_only_turns_that_began_before():
    out = {}

    def old_turn(ready, go):
        tc.begin_turn()
        ready.set()
        go.wait(2)
        out["old"] = tc.turn_cancelled()

    ready, go = threading.Event(), threading.Event()
    t = threading.Thread(target=old_turn, args=(ready, go))
    t.start()
    ready.wait(2)
    tc.cancel_turn()
    go.set()
    t.join(2)
    assert out["old"] is True
    tc.begin_turn()                                          # a turn that begins after the Esc is not cancelled
    assert tc.turn_cancelled() is False


def test_threads_without_a_turn_are_never_cancelled():
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("v", tc.turn_cancelled()))
    t.start()
    t.join(2)
    assert out["v"] is False


def test_short_llm_errors():
    from engine.tools_bench import _stubs
    _stubs.install()
    brain = _stubs.import_with_stubs("engine.brain.brain")
    _short_llm_error = brain._short_llm_error

    class E(Exception):
        def __init__(self, msg, code=None):
            super().__init__(msg)
            self.status_code = code

    long402 = "Error code: 402 - This request requires more credits, or fewer max_tokens. You requested up to 700 tokens"
    cases = [
        (E(long402, 402), "Out of LLM credits."),
        (E("nope", 404), "That model isn't available."),
        (E("slow down", 429), "The LLM is busy. Try again soon."),
        (E("boom", 503), "The LLM service is down."),
        (E("bad key", 401), "LLM key rejected."),
        (E("Connection error."), "Can't reach the LLM."),
        (E("something odd"), "The LLM request failed."),
    ]
    for err, want in cases:
        got = _short_llm_error(err)
        assert got == want, (str(err), got)
        assert len(got) < 40
