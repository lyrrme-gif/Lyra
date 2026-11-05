# Cleanup 2: study removed, agent leftovers removed, pipeline in stages

Edited files: see the zip (same paths as in the project, copy over). DELETE by hand (a zip cannot delete):
- backend/study.py, backend/study_math.py, backend/tests/test_study_math.py
- ui/src/views/study.ts, ui/src/study.css, ui/src/study/ (whole folder)

What changed
- Study tab and everything it had is gone (backend, routes, settings keys, UI, katex). "Focus timer" keeps the Pomodoro sliders.
- Bugs from the file split fixed: command_helpers.py never imported `actions` (confirming a text action always failed),
  tts.py called `_fix_heard` which only existed in voice.py (barge-in broke), island.ts used `DL_C` without importing it.
- command_pipeline.py: process_command_chain is now ~30 small stage functions instead of one 660-line loop.
- Rust: settings no longer has hooks_installed / active_integrations (the UI stopped sending them), dead commands removed.
- Tests updated for tts.py / command_helpers.py; settings-log tests turn the trace on explicitly.

# Simple Settings menu: banner and sidebar Mochi removed

Edited files (same paths as in the project, copy over):
- ui/src/settings/main.ts
- ui/src/settings/settings-lyra-shell.css

DELETE this file by hand (a zip cannot delete):
- ui/src/settings/lyra/hero.ts

What changed
- The top banner (starfield canvas + 3 Mochis) is gone: no createHero(), no hero element in the page.
- The Mochi in the top-left logo is gone: the sidebar shows just the LYRA wordmark. (It is no longer a button.)
- The page title now shows in the top bar and the page description under it again (the CSS that hid both
  "because the banner carries them" is removed). Search results show the title "Search results".
- Unused imports/helpers removed (createCharacter, gazeToward, onMochiShape, groupOf, subOf); banner + .hm CSS removed.

Not touched (still used elsewhere): the Mochi page, "Your island" preview, Features/Screen previews, setup wizard.
Those still draw Mochis when you are on those pages.

Checked: tsc (noUnusedLocals on) gives the same 72 errors before and after in the sandbox (they come from
missing node_modules there); injecting a bad import raises it, so the check does detect mistakes.
NOT checked: I did not run the app, so the new look of the top bar is untested on screen.

## Update: Mochi page preview no longer follows the mouse
- ui/src/settings/mochiPanel.ts: removed the window "pointermove" listener that made the big Mochi's eyes follow the cursor
  (and the now-unused gazeToward import). The preview still reacts to the "Try a mood" buttons.
- Files in this zip: main.ts, settings-lyra-shell.css, mochiPanel.ts. Still delete ui/src/settings/lyra/hero.ts by hand.

## Update: animations at 60 fps
- ui/src/core/fps.ts: FRAME_MS 30 -> 15. This is the one gate every Mochi/canvas draw loop uses.
  Effective rate (simulated with the same gate logic as mascot/character.ts): 60 Hz -> 60 fps, 120 Hz -> 60, 144 Hz -> 72.
- Side effect: with a 15 ms gate the character loop always uses requestAnimationFrame (before, the 28 ms wait went
  through setTimeout), so a covered or minimised window pauses its drawing like a normal page.
- Costs more CPU/GPU than 30 fps while a Mochi is visible. To go back, set FRAME_MS to 30.
- Files in this zip: main.ts, settings-lyra-shell.css, mochiPanel.ts, fps.ts. Still delete ui/src/settings/lyra/hero.ts by hand.

## Update: Live Activities sound + light (merged onto this version)
- New: ui/src/core/activitySounds.ts, ui/src/island/activityFx.ts, ui/src/island/activityFx.css
- Edited: core/sound.ts, island/island.ts, settings/main.ts, settings/activityPreviews.ts/.css, views/clipboard.ts
- Previews replay the same effect and have a speaker button. See BUILD.md for the overview.

## Update: Eco mode drops the Live Activities light show; voice model is required in first setup
- island/activityFx.ts/.css: in Eco mode no light show (island and Settings previews); sounds keep playing.
- settings/setupWizard.ts: the voice-model page now comes right after "Meet Lyra" (before the Right Ctrl page). On first setup
  Continue stays disabled until speech recognition is downloaded; Skip and Finish send you back to it. The offline voice stays optional.
  Replaying the tour from Settings is not blocked. If the speech package is missing (app falls back to Whisper) it does not trap you.

## Update: the timer grows with the island on hover
- ui/src/island/invite.css: the timer chip now fills the island's height (stays centred) and, while the pointer rests on the notch,
  its clock goes 15 -> 18 px, the ring scales up and the side margins widen, in step with the swell.

## Update: the voice model is required (no delete, no engine choice)
- ui/src/settings/panels/p1_voice_engine.ts: "Delete model" and the Auto / Parakeet / Whisper picker are gone from Settings → Voice.
- backend/stt_engines/__init__.py: mode() is always "parakeet"; set_engine() keeps it there; delete_model() refuses.
  settings_store.py default is now "parakeet". Whisper is still the automatic safety net for a language Parakeet cannot do or if it fails to load.
- tests/test_p1_engines.py updated for the fixed engine (2 other tests in that file already failed before this change).

## Update: "How should short replies sound?" now plays real previews
- Mochi sounds: the Mochi sound. Speak: Lyra says "Opening Spotify" with her real voice. Both: the Mochi sound, then the voice 0.65 s later.
  The island on the card you clicked pops and its badge pulses while it plays.
- backend/ui_bridge.py: POST /voice/test accepts an optional "text" (plain, max 80 chars); empty body still speaks the usual sample line.
- ui/src/settings/setupWizard.ts + setupWizard.css.

## Update: WhatsApp mic = dictation; recorder requirements bundled
- WhatsApp mic (reply bar and chat search): no longer "talk to Lyra". It focuses the box and starts Lyra's dictation, so what you say is
  written there. Click again (or 8 s of silence) to finish. New POST /dictation/toggle in backend/ui_bridge.py (action: toggle | stop | status).
  ui/src/views/apps.ts, ui/src/messages-apple.css (the mic lights red while dictating).
- Recording: backend/requirements.txt now lists windows-capture (imageio-ffmpeg and pyaudiowpatch were there). lyra-backend.spec bundles
  windows_capture and imageio_ffmpeg (its ffmpeg.exe) and the recorder modules. build_backend.ps1 always runs
  pip install -r requirements.txt in the venv first, so an older venv gets the new package.

## Update: performance (idle RAM / CPU)
- Speech models give their memory back: Parakeet and the streaming (sherpa) model unload after `speech_idle_unload_min` idle minutes
  (default 5, 0 = never; Whisper keeps its own `whisper_idle_unload_min`). Never while dictating or holding the voice key. They load
  again by themselves: Parakeet on the next sentence; the streaming model on the next dictation (it loads on the hotkey thread) or in
  the background after a voice turn (that one turn ends on silence instead of early end-of-speech).
  backend/stt_engines/__init__.py (unload_idle, start_idle_unloader), parakeet.py (last_use), streaming_stt.py (unload, lazy reload),
  dictation.py, main.py, settings_store.py. New tests: tests/test_speech_idle.py.
- Startup no longer loads Whisper when Parakeet is installed (Whisper is only the fallback and loads on demand).
- Rust (not compiled here, please build once): caption.rs cursor thread is adaptive (60 Hz only while the cursor moves or the chat bar
  is open, ~10 Hz when still; the window queries it makes are UI-thread round trips). wheel.rs/lib.rs: the wheel window is built on first
  use and destroyed after 90 s closed (no idle WebView2 process); a quick tap's commit/stay command is queued during the first load.
- Already in place and left alone: blur removed everywhere (design/perf.css), settings/mascot windows destroyed when closed, island
  frame loop stops when idle or hidden, hidden/parked windows stop their loops, Eco mode.

## Update: idle CPU fixes (from your log) + wake-word cleanup
- downloads_watch.py: the 12-15 % idle core. _snapshot() walked the WHOLE Downloads tree (rglob + is_file + stat per file) every 0.35 s.
  It now uses os.scandir on the folder and one level of sub-folders (194 ms -> 0.3 ms per scan on a 2,400-file test tree) and checks once
  a second while nothing is downloading, in every mode (not only Light). New test: tests/test_downloads_snapshot.py.
- browser_downloads_watch.py: skips copying/reading the browser History when it has not changed, in every mode (was Light mode only); 2 s idle.
- dbg.py: the live trace (nvidia-smi + resource sampler every 5 s, key/mouse hook logging) is OFF in the built exe, ON from source.
  LYRA_TRACE=1 turns it on in the exe, LYRA_TRACE=0 off from source.
- app_volume.py: polls half as often after ~30 s without a mixer change.
- Wake word removed for good: config.py (openWakeWord constants; WAKE_CHUNK_SIZE/WAKE_SAMPLE_RATE are now MIC_CHUNK_SIZE/MIC_SAMPLE_RATE),
  model_setup.py (openWakeWord downloader), settings_store.py (wake_sensitivity, voice_activation_mode, the "jarvis" phrases),
  the Settings/wizard defaults for them, dictation.py (renamed constants), tests/test_light_mode.py stubs. The assistant's NAME
  ("hey Lyra", wake_name) stays: spoken commands may still start with it.

## Update: faster start, less RAM (from your importtime log)
Measured with `python -X importtime main.py`; cumulative import cost, one run on your PC:
openai 0.89 s, faster_whisper stack ~0.14 s (+ av/ctranslate2/tokenizers), edge_tts + aiohttp ~0.14 s.
- New backend/lazy_imports.py: OpenAI(...) and LazyClient. brain.py, router.py, actions/apps.py no longer import openai at launch
  (brain._llm_client / router._router_client are LazyClients; the BadRequestError except became a type-name check).
  main.py imports it in the background warm-up thread after the speech models, so the first LLM command is not delayed.
- stt.py: WhisperModel is a small shim that imports faster_whisper when Whisper is first needed (it is only the fallback now).
- voice.py: edge_tts is imported the first time Lyra speaks with the online voice.
- sysstats.py: the 1 Hz CPU sampler thread no longer starts at import; it starts with the first Stats request and ends after 30 s idle.
- Tests: tests/test_lazy_imports.py. Full suite: same failures as your zip (missing dependencies in the test sandbox), no new ones.
- Not trusted from the log: the big "self" times of study / actions.apps / sysstats are mixed with other threads' imports (note the
  negative numbers). Profile those in isolation before touching them.
