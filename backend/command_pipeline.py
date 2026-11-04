"""Lyra — command pipeline: process one user turn end-to-end.

Extracted from main.py (Phase 2b). Owns process_command_chain / _process_command_chain.
main.py keeps the process entry points (voice/text loops) and calls into here.
"""
from __future__ import annotations

import re
import sys

import ui
import voice
import actions
import brain
import router
import shortcuts
import compound
import perf
import fastpath
import convo_idle
from config import LLM_FIRST

from command_helpers import (
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
        from permissions import PermissionManager
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

        # Follow-up answers to Lyra's last clarifying question
        # e.g. user: "play X" → Lyra: "Spotify or YouTube?" → user: "spotify"
        try:
            import session_context
            sctx = session_context.get()
            if sctx.has_pending() and len(re.findall(r"[a-zA-Z0-9']+", command)) <= 6:
                question = getattr(sctx, "pending_question", "") or ""
                pending, kind = sctx.take_pending()
                if pending:
                    combined = _combine_pending(pending, command, kind, question)
                    ui.trace(f"follow-up: {command!r} + pending {pending!r} -> {combined!r}")
                    command = combined
        except Exception as e:
            ui.log.warning("pending follow-up: %s", e)

        # Circle to ask: a circled part of the screen is waiting for its question (the default
        # "What is this?" after a circle, or whatever you say or type next).
        try:
            import circle_ask
            if circle_ask.has_pending():
                circle_reply = circle_ask.answer(command)
                if circle_reply:
                    # A real ANSWER about the picture: it is spoken, shown as a subtitle and the island shows her talking.
                    # Without this a short explanation counted as a "task result" and became a silent Mochi sound.
                    _mark_info(circle_reply)
                    ui.print_chat("lyra", circle_reply)
                    command = voice.speak(circle_reply, recognizer, source) or ""
                    continue
        except Exception as e:
            ui.log.warning("circle to ask: %s", e)

        # Screen guide: Lyra is acting on the screen and asked "yes or no?" about the next step.
        try:
            import guide
            guide_reply = guide.answer_pending(command)
            if guide_reply:
                ui.print_chat("lyra", guide_reply)
                command = voice.speak(guide_reply, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("screen guide answer: %s", e)

        # Music recognition is an explicit, high-priority command. Handle it
        # before LLM-first routing so phrases like "what's the playing song"
        # can never turn into a normal chat answer. The Island owns the visual
        # result; do not speak a duplicate assistant response.
        if _is_music_recognition_command(command):
            try:
                action_reply = actions.try_handle_action(command, engine_context)
                if action_reply:
                    try:
                        conversation_history.append({"user": command, "lyra": "[music recognition shown on Island]"})
                    except Exception:
                        pass
                    command = ""
                    continue
            except Exception as e:
                ui.log.warning("music recognition fast path: %s", e)

        # Priority 0.65: scheduled actions and routines ("remind me to call Mom tomorrow at 9",
        # "every morning at 9:00 open Unity and Spotify", "what's scheduled"). Relative times
        # ("in 20 minutes") are left to the timers just below.
        try:
            import scheduler
            try:
                import tab_sync                  # "add dentist to my calendar tomorrow at 9" -> a scheduled reminder
                _sched_cmd = tab_sync.rewrite(command)
            except Exception:
                _sched_cmd = command
            sched_reply = scheduler.handle_command(_sched_cmd)
            try:
                sched_reply = tab_sync.calendar_reply(command, _sched_cmd, sched_reply)
            except Exception:
                pass
        except Exception as e:
            ui.log.warning("scheduler: %s", e)
            sched_reply = None
        if sched_reply:
            try:
                import tab_sync                  # added -> Calendar tab opens with the "added" animation
                tab_sync.open_for(command, sched_reply, "schedule")
            except Exception:
                pass
            ui.print_chat("lyra", sched_reply)
            command = voice.speak(sched_reply, recognizer, source) or ""
            continue

        # Wallpaper tab by voice: "change my wallpaper", "open the wallpapers".
        try:
            import wallpaper
            wp_reply = wallpaper.handle_command(command)
        except Exception as e:
            ui.log.warning("wallpaper: %s", e)
            wp_reply = None
        if wp_reply:
            ui.print_chat("lyra", wp_reply)
            command = voice.speak(wp_reply, recognizer, source) or ""
            continue

        # Appearance + behaviour settings by voice: "set dark mode", "set accent to blue", "speak faster".
        try:
            import settings_voice
            sv_reply = settings_voice.handle_command(command)
        except Exception as e:
            ui.log.warning("settings voice: %s", e)
            sv_reply = None
        if sv_reply:
            ui.print_chat("lyra", sv_reply)
            command = voice.speak(sv_reply, recognizer, source) or ""
            continue

        # Controls tab by voice: "turn off bluetooth", "dark mode on", "open the controls".
        try:
            import controls
            ctl_reply = controls.handle_command(command)
        except Exception as e:
            ui.log.warning("controls: %s", e)
            ctl_reply = None
        if ctl_reply:
            ui.print_chat("lyra", ctl_reply)
            command = voice.speak(ctl_reply, recognizer, source) or ""
            continue

        # Priority 0.7: timers — keep timer commands deterministic and instant.
        # The timer backend owns the notification when a timer finishes; no Timer tab
        # or countdown UI is required here.
        try:
            import timers
            with perf.span("action"):
                timer_reply = timers.handle_command(command)
        except Exception as e:
            ui.log.warning("timers: %s", e)
            timer_reply = None
        if timer_reply:
            try:
                import tab_sync                  # "how much time is left" -> answer + the Timer tab
                tab_sync.open_for(command, timer_reply, "timer")
            except Exception:
                pass
            ui.print_chat("lyra", timer_reply)
            command = voice.speak(timer_reply, recognizer, source) or ""
            continue

        # Check for mode switching commands
        if ui.HAS_CONSOLE and voice.SWITCH_TO_TEXT_PATTERN.search(command):
            ui.print_chat("lyra", "Switching to text mode, sir.")
            voice.speak("Switching to text mode, sir.")
            ui.switch_to_text_mode()
            return True  # Exit voice mode

        if voice.STANDBY_PATTERN.search(command) and not voice.NEGATIVE_STANDBY_PATTERN.search(command):
            ui.print_chat("lyra", "Going back to standby, sir.")
            voice.speak("Going back to standby, sir.")
            return True

        if voice.EXIT_PATTERN.search(command):
            ui.print_chat("lyra", "Powering down systems. Goodbye, sir.")
            voice.speak("Powering down systems. Goodbye, sir.")
            sys.exit()

        # Shortcut create/edit/delete wizard — must be checked before
        # shortcut/command matching, otherwise a wizard reply like "yes" or
        # "open chrome" (an action step, not a command to run right now)
        # would get swallowed by the normal matching below instead of
        # advancing the wizard.
        if shortcuts.wizard_active():
            wiz_reply = shortcuts.handle_wizard_turn(command)
            detail = ui._pop_trace()
            ui.print_chat("lyra", wiz_reply, detail)
            command = voice.speak(wiz_reply, recognizer, source) or ""
            continue

        wiz_reply = shortcuts.try_start_wizard(command)
        if wiz_reply:
            detail = ui._pop_trace()
            ui.print_chat("lyra", wiz_reply, detail)
            command = voice.speak(wiz_reply, recognizer, source) or ""
            continue
            continue


        # Open-chat commands must be handled before shortcuts and the planner;
        # otherwise a loose shortcut/app match can turn "open X chat" into
        # simply opening Discord (or another unrelated app).
        try:
            import messaging
            chat_open_reply = messaging.try_open_chat(command, engine_context)
            if chat_open_reply:
                ui.print_chat("lyra", chat_open_reply)
                command = voice.speak(chat_open_reply, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("messaging open-chat: %s", e)

        # Priority 1: Check shortcuts (exact match)
        shortcut_reply = shortcuts.try_handle_shortcut(command, engine_context)
        if shortcut_reply:
            detail = ui._pop_trace()
            ui.print_chat("lyra", shortcut_reply, detail)
            command = voice.speak(shortcut_reply, recognizer, source) or ""
            continue

        # Deterministic send-message and media paths run before the planner.
        try:
            import messaging
            send_reply=messaging.try_handle(command, engine_context)
            if send_reply:
                ui.print_chat("lyra",send_reply)
                conversation_history.append({"user":command,"lyra":send_reply})
                command=voice.speak(send_reply,recognizer,source) or ""
                continue
        except Exception as e:
            ui.log.warning("messaging: %s",e)
        # Spotify: "search X on Spotify" shows song cards on the island; "play X on Spotify" plays the
        # best match straight away (no search, same "Playing ..." banner). Typed and spoken alike.
        # If nothing is found, fall through to the old direct-play path below.
        _sp_mode, _sp_q = "", ""
        try:
            import spotify_island as _spotify_island
            _sp_mode, _sp_q = _spotify_island.parse_intent(command)
        except Exception as e:
            ui.log.warning("spotify parse: %s", e)
        if _sp_q:
            if _sp_mode == "play":
                reply = _spotify_island.play_query(_sp_q)
                ui.trace(f"spotify island: playing {_sp_q!r}" if reply else f"spotify island: nothing to play for {_sp_q!r}")
            else:
                reply = _spotify_island.show(_sp_q)
                ui.trace(f"spotify island: cards for {_sp_q!r}" if reply else f"spotify island: no cards for {_sp_q!r}")
            if reply:
                conversation_history.append({"user": command, "lyra": reply})
                ui.print_chat("lyra", reply)
                command = voice.speak(reply, recognizer, source) or ""
                continue
            ui.trace(f"spotify island: falling back to direct play for {_sp_q!r}")
        # YouTube lookup/search is deliberately separate from playback.
        # "look for/search/find X on YouTube" should show real result cards in
        # Chat without opening a video; "play X on YouTube" remains the direct
        # playback path below.
        _yt_search_q = ""
        try:
            import yt_island as _yt_island
            _yt_search_q = _yt_island.parse_query(command)
        except Exception as e:
            ui.log.warning("youtube parse: %s", e)
        if _yt_search_q:
            q = _yt_search_q
            reply = _yt_island.show(q) or f"I couldn't find any YouTube results for {q}."
            conversation_history.append({"user": command, "lyra": reply})
            if _results_on_island(reply):
                command = ""  # results are on the island: say nothing
                continue
            ui.print_chat("lyra", reply)
            command = voice.speak(reply, recognizer, source) or ""
            continue

        # "search the web / on Google for X": result cards on the island, and Lyra says a few words.
        _web_q = ""
        try:
            import web_island as _web_island
            _web_q = _web_island.parse_query(command)
        except Exception as e:
            ui.log.warning("web search parse: %s", e)
        if _web_q:
            # The results go to the island first, then Lyra talks over them (caption + voice wave).
            reply = _web_island.show(_web_q) or f"I couldn't find any results for {_web_q}."
            conversation_history.append({"user": command, "lyra": reply})
            ui.print_chat("lyra", reply)
            command = voice.speak(reply, recognizer, source) or ""
            continue

        # "play the first result" right after a YouTube search on the island
        try:
            import yt_island as _yt_island2
            _pick = _yt_island2.parse_pick(command)
            _pick_reply = _yt_island2.play_pick(_pick) if _pick is not None else ""
        except Exception as e:
            ui.log.warning("youtube pick: %s", e)
            _pick_reply = ""
        if _pick_reply:
            ui.print_chat("lyra", _pick_reply)
            conversation_history.append({"user": command, "lyra": _pick_reply})
            command = voice.speak(_pick_reply, recognizer, source) or ""
            continue

        # "open all those searches": every result now on the island, not a guess by the planner.
        try:
            import yt_island as _yt_island3
            _all_reply = _yt_island3.open_all() if _yt_island3.parse_open_all(command) else ""
        except Exception as e:
            ui.log.warning("open all results: %s", e)
            _all_reply = ""
        if _all_reply:
            ui.print_chat("lyra", _all_reply)
            conversation_history.append({"user": command, "lyra": _all_reply})
            command = voice.speak(_all_reply, recognizer, source) or ""
            continue

        _yt_q = _youtube_play_query(command)
        if _yt_q:
            q = _yt_q
            try:
                from actions.apps import play_youtube
                ok,detail=play_youtube(q)
                if ok:
                    _YT_STATE["q"] = q
                reply=f"Opened the first YouTube result for {q}." if ok else detail
            except Exception as e:
                reply=f"I couldn't open that YouTube video: {e}"
            ui.print_chat("lyra",reply); conversation_history.append({"user":command,"lyra":reply})
            command=voice.speak(reply,recognizer,source) or ""
            continue

        # Priority 1.25: explicit app/site opens must reach the deterministic
        # action router before LLM-first routing. The activity HUD still shows
        # the polished Opening… animation, but the command ALSO completes a
        # normal chat turn so the composer is unlocked and the user gets an
        # honest answer (e.g. “Opening Spotify, sir.”).
        if _is_open_visual_command(command):
            try:
                open_reply = actions.try_handle_action(command, engine_context)
                if open_reply and _results_on_island(open_reply):
                    command = ""
                    continue
                if open_reply:
                    detail = ui._pop_trace()
                    ui.print_chat("lyra", open_reply, detail)
                    try:
                        conversation_history.append({"user": command, "lyra": open_reply})
                    except Exception:
                        pass
                    try:
                        ui.set_state("idle")
                    except Exception:
                        pass
                    command = voice.speak(open_reply, recognizer, source) or ""
                    continue
            except Exception as e:
                ui.log.warning("explicit app/site open: %s", e)

        # Priority 1.4: quick local abilities - exact answers (battery, disk, time left, maths,
        # conversions, notes, lists, clipboard AI) must beat the chat model, which only guesses.
        # Loose phrasings ("lock my pc") are first rewritten into the wording the matchers know.
        try:
            command = actions.normalize_command(command)
            quick = actions.quick_reply(command)
            if not quick:
                try:
                    # `shortcuts` is imported at the top of the file: never re-import it here -
                    # a local import would make the name local to this whole function.
                    quick = shortcuts.try_handle_shortcut_exact(command, engine_context)
                except Exception as e:
                    ui.log.warning("routine: %s", e)
            if quick:
                try:
                    import activity_log
                    activity_log.record("quick", command, quick)
                except Exception:
                    pass
                detail = ui._pop_trace()
                ui.print_chat("lyra", quick, detail)
                try:
                    conversation_history.append({"user": command, "lyra": quick})
                except Exception:
                    pass
                command = voice.speak(quick, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("quick abilities: %s", e)

        # Feature commands must reach their deterministic handlers before the general LLM router.
        try:
            _feature_reply = actions.try_optional_action(command, engine_context)
            if _feature_reply:
                if _results_on_island(_feature_reply):
                    command = ""
                    continue
                detail = ui._pop_trace()
                ui.print_chat("lyra", _feature_reply, detail)
                conversation_history.append({"user": command, "lyra": _feature_reply})
                command = voice.speak(_feature_reply, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("feature command: %s", e)

        # An active translation session consumes each utterance before the chat model.
        try:
            import translate_mode
            _translation = translate_mode.handle_turn(command)
            if _translation is not None:
                if _translation:
                    ui.print_chat("lyra", _translation)
                    conversation_history.append({"user": command, "lyra": _translation})
                    command = voice.speak(_translation, recognizer, source) or ""
                else:
                    command = ""
                continue
        except Exception as e:
            ui.log.warning("translation mode: %s", e)

        # Explicit screen clicks are deterministic actions. Handle them before
        # LLM-first planning so a spoken "click <target>" cannot be rewritten
        # as opening a site or choosing a different visible result.
        if re.match(r"^\s*(?:click|tap|press|hit)\b", command, re.I):
            try:
                _click_reply = actions.try_handle_action(command, engine_context)
            except Exception as e:
                ui.log.warning("direct click: %s", e)
                _click_reply = None
            if _click_reply:
                detail = ui._pop_trace()
                ui.print_chat("lyra", _click_reply, detail)
                conversation_history.append({"user": command, "lyra": _click_reply})
                command = voice.speak(_click_reply, recognizer, source) or ""
                continue

        # Planner owns explicit multi-step requests and its confirm/stop replies.
        try:
            import planner
            _plan_reply = planner.handle_turn(command)
            if _plan_reply is not None:
                if _plan_reply:
                    ui.print_chat("lyra", _plan_reply)
                    conversation_history.append({"user": command, "lyra": _plan_reply})
                    command = voice.speak(_plan_reply, recognizer, source) or ""
                else:
                    command = ""
                continue
        except Exception as e:
            ui.log.warning("planner: %s", e)

        # Priority 1.45: fast local path (config.FAST_PATH). Plain "volume up", "pause", "next song", "mute"...
        # are done at once, with no LLM round trip: the ACTION runs first, Lyra speaks AFTER it.
        # Open-app, tab and timer commands already skip the LLM (open-app block, quick abilities, timers).
        if _fast_path_on():
            fast_reply = fastpath.run(command, engine_context)
            if fast_reply:
                detail = ui._pop_trace()
                ui.print_chat("lyra", fast_reply, detail)
                try:
                    conversation_history.append({"user": command, "lyra": fast_reply})
                except Exception:
                    pass
                command = voice.speak(fast_reply, recognizer, source) or ""
                continue

        # Priority 1.4: "remember ..." is saved to Lyra's memory directly. With LLM-first routing the planner
        # used to claim it and file it as a note, so it never reached Settings > Privacy & Memory.
        try:
            from actions.memory_commands import try_remember
            _mem_reply = try_remember(command)
            if _mem_reply:
                ui.trace("action: remember (memory)")
                detail = ui._pop_trace()
                ui.print_chat("lyra", _mem_reply, detail)
                try:
                    conversation_history.append({"user": command, "lyra": _mem_reply})
                except Exception:
                    pass
                command = voice.speak(_mem_reply, recognizer, source) or ""
                continue
        except Exception as e:
            ui.log.warning("memory: %s", e)

        # Priority 1.5: LLM-first routing (config.LLM_FIRST) — or, when off,
        # just the multi-step agent (open X then Y then Z).
        try:
            import agent
            if LLM_FIRST or agent.looks_multi_step(command):
                # >>> phase-L begin
                if LLM_FIRST and _chat_pregate_on() and fastpath.is_plain_chat(command):
                    agent_reply, agent_route = None, "chat"       # pure small talk: no planner call
                    ui.trace("path: chat pre-gate (planner skipped)")
                elif LLM_FIRST and _chat_pregate_on() and fastpath.question_route(command):
                    agent_reply, agent_route = None, fastpath.question_route(command)
                    ui.trace(f"path: question pre-gate (planner skipped, routed as {agent_route})")
                else:
                    agent_reply, agent_route = agent.route_and_run(command, engine_context, force=LLM_FIRST)
                # <<< phase-L end
                if agent_reply:
                    detail = ui._pop_trace()
                    ui.print_chat("lyra", agent_reply, detail)
                    try:
                        conversation_history.append({"user": command, "lyra": agent_reply})
                    except Exception:
                        pass
                    command = voice.speak(agent_reply, recognizer, source) or ""
                    continue
                if LLM_FIRST and agent_route in ("chat", "research"):
                    # Not an action: straight to the chat brain, no regex/router detour.
                    stream = _speech_stream(recognizer, source)
                    reply = _ask_brain(command, conversation_history, agent_route, stream)
                    detail = ui._pop_trace()
                    _mark_info(reply)
                    ui.print_chat("lyra", reply, detail)
                    _remember_followup(command, reply)
                    command = _say(reply, stream, recognizer, source) or ""
                    continue
        except Exception as e:
            ui.log.warning("agent: %s", e)

        # Priority 2: Check direct commands
        action_reply = actions.try_handle_action(command, engine_context)
        if action_reply and _results_on_island(action_reply):
            command = ""
            continue
        if action_reply:
            if _is_music_recognition_command(command):
                # Recognition owns the Island UI. Do not duplicate the result in
                # the chat transcript or speak it as a normal assistant answer.
                try:
                    conversation_history.append({"user": command, "lyra": "[music recognition shown on Island]"})
                except Exception:
                    pass
                command = ""
                continue
            detail = ui._pop_trace()
            ui.print_chat("lyra", action_reply, detail)
            # Keep action turns in history so "that" / "again" have context
            try:
                conversation_history.append({"user": command, "lyra": action_reply})
            except Exception:
                pass
            command = voice.speak(action_reply, recognizer, source) or ""
            continue

        # Short / unclear utterances (e.g. a single word) should NOT fall
        # through to the LLM dictionary mode. Only define when the user
        # explicitly asks what something means.
        clarify = _maybe_ask_for_clarification(command)
        if clarify:
            ui.print_chat("lyra", clarify)
            try:
                import session_context
                # Keep original so a one-word answer can complete it
                session_context.get().set_pending(command, "general")
            except Exception:
                pass
            command = voice.speak(clarify, recognizer, source) or ""
            continue

        # Fast/free local matching (regex + fuzzy) already missed — take one
        # LLM-assisted pass to normalize STT noise and classify the intent,
        # then give the action router a second shot with the cleaned-up
        # text before falling through to plain chat. Only commands that
        # would otherwise have gone nowhere pay this extra latency.
        route = router.classify(command)
        working_command, intent, guessed_url = route["corrected"], route["intent"], route.get("guessed_url")

        if working_command != command:
            if _is_music_recognition_command(working_command):
                action_reply = actions.try_handle_action(working_command, engine_context)
                if action_reply:
                    try:
                        conversation_history.append({"user": command, "lyra": "[music recognition shown on Island]"})
                    except Exception:
                        pass
                    command = ""
                    continue
            action_reply = actions.try_handle_action(working_command, engine_context)
            if action_reply:
                detail = ui._pop_trace()
                ui.print_chat("lyra", action_reply, detail)
                command = voice.speak(action_reply, recognizer, source) or ""
                continue

        # Still nothing — if the router was confident enough to guess a URL
        # for this (a website not in any known list), try it and remember
        # it for next time. Scoped to websites only; see try_learned_open()
        # for why local apps aren't auto-learned the same way.
        if intent == "action" and guessed_url:
            action_reply = actions.try_learned_open(working_command, guessed_url)
            if action_reply:
                detail = ui._pop_trace()
                ui.print_chat("lyra", action_reply, detail)
                command = voice.speak(action_reply, recognizer, source) or ""
                continue

        stream = _speech_stream(recognizer, source)
        reply = _ask_brain(working_command, conversation_history, intent, stream)
        detail = ui._pop_trace()
        _mark_info(reply)
        ui.print_chat("lyra", reply, detail)
        # If Lyra asked a follow-up, remember the original request
        _remember_followup(working_command, reply)
        command = _say(reply, stream, recognizer, source) or ""

    if chain is not None and not pending_steps:
        # The last step has no next pass to check it. Its own reply already said what went
        # wrong; add which steps finished.
        stop_msg = _chain_failure(chain, chain_mark, include_reply=False)
        if stop_msg:
            ui.print_chat("lyra", stop_msg)
            voice.speak(stop_msg, recognizer, source)
    return False


