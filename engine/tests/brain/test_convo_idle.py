import sys, types

import engine.brain.convo_idle as ci


def setup_function(_):
    ci._last = None


def test_first_turn_never_clears():
    h = [{"user": "a", "lyra": "b"}]
    assert ci.reset_if_idle(h, now=1000.0) is False and len(h) == 1


def test_a_quick_follow_up_keeps_the_chat():
    h = []
    ci.reset_if_idle(h, now=100.0)
    h.append({"user": "hey", "lyra": "hi"})
    assert ci.reset_if_idle(h, now=160.0) is False and len(h) == 1


def test_a_long_quiet_spell_starts_a_fresh_chat():
    h = [{"user": "hey", "lyra": "hi"}]
    ci.reset_if_idle([], now=100.0)
    assert ci.reset_if_idle(h, now=100.0 + 181) is True and h == []


def test_the_quiet_spell_counts_from_the_end_of_the_last_turn():
    h = [{"user": "hey", "lyra": "hi"}]
    ci.reset_if_idle([], now=0.0)
    ci.touch(now=500.0)                                   # a slow answer finished at 500
    assert ci.reset_if_idle(h, now=600.0) is False and len(h) == 1


def test_zero_turns_the_reset_off(monkeypatch):
    monkeypatch.setitem(sys.modules, "engine.core.config", types.SimpleNamespace(CONVO_IDLE_RESET_SECONDS=0))
    h = [{"user": "hey", "lyra": "hi"}]
    ci.reset_if_idle([], now=0.0)
    assert ci.reset_if_idle(h, now=10_000.0) is False and len(h) == 1


def test_the_limit_comes_from_config(monkeypatch):
    monkeypatch.setitem(sys.modules, "engine.core.config", types.SimpleNamespace(CONVO_IDLE_RESET_SECONDS=30))
    h = [{"user": "hey", "lyra": "hi"}]
    ci.reset_if_idle([], now=0.0)
    assert ci.reset_if_idle(h, now=31.0) is True
