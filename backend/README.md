# Lyra backend

Python engine for the Lyra desktop assistant (voice, actions, memory, island bridge).

## Quick start (from source)

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
# Set GROQ_API_KEY (Settings → AI, or llm_keys.example.json)
python main.py
```

The full UI is the Tauri app under `ui/` (it starts this backend automatically).

## Layout

| Module | Role |
|--------|------|
| `main.py` | Process entry, startup, text/voice loops |
| `command_pipeline.py` | One user turn (`process_command_chain`) |
| `command_helpers.py` | Clarification, follow-ups, YouTube, chain helpers |
| `tts.py` | TTS (edge / Kokoro / mixer / SpeechStream) |
| `voice.py` | Listen + patterns; re-exports TTS API |
| `brain.py` / `router.py` | Chat LLM + intent router |
| `actions/` | Local command handlers |
| `paths.py` | Data dir: always `%APPDATA%\Lyra` (override with `LYRA_DATA_DIR`) |
| `plugins/` | Drop-in plugins |

## Data directory

Runtime data (settings, logs, decks, shortcuts, models cache) goes to:

- Windows: `%APPDATA%\Lyra`
- Override: env `LYRA_DATA_DIR`
- First run from source migrates any old files left in `backend/` into that folder

## Tests

```bash
cd backend
python -m unittest discover -s tests -v
```

See root `BUILD.md` for the Windows installer.
