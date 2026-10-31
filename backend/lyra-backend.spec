# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: one-folder, windowed (no console) build of the Lyra backend.
# Build with build_backend.ps1. Output: dist\lyra-backend\lyra-backend.exe
#
# Models (faster-whisper) are NOT bundled - model_setup.py downloads
# them into %APPDATA%\Lyra\models on first run.
import os
from PyInstaller.utils.hooks import (
    collect_all, collect_data_files, collect_dynamic_libs, collect_submodules,
)

HERE = os.path.abspath(SPECPATH)

datas, binaries, hiddenimports = [], [], []


def grab_all(pkg):
    """collect_all, but never fail the build because an optional package is absent."""
    try:
        d, b, h = collect_all(pkg)
    except Exception as e:  # noqa: BLE001
        print(f"[spec] skipping {pkg}: {e}")
        return
    datas.extend(d); binaries.extend(b); hiddenimports.extend(h)


def subs(pkg):
    try:
        hiddenimports.extend(collect_submodules(pkg))
    except Exception as e:  # noqa: BLE001
        print(f"[spec] skipping submodules of {pkg}: {e}")


# Speech: Whisper runtime (ctranslate2 DLLs, faster_whisper/assets VAD file, av, tokenizers)
for p in ("faster_whisper", "ctranslate2", "av", "tokenizers", "onnxruntime", "sherpa_onnx"):
    grab_all(p)
subs("huggingface_hub")

# Audio / TTS / recognition
for p in ("speech_recognition", "edge_tts", "pygame"):
    grab_all(p)
hiddenimports += ["pyaudio", "_portaudio", "audioop"]
# Offline voice (Kokoro), song-recognition loopback capture, icon helper.
for p in ("kokoro_onnx", "espeakng_loader", "phonemizer", "soundfile", "pyaudiowpatch", "onnxruntime", "shazamio", "shazamio_core"):
    grab_all(p)
hiddenimports += ["app_icons", "numpy"]
datas += collect_data_files("certifi")
subs("aiohttp")

# Windows audio sessions (ducking). comtypes builds COM wrappers at run time.
subs("comtypes")
subs("pycaw")
hiddenimports += ["comtypes.client", "comtypes.stream"]

# LLM + web search. search.py imports ddgs via importlib, so PyInstaller can't see it.
for p in ("openai", "ddgs", "duckduckgo_search", "primp"):
    grab_all(p)
hiddenimports += ["requests"]

# Desktop automation
for p in ("pyautogui", "pygetwindow", "pymsgbox", "pytweening", "pyscreeze", "pyperclip", "pywinauto"):
    subs(p)
hiddenimports += ["PIL.ImageGrab", "psutil"]

# Circle to Ask is imported dynamically by main's command/startup paths.
hiddenimports += ["circle_ask"]

# Our own packages (some are imported lazily inside functions)
for p in ("actions", "tools", "shortcuts", "computer"):
    subs(p)

# Read-only defaults shipped with the app (found via paths.app_path(...))
for name in ("contacts.example.json", "llm_keys.example.json"):
    if os.path.exists(os.path.join(HERE, name)):
        datas.append((os.path.join(HERE, name), "."))

icon = os.path.join(HERE, "..", "ui", "src-tauri", "icons", "icon.ico")

a = Analysis(
    [os.path.join(HERE, "main.py")],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    excludes=["PyQt6", "PyQt5", "PySide6", "tflite_runtime", "torch", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # one-folder: binaries go to COLLECT, not into the exe
    name="lyra-backend",
    debug=False,
    strip=False,
    upx=False,                  # UPX breaks/flags many DLLs; not worth it
    console=False,              # windowed: no console window
    icon=icon if os.path.exists(icon) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="lyra-backend",
)
