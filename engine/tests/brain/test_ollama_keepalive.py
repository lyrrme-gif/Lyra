"""Ollama keep-alive: the local model leaves video memory soon after the last answer instead of 5 minutes later."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import engine.brain.ollama_support as op


class Srv:
    def __init__(self):
        self.posts = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.posts.append((self.path, json.loads(self.rfile.read(n) or b"{}")))
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(b'{"done":true}')

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def wait(self, n, t=3.0):
        end = time.time() + t
        while len(self.posts) < n and time.time() < end:
            time.sleep(0.02)
        return self.posts


@pytest.fixture
def env(monkeypatch):
    srv = Srv()
    cfg = {}
    monkeypatch.setattr(op, "base_url", lambda: srv.url)
    monkeypatch.setattr(op, "_cfg", lambda k, d="": cfg.get(k, d))
    op._arming.clear()
    yield srv, cfg
    srv.httpd.shutdown()


def test_default_and_validation(env):
    _, cfg = env
    assert op.keep_alive_setting() == "1m"
    cfg["ollama_keep_alive"] = "30s"; assert op.keep_alive_setting() == "30s"
    cfg["ollama_keep_alive"] = "-1"; assert op.keep_alive_setting() == "-1"
    cfg["ollama_keep_alive"] = "banana"; assert op.keep_alive_setting() == "1m"


def test_clean_updates_keeps_good_drops_bad(env):
    assert op.clean_updates({"ollama_keep_alive": " 5M "})["ollama_keep_alive"] == "5m"
    assert "ollama_keep_alive" not in op.clean_updates({"ollama_keep_alive": "forever"})


def test_arm_sets_the_short_keep_alive_after_an_answer(env):
    srv, _ = env
    op.arm_keep_alive("llama3.2:3b")
    posts = srv.wait(1)
    assert posts == [("/api/generate", {"model": "llama3.2:3b", "keep_alive": "1m"})]


@pytest.mark.parametrize("setting,sent", [("30s", "30s"), ("-1", -1), ("0", 0)])
def test_arm_other_values(env, setting, sent):
    srv, cfg = env
    cfg["ollama_keep_alive"] = setting
    op.arm_keep_alive("m")
    assert srv.wait(1)[0][1]["keep_alive"] == sent


def test_default_five_minutes_means_no_extra_request(env):
    srv, cfg = env
    cfg["ollama_keep_alive"] = "5m"
    op.arm_keep_alive("m")
    time.sleep(0.3)
    assert srv.posts == []


def test_preload_loads_without_prompt(env):
    srv, cfg = env
    op.preload("m")
    assert srv.posts == [("/api/generate", {"model": "m", "keep_alive": "1m"})]
    cfg["ollama_keep_alive"] = "0"                       # unload-after-answer still pre-warms for the answer that follows
    op.preload("m")
    assert srv.posts[-1][1]["keep_alive"] == "1m"


class FakeComps:
    def __init__(self, out=None, boom=None):
        self.out, self.boom = out, boom

    def create(self, **kw):
        if self.boom:
            raise self.boom
        return self.out


class FakeStream:
    def __init__(self, chunks):
        self.it, self.closed = iter(chunks), False

    def __next__(self):
        return next(self.it)

    def close(self):
        self.closed = True

    extra = "kept"


def test_plain_call_result_untouched_and_keep_alive_set_after(env):
    srv, _ = env
    c = FakeComps(out={"choices": 1}); op._wrap_create(c)
    assert c.create(model="m", messages=[]) == {"choices": 1}
    assert srv.wait(1)[0][1]["model"] == "m"


def test_stream_chunks_pass_through_and_keep_alive_waits_for_the_end(env):
    srv, _ = env
    c = FakeComps(out=FakeStream(["a", "b", "c"])); op._wrap_create(c)
    s = c.create(model="m", stream=True)
    assert next(s) == "a" and next(s) == "b"
    time.sleep(0.25)
    assert srv.posts == [], "must not touch the model while it is still talking"
    assert list(s) == ["c"]
    assert srv.wait(1)[0][1]["model"] == "m"
    assert s.extra == "kept"                              # other attributes still reachable


def test_closing_a_stream_early_also_sets_it_once(env):
    srv, _ = env
    inner = FakeStream(["a", "b"]); c = FakeComps(out=inner); op._wrap_create(c)
    with c.create(model="m", stream=True) as s:
        next(s)
    assert inner.closed and len(srv.wait(1)) == 1
    time.sleep(0.2); assert len(srv.posts) == 1


def test_failed_call_still_sets_it_and_reraises(env):
    srv, _ = env
    c = FakeComps(boom=TimeoutError("slow")); op._wrap_create(c)
    with pytest.raises(TimeoutError):
        c.create(model="m")
    assert len(srv.wait(1)) == 1


def test_unreachable_ollama_never_raises(monkeypatch):
    monkeypatch.setattr(op, "base_url", lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(op, "_cfg", lambda k, d="": d)
    op._arming.clear()
    op.arm_keep_alive("m")
    time.sleep(0.4)
    assert op._arming == set()


def test_warm_uses_the_native_load(monkeypatch):
    import sys; import types; from engine.brain import llm_warm; from engine.brain import llm_store
    calls = []
    monkeypatch.setitem(sys.modules, "engine.brain.brain", types.SimpleNamespace(_get_client=lambda: (object(), "m")))
    monkeypatch.setattr(llm_store, "get", lambda: {"provider": "ollama"})
    monkeypatch.setattr(op, "preload", lambda m: calls.append(m))
    llm_warm._warm()
    assert calls == ["m"]


def test_real_client_is_wrapped_once_and_cached(monkeypatch):
    openai = pytest.importorskip("openai")
    monkeypatch.setattr(op, "openai_base", lambda: "http://127.0.0.1:9/v1")
    op._clients.clear()
    c1 = op._make_client(5.0)
    c2 = op._make_client(5.0)
    assert c1 is c2 and c1.chat.completions.create.__name__ == "create" and c1.chat.completions.create.__module__ == op.__name__
