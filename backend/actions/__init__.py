"""Lyra — action router. try_handle_action() is the single entry point
that main.py calls before falling through to the LLM; it dispatches to
the files/apps/system submodules based on which regex pattern matches."""

import os
import re
import time

import ui
import perf
import memory
from permissions import PermissionManager

_permission_manager = PermissionManager()

from .system import (
    CANCEL_SHUTDOWN_PATTERN, system_power_cancel,
    VOLUME_UP_PATTERN, VOLUME_DOWN_PATTERN, adjust_volume,
    MUTE_PATTERN, _send_media_key, VK_VOLUME_MUTE, VK_MEDIA_PLAY_PAUSE, VK_MEDIA_NEXT, VK_MEDIA_PREV,
    PAUSE_PLAY_PATTERN, NEXT_TRACK_PATTERN, PREV_TRACK_PATTERN,
    SHUTDOWN_PATTERN, system_power_action,
    LOCK_SCREEN_PATTERN, lock_screen,
    SCREENSHOT_PATTERN, CAPTURE_CONTEXT_PATTERN, take_screenshot,
    get_system_status, get_current_window,
    SET_VOLUME_PATTERN, SET_APP_VOLUME_PATTERN, MUTE_APP_PATTERN, RECOGNIZE_MUSIC_PATTERN, set_volume_level,
    CLIPBOARD_READ_PATTERN, get_clipboard_text,
)
from .files import (
    FOLDER_CREATE_PATTERN, FOLDER_CREATE_NO_NAME_PATTERN, WRITE_TO_FILE_PATTERN,
    FILE_CREATE_PATTERN, DELETE_PATTERN, RENAME_PATTERN, LIST_DIR_PATTERN, EXPLORER_OPEN_PATTERN,
    COMMON_LOCATIONS, resolve_location, create_folder, create_text_file,
    find_in_common_locations, delete_path, rename_path, open_explorer_at, list_directory,
)
from .apps import (
    ACTION_SEARCH_PATTERNS, ACTION_OPEN_PATTERN, ACTION_OPEN_FUZZY_PATTERN,
    SCREEN_CLICK_PATTERN, KNOWN_SITE_KEYS, KNOWN_APP_KEYS,
    open_spotify, open_site, open_url, open_local_app, click_on_screen,
    describe_screen, describe_screen_change, fuzzy_match_target,
    find_installed_app, open_installed_app, scan_installed_apps, scan_installed_apps_startup, list_installed_apps,
    right_click_on_screen, double_click_on_screen, scroll_screen, type_text, focus_window,
    send_message_in_focused_chat, control_call,
)
from .memory_commands import (
    REMEMBER_PATH_PATTERN, REMEMBER_FACT_PATTERN, remember_path_command, remember_fact_command,
)
from .windows_mgr import (
    MINIMIZE_PATTERN, MAXIMIZE_PATTERN, RESTORE_PATTERN, CLOSE_WINDOW_PATTERN,
    SWITCH_TO_PATTERN, SNAP_PATTERN, LIST_WINDOWS_PATTERN, NEXT_WINDOW_PATTERN,
    minimize_active, maximize_active, restore_active, close_active,
    switch_to, snap_active, list_windows, next_window,
)

trace = ui.trace

# Phase 1 — live screen watch
_START_WATCH_PATTERN = re.compile(
    r"\b(?:start\s+(?:screen\s+)?watch(?:ing)?|watch\s+(?:my\s+)?screen|"
    r"start\s+watching(?:\s+(?:my\s+)?screen)?)\b",
    re.IGNORECASE,
)
_WATCH_MODE_PATTERN = re.compile(
    r"\b(?:screen\s+watch|watch\s+mode)(?:\s+to)?\s+(?P<mode>off|ask|always|when\s+needed)\b",
    re.IGNORECASE,
)
_STOP_WATCH_PATTERN = re.compile(
    r"\b(?:stop\s+(?:screen\s+)?watch(?:ing)?|stop\s+watching(?:\s+(?:my\s+)?screen)?)\b",
    re.IGNORECASE,
)
_WHAT_CHANGED_PATTERN = re.compile(r"\bwhat(?:\s+has)?\s+changed(?:\s+on\s+(?:my\s+)?screen)?\b", re.IGNORECASE)
_WHAT_LOOKING_AT_PATTERN = re.compile(r"\bwhat(?:\s+am|\s+are)\s+(?:i|we)\s+looking\s+at\b", re.IGNORECASE)
_WHERE_SCREEN_PATTERN = re.compile(r"^\s*(?:where\s+is|where\s+are|can\s+you\s+find)\s+(?:the\s+)?(?P<what>.+?)(?:\s+on\s+(?:my\s+)?screen)?[?!.]*\s*$", re.IGNORECASE)
_DESCRIBE_SCREEN_PATTERN = re.compile(
    r"\b(?:what\s+(?:do\s+you\s+see|is\s+on\s+(?:my\s+)?(?:the\s+)?screen)|"
    r"what(?:'s|\s+is)\s+on\s+(?:my\s+)?(?:the\s+)?screen|"
    r"describe\s+(?:my\s+)?(?:the\s+)?screen|"
    r"screen\s+watch\s+status)\b",
    re.IGNORECASE,
)
# Phase 2 — click without requiring "on screen" (tried last, after other actions)
_SOFT_CLICK_PATTERN = re.compile(
    r"^(?:click(?:\s+on)?|press|tap|hit)\s+(?:the\s+)?(?P<desc>.+)$",
    re.IGNORECASE,
)
# Phase 3 — richer input
_RIGHT_CLICK_PATTERN = re.compile(
    r"^(?:right[-\s]?click(?:\s+on)?)\s+(?:the\s+)?(?P<desc>.+?)(?:\s+on\s+screen)?$",
    re.IGNORECASE,
)
_DOUBLE_CLICK_PATTERN = re.compile(
    r"^(?:double[-\s]?click(?:\s+on)?)\s+(?:the\s+)?(?P<desc>.+?)(?:\s+on\s+screen)?$",
    re.IGNORECASE,
)
_SCROLL_PATTERN = re.compile(
    r"\bscroll\s+(?P<dir>up|down)(?:\s+(?P<n>\d+))?\b",
    re.IGNORECASE,
)
_TYPE_PATTERN = re.compile(
    r"^(?:type|enter)\s+(?:in\s+)?(?:[\"'](?P<q>.+?)[\"']|(?P<plain>.+))$",
    re.IGNORECASE,
)
_CLICK_IN_WINDOW_PATTERN = re.compile(
    r"^(?:click(?:\s+on)?|press|tap)\s+(?:the\s+)?(?P<desc>.+?)\s+in\s+(?P<win>.+)$",
    re.IGNORECASE,
)

def normalize_command(command: str) -> str:
    """Rewrite loose phrasings into the wording the pattern matchers know (see actions/quick.py)."""
    try:
        from actions import quick as _q
        out = _q.normalize(command)
    except Exception:  # noqa: BLE001
        out = command
    try:
        import tab_sync                          # "whats on my todo" / "read my tasks" -> the to-do wording
        out = tab_sync.rewrite(out)
    except Exception:  # noqa: BLE001
        pass
    return out


def quick_reply(command: str):
    """Answer from the quick local abilities (battery, disk, maths, notes...), or None."""
    from actions import quick as _q
    reply = _q.try_quick(command)
    if reply:
        try:
            import tab_sync
            tab_sync.open_for(command, reply)
        except Exception:  # noqa: BLE001
            pass
    return reply


def try_optional_action(command: str, engine_context=None):
    """Run the isolated feature handlers before LLM-first routing can claim their commands."""
    for _module_name, _function, _with_context in (
        ("translate_mode", "try_translate", False),
        ("screen_record", "try_command", False),
        ("actions.file_ops", "try_file_ops", False),
        ("guide", "try_guide", True),
        ("file_index", "try_file_search", False),
        ("plugins.loader", "try_plugin", True),
        ("study", "try_study", True),
        ("actions.dev", "try_dev", True),
        ("macros", "try_macro", True),
        ("tab_sync", "try_calendar", True),
        ("google_workspace", "try_google", True),
    ):
        try:
            import importlib
            _handler = getattr(importlib.import_module(_module_name), _function)
            _reply = _handler(command, engine_context) if _with_context else _handler(command)
            if _reply:
                return _reply
        except Exception as _e:  # optional feature failures fall through to the normal router
            trace(f"optional action {_module_name} skipped: {_e}")
    return None


# ---------------------------------------------------------------------------
# Action router
# ---------------------------------------------------------------------------
def try_handle_action(command: str, engine_context=None):
    """Route a command to the appropriate action handler.

    Args:
        command: User command string
        engine_context: Optional dict with {'recognizer': ..., 'source': ...} for voice confirmations
    """
    command = command.strip()
    if not command:
        return None

    # --- Quick abilities (battery, disk, time left, maths, conversions, create folder/file) and
    #     rewriting of loose phrasings ("lock my pc") into the wording the matchers below know ---
    try:
        from actions import quick as _quick
        command = _quick.normalize(command)
        try:
            import tab_sync
            command = tab_sync.rewrite(command)
        except Exception:  # noqa: BLE001
            pass
        _quick_reply = _quick.try_quick(command)
        if _quick_reply:
            trace("action: quick ability")
            return _quick_reply
    except Exception as _e:  # noqa: BLE001 - never let a helper break the normal router
        trace(f"quick ability skipped: {_e}")

    if (_feature_reply := try_optional_action(command, engine_context)):
        return _feature_reply

    # --- Explicit messaging / call controls (never run automatically) ---
    send_match = re.match(r"^(?:send message|send this message)\s*[:：]\s*(.+)$", command, re.I | re.S)
    if send_match:
        return send_message_in_focused_chat(send_match.group(1))

    call_match = re.match(
        r"^(?:please\s+)?(?P<action>answer|accept|decline|reject|end|hang up|close)\s+(?:the\s+)?(?:incoming\s+)?call(?:\s+(?:on|in|using)\s+(?P<app>whatsapp|discord))?[.! ]*$",
        command, re.I,
    )
    if call_match:
        verb = call_match.group("action").lower()
        action = "answer" if verb in {"answer", "accept"} else "decline" if verb in {"decline", "reject"} else "end"
        app = call_match.group("app") or ""
        return control_call(action, app, engine_context=engine_context)

    # --- Phase 1: privacy-aware live screen watch ---
    m = _WATCH_MODE_PATTERN.search(command)
    if m:
        try:
            import screen_watch
            mode = m.group("mode").lower().replace("when needed", "ask")
            return screen_watch.set_mode(mode)
        except Exception as e:
            return f"I couldn't change screen watch mode — {e}"
    if _START_WATCH_PATTERN.search(command):
        try:
            import screen_watch
            reply = screen_watch.start_watching()
            trace("action: screen_watch.start()")
            return reply
        except Exception as e:
            return f"I couldn't start screen watch — {e}"
    if _STOP_WATCH_PATTERN.search(command):
        try:
            import screen_watch
            reply = screen_watch.stop_watching()
            trace("action: screen_watch.stop()")
            return reply
        except Exception as e:
            return f"I couldn't stop screen watch — {e}"
    if _WHAT_LOOKING_AT_PATTERN.search(command):
        ui.set_state("thinking")
        reply = describe_screen(command)
        trace("action: describe_screen(question)")
        return reply
    m = _WHERE_SCREEN_PATTERN.match(command)
    if m and not re.search(r"\b(website|page|app)\b", command, re.I):
        ui.set_state("thinking")
        reply = describe_screen(f"Look at the current screen and answer this location question: where is {m.group('what')}? Give the approximate location or UI area if visible. Do not invent it.")
        trace("action: locate screen target via vision")
        return reply
    if _WHAT_CHANGED_PATTERN.search(command):
        ui.set_state("thinking")
        reply = describe_screen_change()
        trace("action: describe_screen_change()")
        return reply
    if _DESCRIBE_SCREEN_PATTERN.search(command):
        low = command.lower()
        if "status" in low:
            try:
                import screen_watch
                return screen_watch.get_watch().status_text()
            except Exception as e:
                return f"Couldn't read watch status — {e}"
        ui.set_state("thinking")
        reply = describe_screen()
        trace("action: describe_screen()")
        return reply
    m = re.search(
        r"\b(?:tell\s+me\s+when|watch\s+for|notify\s+me\s+when)\s+(?P<what>.+)$",
        command,
        re.IGNORECASE,
    )
    if m:
        try:
            import screen_watch
            reply = screen_watch.watch_for(m.group("what").strip())
            trace("action: screen_watch.watch_for()")
            return reply
        except Exception as e:
            return f"Couldn't set watch-for — {e}"

    # --- Window management ---
    if LIST_WINDOWS_PATTERN.search(command):
        titles = list_windows()
        if not titles:
            return "I don't see any windows."
        return "Open windows: " + "; ".join(titles[:8])
    if MINIMIZE_PATTERN.search(command):
        return minimize_active()
    if MAXIMIZE_PATTERN.search(command):
        return maximize_active()
    if RESTORE_PATTERN.search(command):
        return restore_active()
    # Avoid eating "close the folder X" file commands — only plain close window
    if CLOSE_WINDOW_PATTERN.search(command) and not re.search(r"\b(file|folder|document)\b", command, re.I):
        return close_active()
    if NEXT_WINDOW_PATTERN.search(command):
        return next_window()
    m = SNAP_PATTERN.search(command)
    if m:
        return snap_active(m.group("side"))
    m = SWITCH_TO_PATTERN.search(command)
    if m:
        return switch_to(m.group("title").strip())

    # --- System control shortcuts (checked first so they beat LLM) ---
    if CANCEL_SHUTDOWN_PATTERN.search(command):
        ok, result = system_power_cancel()
        trace(f"action: system_power_cancel() -> {'ok' if ok else result}")
        return result if ok else f"I couldn't cancel it, sir — {result}."
    if RECOGNIZE_MUSIC_PATTERN.search(command):
        try:
            import music_recognition
            import ui_bridge
            ui_bridge.push_activity("music_recognition", "Listening…", phase="start", subtitle="Checking a short system-audio sample.", duration_ms=12000)
            result = music_recognition.recognize(force_audio=True)
            if result.ok:
                music_recognition.save_history(result)
                ui_bridge.push_activity(
                    "music_recognition",
                    f"{result.title} — {result.artist}",
                    phase="success",
                    duration_ms=8000,
                    subtitle="Song recognized.",
                )
                # Rich event for Island card
                try:
                    ui_bridge._push_event({
                        "type": "music_recognition",
                        "ok": True,
                        **{k: v for k, v in result.to_dict().items() if k != "ok"},
                        "ts": int(__import__("time").time() * 1000),
                    })
                except Exception:
                    pass
                bits = [result.title, result.artist]
                if result.album:
                    bits.append(result.album)
                return "That's " + " by ".join([bits[0], bits[1]]) + ("." if not result.album else f", from {result.album}.")
            else:
                ui_bridge.push_activity("music_recognition", result.message or "Couldn't identify the song.", phase="fail", duration_ms=3200)
                return result.message or "Couldn't identify the song."
        except Exception as e:
            return f"Music recognition failed — {e}"

    m = MUTE_APP_PATTERN.search(command)
    if m:
        app_name = (m.group("app") or "").strip()
        want_mute = not bool(re.search(r"\bunmute\b", command, re.I))
        try:
            import app_volume
            name, muted = app_volume.mute_app_by_name(app_name, want_mute)
            trace(f"action: mute_app('{name}', {muted})")
            return f"{name} {'muted' if muted else 'unmuted'}."
        except Exception as e:
            return f"Couldn't mute {app_name} — {e}"

    m = SET_APP_VOLUME_PATTERN.search(command)
    if m:
        app_name = (m.group("app") or m.group("app2") or "").strip()
        pct = int(m.group("pct") or m.group("pct2") or 50)
        pct = max(0, min(100, pct))
        if app_name.lower() not in ("volume", "the volume", "master", "system"):
            try:
                import app_volume
                name, level = app_volume.set_app_by_name(app_name, pct / 100.0)
                trace(f"action: set_app_volume('{name}', {pct})")
                return f"{name} set to {int(round(level * 100))}%."
            except Exception as e:
                if "no audio session" not in str(e).lower():
                    return f"Couldn't set {app_name} volume — {e}"

    m = SET_VOLUME_PATTERN.search(command)
    if m:
        level = int(m.group(1) or m.group(2) or 50)
        ok, msg = set_volume_level(level)
        trace(f"action: set_volume_level({level}) -> {ok}")
        return msg if ok else f"Couldn't set volume — {msg}"
    if VOLUME_UP_PATTERN.search(command):
        adjust_volume("up", steps=5)
        trace("action: volume up (media key)")
        return "Volume up."
    if VOLUME_DOWN_PATTERN.search(command):
        adjust_volume("down", steps=5)
        trace("action: volume down (media key)")
        return "Volume down."
    if MUTE_PATTERN.search(command):
        _send_media_key(VK_VOLUME_MUTE)
        trace("action: mute toggle (media key)")
        return "Toggling mute."
    if CLIPBOARD_READ_PATTERN.search(command):
        text = get_clipboard_text()
        trace("action: get_clipboard_text()")
        if not text:
            return "Your clipboard is empty, sir."
        # Keep reply short for TTS
        preview = text if len(text) <= 200 else text[:200] + "…"
        return f"Clipboard says: {preview}"
    # --- Media + session context ("pause the video" … later "play it again") ---
    import session_context as _sctx

    again_play = re.search(
        r"\b(?:play\s+(?:it|that|the\s+video)?\s*again|resume(?:\s+(?:it|that|the\s+video|playback))?|"
        r"continue(?:\s+(?:the\s+)?(?:video|playback))?|unpause(?:\s+(?:it|that))?)\b",
        command,
        re.IGNORECASE,
    )
    explicit_pause = re.search(
        r"\b(?:pause(?:\s+(?:it|that|the\s+video|playback))?|stop\s+(?:the\s+)?(?:video|playback))\b",
        command,
        re.IGNORECASE,
    )
    # Only treat as media play when it's clearly playback — not "play X on Spotify"
    explicit_play = bool(re.search(
        r"^(?:play(?:\s+(?:the\s+)?(?:video|it|that))?)$",
        command.strip(),
        re.IGNORECASE,
    )) and not again_play

    if again_play or explicit_pause or explicit_play:
        # Prefer dedicated pause/play when we know state; else toggle
        win = _sctx.current_window_title()
        if again_play or explicit_play or explicit_pause:
            # Refocus last media window when user says "again" / "it"
            if again_play or re.search(r"\b(it|that|the\s+video)\b", command, re.I):
                _sctx.refocus_media_window()
                time.sleep(0.25)
                win = _sctx.current_window_title() or _sctx.get().last_media_window or win

        if explicit_pause:
            # If already paused, still send toggle only if state unknown; try pause via toggle once
            _send_media_key(VK_MEDIA_PLAY_PAUSE)
            _sctx.get().note_media("pause", window_title=win, state="paused")
            trace("action: pause (media key + session)")
            where = f" on {_sctx.get().last_media_window}" if _sctx.get().last_media_window else ""
            return f"Paused{where}."
        if again_play or explicit_play:
            state = _sctx.get().last_media_state
            # If we believe it's paused, toggle to play; if unknown, toggle
            _send_media_key(VK_MEDIA_PLAY_PAUSE)
            _sctx.get().note_media("play", window_title=win, state="playing")
            trace("action: play/resume (media key + session)")
            where = f" on {_sctx.get().last_media_window}" if _sctx.get().last_media_window else ""
            return f"Playing again{where}." if again_play else f"Playing{where}."

    if PAUSE_PLAY_PATTERN.search(command):
        win = _sctx.current_window_title()
        _send_media_key(VK_MEDIA_PLAY_PAUSE)
        _sctx.get().note_media("toggle", window_title=win)
        trace("action: play/pause toggle (media key + session)")
        return "Toggling play/pause."
    if NEXT_TRACK_PATTERN.search(command):
        win = _sctx.current_window_title()
        _send_media_key(VK_MEDIA_NEXT)
        _sctx.get().note_media("next", window_title=win)
        trace("action: next track (media key)")
        return "Skipping to the next track."
    if PREV_TRACK_PATTERN.search(command):
        win = _sctx.current_window_title()
        _send_media_key(VK_MEDIA_PREV)
        _sctx.get().note_media("prev", window_title=win)
        trace("action: previous track (media key)")
        return "Going back a track."
    m = SHUTDOWN_PATTERN.search(command)
    if m:
        verb = re.sub(r"\s+", "", m.group("verb"))  # "shut down" -> "shutdown"

        # Permission check for destructive system action
        allowed, reason = _permission_manager.check_permission(
            "system_power_action",
            f"{verb} the computer",
            engine_context
        )
        if not allowed:
            trace(f"action: system_power_action('{verb}') -> denied by user")
            return reason or "I won't do that without your confirmation, sir."

        ok, result = system_power_action(verb)
        trace(f"action: system_power_action('{verb}') -> {'ok' if ok else result}")
        if ok:
            return result
        return f"I couldn't do that, sir — {result}."
    if LOCK_SCREEN_PATTERN.search(command):
        lock_screen()
        trace("action: lock workstation")
        return "Locking the screen."
    if CAPTURE_CONTEXT_PATTERN.search(command) or (
        SCREENSHOT_PATTERN.search(command) and re.search(r"\b(context|vision|look|see|describe)\b", command, re.I)
    ):
        try:
            import screen_capture
            result = screen_capture.capture(mode="full", keep_context=True)
            if result.get("ok"):
                path = result.get("path") or ""
                return f"Screen captured for context ({result.get('width')}x{result.get('height')})" + (f" — {path}" if path else ".")
            return result.get("message") or "Couldn't capture the screen."
        except Exception as e:
            return f"Couldn't capture the screen — {e}"
    if SCREENSHOT_PATTERN.search(command):
        path = take_screenshot()
        trace(f"action: screenshot -> {path}")
        return f"Screenshot saved to {path}." if not path.startswith("failed") else f"I couldn't take the screenshot — {path}."

    # --- Memory: teach Lyra a path alias or a fact (path checked first —
    # it's the more specific pattern, and would otherwise get swallowed by
    # the generic "remember X is Y" fact pattern below) ---
    m = REMEMBER_PATH_PATTERN.search(command)
    if m:
        reply = remember_path_command(m.group("alias").strip(), m.group("path").strip())
        trace(f"action: remember_path('{m.group('alias').strip()}')")
        return reply
    m = REMEMBER_FACT_PATTERN.search(command)
    if m:
        reply = remember_fact_command(m.group("key").strip(), m.group("value").strip())
        trace(f"action: remember_fact('{m.group('key').strip()}')")
        return reply

    # --- File / folder operations ---
    m = FOLDER_CREATE_PATTERN.search(command)
    if m:
        name = m.group("name").strip()
        location = resolve_location(m.group("location"))
        if location:
            ok, result = create_folder(os.path.join(location, name))
            trace(f"action: create_folder('{name}', in='{location}') -> {'ok' if ok else result}")
            if ok:
                open_explorer_at(location)
                return f"Created the folder {name} in {location}, sir."
            return f"I couldn't create that folder, sir — {result}."
        return None

    m = FOLDER_CREATE_NO_NAME_PATTERN.search(command)
    if m:
        location = resolve_location(m.group("location"))
        if location:
            base_name = "New Folder"
            candidate = base_name
            n = 2
            while os.path.exists(os.path.join(location, candidate)):
                candidate = f"{base_name} ({n})"
                n += 1
            ok, result = create_folder(os.path.join(location, candidate))
            trace(f"action: create_folder('{candidate}', in='{location}') -> {'ok' if ok else result}")
            if ok:
                open_explorer_at(location)
                return f"Created a new folder in {location}, sir."
            return f"I couldn't create that folder, sir — {result}."
        return None

    m = WRITE_TO_FILE_PATTERN.search(command)
    if m:
        content = (m.group("quoted_content") or m.group("content") or "").strip()
        name = m.group("name").strip()
        location = COMMON_LOCATIONS["desktop"]()
        ok, result = create_text_file(os.path.join(location, name), content=content)
        trace(f"action: write_to_file('{name}', content='{content[:40]}') -> {'ok' if ok else result}")
        if ok:
            return f"Written to {name} on your Desktop, sir."
        return f"I couldn't write to that file, sir — {result}."

    m = FILE_CREATE_PATTERN.search(command)
    if m:
        name = m.group("name").strip()
        location = resolve_location(m.group("location"))
        if location:
            ok, result = create_text_file(os.path.join(location, name))
            trace(f"action: create_file('{name}', in='{location}') -> {'ok' if ok else result}")
            if ok:
                open_explorer_at(location)
                return f"Created the file {name} in {location}, sir."
            return f"I couldn't create that file, sir — {result}."
        return None

    m = DELETE_PATTERN.search(command)
    if m:
        raw = m.group("path").strip()
        path = (
            resolve_location(raw)
            or find_in_common_locations(raw)
            or (os.path.normpath(os.path.expandvars(raw)) if raw else None)
        )
        if path:
            # Permission check for delete
            allowed, reason = _permission_manager.check_permission(
                "delete_path",
                f"delete the file or folder at {path}",
                engine_context
            )
            if not allowed:
                trace(f"action: delete_path('{path}') -> denied by user")
                return reason or "I won't delete that without your confirmation, sir."

            ok, result = delete_path(path)
            trace(f"action: delete('{path}') -> {'ok' if ok else result}")
            if ok:
                return f"Deleted {path}, sir."
            return f"I couldn't delete that, sir — {result}."
        return f"I couldn't find {raw} anywhere obvious, sir — can you tell me which folder it's in?"

    m = RENAME_PATTERN.search(command)
    if m:
        old = resolve_location(m.group("old").strip()) or os.path.normpath(m.group("old").strip())
        new = resolve_location(m.group("new").strip()) or os.path.normpath(m.group("new").strip())

        # Permission check for rename
        allowed, reason = _permission_manager.check_permission(
            "rename_path",
            f"rename {old} to {new}",
            engine_context
        )
        if not allowed:
            trace(f"action: rename_path('{old}' -> '{new}') -> denied by user")
            return reason or "I won't rename that without your confirmation, sir."

        ok, result = rename_path(old, new)
        trace(f"action: rename('{old}' -> '{new}') -> {'ok' if ok else result}")
        if ok:
            return f"Renamed to {result}, sir."
        return f"I couldn't rename that, sir — {result}."

    m = LIST_DIR_PATTERN.search(command)
    if m:
        location = resolve_location(m.group("location"))
        if location:
            trace(f"action: list_directory('{location}')")
            return list_directory(location)
        return None

    m = EXPLORER_OPEN_PATTERN.search(command)
    if m:
        location_raw = m.group("location")
        location = resolve_location(location_raw) if location_raw else os.path.expanduser("~")
        if location:
            open_explorer_at(location)
            trace(f"action: open_explorer('{location}')")
            return f"Opening File Explorer at {location}, sir." if location_raw else "Opening File Explorer, sir."
        return None

    # --- Installed-app discovery ---
    # Refresh the local Windows application index on demand.
    if re.search(r"\b(?:rescan|refresh|update)\b.*\b(?:apps?|applications?)\b", command, re.I):
        count = scan_installed_apps(force=True)
        return f"Application list refreshed. I found {count} apps, sir."

    if re.search(r"\b(?:list|show)\b.*\b(?:installed\s+)?(?:apps?|applications?)\b", command, re.I):
        names = list_installed_apps(20)
        if not names:
            return "I couldn't find any installed applications, sir."
        return "Installed apps include: " + ", ".join(names)

    # Bare target only when clearly a known app/site — never random words.
    bare = command.strip()
    words_bare = re.findall(r"[a-zA-Z0-9']+", bare)
    if bare and 1 <= len(words_bare) <= 2:
        low_bare = " ".join(w.lower() for w in words_bare)
        _blocked_bare = {
            "hello", "hi", "hey", "yo", "sup", "huh", "what", "wtf", "lol",
            "ok", "okay", "yes", "no", "yeah", "yep", "nope", "idk", "hm",
            "hmm", "ah", "oh", "thanks", "thank", "please", "sorry", "bruh",
            "sure", "fine", "cool", "nice", "wow", "ugh", "nvm", "nevermind",
            "ahem", "aheem", "sir", "help", "test",
        }
        if low_bare in _blocked_bare:
            pass
        elif low_bare in ("spotify",) or fuzzy_match_target(bare, ["spotify"]) == "spotify":
            open_spotify()
            trace("action: open_spotify() [bare name]")
            return "Opening Spotify, sir."
        elif low_bare in KNOWN_APP_KEYS:
            if open_local_app(low_bare):
                return f"Opening {low_bare.title()}, sir."
        elif low_bare in KNOWN_SITE_KEYS:
            if open_installed_app(low_bare):
                return f"Opening {low_bare.title()} app, sir."
            open_site(low_bare)
            trace(f"action: open_site('{low_bare}') [bare name]")
            return f"Opening {low_bare.title()}, sir."
        else:
            entry = find_installed_app(bare)
            if entry and entry.name:
                en = entry.name.lower().replace(" ", "")
                bn = low_bare.replace(" ", "")
                if bn in en or en in bn:
                    if open_installed_app(bare):
                        return f"Opening {entry.name}, sir."

    # --- App / site actions ---
    for pattern in ACTION_SEARCH_PATTERNS:
        m = pattern.search(command)
        if m:
            app_raw = m.group("app").strip()
            query = m.group("query").strip()
            app_low = app_raw.lower()
            # Exact match first, then fuzzy fallback for STT mishearings
            # (e.g. "spitofy" -> "spotify") — see fuzzy_match_target().
            if "spotify" in app_low or fuzzy_match_target(app_raw, ["spotify"]):
                try:                                  # "search" = song cards (a tap plays); "play" = play the best match now
                    import spotify_island
                    if spotify_island.intent_of(command) == "play":
                        shown = spotify_island.play_query(query)
                        if shown:
                            trace(f"action: spotify island play (query='{query}')")
                            return shown
                    else:
                        shown = spotify_island.show(query)
                        if shown:
                            trace(f"action: spotify island results (query='{query}')")
                            return shown
                except Exception:
                    pass  # fall back to opening Spotify directly
                open_spotify(query)
                trace(f"action: open_spotify(query='{query}')")
                return f"Playing {query} on Spotify, sir."
            app_key = next((k for k in KNOWN_SITE_KEYS if k in app_low), None) \
                or fuzzy_match_target(app_raw, KNOWN_SITE_KEYS)
            if app_key == "youtube":
                try:
                    import yt_island
                    shown = yt_island.show(query)
                    if shown:
                        trace(f"action: youtube island results (query='{query}')")
                        return shown
                except Exception:
                    pass  # fall through to the browser search page
            if app_key:
                fuzzy_note = "" if app_key in app_low else f" [fuzzy-corrected from '{app_raw}']"
                trace(f"action: open_site('{app_key}', query='{query}'){fuzzy_note}")
                open_site(app_key, query)
                return f"Searching for {query} on {app_key.title()}, sir."
            return None

    m = ACTION_OPEN_PATTERN.search(command)
    if m:
        low_command = command.lower()
        verb_match = re.match(r"\s*(open|go\s+to|launch|start)\b", command, re.I)
        verb = (verb_match.group(1).lower() if verb_match else "open")
        site_hits = [k for k in KNOWN_SITE_KEYS if k in low_command]
        app_hits = [k for k in KNOWN_APP_KEYS if k in low_command]

        if "spotify" in low_command:
            open_spotify()
            trace("action: open_spotify() [no query]")
            return "Opening Spotify, sir."

        # For "open/launch/start", prefer a real local app when a name is
        # available in both the app and website tables (Discord, WhatsApp, etc.).
        if verb != "go to" and app_hits:
            best = app_hits[0]
            if open_local_app(best):
                trace(f"action: open_local_app('{best}')")
                return f"Opening {best.title()}, sir."

        if site_hits:
            specific = [s for s in site_hits if s != "google"]
            best = specific[0] if specific else site_hits[0]
            # Prefer installed desktop app over website when both exist
            # (WhatsApp, Discord, etc.)
            if verb != "go to" and open_installed_app(best):
                return f"Opening {best.title()} app, sir."
            if verb != "go to" and open_local_app(best):
                trace(f"action: open_local_app('{best}') [preferred over site]")
                return f"Opening {best.title()}, sir."
            open_site(best)
            trace(f"action: open_site('{best}')")
            return f"Opening {best.title()}, sir."

        if app_hits:
            best = app_hits[0]
            if open_local_app(best):
                trace(f"action: open_local_app('{best}')")
                return f"Opening {best.title()}, sir."
    else:
        # No exact key matched anywhere in the command — try the loose
        # fallback pattern + fuzzy matching before giving up on this being
        # an "open X" command at all. Handles STT mangling the target name
        # itself (e.g. "open spitofy", "open note pad").
        m = ACTION_OPEN_FUZZY_PATTERN.search(command)
        if m:
            app_raw = m.group("app").strip()
            app_low = app_raw.lower()

            # Sites the router successfully guessed before (see
            # try_learned_open() below) — an instant lookup, no LLM call.
            learned_url = memory.get_learned_site(app_low)
            if learned_url:
                open_url(learned_url)
                trace(f"action: open_url('{learned_url}') [learned site: '{app_low}']")
                return f"Opening {app_low.title()}, sir."

            # New: the target does not need to exist in KNOWN_APP_KEYS.
            # Search the Windows-installed application index.
            installed = find_installed_app(app_raw)
            if installed and open_installed_app(app_raw):
                trace(
                    f"action: open_installed_app('{installed.name}') "
                    f"[matched from '{app_raw}']"
                )
                return f"Opening {installed.name}, sir."

            target = fuzzy_match_target(app_raw, KNOWN_SITE_KEYS + KNOWN_APP_KEYS + ["spotify"])
            if target == "spotify":
                open_spotify()
                trace(f"action: open_spotify() [no query, fuzzy-corrected from '{app_raw}']")
                return "Opening Spotify, sir."
            if target in KNOWN_SITE_KEYS:
                if open_installed_app(target) or open_local_app(target):
                    return f"Opening {target.title()} app, sir."
                open_site(target)
                trace(f"action: open_site('{target}') [fuzzy-corrected from '{app_raw}']")
                return f"Opening {target.title()}, sir."
            if target in KNOWN_APP_KEYS:
                if open_local_app(target):
                    trace(f"action: open_local_app('{target}') [fuzzy-corrected from '{app_raw}']")
                    return f"Opening {target.title()}, sir."

    # --- Phase 3: scroll / type / right-click / double-click / click-in-window ---
    m = _SCROLL_PATTERN.search(command)
    if m:
        direction = (m.group("dir") or "down").lower()
        amount = int(m.group("n") or 3)
        reply = scroll_screen(direction, amount)
        trace(f"action: scroll_screen({direction}, {amount})")
        return reply
    m = _TYPE_PATTERN.match(command.strip())
    if m:
        text = (m.group("q") or m.group("plain") or "").strip()
        # Strip trailing "into the field" style noise lightly
        text = re.sub(r"\s+in(?:to)?\s+(?:the\s+)?(?:search\s+)?(?:box|field|bar)$", "", text, flags=re.I).strip()
        reply = type_text(text)
        trace(f"action: type_text({text!r})")
        return reply
    m = _RIGHT_CLICK_PATTERN.match(command.strip())
    if m:
        desc = m.group("desc").strip()
        reply = right_click_on_screen(desc, engine_context=engine_context)
        trace(f"action: right_click({desc!r})")
        return reply
    m = _DOUBLE_CLICK_PATTERN.match(command.strip())
    if m:
        desc = m.group("desc").strip()
        reply = double_click_on_screen(desc, engine_context=engine_context)
        trace(f"action: double_click({desc!r})")
        return reply
    m = _CLICK_IN_WINDOW_PATTERN.match(command.strip())
    if m:
        desc = m.group("desc").strip()
        win = m.group("win").strip()
        focus_window(win)
        reply = click_on_screen(desc, engine_context=engine_context, require_confirm=True, window_title=win)
        trace(f"action: click_in_window({desc!r}, {win!r})")
        return reply

    # --- Screen click with anchor ("on screen" / "here" / "there") ---
    m = SCREEN_CLICK_PATTERN.search(command)
    if m:
        desc = m.group("desc").strip()
        reply = click_on_screen(desc, engine_context=engine_context, require_confirm=True)
        trace(f"action: click_on_screen('{desc}') -> {reply!r}")
        return reply

    # --- Phase 2: soft click without "on screen" (only if nothing else matched) ---
    m = _SOFT_CLICK_PATTERN.match(command.strip())
    if m:
        desc = m.group("desc").strip()
        low = desc.lower()
        if low in ("play", "pause", "play pause", "next", "previous"):
            return None
        reply = click_on_screen(desc, engine_context=engine_context, require_confirm=True)
        trace(f"action: soft_click_on_screen('{desc}') -> {reply!r}")
        return reply

    return None


def try_learned_open(command: str, guessed_url: str):
    """Called from main.py only after try_handle_action() (including its
    fuzzy fallback above) already found nothing, AND router.classify() came
    back with a confident guessed_url for a website. Opens it and — if
    nothing raised — remembers the mapping so next time it's the instant
    memory.get_learned_site() lookup above instead of another LLM call.

    Honest limitation: webbrowser.open() just launches the browser and
    returns; there's no way from here to confirm the page actually loaded
    correctly. "Learned" means "the router was confident and opening it
    didn't error out" — not "verified working". If the router's guess was
    ever wrong, ask to forget it via memory_commands and it'll go back to
    asking the router fresh each time."""
    if not (isinstance(guessed_url, str) and guessed_url.startswith(("http://", "https://"))):
        return None

    m = ACTION_OPEN_FUZZY_PATTERN.search(command)
    name = m.group("app").strip().lower() if m else command.strip().lower()

    try:
        open_url(guessed_url)
    except Exception as e:
        trace(f"action: learned-site open failed for '{name}' -> {guessed_url}: {e}")
        return None

    memory.remember_learned_site(name, guessed_url)
    trace(f"action: open_url('{guessed_url}') [newly learned as '{name}']")
    return f"Opening {name.title()}, sir. I'll remember that one for next time."


# ---------------------------------------------------------------------------
# Activity log: remember every command that produced an action reply
# ---------------------------------------------------------------------------
_try_handle_action_impl = try_handle_action


def try_handle_action(command: str, engine_context=None):  # noqa: F811 - intentional wrapper
    with perf.span("action"):
        reply = _try_handle_action_impl(command, engine_context)
    if reply:
        try:
            import activity_log
            activity_log.record("action", command, str(reply))
        except Exception:  # noqa: BLE001
            pass
        try:
            import tab_sync                      # to-do / weather / calendar / timer question: open its tab too
            tab_sync.open_for(command, str(reply))
        except Exception:  # noqa: BLE001
            pass
    return reply
