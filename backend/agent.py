"""Lyra — multi-step agent.

Runs a small plan -> act -> observe loop. The model proposes a few tool
calls, Lyra executes them (checking permissions first), feeds the results
back, and the model either finishes with a short spoken reply or adjusts
(e.g. after a failed step). Bounded by MAX_ROUNDS / MAX_ACTIONS.

Handles both explicit chains ("open chrome, then go to youtube") and goal
phrasing ("get me ready for work", "set things up so I can study").
Exact shortcuts and direct commands are still matched earlier in main.py.
"""

from __future__ import annotations

import json
import re
import time

import ui
import perf
from tools.registry import ensure_registered_tools, call_tool, get_registry
from config import GROQ_API_KEY, GROQ_MODEL_NAME
from persona import AGENT_REPLY_STYLE
from permissions import PermissionManager

log = ui.log
trace = ui.trace

_permissions = PermissionManager()

# Legacy direct-Groq client, used only if the shared provider client
# (brain._get_client, driven by Settings / llm_keys.json) is unavailable.
_client = None
try:
    from openai import OpenAI
    if GROQ_API_KEY:
        _client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY, max_retries=0, timeout=15.0)
except Exception:
    _client = None

MAX_ROUNDS = 4     # plan/observe cycles per request
MAX_ACTIONS = 12   # total tool calls per request

# Explicit chains
MULTI_STEP_HINT = re.compile(
    r"\b(then|after\s+that|and\s+then|next|finally|also)\b",
    re.I,
)
# Goal-style requests that need several actions to satisfy
GOAL_HINT = re.compile(
    r"\b(get\s+(?:me\s+)?ready|prepare\s+(?:my|the)|set\s+(?:things|everything)\s+up|"
    r"set\s+up\s+(?:my|the)|start\s+my|open\s+my\s+[\w\s]{0,30}?setup)\b",
    re.I,
)

_SYSTEM = """You are Lyra's planner. Work out what the user wants and carry it out with tools.
Available tools are injected from the central Tool Registry at runtime. Use only registered tool ids and their declared parameters.

Reply with ONLY one JSON object, no markdown:
{"intent": "action"|"chat"|"research"|"command", "actions": [{"tool": "...", "args": {...}}], "done": true|false, "reply": "short spoken reply"}

"intent" says what kind of request this is:
- "action": it can be done with the tools above. Fill "actions".
- "chat": conversation, greetings, opinions, small talk, anything needing no tools. Leave "actions" empty and "reply" empty; Lyra's chat brain answers.
- "research": a factual or current-events question that needs knowledge or a web search. Leave "actions" and "reply" empty.
- "command": something to do on the PC that these tools can NOT do: file create/delete/rename, shutdown/restart/cancel, screenshots, remember/forget facts, system status, editing Lyra's own code, managing shortcuts. Leave "actions" and "reply" empty; Lyra's local command handlers take over.
The request comes from speech-to-text, so fix obvious mishearings ("open spot a fi" = Spotify).

Rules:
- Treat natural-language desktop requests as actionable whenever a registered tool can perform them.
- You can combine app/window control, mouse clicks, typing, scrolling, files, media, volume and web search into one plan.
- Never bypass the permission layer for interactive or destructive actions.
- Use at most 6 actions per turn and only the tools listed. Never invent a tool; if something
  can't be done with these tools (e.g. closing a specific app), say so in "reply".
- A web search is for looking something up, never a way to DO what the user asked (set an alarm,
  change a setting, send something). If no tool can do it, intent "action", no actions, and
  "reply": "I can't do that yet."
- After your actions run you will be shown the results. Set "done": false only if you need to
  react to a failure or check an outcome; otherwise set "done": true.
- When "done" is true, "reply" is what Lyra says out loud. {style}
Example: {"intent": "action", "actions": [{"tool":"open_app","args":{"app_name":"code"}},{"tool":"open_spotify","args":{"query":"focus"}}], "done": true, "reply": "VS Code and some focus music are up."}
""".replace("{style}", AGENT_REPLY_STYLE)


def looks_multi_step(command: str) -> bool:
    if not command or len(command.split()) < 4:
        return False
    return bool(MULTI_STEP_HINT.search(command) or GOAL_HINT.search(command))


def _asked_for(step: dict, command: str) -> bool:
    """The planner may start a saved shortcut or an app only if the request names it (or is a
    goal like "get me ready"). Otherwise a vague request such as "open all those searches" can
    make the model run shortcuts (even "Good night", which locks the PC) or launch a random program."""
    tool = (step.get("tool") or "").strip().lower()
    if tool not in ("run_shortcut", "open_app", "open_local_app"):
        return True
    if GOAL_HINT.search(command or ""):
        return True
    args = step.get("args") or {}
    name = str(args.get("name") or args.get("app_name") or args.get("app") or "").lower()
    words = re.findall(r"[a-z0-9]+", name)
    text = (command or "").lower()
    return bool(words) and any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _execute_step(step: dict):
    """Execute only through the central registry; returns ToolResult."""
    ensure_registered_tools()
    tool=(step.get("tool") or "").strip().lower()
    args=step.get("args") or {}
    # Legacy aliases remain supported.
    aliases={"open_local_app":"open_app","open_url":"open_url","media_prev":"media_previous","media_next":"media_next"}
    tool=aliases.get(tool,tool)
    if tool == "run_shortcut":
        args={"name":args.get("name","")}
    return call_tool(tool, **args)


_CTX: dict = {}   # per-run scratch (engine_context for tools that need it)


def _run_saved_shortcut(name: str, engine_context=None) -> str:
    import shortcuts
    want = (name or "").strip().lower()
    for item in shortcuts._manager.list_shortcuts():
        if want in (item["id"].lower(), item["name"].lower()) or want in [t.lower() for t in item["triggers"]]:
            sc = shortcuts._manager.get_shortcut(item["id"])
            ok, msg = shortcuts._executor.execute_shortcut(item["name"], sc.get("actions", []), engine_context)
            return msg if ok else f"Failed: {msg}"
    return f"No shortcut named '{name}'"


def _llm():
    """Shared provider client (Settings / llm_keys.json), else legacy Groq."""
    try:
        import brain
        client, model = brain._get_client()
        if client:
            return client, model
    except Exception as e:
        log.warning("agent: provider client unavailable: %s", e)
    if _client:
        return _client, GROQ_MODEL_NAME
    return None, None


def _context_block() -> str:
    parts = []
    try:
        import shortcuts
        items = shortcuts._manager.list_shortcuts()
        if items:
            parts.append("Saved shortcuts: " + "; ".join(
                f"{i['name']} (say: {', '.join(i['triggers'][:3])})" for i in items))
    except Exception:
        pass
    try:
        import memory
        facts = memory.all_facts()
        if facts:
            parts.append("Known about the user: " + "; ".join(f"{k}: {v}" for k, v in facts))
    except Exception:
        pass
    return "\n".join(parts)


def _parse_plan(raw: str) -> dict | None:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I | re.M).strip()
    obj = None
    # Try whichever bracket opens first, so a bare [..] array isn't mistaken
    # for the {..} object inside it.
    first_obj, first_arr = raw.find("{"), raw.find("[")
    patterns = [r"\{.*\}", r"\[.*\]"]
    if first_arr != -1 and (first_obj == -1 or first_arr < first_obj):
        patterns.reverse()
    for pattern in patterns:
        m = re.search(pattern, raw, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                break
            except Exception:
                continue
    if isinstance(obj, list):               # legacy bare-array plans
        obj = {"actions": obj, "done": True}
    if not isinstance(obj, dict):
        return None
    actions = obj.get("actions")
    obj["actions"] = [a for a in actions if isinstance(a, dict)] if isinstance(actions, list) else []
    return obj


def _describe(step: dict) -> str:
    tool, args = step.get("tool", ""), step.get("args") or {}
    if tool == "close_window": return "close the active window"
    if tool == "type_text": return f"type \"{str(args.get('text',''))[:40]}\" into the active window"
    if tool == "click_on_screen": return f"click {str(args.get('description','the target'))[:60]} on the screen"
    if tool in ("double_click_on_screen","right_click_on_screen"): return f"{tool.replace('_on_screen','').replace('_','-')} {str(args.get('description','the target'))[:60]} on the screen"
    if tool == "switch_window": return f"switch to the window {str(args.get('title','specified'))[:60]}"
    return f"run {tool}"


def run_agent(command: str, engine_context=None) -> str | None:
    """Back-compat wrapper: only handles multi-step looking requests and
    returns the reply, or None if the agent shouldn't handle it."""
    if not looks_multi_step(command):
        return None
    return route_and_run(command, engine_context, force=False)[0]


def route_and_run(command: str, engine_context=None, force: bool = False) -> tuple[str | None, str]:
    """Understand `command` with the LLM, then act on it.

    Returns (reply, route):
      (text, "handled")       tools ran / the agent answered; speak `text`
      (None, "chat"|"research") not an action; let the chat brain answer
      (None, "command")       local command handlers should take it (or the
                              LLM was unavailable / unparseable)
    `force=False` keeps the old behaviour of only engaging for multi-step
    looking requests.
    """
    ensure_registered_tools()
    if not force and not looks_multi_step(command):
        return None, "command"

    if force:
        # Pure local answers (date/time/math) never need a planner call.
        try:
            from knowledge import try_knowledge_shortcut
            if try_knowledge_shortcut(command):
                return None, "chat"
        except Exception:
            pass

    client, model = _llm()
    if not client:
        return None, "command"

    ui.set_state("thinking")
    trace("path: agent loop" + (" (llm-first)" if force else ""))
    _CTX["engine_context"] = engine_context

    ctx = _context_block()
    system = _SYSTEM + "\n\nAvailable registered tools (id(parameters): description; ? = optional):\n" + get_registry().get_tools_compact()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": (ctx + "\n\n" if ctx else "") + "Request: " + command},
    ]

    log_lines: list[str] = []
    final_reply = ""
    n_actions = 0
    cancelled = False
    skipped = 0

    for round_no in range(MAX_ROUNDS):
        try:
            perf.count("llm_calls")
            perf.count("llm_prompt_chars", sum(len(str(m.get("content", ""))) for m in messages))
            with perf.span("llm_route"):
                resp = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=500, temperature=0.2,
                )
            raw = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            log.warning("agent plan failed: %s", e)
            break
        plan = _parse_plan(raw)
        if plan is None:
            log.warning("agent: unparseable plan: %r", raw[:200])
            break

        if round_no == 0 and not plan["actions"]:
            intent = str(plan.get("intent") or "").strip().lower()
            if intent in ("chat", "research", "command"):
                _CTX.pop("engine_context", None)
                trace(f"agent: routed as {intent}")
                return None, intent

        results = []
        for step in plan["actions"][:6]:
            if n_actions >= MAX_ACTIONS:
                results.append("Skipped: action limit reached")
                break
            tool = (step.get("tool") or "").strip().lower()
            if not _asked_for(step, command):
                trace(f"agent: skipped {tool} {step.get('args')} (not named in the request)")
                results.append(f"{tool}: skipped (the user did not ask for it)")
                skipped += 1
                continue
            context = dict(engine_context or {})
            # Typed chat (text mode, or the island UI while in voice mode) has no mic to
            # answer a confirmation, so queue the step and let the typed "yes" run it.
            _typed = __import__("ui").INPUT_MODE == "text" or not (
                engine_context and "recognizer" in engine_context and "source" in engine_context)
            if _typed:
                context["confirm_callback"] = lambda step=step: _execute_step(step)
            allowed, why = _permissions.check_permission(tool, _describe(step), context)
            if not allowed:
                results.append(f"{tool}: skipped ({why})")
                # A queued typed confirmation is not a cancellation; return the prompt.
                cancelled = why != "Awaiting typed confirmation."
                if not cancelled:
                    return f"Please confirm: {_describe(step)}", "handled"
                break
            with perf.span("action"):
                result = _execute_step(step)
            n_actions += 1
            status = "verified" if getattr(result, "verified", None) is True else ("executed" if result.success else "failed")
            msg = result.message
            results.append(f"{tool}: [{status}] {msg}")
            # Keep execution metadata in the internal observation sent back to
            # the planner, but show the user only the tool's actual answer.
            log_lines.append(msg)
            # Let the app settle before the NEXT step; after the last step of a finished plan there is
            # nothing left to wait for (this was a fixed 250 ms added to every single action).
            if not (plan.get("done", True) and step is plan["actions"][:6][-1]):
                time.sleep(0.25)

        final_reply = (plan.get("reply") or "").strip()
        if cancelled or plan.get("done", True) or not plan["actions"] or n_actions >= MAX_ACTIONS:
            break
        # Observe: show the model what happened and let it react
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": "Results:\n" + "\n".join(results)
                         + "\nContinue or finish (same JSON format)."})

    _CTX.pop("engine_context", None)

    if cancelled:
        return "Okay, stopping there.", "handled"
    if log_lines:
        return " ".join(log_lines), "handled"
    if final_reply and not log_lines and not skipped:
        return final_reply, "handled"
    return None, "command"
