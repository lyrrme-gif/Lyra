# Building Lyra.exe (Windows)

Needs: Windows 10/11, Python 3.13, Node 20+, Rust (MSVC toolchain + C++ build tools), WebView2.

    powershell -ExecutionPolicy Bypass -File .\build_exe.ps1

Output: `ui\src-tauri\target\release\bundle\nsis\Lyra v1.0_1.0.0_x64-setup.exe`

Flags: `-SkipBackend` (reuse last backend build), `-Smoke` (start backend and wait for /health).

## Mochi at start-up
Mochi always starts docked in the island. Nothing moves it out automatically (stay-on-screen setting,
subtitle-following, goto / point / tour commands are all blocked) until you drag it out yourself once.
After that first drag, it behaves normally. Code: `ui/src/mascot/engine.ts` -> `startupLocked`.

## Removed in the cleanup
- backend/ui.log, backend/lyra_memory.db (runtime/personal data, recreated in %APPDATA%\Lyra)
- backend/mods.py + mods.json (nothing imports them)
- backend/build_and_install.ps1 (old PyInstaller script for a deleted app_qt.py)
- ui/src/design/skin-wheel.css (not imported anywhere)
- ui/src-tauri/tauri.linux.conf.json (pointed at missing lyra-hook files)
- empty backend/notetaker/sessions + tmp (created at runtime), all __pycache__
- backend/test_guide.py, test_google_workspace.py moved into backend/tests/
- backend/plugins/spotify_library.py (notch Spotify library plugin; Now Playing is unchanged)
- ui/src/settings/lyra/hero.ts (old settings hero banner; nothing imported it)
- ui/public/sounds/blackcat/, penguin/, whitecat/ (no character ids; ~3 MB)
- Runtime leftovers that belonged in %APPDATA%: decks.json, updater.json, shortcuts.json, routines_v1.flag

## Fixed
- lyra-backend.spec did not bundle backend/plugins, so the exe would have lost the WhatsApp,
  dice and quote plugins. It does now, plus data files for docx/pptx/keyring/onnx-asr/winsdk etc.

## Live Activities: sound + light (new)
- `ui/src/core/activitySounds.ts`: synthesized sounds for every activity (no samples). `Sound.activity(name)` in `core/sound.ts`; calls ring until answered (`Sound.ringStart/ringStop`).
- `ui/src/island/activityFx.ts` + `.css`: the light show (edge light, shockwaves, sheen, sparks, notch buzz). Used by the island AND by the Settings previews.
- Previews (`settings/activityPreviews.ts`) replay the real effect on the real island snapshot; each tile has a speaker button.
- Sound switch off silences everything except calls, reminders and timer-done.

## Live Activities: glow switch, WhatsApp only, tour QR
- Each Live Activities tile has a sparkle button (Settings → Live Activities) that turns that activity's light show off or on. Stored as `extras.glow[activity]` (missing = on); checked in `island/activityFx.ts` (`glowEnabled`), so the island and the previews obey it. Sounds are not affected. Clipboard has no light, so no switch.
- Spotify library plugin removed (`backend/plugins/spotify_library.py` deleted); WhatsApp is the only notch app. The music Now Playing activity is unchanged.
- Setup tour → Link WhatsApp now starts WhatsApp's helper itself (`/apps/connect`, retried every ~8 s while there is no code) and has a Connect button as a fallback. Before, only Settings → Apps started it, so the tour sat on "Press Connect to link WhatsApp."
