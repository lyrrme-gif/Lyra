"""Word-timed lyrics (NetEase YRC): parsing only. The live service is not called here."""
from engine.features import lyrics

SAMPLE = """{"t":0,"c":[{"tx":"credit"}]}
[16210,3460](16210,670,0)I (16880,410,0)don't (17290,500,0)want (19670,0,0)Christ(19900,400,0)mas
[23000,2000](23000,500,0)There (23500,1500,0)is
not a lyric line
"""


def test_parse_yrc_lines_and_words():
    r = lyrics.parse_yrc(SAMPLE)
    assert [l["t"] for l in r] == [16210, 23000]
    assert r[0]["text"] == "I don't want Christmas"
    assert [w["text"] for w in r[0]["words"]] == ["I ", "don't ", "want ", "Christ", "mas"]
    assert r[0]["words"][3]["d"] == 1                      # a 0 ms word becomes 1 ms


def test_parse_yrc_ignores_garbage():
    assert lyrics.parse_yrc("") == []
    assert lyrics.parse_yrc("[1,2]x\n{}\nhello") == []


# ── NetEase answers with odd shapes: must never raise "'str' object has no attribute 'get'" ──
_YRC = "[1000,2000](1000,500,0)Hello (1500,500,0)world"
_HIT = {"result": {"songs": [{"id": 1, "name": "Taste", "artists": [{"name": "Sabrina Carpenter"}], "duration": 157000}]}}


def _lookup_with(search, lyric):
    old = lyrics._ne_json
    lyrics._ne_json = lambda url, data=None: search if "search" in url else lyric
    try:
        return lyrics._netease_lookup("Taste", "Sabrina Carpenter", 157, want_words=True)
    finally:
        lyrics._ne_json = old


def test_netease_odd_shapes_do_not_raise():
    assert _lookup_with("blocked", None) == {"found": False}                       # result is a string
    assert _lookup_with({"result": {"songs": ["abc"]}}, None) == {"found": False}   # songs are strings
    assert _lookup_with(_HIT, "oops") == {"found": False}                            # lyric reply is a string
    assert _lookup_with(_HIT, {"yrc": _YRC}) ["found"] is True                      # yrc given as a bare string


def test_netease_artists_as_strings_and_good_reply_keep_word_timing():
    odd = {"result": {"songs": [{"id": 1, "name": "Taste", "artists": ["Sabrina Carpenter"], "duration": 157000}]}}
    for search in (odd, _HIT):
        r = _lookup_with(search, {"yrc": {"lyric": _YRC}})
        assert r["found"] and r["word_timed"]
        assert [w["text"] for w in r["synced"][0]["words"]] == ["Hello ", "world"]
