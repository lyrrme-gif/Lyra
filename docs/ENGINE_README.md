# Lyra engine (the Python backend)

Python engine for the Lyra desktop assistant (voice, actions, memory, island bridge).

## Quick start (from source)

```bash
cd engine
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
cd ..                          # back to the repo root (the folder that holds engine/)
# Set GROQ_API_KEY (Settings → AI, or engine/llm_keys.example.json)
python -m engine.core.main
```

The full UI is the Tauri app under `desktop/` (it starts this backend automatically).

## Layout

Everything is a package under `engine/` and is imported as `engine.<group>.<module>` (for example `from engine.core import config`).

| Folder | Role |
|--------|------|
| `core/` | `main.py` (process entry, startup, text/voice loops), `command_pipeline.py` (one user turn), `command_helpers.py`, config, paths, settings/secrets stores, permissions, updater, perf, low-CPU and game mode |
| `voice/` | `voice.py` (listen + patterns; re-exports the TTS API), `tts*.py`, `stt*.py`, `stt_engines/`, push-to-talk, dictation, mic devices, Mochi sounds |
| `brain/` | `brain.py` / `router.py` (chat LLM + intent router), planner, agent, fast path, knowledge, persona, memory, search |
| `features/` | Timers, scheduler, screen recording, OBS, music recognition, island helpers, clipboard, Google, messaging, proactive alerts, file index ... |
| `bridge/` | The local HTTP side the desktop app talks to: `ui_bridge.py`, `api_p0` ... `api_p8`, `ui.py`, `ui_trace.py` |
| `actions/` `tools/` `computer/` `plugins/` `shortcuts/` `notetaker/` | Local command handlers, LLM tools, screen/PC control, drop-in plugins, voice shortcuts, meeting notes |
| `tests/` | Mirrors the folders above (`tests/core`, `tests/voice` ...); `tests/desktop` checks desktop files from Python |
| `paths.py` | (in `core/`) Data dir: always `%APPDATA%\Lyra` (override with `LYRA_DATA_DIR`) |

## Data directory

Runtime data (settings, logs, shortcuts, models cache) goes to:

- Windows: `%APPDATA%\Lyra`
- Override: env `LYRA_DATA_DIR`
- First run from source migrates any old files left in `engine/` (the old `backend/`) into that folder

## Tests

```bash
# from the repo root, one test file per process:
python -m pytest engine/tests/core/test_hotkeys.py
```

The tests need `pytest`. `engine/tests/conftest.py` makes the engine packages honour test stubs in `sys.modules`.
A module that has a `__main__` block is started with `-m`, e.g. `python -m engine.features.google_workspace connect`.

See `docs/BUILD.md` for the Windows installer.
