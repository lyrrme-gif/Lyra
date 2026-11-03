# Lyra UI

Lyra UI — visual shell for the Lyra desktop assistant.

## What's included

- Animated Lyra character (animations, expressions, greeting)
- Dynamic Island at the top of the screen
- Transparent always-on-top window + system tray
- Settings window shell
- Sounds support (you need to point SOUNDS_DIR or place sounds)

## What's removed

- Claude / Codex / Gemini agent system
- Agent hooks and permission cards
- Lyra chat
- Integrations (Stripe, n8n, GitHub, etc.)
- File-upload → Claude flow

## Try it

### Frontend only (browser preview)

```bash
npm install
npm run dev
```

Open http://localhost:1420 — click the page to wake Lyra UI.

### Full Tauri app (Windows)

Requirements: Rust, Node 20+, MSVC build tools, WebView2.

```bash
npm install
npm run tauri:dev
```

## Lyra integration (next step)

Lyra's Python backend already broadcasts states via `ui.set_state(...)`.

Next: add a thin bridge (WebSocket or named pipe) so Lyra can push:

```
IDLE | LISTENING | THINKING | SPEAKING | EXECUTING | SUCCESS | ERROR
```

into the island FSM / Lyra UI expressions.

## Structure

```
src/          TypeScript frontend (Lyra character, island, views)
src-tauri/    Rust + Tauri (window, tray, platform)
```
