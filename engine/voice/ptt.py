"""Lyra — global Right Ctrl push-to-talk.

Hold Right Ctrl (a moment) → start recording
Release → stop, STT, process_command_chain
Pressing any other key while holding (Right Ctrl+C, ...) cancels: it's another shortcut. A quick tap
of Right Ctrl does nothing at all, so the key keeps working in your own shortcuts.

Uses a low-level Windows keyboard hook so it works without console focus.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from typing import Callable

from engine.bridge import ui

log = ui.log

# Virtual key codes
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_T = 0x54
VK_SHIFT = 0x10
VK_MENU = 0x12           # Alt
LLKHF_INJECTED = 0x10
_CTRL_VKS = frozenset({VK_CONTROL, VK_LCONTROL, VK_RCONTROL})
_WIN_VKS = frozenset({VK_LWIN, VK_RWIN})
_ALT_VKS = frozenset({0x12, 0xA4, 0xA5})
_SHIFT_VKS = frozenset({0x10, 0xA0, 0xA1})
_ALL_MODS = _CTRL_VKS | _WIN_VKS | _ALT_VKS | _SHIFT_VKS
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WH_KEYBOARD_LL = 13
LLKHF_UP = 0x0080

_started = False
_hook = None
_thread: threading.Thread | None = None
_hook_tid = 0                    # id of the thread that pumps the hook's messages (stop() posts WM_QUIT to it)
WM_QUIT = 0x0012
_holding = False
_down: set[int] = set()          # modifier keys currently held (tracked from hook events)
_discard = threading.Event()     # set when a hold is cancelled: drop the recording, don't answer
_record_stop = threading.Event()
_record_thread: threading.Thread | None = None
_on_command: Callable[[str], None] | None = None
_busy = threading.Lock()
_before_record: Callable[[], None] | None = None
_after_record: Callable[[], None] | None = None

# The low-level hook callback must return in a few milliseconds (Windows silently
# bypasses a hook that is too slow). It therefore only records what happened and
# posts it here; this dispatcher thread does the slow work (UI state, mic hand-over).
_events: "queue.SimpleQueue[tuple[str, bool]]" = queue.SimpleQueue()
_dispatcher: threading.Thread | None = None


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


def _async_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


HOLD_DELAY = 0.15        # Right Ctrl must stay down alone this long before Lyra starts listening


_chord_cache: tuple = (0.0, frozenset({VK_RCONTROL}))


def _talk_chord() -> frozenset:
    """The modifier keys that make up the talk key (Settings > Voice / Shortcuts; Right Ctrl by default).
    Cached for a moment: this runs inside the keyboard hook."""
    global _chord_cache
    now = time.monotonic()
    if now - _chord_cache[0] < 1.5:
        return _chord_cache[1]
    chord = frozenset({VK_RCONTROL})
    try:
        from engine.voice import dictation_clean
        from engine.voice import dictation_hotkey
        spec = dictation_hotkey.parse_hotkey(str(dictation_clean.pref("p2_talk_key") or "rctrl"))
        if spec is not None and spec.main is None and spec.chord:
            chord = frozenset(spec.chord)
    except Exception:
        pass
    _chord_cache = (now, chord)
    return chord


def _combo_down() -> bool:
    """The talk key is down, and nothing else of Ctrl / Alt / Shift / Win with it (that is another shortcut)."""
    chord = _talk_chord()
    return chord <= _down and not (_down - chord)


def _prune_stale(current_vk: int) -> None:
    """Forget modifiers the hook never saw released (e.g. after Win+L)."""
    for vk in list(_down):
        if vk == current_vk:
            continue
        try:
            if not _async_down(vk):
                _down.discard(vk)
        except Exception:
            pass


def _start_recording() -> None:
    global _record_thread
    if _record_thread and _record_thread.is_alive():
        # The previous turn released the lock a moment before its thread ended.
        _record_thread.join(timeout=1.0)
        if _record_thread.is_alive():
            return
    _record_stop.clear()
    # Show "Listening" FIRST: pausing the wake-word microphone below can take a
    # second, and the user must see the reaction the moment they press the keys.
    ui.set_state("listening")
    try:
        from engine.features import status_watch
        status_watch.set_mic_active(True)
    except Exception:
        pass
    hook_paused = False
    if _before_record:
        try:
            _before_record()
            hook_paused = True
        except Exception as e:
            log.warning("Could not pause the wake-word microphone: %s", e)

    def worker():
        audio_frames = []
        sample_rate = 16000
        try:
            import pyaudio
            import wave
            import io
            import numpy as np

            # Prefer same input device as wake
            device_index = None
            try:
                from engine.voice import mic_devices
                device_index = mic_devices.preferred_input_index()
            except Exception:
                pass

            pa = pyaudio.PyAudio()
            kwargs = dict(
                format=pyaudio.paInt16,
                channels=1,
                rate=sample_rate,
                input=True,
                frames_per_buffer=1024,
            )
            if device_index is not None:
                kwargs["input_device_index"] = device_index
            stream = pa.open(**kwargs)
            # Partial text while the key is held: only used to pre-warm the LLM (the command
            # itself still runs on the final Whisper transcript).
            partials = None
            try:
                from engine.voice import streaming_stt
                from engine.brain import llm_warm
                partials = streaming_stt.new_session()
            except Exception:
                pass
            live_last, live_at = "", 0.0
            while not _record_stop.is_set():
                try:
                    audio_frames.append(stream.read(1024, exception_on_overflow=False))
                except Exception:
                    break
                if partials is not None:
                    try:
                        ptext = partials.feed(audio_frames[-1])[0]
                        llm_warm.note_partial(ptext, need_wake=False)
                        # The words so far, on the island while you talk (display only: the command still runs
                        # on the accurate transcript, and the island swaps the live guess for it).
                        now = time.monotonic()
                        if ptext != live_last and now - live_at >= 0.15:
                            live_last, live_at = ptext, now
                            from engine.bridge import ui_bridge
                            ui_bridge.push_live_text(ptext, "ptt")
                    except Exception:
                        partials = None
            # Released: show "Thinking" immediately, before closing the device / transcribing.
            if not _discard.is_set():
                from engine.core import perf
                perf.begin("ptt")      # the speaker stopped: the clock for this turn starts here
                ui.set_state("thinking")
            stream.stop_stream()
            stream.close()
            pa.terminate()

            if not audio_frames:
                ui.set_state("idle")
                return

            if _discard.is_set():          # hold was cancelled (another shortcut)
                ui.set_state("idle")
                return

            pcm = b"".join(audio_frames)
            # Minimum ~0.3s of audio
            if len(pcm) < sample_rate * 2 * 0.3:
                ui.set_state("idle")
                try:
                    from engine.bridge import ui_bridge
                    ui_bridge.push_activity("ptt", "No speech detected", phase="fail", duration_ms=2000)
                except Exception:
                    pass
                return

            # Transcribe via the same STT engine as wake
            from engine.voice import voice
            status, text = voice._stt_engine.transcribe(pcm, sample_rate=sample_rate)
            if status != "ok" or not (text or "").strip():
                ui.set_state("idle")
                try:
                    from engine.bridge import ui_bridge
                    ui_bridge.push_activity("ptt", "Didn't catch that", phase="fail", duration_ms=2200)
                except Exception:
                    pass
                return

            text = text.strip()
            try:
                from engine.voice import stt_fix
                text = stt_fix.fix(text)          # same repair step as the wake-word path
            except Exception:
                pass
            # process_command_chain() owns the single user transcript entry.
            # Do not print it here as well, or PTT commands appear twice in chat.
            if _on_command:
                _on_command(text)
            else:
                try:
                    import engine.core.main as engine
                    engine.process_command_chain(text, [])
                except Exception as e:
                    log.error("PTT command failed: %s", e)
                    ui.set_state("idle")
        except Exception as e:
            log.error("PTT recording failed: %s", e)
            ui.set_state("idle")
            try:
                from engine.bridge import ui_bridge
                ui_bridge.push_activity("ptt", "Mic error", phase="fail", duration_ms=2200)
            except Exception:
                pass
        finally:
            # Whatever happened (error, empty answer, a reply that was never spoken),
            # the turn is over: do not leave the UI stuck on Listening / Thinking.
            try:
                if getattr(ui, "_last_state", "idle") in ("listening", "thinking"):
                    ui.set_state("idle")
            except Exception:
                pass
            try:
                from engine.features import status_watch
                status_watch.set_mic_active(False)
            except Exception:
                pass
            if hook_paused and _after_record:
                try:
                    _after_record()
                except Exception as e:
                    log.warning("Could not resume the wake-word microphone: %s", e)
            if _busy.locked():
                try:
                    _busy.release()
                except Exception:
                    pass

    _record_thread = threading.Thread(target=worker, daemon=True, name="lyra-ptt-record")
    _record_thread.start()


def toggle_listen() -> bool:
    """Chat's mic button: start listening, or finish the current recording. True = now listening."""
    if _record_thread and _record_thread.is_alive():
        _stop_recording()
        return False
    _start_recording()
    return True


def _stop_recording() -> None:
    _record_stop.set()


def _interrupt_speech() -> None:
    """A new Right Ctrl press while Lyra is still talking: stop her so the user can speak."""
    try:
        from engine.voice import voice
        voice.pygame.mixer.music.stop()
    except Exception:
        pass


def _begin_hold() -> None:
    """Dispatcher thread: the user pressed Right Ctrl."""
    global _holding
    if _holding:
        return
    try:
        from engine.voice import dictation
        if dictation.is_active():          # dictation owns the microphone
            log.info("PTT: dictation is running, ignoring this press")
            return
    except Exception:
        pass
    if not _busy.acquire(blocking=False):
        # Previous turn still running. If she is talking, cut her off and take the turn.
        if getattr(ui, "_last_state", "idle") == "speaking":
            _interrupt_speech()
            if not _busy.acquire(timeout=3.0):
                log.info("PTT: previous turn did not finish, ignoring this press")
                return
        else:
            log.info("PTT: busy with the previous turn, ignoring this press")
            return
    _holding = True
    _discard.clear()
    log.info("PTT: Right Ctrl pressed - listening")
    try:
        _start_recording()
    except Exception as e:
        log.error("PTT could not start recording: %s", e)
        _holding = False
        ui.set_state("idle")
        try:
            _busy.release()
        except Exception:
            pass


def _end_hold(discard: bool = False) -> None:
    """Dispatcher thread: Right Ctrl was released (or the hold was cancelled)."""
    global _holding
    if not _holding:
        return
    _holding = False
    log.info("PTT: Right Ctrl %s", "cancelled by another key" if discard else "released - transcribing")
    if discard:
        _discard.set()
    _stop_recording()
    if discard:
        # Cancelled by another shortcut: drop the indicator right away.
        ui.set_state("idle")

    # Worker releases busy after command; if worker never started, release
    def _release_later():
        if _record_thread:
            _record_thread.join(timeout=60)
        if _busy.locked() and not (_record_thread and _record_thread.is_alive()):
            try:
                _busy.release()
            except Exception:
                pass
    threading.Thread(target=_release_later, daemon=True).start()


def _dispatch_loop() -> None:
    """Runs begin/end requests posted by the keyboard hook, off the hook thread."""
    _refresh_chat_spec()
    while True:
        try:
            kind, flag = _events.get(timeout=3.0)
        except queue.Empty:                 # idle: pick up a changed shortcut from Settings
            _refresh_chat_spec()
            continue
        if time.monotonic() - _chat_spec_loaded > 3.0:
            _refresh_chat_spec()
        try:
            if kind == "begin":
                time.sleep(HOLD_DELAY)
                if not _hook_hold:           # let go early, or another key came: an ordinary Ctrl shortcut
                    continue
                _begin_hold()
            elif kind == "end":
                _end_hold(discard=flag)
            elif kind == "chat":
                _open_chat_bar()
            elif kind == "stop":
                from engine.core import turn_control
                turn_control.cancel_turn()
            elif kind == "chatmod":
                time.sleep(0.3)               # Ctrl+Win+<key> is somebody else's shortcut: only a bare hold counts
                spec = _chat_spec
                if spec is not None and spec[1] == 0 and not _chat_mod_cancel and _chat_mods_match(spec[0]):
                    _open_chat_bar()
            elif kind == "quit":
                return
        except Exception as e:
            log.error("PTT dispatcher error: %s", e)


_hook_hold = False   # hook-thread view of "Right Ctrl is being held"
_chat_mod_armed = False   # modifier-only chat combo already handled for this press
_chat_mod_cancel = False  # another key was pressed while the modifiers were down
_chat_held = False   # the chat key is physically down: key auto-repeat must not open the chat bar again

# "Switch to typing" (Settings -> Shortcuts): opens Lyra's chat bar from anywhere, whatever program is in front.
# The key is never swallowed. Default Ctrl+Win (modifiers only, so it never clashes with app shortcuts such as Ctrl+T "new tab"). Talk is Right Ctrl,
# and Left Alt belongs to dictation (tap-then-hold Ctrl is circle-to-ask).
CHAT_DEFAULT_COMBO = "ctrl+meta"
_NAMED_VKS = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "backspace": 0x08, "escape": 0x1B, "capslock": 0x14,
              "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
              "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
_chat_spec: "tuple[frozenset[str], int] | None" = None      # (modifiers, key) ; None = shortcut off
_chat_spec_loaded = 0.0


def parse_chat_combo(text: str) -> "tuple[frozenset[str], int] | None":
    """"alt+meta+t" -> (frozenset({"alt", "meta"}), 0x54); "ctrl+meta" -> (frozenset({"ctrl", "meta"}), 0). None when it is not a usable combo
    (no key, more than one key, an unknown key, or no modifier at all)."""
    mods: set[str] = set()
    keys: list[str] = []
    for part in str(text or "").lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in ("ctrl", "alt", "shift", "meta"):
            mods.add(part)
        else:
            keys.append(part)
    if not keys and len(mods) >= 2:        # modifier-only combo such as Ctrl+Win: key code 0
        return frozenset(mods), 0
    if len(keys) != 1 or not mods:
        return None
    k = keys[0]
    if len(k) == 1 and k.isalnum():
        vk = ord(k.upper())
    elif k in _NAMED_VKS:
        vk = _NAMED_VKS[k]
    elif k.startswith("f") and k[1:].isdigit() and 1 <= int(k[1:]) <= 12:
        vk = 0x70 + int(k[1:]) - 1
    else:
        return None
    return frozenset(mods), vk


def _refresh_chat_spec() -> None:
    """Read the combo from Settings. Called from the dispatcher thread, never from inside the hook."""
    global _chat_spec, _chat_spec_loaded
    _chat_spec_loaded = time.monotonic()
    try:
        from engine.core import settings_store
        if settings_store.get("shortcut_typing_enabled") is False:
            _chat_spec = None
        else:
            _chat_spec = parse_chat_combo(settings_store.get("shortcut_typing_combo") or CHAT_DEFAULT_COMBO)
    except Exception:
        _chat_spec = parse_chat_combo(CHAT_DEFAULT_COMBO)


def _chat_mods_match(spec_mods: "frozenset[str]") -> bool:
    """Exactly the modifiers of the combo are held (no extra ones)."""
    try:
        held = set()
        if _down & _CTRL_VKS:
            held.add("ctrl")
        if _down & _WIN_VKS:
            held.add("meta")
        if _async_down(VK_MENU):
            held.add("alt")
        if _async_down(VK_SHIFT):
            held.add("shift")
        return held == set(spec_mods)
    except Exception:
        return False


def _open_chat_bar() -> None:
    """The chat shortcut: open the Chat tab on the island (the same thing "open chat" does)."""
    try:
        from engine.bridge import ui_bridge
        log.info("Chat shortcut: opening the chat bar")
        ui_bridge.push_open_view("prompt")
    except Exception as e:
        log.error("Chat shortcut: could not open the chat bar: %s", e)


def _on_key(down: bool, vk: int, injected: bool = False) -> None:
    """Hold Right Ctrl to talk. Runs INSIDE the low-level hook: it must stay instant,
    so it only tracks the keys and posts begin/end requests to the dispatcher thread.
    Tracks modifiers itself because GetAsyncKeyState doesn't include the key a
    low-level hook is currently processing."""
    global _hook_hold, _chat_held, _chat_mod_armed, _chat_mod_cancel
    if injected:
        return
    if down and vk == 0x1B and getattr(ui, "_last_state", "idle") in ("speaking", "thinking"):
        _events.put(("stop", False))      # Esc while she talks or thinks: stop her (the key still reaches the app)
    is_mod = vk in _ALL_MODS
    spec = _chat_spec
    if spec is not None and vk == spec[1] and not down:
        _chat_held = False
    if down:
        _prune_stale(vk)
        if not is_mod:
            _chat_mod_cancel = True
        if spec is not None and vk == spec[1] and not is_mod:
            if not _chat_held:
                _chat_held = True
                if _chat_mods_match(spec[0]):
                    _events.put(("chat", False))
        if _hook_hold and not _combo_down():
            _hook_hold = False            # the release was missed: close the old hold
            _events.put(("end", True))
        if is_mod:
            _down.add(vk)
            if spec is not None and spec[1] == 0 and not _chat_mod_armed and _chat_mods_match(spec[0]):
                _chat_mod_armed, _chat_mod_cancel = True, False
                _events.put(("chatmod", False))
            if _combo_down() and not _hook_hold:
                _hook_hold = True
                _events.put(("begin", False))
            elif _hook_hold and not _combo_down():
                _hook_hold = False            # Left Ctrl / Win joined in: another shortcut
                _events.put(("end", True))
        elif _hook_hold:
            _hook_hold = False
            _events.put(("end", True))   # Right Ctrl+<key> belongs to Windows or another app
    else:
        _down.discard(vk)
        if not (_down & _ALL_MODS):
            _chat_mod_armed = False
        if _hook_hold and not _combo_down():
            _hook_hold = False
            _events.put(("end", False))


# Hook callback must be kept alive
_HOOK_PROC = None


def _configure_winapi():
    """Declare pointer-sized Win32 signatures and preserve GetLastError."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    return user32, kernel32


def _install_hook() -> bool:
    """Install the global hook on the same thread that pumps its message queue."""
    global _hook, _HOOK_PROC
    user32, kernel32 = _configure_winapi()

    def low_level_handler(nCode, wParam, lParam):
        if nCode >= 0:
            try:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                wp = int(wParam)
                is_up = bool(kb.flags & LLKHF_UP) or wp in (WM_KEYUP, WM_SYSKEYUP)
                is_down = wp in (WM_KEYDOWN, WM_SYSKEYDOWN) and not is_up
                injected = bool(kb.flags & LLKHF_INJECTED)
                if is_down:
                    _on_key(True, int(kb.vkCode), injected)
                elif is_up:
                    _on_key(False, int(kb.vkCode), injected)
            except Exception:
                pass
        return user32.CallNextHookEx(_hook, nCode, wParam, lParam)

    # WINFUNCTYPE is the correct Windows callback ABI. Keep the object global for
    # the entire lifetime of the hook so Windows never calls a collected callback.
    CMPFUNC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    _HOOK_PROC = CMPFUNC(low_level_handler)

    module = kernel32.GetModuleHandleW(None)
    ctypes.set_last_error(0)
    _hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, ctypes.cast(_HOOK_PROC, ctypes.c_void_p), module, 0)
    if not _hook:
        err = ctypes.get_last_error()
        log.warning("PTT: SetWindowsHookExW failed (Win32 error %s)", err)
        _HOOK_PROC = None
        return False
    return True


def start(on_command: Callable[[str], None] | None = None) -> bool:
    """Start the global Right Ctrl hook. Safe to call once."""
    global _started, _on_command, _thread
    if on_command is not None:
        _on_command = on_command
    _talk_chord()          # read the chosen key now, not inside the keyboard hook
    if _started:
        return True
    try:
        from engine.core import game_mode
        if game_mode.is_active():          # a game is running: no keyboard hook until it ends
            game_mode.defer("ptt")
            return False
    except Exception:
        pass

    # WH_KEYBOARD_LL callbacks are delivered to the installing thread. Installing
    # on the main backend thread and pumping messages on a different thread is an
    # easy way to get a hook that never works. Install + pump on one daemon thread.
    ready = threading.Event()

    def runner():
        global _started, _hook_tid
        ok = _install_hook()
        ready.ok = ok  # type: ignore[attr-defined]
        if ok:
            _hook_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            _started = True
        ready.set()
        if not ok:
            return
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        while _started:
            result = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        _unhook()                      # stop() ended the pump: remove the hook from the thread that owns it
        _hook_tid = 0

    global _dispatcher
    if _dispatcher is None or not _dispatcher.is_alive():
        _dispatcher = threading.Thread(target=_dispatch_loop, daemon=True, name="lyra-ptt-dispatch")
        _dispatcher.start()
    _thread = threading.Thread(target=runner, daemon=True, name="lyra-ptt-hook")
    _thread.start()
    ready.wait(timeout=2.0)
    if not getattr(ready, "ok", False):
        log.warning("PTT: global Right Ctrl hook could not be armed")
        return False
    log.info("PTT: Right Ctrl hold-to-talk armed (%s)", __file__)
    return True

def is_running() -> bool:
    """True while the Right Ctrl keyboard hook is installed."""
    return bool(_started and _hook)


def _unhook() -> None:
    global _hook, _HOOK_PROC
    hook, _hook = _hook, None
    if hook:
        try:
            user32, _ = _configure_winapi()
            user32.UnhookWindowsHookEx(hook)
        except Exception:
            pass
    _HOOK_PROC = None


def stop() -> None:
    """Uninstall the hook and end its threads, so start() can arm it again later."""
    global _started, _thread, _dispatcher, _hook_hold, _holding, _hook_tid
    _started = False
    if _holding:
        _discard.set()                 # a hold was under way: drop the recording, answer nothing
    _record_stop.set()
    tid, thread = _hook_tid, _thread
    if tid:
        try:
            ctypes.windll.user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)    # wakes GetMessageW: the thread unhooks and ends
        except Exception:
            pass
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=2.0)
    _unhook()                          # no-op when the hook thread already did it
    d = _dispatcher
    _events.put(("quit", False))
    if d is not None and d is not threading.current_thread():
        d.join(timeout=1.0)
    _dispatcher = None
    _thread = None
    while True:                        # nothing stale may reach the next dispatcher
        try:
            _events.get_nowait()
        except queue.Empty:
            break
    _hook_hold = False
    _holding = False
    _down.clear()
    log.info("PTT: Right Ctrl hook removed")
