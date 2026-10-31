# Lyra

Local voice assistant, now split into modules and running its chat brain
(and vision-assist) on the Groq API instead of local Ollama models.

## Setup

1. `pip install -r requirements.txt`
2. Get a free Groq API key at https://console.groq.com (no credit card) and set it:
   - Windows PowerShell: `setx GROQ_API_KEY "your-key-here"`
3. (Optional) Get a free Tavily key at https://tavily.com for web search context:
   - `setx TAVILY_API_KEY "your-key-here"`
   - Without it, Lyrr automatically falls back to DDGS (no key needed, slightly lower quality).
4. Run it: `python main.py` (from inside this folder) for the plain console
   chat transcript, or `python app_qt.py` for the floating glass-bubble
   overlay + system tray icon (bottom-right of your screen, animates while
   listening/thinking/speaking). Both run the exact same engine underneath —
   pick whichever front-end you want.

## Layout

```
config.py     - all constants (wake words, model names, API keys, timeouts)
ui.py         - logging setup, chat transcript printing, voice/text mode toggle state,
                event bus (add_listener/set_state) used by overlay.py
voice.py      - speak() (TTS + barge-in) and listen() (STT)
knowledge.py  - date/time/math shortcuts that skip the LLM entirely
search.py     - Tavily (primary) / DDGS (fallback) web search context
brain.py      - ask_brain(): local shortcuts first, then Groq for everything else
actions/
  files.py    - create/delete/rename files & folders, list directories
  apps.py     - open websites/local apps, Spotify + vision-click workaround
  system.py   - volume/media keys, lock, screenshot, shutdown/restart, system status
  __init__.py - try_handle_action(): the router that dispatches to the three above
main.py       - run_voice_mode / run_text_mode / process_command_chain / main()
                (the engine — same code whether run via console or the Qt overlay)
overlay.py    - Qt front-end: the bubble widget, tray icon, and the QThread
                that runs main.main() in the background. The ONLY file that
                imports PyQt6 — everything else stays GUI-agnostic.
app_qt.py     - entry point for the bubble overlay (`python app_qt.py`)
```

## How the bubble overlay stays decoupled from the engine

`overlay.py` never calls into `brain`/`actions`/`voice` directly, and those
modules have no idea a GUI exists. The connection is one-way and event-based:

1. `main.py`'s loop runs on a background `QThread` (`overlay.EngineThread`),
   completely unmodified — same blocking mic/`input()` calls as console mode.
2. Wherever the engine already calls `ui.print_chat(...)` or the new
   `ui.set_state(...)`, those calls also notify any listeners registered via
   `ui.add_listener()` — a plain Python callback list, no Qt involved.
3. `overlay.py` registers one listener (`UiBridge`) that re-emits those
   events as Qt signals, which Qt safely delivers across threads to the
   `Bubble` widget on the main thread.

This means console mode (`main.py`) is completely unaffected — it still just
prints to stdout like before — and the bubble is free to be swapped out,
redesigned, or run side-by-side with console debugging without either side
needing to change.


## A note on module-level state

`ui.py` holds `INPUT_MODE` (voice/text) and `VERBOSE_DISPLAY` as live module
globals. Other modules read them as `ui.INPUT_MODE` / `ui.VERBOSE_DISPLAY`
(via `import ui`) rather than `from ui import INPUT_MODE` — the latter would
copy the value at import time and never see later updates from
`switch_to_text_mode()` / `switch_to_voice_mode()`.

## What changed from the single-file version

- `brain.py`: `ollama.generate(...)` was replaced with a Groq chat-completions
  call (see `GROQ_MODEL_NAME` in `config.py` for the current model — Groq
  deprecates model names periodically, so check `console.groq.com/docs/models`
  if it drifts). Conversation history is now sent as proper alternating
  `user`/`assistant` messages instead of one flattened text blob, which is
  what a chat-completions endpoint expects.
- `apps.py`: the Spotify play-button vision-assist also moved off Ollama —
  it now sends the screenshot to a Groq vision model (`GROQ_VISION_MODEL_NAME`
  in `config.py`) instead of a local `moondream` model. Ollama is no longer
  a dependency anywhere in the project.
- Everything else — actions, search, STT/TTS, the chat UI — is unchanged
  logic, just reorganized into files.

## Live trace (what you clicked, what opened, what failed)

Everything is printed in the backend terminal and `ui.log` as `[trace] ...` lines (off with `LYRA_TRACE=0`).

* `ui:island:click`  - every press, with the element, where in the island, and whether it hit the island at all
* `ui:island:after`  - the island 250 ms / 900 ms after a click: what changed, or "NO visible change"
* `ui:island:state`  - mode / tab / small-card-vs-full-tab / pin changes, with the cause (click, key, backend event, timer)
* `ui:island:island` - open/close decisions (FSM transition + reason, expand/setView/collapse, clicks the FSM ignored)
* `ui:island:appeared` / `gone` / `dom` / `anim` / `size` - what appeared, disappeared, animated, resized
* `ui:*:http`, `ERROR`, `LAG` - failed or slow backend calls, JS errors, UI thread frozen > 120 ms
* `http:` / `push:` - what the UI asked the backend for, and what the backend pushed to the UI

Typed text is never logged (only lengths / key names). Browser-side switch: `localStorage["lyra-trace"]="0"`.
