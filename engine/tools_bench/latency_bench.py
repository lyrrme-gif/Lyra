"""Lyra latency bench (phase L).

Runs the REAL agent.route_and_run and brain.ask_brain (and brain._get_client, llm_warm._warm, fastpath)
against a fake LLM provider whose NETWORK is simulated. What is real: which code path a turn takes, how many
LLM calls it makes, how big each prompt is (the real 50-tool planner prompt), whether the client / connection
is reused, the 250 ms settle sleep after actions. What is simulated: connect time, round trip, prefill speed
and tokens per second (parameters below). So the numbers show the effect of the STRUCTURE of a turn; they are
not your provider's speed. For real numbers run:   python tools_bench/latency_bench.py --live

    python tools_bench/latency_bench.py                        # simulated, this tree
    python tools_bench/latency_bench.py --json after.json      # save
    python tools_bench/latency_bench.py --compare before.json after.json

To compare against an older tree, run the same file from that tree's backend folder (it adapts: it only uses
features that exist) and pass both JSON files to --compare.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(BACKEND))        # the folder that holds engine/
os.environ.setdefault("LYRA_DATA_DIR", tempfile.mkdtemp(prefix="lyra-bench-"))   # never touch real user data

# (command, kind, planner reply). kind: chat | action | multi
SCENARIOS = [
    ("tell me a joke", "chat", {"intent": "chat", "actions": [], "done": True, "reply": ""}),
    ("explain how rainbows form", "chat", {"intent": "chat", "actions": [], "done": True, "reply": ""}),
    ("open spotify", "action", {"intent": "action", "done": True, "reply": "",
                                "actions": [{"tool": "open_app", "args": {"name": "spotify"}}]}),
    ("open discord then open chrome", "multi", {"intent": "action", "done": True, "reply": "",
                                                "actions": [{"tool": "open_app", "args": {"name": "discord"}},
                                                            {"tool": "open_app", "args": {"name": "chrome"}}]}),
]
CHAT_ANSWER = ("Rainbows form when sunlight is refracted, reflected inside raindrops and refracted again, "
               "splitting white light into its colours. You need the sun behind you and rain in front.")


class Net:
    def __init__(self, a):
        self.connect_ms = a.connect_ms      # TCP + TLS handshake to the provider
        self.rtt_ms = a.rtt_ms              # request/response round trip
        self.queue_ms = a.queue_ms          # provider queue + time to first token floor
        self.prefill_ms_per_1k = a.prefill  # per 1000 prompt tokens
        self.tps = a.tps                    # output tokens per second


class Stats:
    def __init__(self):
        self.calls = 0
        self.prompt_chars = 0
        self.connects = 0


def _tokens(s):
    return max(1, len(s) // 4)


class FakeClient:
    """One object == one connection pool. `connected` flips after the first request (handshake paid once)."""

    def __init__(self, net, stats, shared=None):
        self.net, self.stats = net, stats
        self._pool = shared if shared is not None else {"connected": False}
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))
        self.models = types.SimpleNamespace(list=self._models_list)

    def with_options(self, **kw):                       # the real SDK copy shares the pool
        return FakeClient(self.net, self.stats, self._pool)

    def _handshake(self):
        if not self._pool["connected"]:
            self._pool["connected"] = True
            self.stats.connects += 1
            time.sleep(self.net.connect_ms / 1000)

    def _models_list(self, **kw):
        self._handshake()
        time.sleep(self.net.rtt_ms / 1000)

    def _create(self, model=None, messages=(), stream=False, **kw):
        self.stats.calls += 1
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        self.stats.prompt_chars += chars
        self._handshake()
        time.sleep((self.net.rtt_ms + self.net.queue_ms + self.net.prefill_ms_per_1k * _tokens("x" * chars) / 1000) / 1000)
        planner = "Lyra's planner" in str(messages[0].get("content", ""))
        if planner:
            text = json.dumps(CURRENT["plan"])
            time.sleep(_tokens(text) / self.net.tps)
            return _resp(text)
        if stream:
            return self._stream(CHAT_ANSWER)
        time.sleep(_tokens(CHAT_ANSWER) / self.net.tps)
        return _resp(CHAT_ANSWER)

    def _stream(self, text):
        words = text.split(" ")
        for i, w in enumerate(words):
            time.sleep(1 / self.net.tps * 1.3)
            yield types.SimpleNamespace(choices=[types.SimpleNamespace(
                delta=types.SimpleNamespace(content=w + (" " if i < len(words) - 1 else "")))])


def _resp(text):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))], usage=None)


CURRENT = {"plan": None}


def _load(live):
    from engine.tools_bench import _stubs
    _stubs.install()
    if live:                                    # real packages are expected; stub only what is missing
        pass
    for m in ("engine.brain.brain", "engine.brain.agent", "engine.brain.fastpath", "engine.brain.llm_warm", "engine.core.perf"):
        _stubs.import_with_stubs(m)
    from engine.brain import agent; from engine.brain import brain; from engine.brain import fastpath; from engine.brain import llm_warm; from engine.core import perf      # noqa
    return agent, brain, fastpath, llm_warm, perf


def run(args):
    agent, brain, fastpath, llm_warm, perf = _load(args.live)
    net, stats = Net(args), Stats()
    from engine.core import config

    if not args.live:
        brain.OpenAI = lambda **kw: FakeClient(net, stats)          # the REAL _get_client decides how often
        from engine.brain import llm_store
        llm_store.get = lambda: {"provider": "groq", "groq_api_key": "bench-key", "groq_model": "bench-model"}
        brain._CLIENT_CACHE.clear() if hasattr(brain, "_CLIENT_CACHE") else None
        agent._execute_step = lambda step: (time.sleep(0.04), types.SimpleNamespace(
            success=True, verified=None, message="Done."))[1]
        agent._asked_for = lambda step, command: True
        agent._permissions.check_permission = lambda *a, **k: (True, "")
        agent._CTX.clear()
    pregate = getattr(fastpath, "is_plain_chat", None) if getattr(config, "CHAT_PREGATE", False) else None

    rows = []
    scenarios = [x for x in SCENARIOS if x[1] == "chat"] if args.live else SCENARIOS   # --live never runs real actions
    for command, kind, plan in scenarios:
        samples = []
        for _ in range(args.runs):
            CURRENT["plan"] = plan
            stats.calls = stats.prompt_chars = stats.connects = 0
            if hasattr(brain, "_CLIENT_CACHE"):
                brain._CLIENT_CACHE.clear()
            if args.prewarm:
                llm_warm._warm()                      # real app: runs while the user is still speaking
            stats.connects = 0                        # warm-up happens during speech: not part of the turn
            first = {}
            t0 = time.monotonic()

            def sink(piece, first=first, t0=t0):
                first.setdefault("t", time.monotonic() - t0)

            if pregate and pregate(command):
                reply, route = None, "chat"
            else:
                reply, route = agent.route_and_run(command, None, force=True)
            if reply:
                out = time.monotonic() - t0
                done = out
            elif route in ("chat", "research"):
                brain.ask_brain(command, [], intent=route, on_text=sink)
                out = first.get("t", time.monotonic() - t0)
                done = time.monotonic() - t0
            else:
                out = done = time.monotonic() - t0
            samples.append((out, done, stats.calls, stats.prompt_chars, stats.connects))
        n = len(samples)
        rows.append({"command": command, "kind": kind,
                     "first_output_ms": round(sum(s[0] for s in samples) / n * 1000),
                     "done_ms": round(sum(s[1] for s in samples) / n * 1000),
                     "llm_calls": samples[-1][2], "prompt_chars": samples[-1][3], "new_connections": samples[-1][4]})
    return {"mode": "live" if args.live else "simulated", "params": vars(args), "rows": rows,
            "has_client_cache": hasattr(brain, "_CLIENT_CACHE"), "has_pregate": pregate is not None}


def show(res):
    p = res["params"]
    print(f"\n[{res['mode']}] client cache: {res['has_client_cache']} | chat pre-gate: {res['has_pregate']}")
    if res["mode"] == "simulated":
        print(f"SIMULATED NETWORK: connect {p['connect_ms']} ms, rtt {p['rtt_ms']} ms, queue {p['queue_ms']} ms, "
              f"prefill {p['prefill']} ms/1k tok, {p['tps']} tok/s. Calls/prompts/paths are REAL.")
    print(f"{'turn':34}{'first output':>14}{'done':>9}{'LLM calls':>11}{'prompt chars':>14}{'new TLS':>9}")
    for r in res["rows"]:
        print(f"{r['command'][:33]:34}{r['first_output_ms']:>11} ms{r['done_ms']:>6} ms{r['llm_calls']:>11}"
              f"{r['prompt_chars']:>14}{r['new_connections']:>9}")


def compare(a_path, b_path):
    a, b = json.load(open(a_path)), json.load(open(b_path))
    print(f"\nBEFORE = {a_path}   AFTER = {b_path}   (both {a['mode']})")
    print(f"{'turn':34}{'before':>10}{'after':>10}{'saved':>10}   calls  TLS")
    for x, y in zip(a["rows"], b["rows"]):
        d = x["first_output_ms"] - y["first_output_ms"]
        print(f"{x['command'][:33]:34}{x['first_output_ms']:>8}ms{y['first_output_ms']:>8}ms{d:>8}ms   "
              f"{x['llm_calls']}->{y['llm_calls']}  {x['new_connections']}->{y['new_connections']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use the real provider from Settings (your key, real network)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--no-prewarm", dest="prewarm", action="store_false")
    ap.add_argument("--connect-ms", type=int, default=220)
    ap.add_argument("--rtt-ms", type=int, default=90)
    ap.add_argument("--queue-ms", type=int, default=150)
    ap.add_argument("--prefill", type=int, default=60, help="ms per 1000 prompt tokens")
    ap.add_argument("--tps", type=int, default=250)
    ap.add_argument("--json")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = ap.parse_args()
    if args.compare:
        return compare(*args.compare)
    res = run(args)
    show(res)
    if args.json:
        json.dump(res, open(args.json, "w"), indent=1)


if __name__ == "__main__":
    main()
