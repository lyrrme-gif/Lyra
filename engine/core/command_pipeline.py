"""Lyra — command pipeline: process one user turn end-to-end.

Extracted from main.py (Phase 2b). Owns process_command_chain / _process_command_chain.
main.py keeps the process entry points (voice/text loops) and calls into here.
"""
from __future__ import annotations

import importlib
import re
import sys

from engine.bridge import ui
from engine.voice import voice
from engine import actions
from engine.brain import router
from engine import shortcuts
from engine.core import compound
from engine.core import perf
from engine.brain import fastpath
from engine.brain import convo_idle
from engine.core.config import LLM_FIRST

from engine.core.command_helpers import (
    _YT_STATE,
    _youtube_play_query,
    _maybe_ask_for_clarification,
    _combine_pending,
    _remember_followup,
    _is_music_recognition_command,
    _is_open_visual_command,
    _chat_pregate_on,
    _fast_path_on,
    _speech_stream,
    _ask_brain,
    _say,
    _results_on_island,
    _confirmed_text_action,
    _chain_split_allowed,
    _chain_failure,
    _mark_info,
)

# ── The command chain, stage by stage ─────────────────────────────────────────────────────────────────────
# One pass of the loop in _process_command_chain runs the stages below in order. A stage looks at the command
# and either answers it (it returns what the user said over Lyra while she was speaking, "" if nothing: the
# loop then goes on with that) or returns None, which means "not mine": the next stage gets its turn.
# A stage may also rewrite t.command; the stages after it see the new text.

_ISLAND_NOTE = "[music recognition shown on Island]"     # what the history keeps of a recognition shown on the island


class _Turn:
    """One step of the chain: the command being handled and everything a stage needs to answer it."""
    __slots__ = ("command", "recognizer", "source", "ctx", "history")

    def __init__(self, command, recognizer, source, ctx, history):
        self.command, self.recognizer, self.source, self.ctx, self.history = command, recognizer, source, ctx, history

    def remember(self, reply: str) -> None:
        """Keep this turn in the history so that "that" / "again" have context."""
        self.history.append({"user": self.command, "lyra": reply})

    def say(self, reply: str, *, trace: bool = False, remember: bool = False, idle: bool = False) -> str:
        """Show Lyra's reply in the chat (trace: with the steps that led to it), keep the turn in the history
        (remember), and speak it. Returns what the user said over her (barge-in), or ''."""
        detail = ui._pop_trace() if trace else ""
        ui.print_chat("lyra", reply, detail)
        if remember:
            self.remember(reply)
        if idle:
            try:
                ui.set_state("idle")
            except Exception:
                pass
        return voice.speak(reply, self.recognizer, self.source) or ""


def _open_tab_for(command: str, reply: str, kind: str) -> None:
    """Let the island open the tab that goes with this answer ("how much time is left" -> the Timer tab)."""
    try:
        from engine.features import tab_sync
        tab_sync.open_for(command, reply, kind)
    except Exception:
        pass


def _stage_pending_followup(t: _Turn):
    """Answer to Lyra's last clarifying question: "play X" -> "Spotify or YouTube?" -> "spotify"."""
    try:
        from engine.brain import session_context
        sctx = session_context.get()
        if sctx.has_pending() and len(re.findall(r"[a-zA-Z0-9']+", t.command)) <= 6:
            question = getattr(sctx, "pending_question", "") or ""
            pending, kind = sctx.take_pending()
            if pending:
                combined = _combine_pending(pending, t.command, kind, question)
                ui.trace(f"follow-up: {t.command!r} + pending {pending!r} -> {combined!r}")
                t.command = combined
    except Exception as e:
        ui.log.warning("pending follow-up: %s", e)


def _stage_circle_ask(t: _Turn):
    """A circled part of the screen is waiting for its question (the default "What is this?", or what you say next)."""
    try:
        from engine.features import circle_ask
        if circle_ask.has_pending():
            reply = circle_ask.answer(t.command)
            if reply:
                # A real ANSWER about the picture: it is spoken, shown as a subtitle and the island shows her talking.
                # Without this a short explanation counted as a "task result" and became a silent Mochi sound.
                _mark_info(reply)
                return t.say(reply)
    except Exception as e:
        ui.log.warning("circle to ask: %s", e)


def _stage_guide_answer(t: _Turn):
    """Screen guide: Lyra is acting on the screen and asked "yes or no?" about the next step."""
    try:
        from engine.features import guide
        reply = guide.answer_pending(t.command)
        if reply:
            return t.say(reply)
    except Exception as e:
        ui.log.warning("screen guide answer: %s", e)


def _stage_music_recognition(t: _Turn):
    """Music recognition is an explicit, high-priority command: it is handled before LLM-first routing so that
    "what's the playing song" can never become a chat answer. The island owns the visual result, so nothing is spoken."""
    if not _is_music_recognition_command(t.command):
        return None
    try:
        if actions.try_handle_action(t.command, t.ctx):
            t.history.append({"user": t.command, "lyra": _ISLAND_NOTE})
            return ""
    except Exception as e:
        ui.log.warning("music recognition fast path: %s", e)


def _stage_schedule(t: _Turn):
    """Scheduled actions and routines ("remind me to call Mom tomorrow at 9", "every morning at 9:00 open
    Unity and Spotify", "what's scheduled"). Relative times ("in 20 minutes") are left to the timers."""
    try:
        from engine.features import scheduler
        try:
            from engine.features import tab_sync                  # "add dentist to my calendar tomorrow at 9" -> a scheduled reminder
            sched_cmd = tab_sync.rewrite(t.command)
        except Exception:
            sched_cmd = t.command
        reply = scheduler.handle_command(sched_cmd)
        try:
            reply = tab_sync.calendar_reply(t.command, sched_cmd, reply)
        except Exception:
            pass
    except Exception as e:
        ui.log.warning("scheduler: %s", e)
        reply = None
    if reply:
        _open_tab_for(t.command, reply, "schedule")     # added -> the Calendar tab opens with the "added" animation
        return t.say(reply)


# Features with a tab that can be driven by voice: (name for the log, module with handle_command(command)).
_VOICE_TAB_HANDLERS = (
    ("wallpaper", "engine.features.wallpaper"),             # "change my wallpaper", "open the wallpapers"
    ("settings voice", "engine.core.settings_voice"),   # "set dark mode", "set accent to blue", "speak faster"
    ("controls", "engine.features.controls"),               # "turn off bluetooth", "dark mode on", "open the controls"
)


def _stage_voice_tabs(t: _Turn):
    for label, module in _VOICE_TAB_HANDLERS:
        try:
            reply = importlib.import_module(module).handle_command(t.command)
        except Exception as e:
            ui.log.warning("%s: %s", label, e)
            reply = None
        if reply:
            return t.say(reply)


def _stage_timers(t: _Turn):
    """Timer commands stay deterministic and instant. The timer backend owns the notification when one finishes."""
    try:
        from engine.features import timers
        with perf.span("action"):
            reply = timers.handle_command(t.command)
    except Exception as e:
        ui.log.warning("timers: %s", e)
        reply = None
    if reply:
        _open_tab_for(t.command, reply, "timer")        # "how much time is left" -> the answer + the Timer tab
        return t.say(reply)


def _voice_session_command(command: str):
    """Voice commands "switch to text", "standby" and "exit". True = leave voice mode (go back to standby); None = not one of them."""
    if ui.HAS_CONSOLE and voice.SWITCH_TO_TEXT_PATTERN.search(command):
        ui.print_chat("lyra", "Switching to text mode, sir.")
        voice.speak("Switching to text mode, sir.")
        ui.switch_to_text_mode()
        return True
    if voice.STANDBY_PATTERN.search(command) and not voice.NEGATIVE_STANDBY_PATTERN.search(command):
        ui.print_chat("lyra", "Going back to standby, sir.")
        voice.speak("Going back to standby, sir.")
        return True
    if voice.EXIT_PATTERN.search(command):
        ui.print_chat("lyra", "Powering down systems. Goodbye, sir.")
        voice.speak("Powering down systems. Goodbye, sir.")
        sys.exit()


def _stage_wizard(t: _Turn):
    """Shortcut create/edit/delete wizard. It must come before shortcut matching: a wizard reply like "yes" or
    "open chrome" (an action step, not something to run now) would otherwise be swallowed by the matching below."""
    if shortcuts.wizard_active():
        return t.say(shortcuts.handle_wizard_turn(t.command), trace=True)
    reply = shortcuts.try_start_wizard(t.command)
    if reply:
        return t.say(reply, trace=True)


def _stage_open_chat(t: _Turn):
    """Commands like "open X chat" must come before shortcuts and the planner, or a loose app match turns it into just opening Discord."""
    try:
        from engine.features import messaging
        reply = messaging.try_open_chat(t.command, t.ctx)
        if reply:
            return t.say(reply)
    except Exception as e:
        ui.log.warning("messaging open-chat: %s", e)


def _stage_shortcut(t: _Turn):
    reply = shortcuts.try_handle_shortcut(t.command, t.ctx)    # exact match
    if reply:
        return t.say(reply, trace=True)


def _stage_send_message(t: _Turn):
    """Deterministic send-message and media paths run before the planner."""
    try:
        from engine.features import messaging
        reply = messaging.try_handle(t.command, t.ctx)
        if reply:
            return t.say(reply, remember=True)
    except Exception as e:
        ui.log.warning("messaging: %s", e)


def _stage_spotify(t: _Turn):
    """Spotify: "search X on Spotify" shows song cards on the island; "play X on Spotify" plays the best match at once.
    If nothing is found the old direct-play path further down gets the command."""
    mode, query = "", ""
    try:
        from engine.features import spotify_island
        mode, query = spotify_island.parse_intent(t.command)
    except Exception as e:
        ui.log.warning("spotify parse: %s", e)
    if not query:
        return None
    if mode == "play":
        reply = spotify_island.play_query(query)
        ui.trace(f"spotify island: playing {query!r}" if reply else f"spotify island: nothing to play for {query!r}")
    else:
        reply = spotify_island.show(query)
        ui.trace(f"spotify island: cards for {query!r}" if reply else f"spotify island: no cards for {query!r}")
    if reply:
        return t.say(reply, remember=True)
    ui.trace(f"spotify island: falling back to direct play for {query!r}")


def _stage_youtube_search(t: _Turn):
    """YouTube: "look for / search / find X on YouTube" shows result cards without opening a video; playing is separate (below)."""
    query = ""
    try:
        from engine.features import yt_island
        query = yt_island.parse_query(t.command)
    except Exception as e:
        ui.log.warning("youtube parse: %s", e)
    if not query:
        return None
    reply = yt_island.show(query) or f"I couldn't find any YouTube results for {query}."
    if _results_on_island(reply):
        t.remember(reply)
        return ""                           # the results are on the island: say nothing
    return t.say(reply, remember=True)


def _stage_web_search(t: _Turn):
    """Web search: "search the web / on Google for X" gives result cards on the island, and Lyra says a few words over them."""
    query = ""
    try:
        from engine.features import web_island
        query = web_island.parse_query(t.command)
    except Exception as e:
        ui.log.warning("web search parse: %s", e)
    if query:
        return t.say(web_island.show(query) or f"I couldn't find any results for {query}.", remember=True)


def _stage_youtube_pick(t: _Turn):
    """Picking: "play the first result" right after a YouTube search on the island."""
    try:
        from engine.features import yt_island
        pick = yt_island.parse_pick(t.command)
        reply = yt_island.play_pick(pick) if pick is not None else ""
    except Exception as e:
        ui.log.warning("youtube pick: %s", e)
        reply = ""
    if reply:
        return t.say(reply, remember=True)


def _stage_youtube_open_all(t: _Turn):
    """Opening everything: "open all those searches" opens every result now on the island, not a guess by the planner."""
    try:
        from engine.features import yt_island
        reply = yt_island.open_all() if yt_island.parse_open_all(t.command) else ""
    except Exception as e:
        ui.log.warning("open all results: %s", e)
        reply = ""
    if reply:
        return t.say(reply, remember=True)


def _stage_youtube_play(t: _Turn):
    query = _youtube_play_query(t.command)
    if not query:
        return None
    try:
        from engine.actions.apps import play_youtube
        ok, detail = play_youtube(query)
        if ok:
            _YT_STATE["q"] = query
        reply = f"Opened the first YouTube result for {query}." if ok else detail
    except Exception as e:
        reply = f"I couldn't open that YouTube video: {e}"
    return t.say(reply, remember=True)


def _stage_open_visual(t: _Turn):
    """Explicit app/site opens reach the deterministic action router before LLM-first routing. The island still plays
    its Opening... animation, but the command also completes a normal chat turn so the composer is unlocked."""
    if not _is_open_visual_command(t.command):
        return None
    try:
        reply = actions.try_handle_action(t.command, t.ctx)
        if reply and _results_on_island(reply):
            return ""
        if reply:
            return t.say(reply, trace=True, remember=True, idle=True)
    except Exception as e:
        ui.log.warning("explicit app/site open: %s", e)


def _stage_quick_abilities(t: _Turn):
    """Exact answers (battery, disk, time left, maths, conversions, notes, lists, clipboard AI) must beat the chat
    model, which only guesses. Loose phrasings ("lock my pc") are first rewritten into the wording the matchers know."""
    try:
        t.command = actions.normalize_command(t.command)
        quick = actions.quick_reply(t.command)
        if not quick:
            try:
                quick = shortcuts.try_handle_shortcut_exact(t.command, t.ctx)
            except Exception as e:
                ui.log.warning("routine: %s", e)
        if quick:
            try:
                from engine.features import activity_log
                activity_log.record("quick", t.command, quick)
            except Exception:
                pass
            return t.say(quick, trace=True, remember=True)
    except Exception as e:
        ui.log.warning("quick abilities: %s", e)


def _stage_feature_commands(t: _Turn):
    """Feature commands reach their deterministic handlers before the general LLM router."""
    try:
        reply = actions.try_optional_action(t.command, t.ctx)
        if reply:
            if _results_on_island(reply):
                return ""
            return t.say(reply, trace=True, remember=True)
    except Exception as e:
        ui.log.warning("feature command: %s", e)


def _stage_translation(t: _Turn):
    """An active translation session consumes each utterance before the chat model."""
    try:
        from engine.features import translate_mode
        translation = translate_mode.handle_turn(t.command)
        if translation is not None:
            return t.say(translation, remember=True) if translation else ""
    except Exception as e:
        ui.log.warning("translation mode: %s", e)


def _stage_screen_click(t: _Turn):
    """Explicit screen clicks are deterministic: handled before planning so "click <target>" can never be rewritten
    as opening a site or choosing a different visible result."""
    if not re.match(r"^\s*(?:click|tap|press|hit)\b", t.command, re.I):
        return None
    try:
        reply = actions.try_handle_action(t.command, t.ctx)
    except Exception as e:
        ui.log.warning("direct click: %s", e)
        reply = None
    if reply:
        return t.say(reply, trace=True, remember=True)


def _stage_planner(t: _Turn):
    """The planner owns explicit multi-step requests and its confirm/stop replies ('' = handled, nothing to say)."""
    try:
        from engine.brain import planner
        reply = planner.handle_turn(t.command)
        if reply is not None:
            return t.say(reply, remember=True) if reply else ""
    except Exception as e:
        ui.log.warning("planner: %s", e)


def _stage_fast_path(t: _Turn):
    """Plain "volume up", "pause", "next song", "mute" are done at once with no LLM round trip (config.FAST_PATH):
    the action runs first, Lyra speaks after it."""
    if not _fast_path_on():
        return None
    reply = fastpath.run(t.command, t.ctx)
    if reply:
        return t.say(reply, trace=True, remember=True)


def _stage_remember(t: _Turn):
    """Memory: "remember ..." goes straight into Lyra's memory (Settings > Privacy & Memory); the planner would file it as a note."""
    try:
        from engine.actions.memory_commands import try_remember
        reply = try_remember(t.command)
        if reply:
            ui.trace("action: remember (memory)")
            return t.say(reply, trace=True, remember=True)
    except Exception as e:
        ui.log.warning("memory: %s", e)


def _ask_and_say(t: _Turn, prompt: str, intent: str) -> str:
    """Ask the chat model, show the answer and speak it (sentence by sentence while it is written, when streaming)."""
    stream = _speech_stream(t.recognizer, t.source)
    reply = _ask_brain(prompt, t.history, intent, stream)
    detail = ui._pop_trace()
    _mark_info(reply)
    ui.print_chat("lyra", reply, detail)
    _remember_followup(prompt, reply)        # if Lyra asked a follow-up, remember the original request
    return _say(reply, stream, t.recognizer, t.source) or ""


def _stage_agent(t: _Turn):
    """LLM-first routing (config.LLM_FIRST) or, when that is off, just the multi-step agent (open X then Y then Z)."""
    try:
        from engine.brain import agent
        if not (LLM_FIRST or agent.looks_multi_step(t.command)):
            return None
        pregate = LLM_FIRST and _chat_pregate_on()
        if pregate and fastpath.is_plain_chat(t.command):
            reply, route = None, "chat"                      # pure small talk: no planner call
            ui.trace("path: chat pre-gate (planner skipped)")
        elif pregate and fastpath.question_route(t.command):
            reply, route = None, fastpath.question_route(t.command)
            ui.trace(f"path: question pre-gate (planner skipped, routed as {route})")
        else:
            reply, route = agent.route_and_run(t.command, t.ctx, force=LLM_FIRST)
        if reply:
            return t.say(reply, trace=True, remember=True)
        if LLM_FIRST and route in ("chat", "research"):
            return _ask_and_say(t, t.command, route)         # not an action: straight to the chat brain
    except Exception as e:
        ui.log.warning("agent: %s", e)


def _stage_direct_action(t: _Turn):
    reply = actions.try_handle_action(t.command, t.ctx)
    if not reply:
        return None
    if _results_on_island(reply):
        return ""
    if _is_music_recognition_command(t.command):
        # Recognition owns the island UI: no chat line, no speech.
        t.history.append({"user": t.command, "lyra": _ISLAND_NOTE})
        return ""
    return t.say(reply, trace=True, remember=True)


def _stage_clarify(t: _Turn):
    """A short or unclear utterance (one word) must not fall through to the LLM dictionary mode: ask what is meant."""
    clarify = _maybe_ask_for_clarification(t.command)
    if not clarify:
        return None
    ui.print_chat("lyra", clarify)
    try:
        from engine.brain import session_context
        session_context.get().set_pending(t.command, "general")      # keep the original so a one-word answer completes it
    except Exception:
        pass
    return voice.speak(clarify, t.recognizer, t.source) or ""


def _stage_router_and_chat(t: _Turn):
    """The free local matching (regex + fuzzy) has missed. One LLM-assisted pass cleans up STT noise and classifies the
    intent; the action router gets a second shot at the cleaned text, then it is plain chat."""
    route = router.classify(t.command)
    working, intent, guessed_url = route["corrected"], route["intent"], route.get("guessed_url")
    if working != t.command:
        if _is_music_recognition_command(working) and actions.try_handle_action(working, t.ctx):
            t.history.append({"user": t.command, "lyra": _ISLAND_NOTE})
            return ""
        reply = actions.try_handle_action(working, t.ctx)
        if reply:
            return t.say(reply, trace=True)
    if intent == "action" and guessed_url:
        # A website the router guessed that is in no known list: try it and remember it (see try_learned_open()).
        reply = actions.try_learned_open(working, guessed_url)
        if reply:
            return t.say(reply, trace=True)
    return _ask_and_say(t, working, intent)


# Before the "leave voice mode" check, and after it. The last stage always answers.
_EARLY_STAGES = (_stage_pending_followup, _stage_circle_ask, _stage_guide_answer, _stage_music_recognition,
                 _stage_schedule, _stage_voice_tabs, _stage_timers)
_STAGES = (_stage_wizard, _stage_open_chat, _stage_shortcut, _stage_send_message, _stage_spotify,
           _stage_youtube_search, _stage_web_search, _stage_youtube_pick, _stage_youtube_open_all,
           _stage_youtube_play, _stage_open_visual, _stage_quick_abilities, _stage_feature_commands,
           _stage_translation, _stage_screen_click, _stage_planner, _stage_fast_path, _stage_remember,
           _stage_agent, _stage_direct_action, _stage_clarify, _stage_router_and_chat)


def _run_stages(stages, t: _Turn):
    """The first stage that answers wins: returns its result (a str), or None when none of them did."""
    for stage in stages:
        nxt = stage(t)
        if nxt is not None:
            return nxt
    return None


def process_command_chain(command: str, conversation_history: list, recognizer=None, source=None) -> bool:
    """Run one command through the chain (see _process_command_chain) and close its timing line."""
    if not perf.active():
        perf.begin("text", command)     # typed in the console: no speech, the clock starts now
    perf.set_text(command)
    convo_idle.reset_if_idle(conversation_history)   # a long quiet spell since the last turn: start a fresh chat
    try:
        return _process_command_chain(command, conversation_history, recognizer, source)
    finally:
        convo_idle.touch()              # the quiet spell is counted from the END of this turn
        perf.finish(spoken=False)       # no-op when speak() already closed the turn


def _process_command_chain(command: str, conversation_history: list, recognizer=None, source=None) -> bool:
    """Process `command`, and in voice mode keep chaining directly into any
    barge-in interruption without waiting for a fresh voice.listen(). Prints each
    turn to the chat transcript. Returns True if we should go back to
    standby (voice mode only — text mode ignores the return value)."""

    # Build engine context for permission confirmations. Typed UI confirmations
    # are resolved by the same PermissionManager instead of blocking on stdin.
    engine_context = {"recognizer": recognizer, "source": source} if recognizer and source else {}
    running = {"cmd": command}   # the step being run: a confirmation re-runs this, not the whole sentence
    if ui.INPUT_MODE == "text" or not (recognizer and source):
        engine_context["confirm_callback"] = lambda cmd=None: _confirmed_text_action(cmd or running["cmd"])
    try:
        from engine.core.permissions import PermissionManager
        pm = PermissionManager()
        if pm.pending_text_confirmation():
            answer = command.strip()
            if re.fullmatch(r"(yes|y|yeah|yep|sure|ok|okay|confirm|go ahead|do it|no|n|nope|nah|cancel|stop)", answer, re.I):
                ui.print_chat("user", command)
                result = pm.resolve_text_confirmation(answer)
                if result:
                    ui.print_chat("lyra", result)
                    return True
    except Exception as e:
        ui.log.warning("text confirmation resolution: %s", e)

    # A compound command ("open discord and open anogashi's chat") is split into steps by
    # compound.split_compound and run one at a time through the handlers below. Every branch
    # below ends in `continue` once it has shown and spoken its reply, so the next pass through
    # this loop starts only after the previous step has finished.
    chain = None              # compound.Chain of the compound command being run, if any
    pending_steps: list = []  # its steps that have not started yet (the same list as chain.pending)
    chain_mark = None         # last Lyra chat line before the running step; a newer one is its reply
    turn = _Turn(command, recognizer, source, engine_context, conversation_history)

    while command or pending_steps:
        command = (command or "").strip()
        if command and chain is not None:
            # A new command while a chain exists: the user spoke over it. Drop what was left.
            if pending_steps:
                ui.trace(f"chain: interrupted, dropped {len(pending_steps)} step(s)")
            chain, pending_steps = None, []
        if not command:
            if not pending_steps:
                break
            # The previous step is over. Go on only if it worked; otherwise say where it stopped.
            stop_msg = _chain_failure(chain, chain_mark)
            if stop_msg:
                chain, pending_steps = None, []
                ui.print_chat("lyra", stop_msg)
                command = voice.speak(stop_msg, recognizer, source) or ""
                continue
            command = chain.next_step()
            ui.trace(f"chain: step {len(chain.ran)}/{chain.total}: {command!r}")
        else:
            ui.print_chat("user", command)
            if _chain_split_allowed(command):
                steps = compound.split_compound(command)
                if len(steps) >= 2:
                    chain = compound.Chain(steps)
                    pending_steps = chain.pending
                    command = chain.next_step()
                    ui.trace(f"chain: {len(steps)} steps; step 1/{chain.total}: {command!r}")
        running["cmd"] = command
        chain_mark = getattr(ui, "_last_lyra_chat", None)
        # Voice turns reach this shared command path without going through the
        # UI chat handler, so publish the processing state here as well. This
        # drives the island's thinking Mochi and the caption window indicator.
        ui.set_state("thinking")

        turn.command = command
        nxt = _run_stages(_EARLY_STAGES, turn)
        if nxt is None:
            if _voice_session_command(turn.command):
                return True                  # voice mode only: back to standby
            nxt = _run_stages(_STAGES, turn)
        command = nxt

    if chain is not None and not pending_steps:
        # The last step has no next pass to check it. Its own reply already said what went
        # wrong; add which steps finished.
        stop_msg = _chain_failure(chain, chain_mark, include_reply=False)
        if stop_msg:
            ui.print_chat("lyra", stop_msg)
            voice.speak(stop_msg, recognizer, source)
    return False


