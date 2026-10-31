"""Offline stand-ins so the real agent / brain / router code can be imported and timed on a machine
without audio, Windows or network packages (CI, a sandbox). Only used by the latency bench and its tests.
Never imported by Lyra itself."""
import sys
import types


class _Dummy:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Dummy()
    def __getattr__(self, k): return _Dummy()
    def __iter__(self): return iter(())
    def __bool__(self): return False


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__getattr__ = lambda k: _Dummy      # `from pkg import Anything` works
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def install(extra=()):
    """Stub third-party / hardware modules that are not installed. Modules that really import are left alone."""
    for name in ("openai", "openai.types", "openai.types.chat", "edge_tts", "pygame", "pygame.mixer",
                 "speech_recognition", "pyttsx3", "sounddevice", "pyaudio", "faster_whisper", "keyboard",
                 "pynput", "pyautogui", "pycaw", "comtypes", *extra):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except Exception:
            _stub(name)
    sys.modules["openai.types.chat"].ChatCompletionMessageParam = dict
    # voice / stt pull in the whole audio stack; the bench never speaks or listens.
    for name in ("voice", "stt"):
        if name not in sys.modules:
            m = _stub(name)
            m.speak = lambda *a, **k: ""
            m.SpeechRecognizer = _Dummy


def import_with_stubs(name, tries=40):
    """Import `name`; every third-party module that turns out to be missing is stubbed on the fly."""
    import importlib
    for _ in range(tries):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as e:
            top = (e.name or "").split(".")[0]
            local = {"actions", "tools", "computer", "shortcuts", "plugins", "tests", "tools_bench"}
            if not top or top in local or top in sys.modules:
                raise
            _stub(top)
    raise ImportError(name)
