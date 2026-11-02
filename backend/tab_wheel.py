"""Lyra — Tab wheel shortcut.

Hold Ctrl+CapsLock: the tab wheel (a ring of tabs, split into pages) appears at your cursor. Point
at a tab and let go to open it. A quick tap (shorter than TAP_SECONDS) leaves the wheel open
instead, so you can click a tab or scroll round the ring; tap again, press Esc, or click
outside it to close it.

  Hold ... open the wheel while the keys are down, release = open the tab you point at
  Tap .... the wheel stays open
  Shift .. never: Ctrl+Shift belongs to Circle to ask, so the wheel ignores it

Why Ctrl+CapsLock: it is two keys, and nothing else on the screen uses it. CapsLock does flip the
Caps Lock light, so the shortcut puts it back the way it was once the keys are up
(RESTORE_CAPS); it will not leave you typing in capitals.

The backend only reports the key presses ("wheel" events); the wheel window draws the ring and
the island goes to the tab you pick. Like Circle to ask it just watches the real key state, so it needs no extra installs
and never swallows the keys. Windows only; anywhere else it quietly does nothing.
"""

from __future__ import annotations

import ctypes
import threading
import time

import ui

log = ui.log

# Virtual-key codes: Ctrl (the generic key covers left and right) and CapsLock.
_VK_CONTROL, _VK_CAPITAL = 0x11, 0x14
DEFAULT_COMBO = "ctrl+capslock"     # Settings -> Shortcuts -> Tab wheel (shortcut_wheel_combo) can change it
SPEC_RELOAD_SECONDS = 1.0           # the combo is re-read this often, so a change in Settings applies at once
_KEYEVENTF_KEYUP = 0x0002
POLL_SECONDS = 0.025


def _eco() -> bool:
    try:
        import settings_store
        return settings_store.eco_on()
    except Exception:
        return False
TAP_SECONDS = 0.25      # a press shorter than this is a tap: the wheel stays open
RESTORE_CAPS = True     # put the Caps Lock light back after the shortcut flipped it
_QUIET_SECONDS = 0.25   # ignore the keyboard this long after we press CapsLock ourselves

_started = False
_poll_thread: threading.Thread | None = None
_open = False           # the wheel is up (held, or left open by a tap)


def _send(action: str) -> None:
    try:
        import ui_bridge
        ui_bridge.push_wheel(action)
    except Exception as e:
        log.debug("tab wheel: could not send %s (%s)", action, e)


def _current_spec():
    """The combo from Settings (hotkey_spec.Spec), or None when the user removed the shortcut."""
    try:
        import hotkey_spec
        import settings_store
        raw = settings_store.get("shortcut_wheel_combo")
        text = DEFAULT_COMBO if raw is None else str(raw)
        return hotkey_spec.parse(text)
    except Exception:  # noqa: BLE001
        import hotkey_spec
        return hotkey_spec.parse(DEFAULT_COMBO)


def _poll_loop() -> None:
    global _open
    user32 = ctypes.windll.user32
    gk = user32.GetAsyncKeyState
    gks = user32.GetKeyState

    def down(vk: int) -> bool:
        return bool(gk(vk) & 0x8000)

    def caps_on() -> bool:
        return bool(gks(_VK_CAPITAL) & 1)

    def tap_caps() -> None:
        user32.keybd_event(_VK_CAPITAL, 0x3A, 0, 0)
        user32.keybd_event(_VK_CAPITAL, 0x3A, _KEYEVENTF_KEYUP, 0)

    held_since: float | None = None
    caps_before = caps_on()          # Caps Lock as it was before the shortcut touched it
    restore_to: bool | None = None   # set while a shortcut is under way: put Caps Lock back to this
    quiet_until = 0.0
    warned = False
    spec, spec_at = _current_spec(), time.monotonic()
    while _started:
        try:
            now = time.monotonic()
            if now - spec_at >= SPEC_RELOAD_SECONDS:
                new = _current_spec()
                if (new.text if new else None) != (spec.text if spec else None) and held_since is not None:
                    _send("commit")                    # the shortcut changed under a held key: put the wheel away
                    held_since = None
                spec, spec_at = new, now
            if spec is None:                           # the user removed the shortcut
                time.sleep(0.25)
                continue
            uses_caps = _VK_CAPITAL in spec.keys
            held = spec.any_down(down)                 # some part of the combo is down
            caps = down(_VK_CAPITAL)
            if now >= quiet_until:
                combo = spec.all_down(down) and not spec.extra_mods_down(down)
                if combo and held_since is None:
                    held_since = now
                    if restore_to is None and uses_caps:
                        restore_to = caps_before
                    _open = True
                    _send("open")
                elif not combo and held_since is not None:
                    _open = now - held_since < TAP_SECONDS      # a tap leaves it up, a hold commits and closes
                    _send("stay" if now - held_since < TAP_SECONDS else "commit")
                    held_since = None
                if held_since is None and not held:
                    # Everything is up: undo the Caps Lock flip (only when CapsLock is part of the combo).
                    if RESTORE_CAPS and uses_caps and restore_to is not None and caps_on() != restore_to:
                        tap_caps()
                        quiet_until = now + _QUIET_SECONDS
                    restore_to = None
                if restore_to is None and not caps and now >= quiet_until:
                    caps_before = caps_on()      # plain Caps Lock presses are the user's own: learn them
        except Exception as e:
            if not warned:
                log.warning("Tab wheel hotkey error: %s", e)
                warned = True
        time.sleep(0.05 if _eco() else POLL_SECONDS)   # Eco mode: slower key polling


def start() -> bool:
    """Arm the hold-Ctrl+CapsLock shortcut. Safe to call more than once; never raises."""
    global _started, _poll_thread
    if _started:
        return True
    try:
        import game_mode
        if game_mode.is_active():          # a game is running: no key polling until it ends
            game_mode.defer("tab_wheel")
            return False
    except Exception:
        pass
    try:
        ctypes.windll.user32.GetAsyncKeyState(_VK_CONTROL)      # Windows only
    except Exception as e:
        log.warning("Tab wheel: shortcut unavailable (%s)", e)
        return False
    _started = True
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="lyra-tab-wheel-hotkey")
    _poll_thread.start()
    spec = _current_spec()
    log.info("Tab wheel: hold %s to open it", spec.text if spec else "(no shortcut set)")
    return True


def is_running() -> bool:
    """True while the Ctrl+CapsLock watcher thread is alive."""
    return bool(_started and _poll_thread is not None and _poll_thread.is_alive())


def stop() -> None:
    """End the watcher thread (it is gone when this returns), so start() can arm it again."""
    global _started, _poll_thread, _open
    _started = False
    t, _poll_thread = _poll_thread, None
    if t is not None and t is not threading.current_thread():
        t.join(timeout=1.0)
    if _open:                            # stopped with the wheel up (a game started): put it away
        _open = False
        _send("close")
