"""Registers the few tools the planner needs to pass text from step to step.

summarize_text / process_text use the language model chosen in Settings (like brain.py);
speak_text reads text aloud. All three are SAFE. Registered through tools/registry.py.
"""
from tools.base import ToolDefinition, ToolParameter, ToolCategory, ToolResult
from tools.registry import register_tool

P = ToolParameter


def _llm(system: str, user: str) -> str:
    import brain
    clients = []
    c, m = brain._get_client()
    if c:
        clients.append((c, m))
    fb = brain._fallback_client()
    if fb:
        clients.append(fb)
    if not clients:
        raise RuntimeError("no language model is set up (add an API key in Settings)")
    last = None
    for client, model in clients:
        try:
            r = client.chat.completions.create(
                model=model, temperature=0.2, timeout=40,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            text = (r.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(str(last) if last else "empty answer")


def _summarize(text, sentences=4):
    text = str(text or "").strip()
    if not text:
        return ToolResult(False, "There is nothing to summarize.")
    try:
        n = max(1, min(int(sentences or 4), 10))
        out = _llm(f"Summarize the text in at most {n} short sentences of plain prose: no bullet points, "
                   "no headings, no line breaks. Use only what the text says; do not add facts. "
                   "Reply with ONLY the summary.", text[:12000])
        return ToolResult(True, " ".join(out.split()), verified=True)
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"Couldn't summarize: {e}")


def _process(instruction, text):
    text = str(text or "").strip()
    if not text:
        return ToolResult(False, "There is no text to work on.")
    try:
        out = _llm("Do exactly what the instruction says with the text. Use only the text for facts. "
                   "Reply with ONLY the result, plain prose, no preamble.\n\nInstruction: " + str(instruction)[:300],
                   text[:12000])
        return ToolResult(True, out.strip(), verified=True)
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"Couldn't do that: {e}")


def _speak(text):
    text = " ".join(str(text or "").split())
    if not text:
        return ToolResult(False, "There is nothing to read out.")
    try:
        import ui
        ui.print_chat("lyra", text)
        import voice
        voice.speak(text if len(text) <= 900 else text[:900].rsplit(" ", 1)[0] + "...")
        return ToolResult(True, "Read it out loud.", verified=True)
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"Couldn't speak: {e}")


register_tool(ToolDefinition(
    "summarize_text", "Summarize text",
    "Condense a piece of text (for example the output of an earlier step) into a few sentences.",
    ToolCategory.SYSTEM,
    [P("text", "string", "The text to summarize"), P("sentences", "number", "Maximum sentences", False, 4)],
    "SAFE", _summarize))
register_tool(ToolDefinition(
    "process_text", "Process text",
    "Do something else with a piece of text: compare, list key points, rewrite, explain, extract facts.",
    ToolCategory.SYSTEM,
    [P("instruction", "string", "What to do with the text"), P("text", "string", "The text to work on")],
    "SAFE", _process))
register_tool(ToolDefinition(
    "speak_text", "Read aloud", "Read text out loud to the user.",
    ToolCategory.SYSTEM, [P("text", "string", "What to say")], "SAFE", _speak))
