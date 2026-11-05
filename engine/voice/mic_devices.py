"""Lyra: which microphone to use (Settings > Voice > Microphone).

Small on purpose: push-to-talk, dictation and circle-to-ask all need the chosen input device,
and none of them should have to load anything heavy for it.
"""

import pyaudio

from engine.bridge import ui

log = ui.log


def list_input_devices() -> list:
    """Names of usable microphones (first occurrence of each name, PortAudio order)."""
    names: list = []
    try:
        pa = pyaudio.PyAudio()
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if int(info.get("maxInputChannels", 0) or 0) > 0:
                    n = str(info.get("name") or f"Device {i}")
                    if n not in names:
                        names.append(n)
        finally:
            pa.terminate()
    except Exception:
        pass
    return names


def preferred_input_index(pa=None):
    """PortAudio index of the microphone chosen in Settings, or None for the default.
    Also used by main.py for the speech_recognition microphone."""
    try:
        from engine.core import settings_store
        name = str(settings_store.get("audio_input") or "").strip()
    except Exception:
        return None
    if not name or name == "Default Microphone":
        return None
    own = pa is None
    try:
        pa = pa or pyaudio.PyAudio()
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if int(info.get("maxInputChannels", 0) or 0) > 0 and str(info.get("name")) == name:
                    return i
        finally:
            if own:
                pa.terminate()
    except Exception:
        pass
    log.warning("Microphone %r not found - using the default one.", name)
    return None
