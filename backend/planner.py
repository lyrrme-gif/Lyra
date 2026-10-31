"""Lyra - multi-step agent with a visible plan.

"Research solar panels, summarize it and save a note" is turned into at most 8 steps that use the
tools in the central registry (tools/registry.py). The plan is shown on the island BEFORE anything
runs, a progress chip follows the current step, and "stop" aborts at any moment.

agent.py is untouched. It runs a tool-call loop whose arguments are fixed when the plan is made, so
it cannot hand the text of one step to the next ("summarize the search, then save THAT"). Here a
step can use the output of an earlier step with a placeholder: {{1}} (or {{step1}}, {{prev}}).

Safety (permissions.py rules are applied to every step):
  SAFE     runs.
  CONFIRM  Lyra asks first ("yes"/"no", typed or spoken) and waits; "no" stops the plan.
  BLOCKED  never runs (it is not even offered to the planner, and is refused again at run time).

Tools of other steps (file search, study, translate...) are NOT imported here. They register
themselves with tools.registry.register_tool() and show up in the planner's tool list automatically.

Entry point: handle_turn(command), called by main.process_command_chain before the LLM-first agent.
It returns None (not for us), or the sentence Lyra should say. The plan itself runs in a background
thread, so the main loop keeps listening and can hear "stop".
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field

MAX_STEPS = 8
AUTO_SAVE_RESULT = True      # save the final text as a note when the plan did not already do it
START_DELAY_S = 1.0          # let the spoken plan summary start before step 1
CONFIRM_TIMEOUT_S = 60
STEP_TIMEOUT_S = 90
_OUT_CAP = 8000              # characters of one step's output kept for later steps

# --------------------------------------------------------------------------------------------
# When does a request get a plan?  (Strict on purpose: simple commands keep their fast paths.)
# --------------------------------------------------------------------------------------------

_CUES = {
    "gather": r"\b(?:research|look\s+up|search(?:\s+for)?|find\s+out|google|read\s+up\s+on|dig\s+into|investigate)\b",
    "process": r"\b(?:summari[sz]e|summary|compare|explain|list\s+the|extract|pick\s+out|rewrite|shorten|simplify)\b",
    "store": r"\b(?:save|store|write\s+(?:it|that|them|this)?\s*down|(?:take|make|add)\s+(?:a\s+)?note|note\s+(?:it|that|this)\s+down)\b",
    "deliver": r"\b(?:read\s+(?:it|that|them|this|the\s+\w+)?\s*(?:back|out|aloud)|say\s+(?:it|that)\s+(?:back|out\s+loud)|tell\s+me\s+(?:what|how|about)|speak\s+it)\b",
}
_EXPLICIT = re.compile(r"\b(?:make|create|build|draw\s+up|give\s+me)\s+(?:a\s+)?plan\s+(?:to|for|and)\b|\bplan\s+(?:this|it)\s+out\b", re.I)
# Things other handlers own: never taken by the planner.
_NOT_OURS = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+)?(?:remind\s+me|(?:take|make|add|write|save)\s+(?:a\s+)?note\b|note\s+(?:that|:)|"
    r"add\s+.+\s+to\s+(?:my|the)\s+.+list|play\b|open\b|call\b|send\b|set\s+(?:a\s+)?(?:timer|alarm)|translate\b)", re.I)

ABORT = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+)?"
    r"(?:stop|cancel|abort|halt|forget\s+it|never\s*mind)"
    r"(?:\s+(?:the\s+)?(?:plan|that|it|this|everything|now|all|please))*\s*[.!]*$", re.I)
_YES = re.compile(r"^(?:yes|y|yeah|yep|yup|sure|ok|okay|confirm|go\s+ahead|do\s+it|proceed)\b", re.I)
_NO = re.compile(r"^(?:no|n|nope|nah|don'?t|do\s+not|cancel|stop|skip)\b", re.I)


def should_plan(command: str) -> bool:
    text = re.sub(r"\s+", " ", (command or "").strip())
    if len(text.split()) < 5 or _NOT_OURS.match(text):
        return False
    if _EXPLICIT.search(text):
        return True
    hits = {k for k, pat in _CUES.items() if re.search(pat, text, re.I)}
    return len(hits) >= 2 and bool(hits & {"gather", "process"})


# --------------------------------------------------------------------------------------------
# State of the run that is going on (one at a time)
# --------------------------------------------------------------------------------------------

@dataclass
class Step:
    n: int
    tool: str
    args: dict
    title: str
    status: str = "waiting"      # waiting | running | done | failed | skipped | blocked | stopped
    output: str = ""


@dataclass
class Run:
    request: str
    title: str
    steps: list[Step]
    abort: threading.Event = field(default_factory=threading.Event)
    answered: threading.Event = field(default_factory=threading.Event)
    decision: bool | None = None
    waiting_for_confirm: bool = False
    finished: threading.Event = field(default_factory=threading.Event)


_lock = threading.RLock()
_run: Run | None = None


def is_running() -> bool:
    r = _run
    return r is not None and not r.finished.is_set()


def current_steps() -> list[Step]:
    return list(_run.steps) if _run else []


def wait_finished(timeout: float = 30.0) -> bool:
    """Used by tests / tools: block until the current run is over."""
    r = _run
    return True if r is None else r.finished.wait(timeout)


# --------------------------------------------------------------------------------------------
# Small helpers around the rest of Lyra (all imports lazy)
# --------------------------------------------------------------------------------------------

def _feature_on() -> bool:
    try:
        import settings_store
        return settings_store.feature_on("planner")
    except Exception:
        return True


def _registry():
    from tools.registry import get_registry
    return get_registry()


def _level(tool_id: str) -> str:
    """SAFE | CONFIRM | BLOCKED, by the same rules permissions.py uses."""
    try:
        t = _registry().get_tool(tool_id)
        if t is not None and str(getattr(t, "permission_level", "")).strip().upper() == "BLOCKED":
            return "BLOCKED"
        from permissions import PermissionManager
        rule = PermissionManager()._rules.get(tool_id)
        if rule is not None:
            return "BLOCKED" if str(rule.value).lower() == "blocked" else "SAFE"
        if t is not None and str(getattr(t, "permission_level", "")).strip().upper() == "SAFE":
            return "SAFE"
    except Exception:
        pass
    return "SAFE"


def _say(text: str) -> None:
    """Print in the chat and speak (from the runner thread, like proactive.py does)."""
    try:
        import ui
        ui.print_chat("lyra", text)
    except Exception:
        pass
    try:
        import voice
        voice.speak(text)
    except Exception:
        pass


def _stop_speaking() -> None:
    try:
        import pygame
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass


def _chip(title: str, phase: str = "start", subtitle: str | None = None, ms: int = 20000) -> None:
    """The progress chip: the existing activity pill on the island."""
    try:
        import ui_bridge
        ui_bridge.push_activity("plan", title, phase=phase, subtitle=subtitle, duration_ms=ms)
    except Exception:
        pass


def _show_plan(run: Run) -> None:
    """The plan on the island, before anything runs: the existing card, steps in its text."""
    try:
        import ui_bridge
        steps = " \u00b7 ".join(f"{s.n}. {s.title}" for s in run.steps)
        card = {"title": f"Plan \u00b7 {len(run.steps)} steps", "subtitle": run.title[:100],
                "extract": steps[:300], "socials": []}
        ui_bridge.push_search_results("Plan", [], "plan", card=card)
    except Exception:
        pass


def _short(text: str, n: int = 300) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    m = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:m + 1] if m > n * 0.4 else cut.rsplit(" ", 1)[0] + "..."


# --------------------------------------------------------------------------------------------
# Making the plan
# --------------------------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{\{\s*(?:step\s*)?(\d+|prev)\s*\}\}", re.I)

_SYSTEM = """You are Lyra's planner. Turn the user's request into a short plan that uses ONLY the tools listed below.

Reply with ONLY one JSON object, no markdown:
{"title": "<what this plan achieves, max 60 characters>",
 "steps": [{"tool": "<tool id>", "args": {...}, "title": "<what this step does, max 28 characters>"}]}

Rules:
- At most 8 steps, in the order they must run. Use as few as the request needs.
- Use only the listed tool ids and their declared parameters. Never invent a tool.
- A step can use the text produced by an earlier step: write {{1}} for the output of step 1, {{2}} for step 2 ({{prev}} = the step just before). Use it inside string arguments, e.g. {"text": "{{2}}"}.
- To read something back out loud use speak_text; to save a note use add_note; to condense text use summarize_text.
- If the request cannot be done with these tools, or is just one simple action, reply {"title": "", "steps": []}.
"""


def _tool_lines() -> str:
    lines = []
    for t in _registry().list_tools():
        if str(getattr(t, "permission_level", "")).strip().upper() == "BLOCKED" or _level(t.id) == "BLOCKED":
            continue                                   # blocked tools are never offered
        params = ", ".join(f"{p.name}:{p.type}{'' if p.required else '?'}" for p in t.parameters)
        lines.append(f"- {t.id}({params}): {t.description}"
                     + (" [asks the user first]" if _level(t.id) == "CONFIRM" else ""))
    return "\n".join(lines)


def _complete(system: str, user: str) -> str:
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
                model=model, temperature=0.1, timeout=30,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            text = (r.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(str(last) if last else "empty answer")


def _parse(raw: str) -> dict | None:
    raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    for cand in (raw, m.group(0) if m else ""):
        try:
            d = json.loads(cand)
        except Exception:
            continue
        if isinstance(d, dict):
            return d
    return None


def _validate(d: dict) -> tuple[str, list[Step]] | None:
    """The plan if it can really run with the registered tools, else None."""
    raw_steps = d.get("steps")
    if not isinstance(raw_steps, list) or not (1 <= len(raw_steps) <= MAX_STEPS):
        return None
    reg = _registry()
    steps: list[Step] = []
    for i, s in enumerate(raw_steps, 1):
        if not isinstance(s, dict):
            return None
        tid = str(s.get("tool") or "").strip().lower()
        tool = reg.get_tool(tid)
        if tool is None or _level(tid) == "BLOCKED":
            return None
        args = s.get("args") if isinstance(s.get("args"), dict) else {}
        known = {p.name for p in tool.parameters}
        args = {k: v for k, v in args.items() if k in known}
        for p in tool.parameters:
            if p.required and p.name not in args:
                return None
        for v in args.values():                         # a placeholder may only point backwards
            if isinstance(v, str):
                for ref in _PLACEHOLDER.findall(v):
                    if ref.lower() != "prev" and not (1 <= int(ref) < i):
                        return None
                    if ref.lower() == "prev" and i == 1:
                        return None
        title = re.sub(r"\s+", " ", str(s.get("title") or tool.name)).strip()[:28] or tool.name
        steps.append(Step(i, tid, args, title))
    title = re.sub(r"\s+", " ", str(d.get("title") or "")).strip()[:60]
    return title, steps


def make_plan(request: str) -> Run | None:
    """Ask the model for a plan. None when the request needs none (or none can be made)."""
    raw = _complete(_SYSTEM + "\nAvailable tools:\n" + _tool_lines(), "Request: " + request)
    d = _parse(raw)
    if not d:
        return None
    ok = _validate(d)
    if not ok:
        return None
    title, steps = ok
    if len(steps) < 2:
        return None                                    # one step is just a command: not our business
    return Run(request=request, title=title or request[:60], steps=steps)


# --------------------------------------------------------------------------------------------
# Running it
# --------------------------------------------------------------------------------------------

def _resolve(args: dict, steps: list[Step], current: int) -> dict:
    """Replace {{n}} by the output of step n."""
    def sub(match: re.Match) -> str:
        ref = match.group(1).lower()
        idx = current - 1 if ref == "prev" else int(ref)
        return steps[idx - 1].output if 1 <= idx <= len(steps) else ""
    out = {}
    for k, v in args.items():
        out[k] = _PLACEHOLDER.sub(sub, v) if isinstance(v, str) else v
    return out


def _describe(step: Step) -> str:
    try:
        t = _registry().get_tool(step.tool)
        name = t.name if t else step.tool
    except Exception:
        name = step.tool
    return f"{step.title.lower()} ({name})"


def _call(step: Step, args: dict, abort: threading.Event):
    """Run the tool in a worker thread so "stop" is honoured even while it is busy."""
    box: dict = {}

    def work():
        try:
            from tools.registry import call_tool
            box["r"] = call_tool(step.tool, **args)
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=work, daemon=True, name=f"plan-step-{step.n}")
    th.start()
    deadline = time.time() + STEP_TIMEOUT_S
    while th.is_alive() and not abort.is_set() and time.time() < deadline:
        th.join(0.1)
    if abort.is_set():
        return None, "stopped"
    if th.is_alive():
        return None, "that step took too long"
    if "e" in box:
        return None, str(box["e"])
    r = box["r"]
    return (r, "") if getattr(r, "success", False) else (r, str(getattr(r, "message", "") or "it failed"))


def _ask_confirm(run: Run, step: Step) -> bool:
    run.decision, run.waiting_for_confirm = None, True
    run.answered.clear()
    _chip(f"Step {step.n}/{len(run.steps)}: waiting for OK", subtitle=step.title, ms=CONFIRM_TIMEOUT_S * 1000)
    _say(f"Step {step.n} needs your OK: {_describe(step)}. Say yes or no.")
    run.answered.wait(CONFIRM_TIMEOUT_S)
    run.waiting_for_confirm = False
    return run.decision is True and not run.abort.is_set()


def _wait_quiet(abort: threading.Event) -> None:
    """Give the spoken plan summary time to finish before step 1 starts talking too."""
    t0 = time.time()
    time.sleep(START_DELAY_S)
    try:
        import pygame
        while (not abort.is_set() and time.time() - t0 < 20
               and pygame.mixer.get_init() and pygame.mixer.music.get_busy()):
            time.sleep(0.2)
    except Exception:
        pass


def _save_note(text: str) -> bool:
    try:
        from actions import notes
        return bool(notes.try_notes("take a note: " + re.sub(r"\s+", " ", text).strip()))
    except Exception:
        return False


def _runner(run: Run) -> None:
    n = len(run.steps)
    outcome, why = "done", ""
    try:
        _wait_quiet(run.abort)
        for step in run.steps:
            if run.abort.is_set():
                outcome = "stopped"
                break
            step.status = "running"
            _chip(f"Step {step.n}/{n}: {step.title}", subtitle=run.title)
            level = _level(step.tool)
            if level == "BLOCKED":
                step.status, outcome, why = "blocked", "failed", f"step {step.n} ({step.title}) is not something I'm allowed to do"
                break
            if level == "CONFIRM" and not _ask_confirm(run, step):
                step.status = "stopped"
                outcome = "stopped" if run.abort.is_set() else "declined"
                break
            res, err = _call(step, _resolve(step.args, run.steps, step.n), run.abort)
            if run.abort.is_set():
                step.status, outcome = "stopped", "stopped"
                break
            if err:
                step.status, outcome, why = "failed", "failed", f"step {step.n} ({step.title}) failed: {_short(err, 120)}"
                break
            step.status, step.output = "done", str(getattr(res, "message", "") or "")[:_OUT_CAP]
    except Exception as e:  # noqa: BLE001 - the thread must always end cleanly
        outcome, why = "failed", f"something went wrong: {_short(str(e), 100)}"
    try:
        _finish(run, outcome, why)
    finally:
        run.finished.set()


def _final_text(run: Run) -> str:
    for s in reversed(run.steps):
        if s.status == "done" and s.output.strip() and s.tool not in ("speak_text",):
            return s.output
    return ""


def _finish(run: Run, outcome: str, why: str) -> None:
    done = [s for s in run.steps if s.status == "done"]
    if outcome == "stopped":
        _chip("Plan stopped", phase="fail", subtitle=f"{len(done)} of {len(run.steps)} steps done", ms=3500)
        return                                             # handle_turn already said "Okay, stopped."
    if outcome == "declined":
        _chip("Plan stopped", phase="fail", subtitle="You said no", ms=3500)
        _say("Okay, I stopped there.")
        return
    if outcome == "failed":
        _chip("Plan stopped", phase="fail", subtitle=_short(why, 80), ms=4500)
        _say(f"I had to stop: {why}.")
        return
    final = _final_text(run)
    saved = False
    if AUTO_SAVE_RESULT and final and not any(s.tool in ("add_note", "save_note") and s.status == "done" for s in run.steps):
        saved = _save_note(f"{run.title}: {final}")
    _chip("Plan done", phase="success", subtitle=f"{len(run.steps)} steps", ms=3500)
    last = run.steps[-1]
    if last.tool == "speak_text" and last.status == "done":
        return                                             # it just read the result out
    msg = "Done. " + (_short(final, 260) if final else "")
    _say(msg.strip() + (" I saved it to your notes." if saved else ""))


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------

def abort(reason: str = "") -> bool:
    r = _run
    if not is_running():
        return False
    r.abort.set()
    r.answered.set()
    _stop_speaking()
    return True


def handle_turn(command: str) -> str | None:
    """None = not for the planner. Otherwise what Lyra says now; the plan itself runs in the background."""
    global _run
    text = re.sub(r"\s+", " ", (command or "").strip())
    if not text:
        return None
    if not _feature_on():
        abort()
        return None

    r = _run
    if is_running():
        if r.waiting_for_confirm and (_YES.match(text) or _NO.match(text)):
            r.decision = bool(_YES.match(text))
            if not r.decision and ABORT.match(text):
                r.abort.set()
            r.answered.set()
            return "" if r.decision else "Okay."
        if ABORT.match(text):
            abort()
            return "Okay, stopped."
        return None                                        # anything else goes its normal way

    if not should_plan(text):
        return None
    try:
        import ui
        ui.set_state("thinking")
    except Exception:
        pass
    try:
        run = make_plan(text)
    except Exception as e:  # noqa: BLE001
        try:
            import ui
            ui.log.warning("planner: %s", e)
        except Exception:
            pass
        return None                                        # let the normal agent have a go
    if run is None:
        return None
    with _lock:
        _run = run
    _show_plan(run)
    th = threading.Thread(target=_runner, args=(run,), daemon=True, name="lyra-plan")
    th.start()
    names = ", ".join(s.title.lower() for s in run.steps[:-1]) + (" and " if len(run.steps) > 1 else "") + run.steps[-1].title.lower()
    return f"Here's my plan, {len(run.steps)} steps: {names}. Say stop to cancel."
