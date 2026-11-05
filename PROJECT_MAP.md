# Lyra: project map (read this first)

This file describes the whole project so you do not have to open every file. Descriptions are generated from each
file's own first docstring/comment, so they are short and sometimes technical. Last regenerated after the reorganisation
(`docs/MOVES.md` has the old -> new paths).

## What it is

Lyra is a Windows desktop AI assistant. It runs as **two programs that talk over HTTP on `127.0.0.1:8765`**:

| Part | Folder | Tech | Job |
|---|---|---|---|
| Engine | `engine/` | Python | Voice (wake word, speech-to-text, text-to-speech), the brain (intent routing, LLM, tools), PC actions, features, and the HTTP bridge. |
| Desktop app | `desktop/` | Tauri (Rust) + Vite/TypeScript | The windows you see: the island (notch), Settings, the floating Mochi, the caption bar, the radial wheel. |

- Installed build: the Rust shell (`desktop/src-tauri/src/backend.rs`) starts `lyra-backend.exe` (the PyInstaller-built engine) and waits for `/health`.
- Development: start the engine yourself (`python -m engine.core.main` from the repo root), then `npm run tauri:dev` in `desktop/`.
- User data lives in `%APPDATA%\Lyra` (override with `LYRA_DATA_DIR`); `engine/core/paths.py` decides where.

## Repository layout

```
Lyra1/
  README.md  PROJECT_MAP.md (this file)  .gitignore
  desktop/                      the app
    index.html settings.html mascot.html mascot-preview.html caption.html wheel.html   one page per window (Vite entries)
    vite.config.ts tsconfig.json package.json
    public/                     static files (sounds, images) served as-is
    scripts/                    check-constellation.mjs (wheel layout check)
    src-tauri/                  Rust shell: src/*.rs, tauri.conf.json, capabilities/, Cargo.toml, icons/, binaries/
    src/
      windows/                  one folder per window: island/ settings/ mascot/ caption/ wheel/
      shared/                   code used by several windows: core/ design/ lyra/ views/ upload/ spotify/ mascot/ activityFx/
  engine/                       the Python package `engine`
    core/ voice/ brain/ features/ bridge/      the five groups (below)
    actions/ tools/ computer/ plugins/ shortcuts/ notetaker/   sub-packages (below)
    tests/                      mirrors the folders above
    tools_bench/                latency benchmark (dev tool)
    lyra-backend.spec           PyInstaller recipe; requirements*.txt; *.example.json
  scripts/                      build_exe.ps1 (whole installer), build_backend.ps1 (engine only)
  docs/                         BUILD.md, MOVES.md, ENGINE_README.md, DESKTOP_README.md, QUICK_START.md, GOOGLE_SETUP.md, CHANGES.md
```

## How one voice command flows

1. `voice/` hears the wake word / push-to-talk, `stt*` turns speech into text.
2. `core/command_pipeline.py` (`process_command_chain`) handles the turn: fast local patterns first (`actions/`, `features/`), then `brain/` (router, planner, agent with `tools/`, LLM).
3. The reply is spoken by `voice/tts*.py` and sent to the UI. `bridge/ui_bridge.py` is the HTTP server; the island polls it (`shared/lyra/client.ts`) and shows state, results, toasts, timers.
4. The Rust side adds what a webview cannot do: window placement, tray, auto-hide context (`autohide.rs`), volume, global input.

## Rules that are easy to break

- **Python imports are package imports**: `from engine.core import config`, never `import config`. Run things with `-m`, from the repo root: `python -m engine.core.main`, `python -m engine.features.google_workspace connect`.
- **Modules loaded by name at run time** (change these strings if you move a module): `engine/actions/__init__.py` (optional action modules), `engine/actions/quick.py`, `engine/tools/registry.py`, `engine/plugins/loader.py`, `engine/core/command_pipeline.py` (voice tabs), `engine/core/game_mode.py` (`_MODULE_PATHS`), `engine/bridge/ui_bridge.py` (feature start list), `engine/brain/agent.py`, and `hiddenimports` in `engine/lyra-backend.spec`.
- `engine/tools/` has no `__init__.py` on purpose (namespace package).
- **Tests**: from the repo root, one file per process: `python -m pytest engine/tests/core/test_x.py`. `engine/tests/conftest.py` makes packages honour `sys.modules` stubs. Nine test files already had failures before the reorganisation and still have exactly the same ones: `test_apps_plugins`, `test_dev`, `test_google_workspace_root`, `test_merge_integration`, `test_p1_engines`, `test_p3_stats`, `test_p4_design_files`, `test_phase3`, `test_planner`. Never weaken an assertion to make a test pass.
- **Line endings**: some files are CRLF (Rust, a few TS/Python). Keep each file's own endings.
- **Windows**: keep window labels and HTML file names; `vite.config.ts` lists the HTML entries, `tauri.conf.json` and `capabilities/default.json` use the labels, and the Rust window files open the pages by name.
- Settings, themes and per-window CSS are tied together by tests (`engine/tests/desktop/`), which read desktop files from Python.

## Where to change common things

| I want to ... | Look at |
|---|---|
| Add a voice command | `engine/actions/` (simple), `engine/features/` (bigger), pattern lists in `engine/actions/quick.py`, `engine/core/command_pipeline.py` |
| Add an LLM tool | `engine/tools/` + `engine/tools/registry.py` |
| Change what Lyra says for a web search | `engine/features/web_island.py` (`spoken_summary`), card data in `engine/features/web_card.py` |
| Change an island tab | `desktop/src/windows/island/views/<tab>.ts` (+ its css) |
| Island open/close/auto-hide behaviour | `desktop/src/windows/island/fsm.ts`, `island.ts` (`applyAutoClose`, `syncAutoHide`), `desktop/src/shared/core/autohide.ts`, `desktop/src-tauri/src/autohide.rs` |
| Volume bar, toasts, "added" pulses | `desktop/src/windows/island/islandHud.ts`, `islandToasts.ts`, `pulse.ts` |
| A Settings page | `desktop/src/windows/settings/` (`main.ts` + `panels/`) |
| Settings defaults / stored settings | `desktop/src/shared/core/state.ts`, `engine/core/settings_store.py` |
| Hotkeys | `engine/core/hotkey*.py`, `desktop/src/shared/design/shortcuts.ts` |
| Mochi character, sounds | `desktop/src/shared/mascot/`, `desktop/src/shared/core/sound.ts`, `desktop/public/sounds/` |
| Performance switches | `desktop/src/shared/design/perf.css`, `eco.css`, `engine/core/perf.py`, `lowcpu.py` |
| Build the installer | `scripts/build_exe.ps1`, `docs/BUILD.md` |

## Engine files (`engine/`)

### `engine/core/`

Process entry, one-turn pipeline, config, paths, settings and secrets, permissions, updater, performance modes.

| File | What it does |
|---|---|
| `command_helpers.py` | Lyra — helpers used by the command pipeline |
| `command_pipeline.py` | Lyra — command pipeline: process one user turn end-to-end |
| `compound.py` | Rule-based splitting of a compound command into ordered steps |
| `config.py` | Lyra — central config |
| `dbg.py` | Lyra - live trace for debugging |
| `game_mode.py` | Lyra — game mode |
| `hotkey_defs.py` | The keyboard shortcuts that run an ACTION (Settings -> Shortcuts) |
| `hotkey_spec.py` | Lyra: one way to read a key combo such as "ctrl+capslock", "lctrl" or "ctrl+alt+r" |
| `hotkeys.py` | Lyra - keyboard shortcuts that run an action (Settings -> Shortcuts) |
| `lazy_imports.py` | Heavy packages that are imported on first use instead of at every launch |
| `lowcpu.py` | Lyra — keep the background work polite |
| `main.py` | Lyra — process entry: voice mode, text mode, and startup |
| `model_setup.py` | Lyra - first-run model downloads + a tiny backend status file |
| `no_console.py` | Keep console windows from flashing in the windowed (no-console) build |
| `paths.py` | Lyra — where files live |
| `perf.py` | Lyra - per-turn stage timing |
| `permissions.py` | Lyra — Permission system |
| `secrets_store.py` | Lyra - secret storage (Phase 0, contract C2) |
| `settings_log.py` | Lyra - the Settings log: what Settings is doing, what it is loading, and what that costs, tab by tab |
| `settings_store.py` | Lyra — persistent user settings stored in settings.json next to this package |
| `settings_voice.py` | Lyra — island theme + behaviour settings by voice or typed chat ("set dark mode", ...) |
| `updater.py` | Lyra - verified updates (phase 8) |

### `engine/voice/`

Wake word, push-to-talk, speech-to-text (`stt_engines/` = the engines), text-to-speech, dictation.

| File | What it does |
|---|---|
| `dictation.py` | Lyra — dictation |
| `dictation_clean.py` | Lyra — dictation self-correction |
| `dictation_hotkey.py` | Lyra — global dictation shortcut |
| `dictation_stats.py` | Lyra - dictation statistics (Phase 3) |
| `dictation_text.py` | Lyra — dictation text rules (pure logic: no audio, no OS calls, easy to test) |
| `dictionary_store.py` | Lyra — personal dictionary (Phase 2, contract C4) |
| `history_store.py` | Lyra — local dictation history (Phase 2, contract C4) |
| `mic_devices.py` | Lyra: which microphone to use (Settings > Voice > Microphone) |
| `mochi_sounds.py` | Lyra — Mochi sounds: short acknowledgements as a Mochi sound instead of speech |
| `ptt.py` | Lyra — global Right Ctrl push-to-talk |
| `speech_wave.py` | Lyra — loudness envelope of her own voice, for the UI waveform |
| `streaming_stt.py` | Lyra — streaming speech recognition (sherpa-onnx) |
| `stt.py` | Lyra — speech-to-text |
| `stt_engines/bench.py` | Lyra - benchmark Whisper vs Parakeet: latency and WER on short clips |
| `stt_engines/downloader.py` | Lyra - one-click model download: progress, resume, SHA-256 verification, delete |
| `stt_engines/languages.py` | Lyra - which speech engine handles which language |
| `stt_engines/parakeet.py` | Lyra - NVIDIA Parakeet TDT 0.6B v3 (ONNX int8) through onnx-asr |
| `stt_engines/textutil.py` | Lyra - small, dependency-light helpers shared by the engines (numpy only). |
| `stt_engines/whisper_engine.py` | Lyra - the faster-whisper side of contract C1 (what Phases 2, 5 and 7 call when Whisper is chosen) |
| `stt_fix.py` | Lyra — fix what the recognizer misheard in a VOICE COMMAND, before the command is acted on |
| `tts.py` | Lyra — TTS (speak), mixer, offline voices, SpeechStream |
| `tts_lang.py` | Pick a TTS voice that matches the language of the text Lyra is about to speak |
| `tts_offline.py` | Lyra - the optional offline voice (Kokoro) |
| `voice.py` | Lyra — STT listen path, wake/exit patterns, and TTS re-exports |

### `engine/brain/`

Intent routing, planner, agent, LLM clients, persona, memory, web search providers.

| File | What it does |
|---|---|
| `agent.py` | Lyra — multi-step agent |
| `brain.py` | Lyra — the "brain": local shortcuts first, then Groq for plain chat |
| `convo_idle.py` | Lyra's chat memory fades after a quiet spell |
| `fastpath.py` | Lyra - fast local path for plain commands: no LLM, no planner round trip |
| `knowledge.py` | Lyra — cheap local answers (date/time/math) that skip the LLM entirely |
| `llm_store.py` | Persistent LLM provider keys + mod slot names for Lyra |
| `llm_warm.py` | Lyra — LLM pre-warm |
| `memory.py` | Lyra — persistent memory: remembered facts and path aliases |
| `ollama_support.py` | Ollama (local models) support for Lyra: run chat, routing and vision with no internet and no API key |
| `persona.py` | Lyra — persona |
| `planner.py` | Lyra - multi-step agent with a visible plan |
| `reply_style.py` | Reply length: Settings -> AI -> "Reply length & style" (short / balanced / detailed) |
| `router.py` | Lyra — the router: a lightweight LLM pass that runs ONLY when the fast, free, local matching (regex action patterns + fuzzy_match_target) already missed |
| `search.py` | Lyra web search providers + structured rich results |
| `session_context.py` | Lyra — short-lived session context (in-memory only) |

### `engine/features/`

Self-contained features: timers, screen recording, OBS, music recognition, island helpers, clipboard, Google, messaging, proactive alerts.

| File | What it does |
|---|---|
| `activity_log.py` | Activity log: what Lyra did for you, kept on this PC (data folder, activity_log.jsonl) |
| `app_icons.py` | Windows HICON -> transparent PNG data URL |
| `app_scanner.py` | Lyra — Windows installed-application scanner and launcher |
| `app_volume.py` | Lyra — per-app volume: read and set each app's own Windows volume (what the Volume Mixer shows), and tell the island when one changes so it can show it in the volume HUD |
| `browser_downloads_watch.py` | Chromium download-history watcher for Lyra's Dynamic Island |
| `camera_store.py` | Camera tab storage: the UI takes the picture / records the video in the page (getUserMedia + canvas + MediaRecorder) and POSTs the finished bytes here |
| `circle_ask.py` | Lyra — Circle to ask |
| `circle_explain.py` | Lyra — circle to explain |
| `clipboard_history.py` | Windows clipboard snapshot helpers for Lyra's session-only Clipboard tab. |
| `clipboard_session.py` | Session-only clipboard: Windows clipboard history + files dropped on the Clipboard tab |
| `controls.py` | Lyra — Controls + Power tab on the island |
| `downloads_watch.py` | Windows Downloads folder watcher for Lyra's Dynamic Island |
| `file_index.py` | Lyra — search your own files |
| `google_workspace.py` | Lyra — Gmail + Google Calendar, READ-ONLY (Step 13) |
| `guide.py` | Lyra — guide mode (Step 3): the mascot walks around the screen and points while Lyra explains |
| `guide_act.py` | Lyra - screen guide that can ACT (builds on guide.py, which only looks) |
| `lyrics.py` | Lyra — lyrics lookup for the island's Music tab |
| `media_control.py` | Windows Global System Media Transport Controls (optional verification). |
| `media_resolver.py` | Network-assisted media resolvers |
| `messaging.py` | Lyra — send messages through desktop chat apps |
| `music_recognition.py` | Lyra — Shazam-like music recognition for system audio |
| `notification_history.py` | Small in-memory notification preview history for Lyra tools. |
| `notifications.py` | Lyra — incoming notifications for the island's compact notch |
| `obs_control.py` | Lyra — remote control of OBS Studio (OBS 28 or newer has the WebSocket server built in) |
| `proactive.py` | Proactive heads-ups: Lyra speaks up (as a quiet island notification) without being asked |
| `proactive_extra.py` | Proactive heads-ups 2.0: Lyra speaks up at the right moment (proactive.py is untouched) |
| `record_audio.py` | Lyra — the sound of a screen recording |
| `record_hotkey.py` | Lyra: keyboard shortcuts for screen recording (Settings -> Shortcuts) |
| `schedule_parse.py` | Lyra - parsing for scheduled actions and routines (English, pure functions, no I/O) |
| `scheduler.py` | Lyra - scheduled actions and routines |
| `screen_capture.py` | Lyra — reusable screen capture service |
| `screen_record.py` | Lyra — screen recording through OBS Studio (the island's Record tab, "Hey Lyra, start recording", the keyboard shortcuts) |
| `screen_watch.py` | Lyra — Phase 1 live screen watch |
| `spotify_island.py` | Lyra — Spotify searches shown as song cards on the island |
| `status_watch.py` | Lyra — system status feed for the island: battery/charging and microphone use |
| `sysstats.py` | Lyra — live PC stats for the island's Stats tab |
| `tab_sync.py` | Lyra - "ask about it, hear the answer AND see its tab" |
| `tab_wheel.py` | Lyra — Tab wheel shortcut |
| `timers.py` | Lyra — timers, stopwatch and Pomodoro |
| `translate_mode.py` | Lyra - translation mode |
| `wallpaper.py` | Lyra — Wallpaper tab on the island |
| `weather_widget.py` | Weather for the Home widget |
| `web_card.py` | Lyra - knowledge card for web searches on the island |
| `web_island.py` | Lyra — web searches shown as result cards on the island |
| `wgc_recorder.py` | Lyra — the built-in screen recorder (Settings -> Recordings -> Recorder: "Built-in", experimental) |
| `yt_island.py` | Lyra — YouTube searches shown as video cards on the island |

### `engine/bridge/`

The HTTP side the desktop app talks to (`ui_bridge.py` and the `api_p*` route groups).

| File | What it does |
|---|---|
| `api_p0.py` | Lyra - Phase 0 HTTP endpoints: "is this key stored?" status dots |
| `api_p1.py` | Lyra - Phase 1 bridge routes: the Settings > Voice engine panel talks to these |
| `api_p2.py` | Lyra — Phase 2 (dictation polish) HTTP API |
| `api_p3.py` | Phase 3 - HTTP side of the dictation statistics (read by ui/src/settings/panels/p3_statistics.ts) |
| `api_p7.py` | Lyra - Phase 7 bridge routes for the Notetaker panel (/p7/notetaker/*) |
| `api_p8.py` | Lyra - verified updates: HTTP routes + state machine (phase 8) |
| `ui.py` | Lyra — logging setup + chat transcript UI + mode-toggle state |
| `ui_bridge.py` | Lyra ↔ Lyra UI bridge over local HTTP (more reliable than WS on Windows) |
| `ui_trace.py` | Lyra - UI side of the live trace |

### `engine/actions/`

Local command handlers ("open X", files, windows, notes, snippets ...).

| File | What it does |
|---|---|
| `apps.py` | Lyra — opening websites, local apps, and Spotify (including the vision-model grid-click workaround for hitting the Play button, via Groq). |
| `dev.py` | Lyra - coding helper (Step 8) |
| `downloads_sort.py` | Tidy the Downloads folder by file type - with a preview and a confirmation, and undo |
| `file_ops.py` | Lyra — create files and folders, find them by name, open them |
| `files.py` | Lyra — filesystem actions: create/delete/rename files & folders, list dirs. |
| `game_volume.py` | Per-game volume presets |
| `help_cmd.py` | 'What can you do?', the activity log ('what did I do today') and 'undo that'. |
| `listen_summary.py` | 'Summarize what I'm hearing': listen to the PC's sound for a while, transcribe it with the local Whisper model, and summarize it with the chat brain |
| `memory_commands.py` | Lyra — 'remember ...' voice/text commands, backed by memory.py |
| `notes.py` | Notes and lists by voice, plus clipboard AI (translate / summarize / fix what you copied) |
| `quick.py` | Quick abilities and phrase normalisation |
| `screen_tools.py` | Screen and selection helpers |
| `snippets.py` | Text snippets: short texts you type often (email, address, a signature line) |
| `system.py` | Lyra — system-level actions: volume/media keys, lock, screenshot, shutdown/restart, and system-status/current-window info helpers. |
| `tabs.py` | Voice: open an island tab, or the tab wheel |
| `windows_mgr.py` | Lyra — window management (minimize, maximize, close, switch, snap). |

### `engine/tools/`

Tools the LLM agent can call.

| File | What it does |
|---|---|
| `ability_tools.py` | Registers Lyra's local abilities as agent tools |
| `agent_tools.py` | Built-in Agent tools backed by Lyra's existing action layer. |
| `app_tools.py` | Lyra — App control tools (open apps, websites, etc.) |
| `base.py` | Lyra — Tool system base classes and interfaces |
| `google_tools.py` | Registers Gmail/Calendar (google_workspace.py) as an agent tool |
| `guide_tools.py` | Registers guide mode (guide.py) and the acting guide (guide_act.py) as agent tools |
| `planner_tools.py` | Registers the few tools the planner needs to pass text from step to step |
| `registry.py` | Lyra — Tool registry for managing and calling tools. |

### `engine/computer/`

Screen vision plus mouse/keyboard/UI-automation control.

| File | What it does |
|---|---|
| `keyboard.py` | Keyboard helpers |
| `mouse.py` | Mouse helpers with explicit DPI handling. |
| `uia.py` | Windows UI Automation helpers (pywinauto, backend="uia") |
| `vision.py` | Precise on-screen element location |

### `engine/plugins/`

Drop-in plugins and their loader (see `plugins/PLUGINS.md`); `whatsapp_bridge/` is a small Node helper.

| File | What it does |
|---|---|
| `apps.py` | Lyra — app plugins (Phase 4) |
| `dice_roll.py` | Example plugin: roll dice |
| `loader.py` | Lyra — plugin loader |
| `random_quote.py` | Example plugin: a random quote |
| `whatsapp.py` | WhatsApp on the notch (app plugin) |

### `engine/shortcuts/`

User-defined voice shortcuts: builder, manager, executor.

| File | What it does |
|---|---|
| `builder.py` | Lyra — conversational shortcut builder |
| `executor.py` | Lyra — Shortcut executor for running multi-action sequences. |
| `manager.py` | Lyra — Shortcut manager for loading, matching, and managing shortcuts. |
| `settings_api.py` | Lyra — shortcuts as seen by the Settings window (list / save / delete) |

### `engine/notetaker/`

Meeting notes: record, transcribe, diarize, summarize, store.

| File | What it does |
|---|---|
| `audio.py` | Lyra notetaker - audio capture helpers |
| `diarize.py` | Lyra notetaker - who spoke when (speaker diarization), as a SEPARATE optional download (~47 MB) |
| `merge.py` | Lyra notetaker - turn per-channel segments into one readable transcript. |
| `pipeline.py` | Lyra notetaker - from temporary audio to a stored transcript, then the audio is deleted |
| `recorder.py` | Lyra notetaker - the recording session: two sources -> two temporary wav tracks. |
| `segment.py` | Lyra notetaker - cut a track into speech segments by loudness (numpy only). |
| `service.py` | Lyra notetaker - the one object the API talks to: recorder + background processing + housekeeping. |
| `settings.py` | Lyra notetaker - settings (namespaced p7_, stored by settings_store; safe defaults if it is missing). |
| `store.py` | Lyra notetaker - local transcript store: <data>/notetaker/sessions/<id>.json |
| `summarize.py` | Lyra notetaker - summary + to-dos from the TRANSCRIPT TEXT (never audio) |
| `transcribe.py` | Lyra notetaker - offline transcription of speech segments |

### Tests

`engine/tests/<group>/` mirrors the groups above (`tests/desktop/` checks desktop files from Python; `tests/plugins/`, `tests/actions/`, `tests/notetaker/`). Each `test_*.py` is named after what it covers; `tests/core/_p0_helpers.py` is a shared helper for the secrets tests.

## Desktop files (`desktop/src/`)

### `desktop/src/shared/activityFx/`

Activity effects (listening, etc.) used by the island and Settings previews.

| File | What it does |
|---|---|
| `activityFx.ts` | Activity FX: the light show around the notch |

### `desktop/src/shared/core/`

State, settings model, bridge to Rust/engine, sounds, visibility, helpers used by every window.

| File | What it does |
|---|---|
| `activities.ts` | ActivityManager — decides which activity owns the main island and which ones become secondary islands |
| `activitySounds.ts` | Activity sounds: a tiny sound-design studio built on WebAudio |
| `anim.ts` | Easing + spring helpers |
| `appsApi.ts` | What the backend says about app plugins (WhatsApp): shared by the island's Apps tab and by Settings → Apps, where an app is set up |
| `autohide.ts` | Auto-hide for the island (Settings → General → Auto-hide) |
| `bridge.ts` | Thin wrapper over the Tauri commands/events |
| `calendarStore.ts` | Calendar events and reminders |
| `destinations.ts` | Tab registry: every place the island can go, in one list |
| `eco.ts` | Eco mode: one switch for "use as little CPU/GPU as possible" |
| `features.ts` | Feature registry: the single list of everything in Lyra that can be turned on or off |
| `featuresCache.ts` | One /settings/features request per window, shared by everything in it |
| `fileDropFx.ts` | A tiny bridge between whoever receives a file drop on the Clipboard tab and Mochi's reaction to it |
| `fps.ts` | Target frame time for every visible animation: ~60 fps |
| `googleCalStore.ts` | Google Calendar events for the Calendar tab |
| `layout.ts` | Island geometry — ported from IslandTypes.swift + IslandWindowController.islandSize + IslandRootView.botPosition |
| `loadOwner.ts` | "Who started this load?" for the Settings log (settings/settingsLog.ts) |
| `mochiSound.ts` | Mochi sounds: a short acknowledgement ("Done", "Opening Spotify") is a Mochi sound + animation + subtitle instead of speech |
| `nowPlaying.ts` | What is playing right now, shared between the island and Home's music widget. |
| `perfProbe.ts` | Per-window performance probe: answers "WHAT inside this WebView2 page is using the CPU / GPU?" |
| `pollLoop.ts` | Polling the backend's /events feed, but only as often as it pays off |
| `scheduleStore.ts` | Scheduled actions, reminders and routines |
| `sound.ts` | SoundEngine: every Mochi character has its own 28 sounds, served as /sounds/<character>/<name>.wav; volume and character come from Settings. |
| `state.ts` | App state — mirror of AppState.swift (the parts the island needs). |
| `todoStore.ts` | To-do list shared by the To-do view and the Home tile |
| `tour.ts` | Signals for the interactive tour (Settings → Replay tour) |
| `trace.ts` | Live UI trace: what you clicked, what appeared, what is animating, what failed |
| `traceIsland.ts` | Island-specific tracing: a one-line picture of the island, and a log line whenever any part of it changes (mode, tab, small/full card, pin, ...), saying what caused it |
| `visibility.ts` | ONE answer to "is this window really on screen?", shared by every loop in every window |
| `visiblePoll.ts` | One helper for every "ask the backend again every so often" loop in the UI |
| `wheelGeometry.ts` | Launcher geometry, with no DOM so it can be tested: ring positions, what a pointer position points at, where a tooltip goes, and how the layout shrinks on a small screen |
| `wheelHome.ts` | "Scroll down to go Home": on an expanded island that isn't on Home, scrolling down takes you back to Home |

### `desktop/src/shared/design/`

Design system: tokens, skins per window, colour, perf/eco switches, shortcut list, boot.

| File | What it does |
|---|---|
| `boot.ts` | Runs first in every window (linked from each HTML file) |
| `color.ts` | Small colour helpers shared by every window (no dependencies) |
| `palette.ts` | Ctrl+K command palette: one search box over settings and actions |
| `shortcuts.ts` | Shortcut model for the recorder: pure logic, no DOM and no imports, so it is unit-tested offline (backend/tests/test_p4_shortcuts.py runs it with Node) |

### `desktop/src/shared/lyra/`

The client for the engine (polling, chat bus, bridge types).

| File | What it does |
|---|---|
| `bridge.ts` | Lyra ↔ UI bridge (stub) Lyra's Python backend already emits states via ui.set_state(): idle / listening / thinking / speaking / executing / success / error Options to con |
| `chatBus.ts` | Tracks the "waiting for an answer" state of the typing box (the island keeps no conversation itself; the caption window shows it). |
| `chatStore.ts` | Pairs what you asked (typed or spoken) with Lyra's answer, in memory only: nothing is saved, and the Chat tab keeps only the conversation of the current opening (see view |
| `client.ts` | The island's link to the engine: polls events, applies Lyra state (listening/thinking/speaking), search results, timers, replies. |

### `desktop/src/shared/mascot/`

The Mochi characters, drawing and behaviour shared by the island and the mascot window.

| File | What it does |
|---|---|
| `carry.ts` | Dragging the Mochi between the island and the screen |
| `character.ts` | Lyra mascot character: the v1.0 Mochi (Canvas 2D, see mochiBot.ts) |
| `characters.ts` | The Mochi cast: Cloud, Fox and Cat |
| `dock.ts` | The mascot lives in the island's notch (src/island/notchMochi.ts) and only leaves it to explain something on the screen |
| `eyeWander.ts` | Idle eye wandering for a resting Mochi |
| `mochiBot.ts` | Lyra v1.0 Mochi: Canvas 2D engine, copied unchanged from v1.0 (ui/src/mochi/engine.ts) except that sounds are muted (see the Sound stub below) |

### `desktop/src/shared/spotify/`

Spotify helpers for the UI.

| File | What it does |
|---|---|
| `feed.ts` | Spotify feed for the island's Music tab |
| `model.ts` | Spotify island — pure helpers (no DOM, no Tauri) so they can be tested alone. |

### `desktop/src/shared/upload/`

File upload/drop handling.

| File | What it does |
|---|---|
| `sequence.ts` | The drop choreography — port of UploadSequenceEngine.swift, itself a port of design/prototype/upload-sequence.html |

### `desktop/src/shared/views/`

Tiny DOM and icon helpers.

| File | What it does |
|---|---|
| `dom.ts` | Minimal DOM helpers — no framework, as specified. |
| `icons.ts` | SVG paths standing in for the SF Symbols used by the macOS island |

### `desktop/src/windows/caption/`

The caption bar (what Lyra heard and said).

| File | What it does |
|---|---|
| `main.ts` | Caption window: Lyra's spoken answer as a plain black subtitle near the bottom of the screen |

### `desktop/src/windows/island/`

The island (notch): the main window. `island.ts` is the big controller; `fsm.ts` the open/close state machine; `views/` one file per tab.

| File | What it does |
|---|---|
| `downloadUi.ts` | Download chip / tile helpers used by the island. |
| `fsm.ts` | Island open/close FSM — port of IslandStateMachine.swift |
| `invite.ts` | Hover invite: the pointer rests on the compact notch without clicking and the island "asks" to be opened |
| `island.ts` | The island: DOM shell, sizing animation, Mochi placement, mouse handling |
| `islandConstants.ts` | Shared island layout constants. |
| `islandDownloads.ts` | IslandDownloads — collaborator extracted from island.ts |
| `islandGeometry.ts` | IslandGeometry — collaborator extracted from island.ts |
| `islandHud.ts` | IslandHud — collaborator extracted from island.ts |
| `islandIcons.ts` | SVG icons used by the island HUD / activities (extracted from island.ts). |
| `islandSearch.ts` | Search result card normalisation for the island. |
| `islandToasts.ts` | IslandToasts — collaborator extracted from island.ts |
| `lyraStatus.ts` | Lyra's voice turn, drawn ON the island (the compact notch), in the same look as the previews on the setup pages: listening  ->  [Mochi]  the words you are saying, as you  |
| `lyricsLine.ts` | Current lyric line for the compact notch |
| `main.ts` | Entry point: full Lyra UI shell + Lyra brain connection. |
| `notchMochi.ts` | The Lyra Mochi lives in the island's notch |
| `pulse.ts` | Island pulse: when something is ADDED (a to-do, a reminder, an event, a timer) the compact notch itself reacts |
| `startup.ts` | Phase 6: the start-up sequence (about 1.5 s), played once when Lyra launches |
| `timerChip.ts` | Timer chip: the live countdown / stopwatch shown at the right of the compact notch |
| `toast.ts` | Notification toast: a Discord / WhatsApp / Telegram message, a call or a reminder shown inside the compact notch (340×40) for a few seconds |
| `views/addedFx.ts` | "Added" / "couldn't add" animation: a small pill pops in at the top of a tab, with an icon that acts out what happened (a bell rings for a reminder, a stopwatch hand swee |
| `views/apps.ts` | Apps: the island's tab for connected app plugins (WhatsApp chats) |
| `views/calendar.ts` | Calendar: focused date view with today's events and named reminders (daily or one-off). |
| `views/camera.ts` | Camera tab: live preview with effects, photo and video capture, and gestures |
| `views/chat.ts` | Chat tab: the "Search or ask Lyra" pill on the island |
| `views/clipboard.ts` | Clipboard history tab of the island. |
| `views/clipboardMochi.ts` | Mochi's reaction to files on the Clipboard tab |
| `views/controls.ts` | Controls: quick switches (Wi-Fi, Bluetooth, Night light, Dark mode), master volume and the power row (lock, sleep, restart, shut down) in one tab |
| `views/dock.ts` | Dock: three permanent destinations (Mochi · Music · Chat) plus state-driven temporary tabs that grow in beside them and leave when their context ends |
| `views/dockScroller.ts` | Scroller navigation: the alternative to the tab wheel |
| `views/home.ts` | The Mochi tab (this used to be Home): the Mochi, up close, plus the two things worth a glance |
| `views/music.ts` | Music tab — Spotify controls inside the island (cover, title, progress, transport) |
| `views/peek.ts` | Peek tier for Music, Timer, Weather and Recognize: the tab first opens as a slightly bigger small island (sizes in core/layout.ts PEEK_SIZES) showing just what you glance |
| `views/recognition.ts` | Music recognition tab of the island (listen, result, history). |
| `views/record.ts` | Record tab: a red button that starts / stops a screen recording (backend/screen_record.py) |
| `views/searchDeck.ts` | The result deck: what Chat shows for a search |
| `views/stats.ts` | Stats: CPU, GPU, RAM rings, disk, network, uptime, top apps and an optional speed test |
| `views/timer.ts` | Timer tab — Apple Clock style |
| `views/todo.ts` | To-do: today's tasks on the island |
| `views/translate.ts` | Translate tab: type or paste, the language is detected, pick the target |
| `views/upload.ts` | Drop flow: reacts to a file approaching, shows quiet progress, then offers the next step |
| `views/views.ts` | Island views — DOM ports of IslandViewContent.swift |
| `views/volume.ts` | Per-application volume mixer — Island Volume tab |
| `views/wallpaper.ts` | Wallpaper: your recent wallpapers or the pictures in a folder; tap one to set it |
| `views/weather.ts` | Weather: shared data store (Home widget + Weather experience read the same thing). |
| `wave.ts` | Voice waveform: a row of white bars at the top-centre of the island that move with Lyra's voice while she speaks (compact pill and expanded island alike) |

### `desktop/src/windows/mascot/`

The floating Mochi window.

| File | What it does |
|---|---|
| `engine.ts` | Movement engine: spring-damper follow, walking to targets, idle behaviour, reactions to Lyra's state, dragging and the mascot event commands |
| `main.ts` | Mascot window: a transparent, click-through overlay covering one monitor |
| `native.ts` | Thin wrapper over the Tauri side of the mascot window (src-tauri/src/mascot.rs) |
| `overlay.ts` | Everything the mascot draws besides the character: speech bubble, the pulsing ring + arrow used by "point", and the right-click menu. |
| `preview.ts` | Standalone preview for the mascot art (npm run dev → /mascot-preview.html) |

### `desktop/src/windows/settings/`

The Settings window: `main.ts` builds the pages, `panels/` the individual panels, `setupWizard.ts` first-run setup.

| File | What it does |
|---|---|
| `accessCards.ts` | Settings → Features: the things that can act on, or read from, your PC |
| `activityPreviews.ts` | Settings → Live Activities: one preview per activity, and each one IS the real island |
| `activitySnapshots.ts` | GENERATED from the real island: each entry is the live #island DOM (computed styles inlined) captured while that Live Activity was showing |
| `appsPanel.ts` | Settings → Apps: where WhatsApp is set up |
| `choiceKit.ts` | Settings "Control Kit": how settings are chosen and changed, in the content area only |
| `featureScenes.ts` | Settings → Features: one preview per feature, the same little scenes as before (black island, same content and animations) but drawn on a whole-screen desktop (screenPrev |
| `files.ts` | Settings → Files: which folders Lyra may search, what to skip, and indexing progress |
| `lazy.ts` | Run a card's first load only when the card is actually on screen |
| `lyra/icons.ts` | Line icons for the settings sidebar (24×24, stroke = currentColor), in the same quiet style as Lyra's |
| `main.ts` | Settings — Lyra: LLM providers + editable mod slots + Lyra general prefs. |
| `mochiPanel.ts` | Settings → General → Mochi: everything about the little character, with live previews |
| `ollama.ts` | Settings → AI: the "Local AI (Ollama)" card |
| `panels/p0_keystatus.ts` | Phase 0: "key stored" status for the LLM card in Settings |
| `panels/p1_voice_engine.ts` | Phase 1 — Voice engine card: the Parakeet voice model (required, no engine choice, no delete), one-click download, status pill |
| `panels/p2_common.ts` | Phase 2 (dictation polish) — shared helpers for the four Dictation panels |
| `panels/p2_dictation.ts` | Phase 2 — "Dictation" panel: shortcuts, how the text arrives, pill position, local history |
| `panels/p2_styles.ts` | Phase 2 — "Writing style" panel: optional AI clean-up, and Natural / Polished per app. |
| `panels/p3_statistics.ts` | Settings → Statistics (Phase 3): how much you dictate, drawn from the local log in backend/dictation_stats.py |
| `panels/p3_statsFormat.ts` | Phase 3 - pure helpers for the Statistics panel (no DOM, so they can be tested with plain node). |
| `panels/p4_design.ts` | Phase 4 · design-language integration for the Settings window |
| `panels/p8_updates.ts` | Settings → About → Updates (phase 8) |
| `recordingsPanel.ts` | Settings → Recordings: how Lyra reaches OBS Studio (it records by remote-controlling OBS: backend/screen_record.py + obs_control.py), where recordings are saved (Videos\L |
| `screenPreview.ts` | Whole-screen previews for Settings: a tiny Windows desktop (wallpaper, taskbar, cursor) with the island on it, so every setting and feature is seen in context instead of  |
| `settingsLog.ts` | The Settings log, UI half |
| `setupWizard.ts` | First-run setup and tour ("Meet Lyra"): one light card, one idea per page, a preview you can try |
| `shortcutList.ts` | Settings → Shortcuts → Keyboard shortcuts: Every row is editable: click its key, press the new keys, and Lyra's key listener uses them straight away |
| `themePreview.ts` | One island theme as a little card picture: the island on the blue desktop, with the real Mochi and a bar in the theme's accent colour |
| `uiKit.ts` | Settings UI kit: nicer pickers than the browser's own |
| `whatsappIcon.ts` | The WhatsApp mark used by Settings: a speech bubble with a phone handset inside, white on WhatsApp green |

### `desktop/src/windows/wheel/`

The radial wheel.

| File | What it does |
|---|---|
| `main.ts` | Tab wheel window: a small transparent window that Rust (src-tauri/src/wheel.rs) shows at the cursor, or in the middle of the screen, while the wheel is up |
| `wheel.ts` | The launcher: ONE ring of group icons; click one and its tabs slide out beside it, on the same ring |

### Styles (`.css`)

CSS is imported from TypeScript (or linked from the HTML pages). Per window skins are in `shared/design/skin-*.css`.

| File | What it does |
|---|---|
| `shared/activityFx/activityFx.css` | Activity FX: the light show around the notch (see activityFx.ts). |
| `shared/design/eco.css` | Eco mode (Settings -> Features -> Eco mode): no shadows, no blurs, no filters |
| `shared/design/perf.css` | Performance: backdrop-filter cannot see the desktop through a transparent window, so in the |
| `shared/design/skin-caption.css` | ═══ Caption window skin (subtitle pill) ═══════════════════ |
| `shared/design/skin-island.css` | ═══ Island window skin (island, widgets, dock, chat, music, timer, clipboard, home) ═══ |
| `shared/design/skin-settings.css` | ═══ Settings window skin ═══════════════════════════════════════════════════ |
| `shared/design/tokens.css` | ═══ Lyra design language · "Constellation" ═══════════════════════════════ |
| `windows/caption/caption.css` | Lyra's answer: white text on a plain black pill |
| `windows/island/invite.css` | Hover invite (see invite.ts): shown while the pointer rests on the compact notch. |
| `windows/island/pulse.css` | Island pulse (see pulse.ts): the notch swells, floods with the tab's colour and acts out what was added. |
| `windows/island/styles/apple.css` | ═══════════════════════════════════════════════════════════════════════════ |
| `windows/island/styles/controls.css` | Controls tab — Apple Control Center style. |
| `windows/island/styles/home-apple.css` | Mochi tab (formerly Home): a quiet, layered macOS widget surface |
| `windows/island/styles/home.css` | ═══ Navigation dock + Home widgets + Weather + Calendar ═══════════════════ |
| `windows/island/styles/homex.css` | ═══ Mochi tab, formerly Home (hx-*) ═══════════════════════════════════════ |
| `windows/island/styles/messages-apple.css` | ═══════════════════════════════════════════════════════════════════════════ |
| `windows/island/styles/stats.css` | Stats tab: rings on the left, top apps + speed test on the right |
| `windows/island/styles/style.css` | Base island styles: design variables, fonts, layout of the island and its pieces. |
| `windows/island/styles/tokens.css` | ═══ Lyra design tokens ═════════════════════════════════════════════════════ |
| `windows/island/styles/wallpaper.css` | Wallpaper tab — Apple style: segmented control, big rounded pictures, a |
| `windows/island/views/addedFx.css` | "Added" / "couldn't add" pill + new-row highlight |
| `windows/island/views/camera.css` | Camera tab: full-bleed preview with overlaid controls (see camera.ts). |
| `windows/island/views/chatExtra.css` | Chat tab: this session's conversation under the typing pill (the island grows a bit, see State.chatExtra). |
| `windows/island/views/clipboardMochi.css` | Mochi reacts to files dropped on the Clipboard tab |
| `windows/island/views/mochiPlay.css` | Playing with the Mochi on the expanded island (views/home.ts) |
| `windows/island/views/peek.css` | Peek tier (see peek.ts): a small island that grows a bit, instead of the full tab. |
| `windows/island/views/record.css` | Record tab |
| `windows/island/views/searchDeck.css` | Result deck (views/searchDeck.ts) |
| `windows/island/views/translate.css` | Translate tab |
| `windows/mascot/mascot.css` | Speech bubble |
| `windows/settings/activityPreviews.css` | Live Activities previews: the real island's own DOM, scaled to the tile (see activityPreviews.ts). |
| `windows/settings/appsPanel.css` | Settings → Apps |
| `windows/settings/controls-v2.css` | ═══════════════════════════════════════════════════════════════════════════ |
| `windows/settings/featureScenes.css` | ── The old feature scenes (136 x 80 design), now drawn straight onto the desktop of a screenPreview ── |
| `windows/settings/screenPreview.css` | Whole-screen previews (see screenPreview.ts): a tiny Windows desktop with the island on it, so a setting or a |
| `windows/settings/settings-frameless.css` | Settings has no OS title bar: a thin drag strip along the top and a close button in the top bar. |
| `windows/settings/settings-lyra-shell.css` | ═══════════════════════════════════════════════════════════════════════════ |
| `windows/settings/settings-lyra.css` | ═══════════════════════════════════════════════════════════════════════════ |
| `windows/settings/settings-mac.css` | ═══════════════════════════════════════════════════════════════════════════ |
| `windows/settings/settings.css` | Settings window — same palette as the island, laid out as a normal window. |
| `windows/settings/setupWizard.css` | First-run setup and tour (setupWizard.ts): a light card on a blue desktop |
| `windows/settings/themePreview.css` | Island theme picture (themePreview.ts): shared by the setup and Settings. |
| `windows/wheel/wheel.css` | ═══ Launcher window (constellation) ═══════════════════════════════════════════ |

## Rust shell (`desktop/src-tauri/src/`)

| File | What it does |
|---|---|
| `autohide.rs` | What the user is looking at, for the Island and Mochi auto-hide options (Settings → Island / Mochi) |
| `backend.rs` | Start / stop the Python backend (`lyra-backend.exe`) as a child of the UI |
| `caption.rs` | The caption window: a transparent, click-through strip near the bottom of the screen where Lyra shows what she heard and what she answers (see src/caption). |
| `island.rs` | Island window: placement on the chosen display, the two window sizes (full panel / invisible wake strip), click-through and the cursor poll |
| `lib.rs` | Lyra UI — Lyra visual shell (window, tray, island) |
| `log.rs` | Small append-only log at %LOCALAPPDATA%\Lyra\lyra.log (Windows) or ~/.local/share/lyra/lyra.log (Linux) — the equivalent of nbLog() in HookServer.swift |
| `main.rs` | Lyra UI runs without a console window: Lyra UI is the desktop interface. |
| `mascot.rs` | The mascot window: a transparent, click-through, always-on-top overlay that covers one whole monitor |
| `settings.rs` | Preferences, stored as plain JSON in ui-settings.json under platform::config_dir(). |
| `tray.rs` | Notification-area icon: Open Lyra, Settings, Quit |
| `updater.rs` | Lyra - verified updates, Windows side (phase 8) |
| `volume.rs` | Speaker volume feed for the island's volume HUD |
| `wheel.rs` | The launcher window: a transparent, always-on-top window that appears in the middle of the active monitor, shows a circle of category icons (a clicked one opens its tabs  |
| `platform/linux.rs` | Linux: XDG directories for files, xdg-open for links and folders, and gtk-layer-shell for the island window |
| `platform/mod.rs` | Everything that differs between operating systems, behind one set of names |
| `platform/windows.rs` | Windows: Win32 for the island window and the cursor, %APPDATA% for files. |
| `spotify/mod.rs` | Spotify/media-session backend |
| `spotify/model.rs` | Spotify island — platform-independent core |
| `spotify/win.rs` | Spotify island — Windows backend |

Other Tauri files: `tauri.conf.json` (windows, bundle, sidecar), `capabilities/default.json` (permissions per window label), `Cargo.toml`/`Cargo.lock`, `icons/`, `binaries/lyra-backend/` (filled by the build script), `build.rs`.

## Assets

- `desktop/public/` holds the sounds (`sounds/<character>/<name>.wav`) and other static files; `desktop/src-tauri/icons/` the app icons.
- Do not hand-edit generated output: `desktop/dist/`, `desktop/src-tauri/target/`, `engine/build/`, `engine/dist/`.

