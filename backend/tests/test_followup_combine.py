"""A greeting must not leave a 'pending request' that later speech gets glued onto
("hello uhm who is tom holland" became the search query). Pure logic: the helpers are pulled out of main.py's source."""
import ast, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "..", "main.py"), encoding="utf-8").read()
WANT = {"_AFFIRM_RE", "_NEGATE_RE", "_OFFER_RE", "_LEADING_FILLER_RE", "_GREETING_ONLY_RE", "_FRESH_REQUEST_RE",
        "_OPEN_OFFER_RE", "_offer_to_command", "_strip_leading_fillers", "_combine_pending", "_looks_like_question",
        "_infer_pending_kind"}
ns = {"re": re}
for node in ast.parse(SRC).body:
    name = getattr(node, "name", None) or (node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) else None)
    if name in WANT:
        exec(compile(ast.Module([node], []), "main.py", "exec"), ns)

combine, looks_q = ns["_combine_pending"], ns["_looks_like_question"]


def test_open_invitation_is_not_a_question():
    assert not looks_q("Hi again! How can I help?")
    assert not looks_q("Hello! What can I do for you?")
    assert looks_q("Spotify or YouTube?")


def test_new_request_replaces_a_stale_greeting():
    assert combine("hello", "uhm who is tom holland", "general", "Hi again! How can I help?") == "who is tom holland"
    assert combine("hello uhm", "who is tom holland", "general") == "who is tom holland"
    assert combine("hello", "search tom holland", "general") == "search tom holland"


def test_real_followups_still_work():
    assert combine("play despacito", "spotify", "play_where", "Spotify or YouTube?") == "play despacito on spotify"
    assert combine("search for", "tom holland", "general") == "search for tom holland"
    assert combine("what's the weather", "yes", "general") == "what's the weather"
