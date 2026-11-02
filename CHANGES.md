# Simple Settings menu: banner and sidebar Mochi removed

Edited files (same paths as in the project, copy over):
- ui/src/settings/main.ts
- ui/src/settings/settings-coucou.css

DELETE this file by hand (a zip cannot delete):
- ui/src/settings/coucou/hero.ts

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
- Files in this zip: main.ts, settings-coucou.css, mochiPanel.ts. Still delete ui/src/settings/coucou/hero.ts by hand.

## Update: animations at 60 fps
- ui/src/core/fps.ts: FRAME_MS 30 -> 15. This is the one gate every Mochi/canvas draw loop uses.
  Effective rate (simulated with the same gate logic as mascot/character.ts): 60 Hz -> 60 fps, 120 Hz -> 60, 144 Hz -> 72.
- Side effect: with a 15 ms gate the character loop always uses requestAnimationFrame (before, the 28 ms wait went
  through setTimeout), so a covered or minimised window pauses its drawing like a normal page.
- Costs more CPU/GPU than 30 fps while a Mochi is visible. To go back, set FRAME_MS to 30.
- Files in this zip: main.ts, settings-coucou.css, mochiPanel.ts, fps.ts. Still delete ui/src/settings/coucou/hero.ts by hand.

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
