"""Lyra — process entry: voice mode, text mode, and startup.

The command pipeline lives in command_pipeline.py; pure helpers in command_helpers.py."""

import os
import sys
import time
import threading

# Windowed (no-console) build: PyInstaller leaves stdin/stdout/stderr as None, and
# libraries that write progress bars or warnings would crash on that.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r")

# Hide the console window of every child process (powershell, shutdown, ffmpeg...). Must stay first.
from engine.core import no_console  # noqa: F401

# pydub is imported by the audio/wake-word stack on some installations.
# Give it a bundled ffmpeg binary when available so startup does not emit the
# misleading "Couldn't find ffmpeg" warning. The recognition path itself uses
# WAV bytes and does not require ffmpeg.
try:
    import imageio_ffmpeg
    os.environ.setdefault("FFMPEG_BINARY", imageio_ffmpeg.get_ffmpeg_exe())
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        from pydub import AudioSegment as _AS
    _AS.converter = os.environ["FFMPEG_BINARY"]
except Exception:
    pass

from engine.bridge import ui
from engine.voice import voice
from engine import actions


from engine.core.command_pipeline import process_command_chain

def run_text_mode(conversation_history: list) -> None:
    """Typed-input loop. Returns as soon as ui.INPUT_MODE flips away from
    'text', e.g. via the '/voice' command."""
    ui.print_chat("system", "Typing mode. Type a message, '/voice' to switch back, '/details' to toggle detail view, '/exit' to quit.")
    while ui.INPUT_MODE == "text":
        try:
            raw = input("> ")
        except (EOFError, KeyboardInterrupt):
            raise KeyboardInterrupt
        command = raw.strip()
        if not command:
            continue

        low = command.lower()
        if low == "/voice":
            ui.print_chat("user", command)
            ui.switch_to_voice_mode()
            return
        if low in ("/details", "/detail"):
            ui.print_chat("user", command)
            ui.toggle_verbose()
            continue
        if low in ("/exit", "/quit"):
            ui.print_chat("user", command)
            ui.print_chat("lyra", "Powering down systems. Goodbye, sir.")
            voice.speak("Powering down systems. Goodbye, sir.")
            sys.exit()

        process_command_chain(command, conversation_history)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def _shortcut_summary() -> str:
    """The startup line about keys, built from the real settings so it can't drift from what is armed."""
    def pretty(combo: str) -> str:
        names = {"meta": "Win", "win": "Win", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift"}
        return "+".join(names.get(p.strip().lower(), p.strip().upper()) for p in str(combo).split("+") if p.strip())
    parts = []
    try:
        from engine.core import settings_store
        if settings_store.get("shortcut_typing_enabled") is not False:
            combo = settings_store.get("shortcut_typing_combo") or "ctrl+meta"
            parts.append(f"{pretty(combo)} = typing mode")
    except Exception:
        pass
    parts.append("tap Ctrl then hold Ctrl = circle to ask")
    for key, default, label in (("shortcut_wheel_combo", "ctrl+capslock", "tab wheel"),):
        try:
            combo = settings_store.get(key)
            combo = default if combo is None else str(combo)
            if combo:
                parts.append(f"{pretty(combo)} = {label}")
        except Exception:
            pass
    return ", ".join(parts) + "."


def main():
    ui.log.info("Lyra starting up.")
    try:
        from engine.core import lowcpu
        lowcpu.apply_process_priority()               # below normal: games and apps always come first
    except Exception:
        pass
    try:
        from engine.bridge.ui_bridge import start_bridge
        if start_bridge():
            ui.log.info("UI bridge started (http://127.0.0.1:8765)")
        else:
            # Voice replies can still run without the bridge, but the island,
            # captions, and UI controls would be disconnected. Do not leave a
            # healthy-looking source process running in that broken state.
            ui.log.error("Exiting: the UI bridge could not start (see the line above).")
            sys.exit(1)
    except Exception as e:
        ui.log.exception("UI bridge failed to start; exiting: %s", e)
        sys.exit(1)

    # Speech models. They used to load one after another on the main thread (Whisper), then two more
    # threads at once, so the first ~25 s after launch were a pile-up of CPU and RAM that stuttered
    # every window. Now ONE low-priority thread loads them in turn, and the hotkeys arm straight away:
    # a press that arrives early simply waits for the model (SpeechRecognizer.transcribe loads lazily).
    #   1. Whisper (first run downloads it; the island shows "Downloading voice models...")
    #   2. the small streaming model (partial text, early turn end, dictation)
    #   3. Parakeet, only once the PC is quiet, and only when the user can use it
    def _speech_warmup():
        try:
            if os.name == "nt":
                import ctypes
                _k = ctypes.windll.kernel32
                _k.SetThreadPriority(_k.GetCurrentThread(), -1)     # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass
        # Whisper is only the fallback now (Parakeet is the voice model): do not load ~1 GB of it at every launch when
        # Parakeet is installed. It still loads by itself the first time a sentence needs it.
        try:
            from engine.voice import stt_engines
            _need_whisper = not stt_engines.parakeet_ready()
        except Exception:
            _need_whisper = True
        if _need_whisper:
            try:
                voice._stt_engine.load()
            except Exception as e:
                ui.log.error("Speech model unavailable (%s) - voice commands will not work.", e)
        try:
            from engine.core import settings_store as _ss
            _light = _ss.feature_on("light_mode")      # on by default (Settings -> Features -> Light mode)
        except Exception:
            _light = True
        if not _light:
            try:
                from engine.voice import streaming_stt
                streaming_stt.load()
            except Exception as e:
                ui.log.warning("Streaming speech not started: %s", e)
        try:
            time.sleep(4)    # let the UI and the first hotkey presses settle
            import psutil
            for _ in range(20):   # ...then wait (up to ~20 s) for a quiet PC so the UI never stutters under the load
                if psutil.cpu_percent(interval=1.0) < 50:
                    break
        except Exception:
            pass
        try:
            from engine.voice import stt_engines
            if not _light:        # light mode: the speech models load on first use, not at launch (they froze the mouse)
                stt_engines.preload()
            stt_engines.start_idle_unloader()     # Parakeet and the streaming model give their RAM back after a few idle minutes
        except Exception as e:
            ui.log.warning("Parakeet preload not started: %s", e)
        # The openai package is no longer imported at launch (it cost ~0.9 s of startup). Load it here, in the background
        # and after everything above, so the first command that needs the LLM is not the one that pays for it.
        try:
            import openai  # noqa: F401
        except Exception:
            pass
    threading.Thread(target=_speech_warmup, name="lyra-speech-warmup", daemon=True).start()
    try:    # recording keyboard shortcuts (Settings -> Shortcuts): Ctrl+Alt+R / Ctrl+Alt+P unless changed or removed
        from engine.features import record_hotkey
        record_hotkey.apply()
    except Exception as e:
        ui.log.debug("record hotkeys not started: %s", e)
    try:    # the other keyboard shortcuts (play / pause, next, open a tab ...): each has its own key in Settings -> Shortcuts
        from engine.core import hotkeys
        hotkeys.apply()
    except Exception as e:
        ui.log.debug("keyboard shortcuts not started: %s", e)

    # Build the Windows app index once at startup. This lets Lyra launch
    # applications that are not hard-coded in actions/apps.py.
    try:
        app_count = actions.scan_installed_apps_startup()   # saved index at once, refreshed in the background when old
        ui.log.info("Indexed %d installed applications.", app_count)
    except Exception as e:
        ui.log.warning("Installed-app scan failed during startup: %s", e)

    # Voice: hold Right Ctrl and talk (ptt.py). There is no always-listening wake word any more:
    # the microphone is only open while a key is held.
    conversation_history = []

    # Global Right Ctrl push-to-talk → process_command_chain
    def _ptt_command(text: str) -> None:
        try:
            import engine.voice.dictation as _dictation
            if _dictation.is_active():         # words spoken into the dictation key are text, never a command
                return
            process_command_chain(text, conversation_history)
        except Exception as e:
            ui.log.error("PTT command error: %s", e)
            ui.set_state("idle")

    try:
        from engine.voice import ptt
        ptt.start(on_command=_ptt_command)
        ui.log.info("Right Ctrl push-to-talk enabled")
    except Exception as e:
        ui.log.warning("PTT not available: %s", e)

    # Dictation: hold (or toggle) a shortcut and Lyra types what you say into the focused window.
    try:
        from engine.voice import dictation_hotkey
        if dictation_hotkey.start():
            ui.log.info("Dictation shortcut enabled")
    except Exception as e:
        ui.log.warning("Dictation shortcut not available: %s", e)

    # Restore the user's privacy-aware screen-watch mode. "ask" never starts
    # a background capture loop; "always" starts the lightweight local watcher.
    try:
        from engine.features import screen_watch; from engine.core import settings_store
        saved_screen_mode = str(settings_store.get("screen_watch_mode") or "off")
        if saved_screen_mode in {"off", "ask", "always"}:
            screen_watch.set_mode(saved_screen_mode)
            ui.log.info("Screen watch mode restored: %s", saved_screen_mode)
    except Exception as e:
        ui.log.debug("Screen watch restore skipped: %s", e)


    ui.print_terminal(  # terminal only — not shown in the chat window
        "system",
        f"Lyra is online. Hold Right Ctrl to talk. {_shortcut_summary()}",
    )
    ui.print_terminal("system", f"Log file: {ui.LOG_PATH}")
    # Silent startup — no spoken greeting until the user wakes Lyra.

    try:
        # PTT owns the microphone. The worker publishes thinking/speaking/idle
        # itself. Do NOT publish "idle" from this heartbeat: speak() runs in
        # the PTT worker while this loop is still alive, so doing that here
        # would overwrite the speaking state every 120 ms and make the island
        # voice wave disappear almost immediately.
        ui.set_state("idle")
        while True:
            if ui.INPUT_MODE == "voice":
                time.sleep(0.12)
            else:
                run_text_mode(conversation_history)

    except KeyboardInterrupt:
        ui.print_chat("system", "Manual interrupt received. Shutting down, sir.")
        voice.speak("Manual interrupt received. Shutting down, sir.")
        sys.exit(0)
    finally:
        pass



if __name__ == "__main__":
    main()
