"""Lyra notetaker - summary + to-dos from the TRANSCRIPT TEXT (never audio). Off by default.

Provider "local" = Ollama on this PC (nothing leaves it); "key" = the provider key set under AI
(the transcript text is sent to that provider). Settings shows this sentence next to the switch."""
from __future__ import annotations

import json
import re

from .store import mmss

CHUNK_CHARS = 12000

SYSTEM = ("You write meeting notes. Use only what is in the transcript; do not invent facts, names or "
          "dates. Reply with ONLY a JSON object: {\"summary\": \"3-6 short sentences\", "
          "\"todos\": [{\"text\": \"action item\", \"owner\": \"name or empty\"}]}. "
          "Reply in the language of the transcript. No to-dos? Use an empty list.")


def transcript_text(lines: list[dict]) -> str:
    return "\n".join(f"[{mmss(l.get('start', 0))}] {l.get('speaker', '?')}: {l.get('text', '')}" for l in lines)


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    parts, cur = [], ""
    for ln in text.split("\n"):
        if cur and len(cur) + len(ln) + 1 > size:
            parts.append(cur)
            cur = ""
        while len(ln) > size:                       # one absurdly long line
            parts.append(ln[:size])
            ln = ln[size:]
        cur += ln + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def parse(reply: str) -> dict:
    """Tolerant: JSON anywhere in the reply, else the whole reply becomes the summary."""
    reply = (reply or "").strip()
    m = re.search(r"\{.*\}", reply, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            todos = []
            for t in d.get("todos") or []:
                if isinstance(t, str) and t.strip():
                    todos.append({"text": t.strip(), "owner": ""})
                elif isinstance(t, dict) and str(t.get("text", "")).strip():
                    todos.append({"text": str(t["text"]).strip(), "owner": str(t.get("owner") or "").strip()})
            return {"text": str(d.get("summary") or "").strip(), "todos": todos}
        except ValueError:
            pass
    return {"text": reply, "todos": []}


def complete(provider: str, messages: list[dict]) -> str:
    """One chat call. local -> Ollama; key -> the configured cloud provider. Raises RuntimeError if unusable."""
    if provider == "local":
        from engine.brain import ollama_support
        client, model = ollama_support.client(primary=True)
        if not client:
            raise RuntimeError("No local model found. Turn on Ollama under AI, or choose your own key.")
    else:
        from engine.brain import brain
        client, model = brain._get_client()
        if not client:
            raise RuntimeError("No API key is set under AI.")
    r = client.chat.completions.create(model=model, messages=messages, temperature=0.2)
    return r.choices[0].message.content or ""


def summarize(lines: list[dict], provider: str, completer=complete) -> dict:
    """-> {text, todos, provider}. Long transcripts: notes per part, then one merge pass."""
    text = transcript_text(lines)
    if not text.strip():
        return {"text": "", "todos": [], "provider": provider}
    parts = chunks(text)

    def ask(body: str) -> dict:
        return parse(completer(provider, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}]))
    if len(parts) == 1:
        out = ask(parts[0])
    else:
        partial = [ask(p) for p in parts]
        merged = "\n\n".join(f"Part {i + 1} notes: {p['text']}\nTo-dos: " + "; ".join(t["text"] for t in p["todos"])
                             for i, p in enumerate(partial))
        out = ask("Combine these notes of one meeting into one summary and one deduplicated to-do list.\n\n" + merged)
        if not out["todos"]:
            seen, out["todos"] = set(), []
            for p in partial:
                for t in p["todos"]:
                    if t["text"].lower() not in seen:
                        seen.add(t["text"].lower())
                        out["todos"].append(t)
    out["provider"] = provider
    return out
