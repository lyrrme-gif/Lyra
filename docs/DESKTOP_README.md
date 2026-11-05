# Lyra UI

Tauri + TypeScript shell for Lyra: Dynamic Island, Mochi mascot, Settings, caption, tab wheel.

## Dev

```bash
cd desktop
npm install
npm run tauri:dev          # full app (starts backend)
# or frontend-only preview:
npm run dev                # http://localhost:1420
```

Requirements for Tauri: Rust (MSVC on Windows), Node 20+, WebView2.

## Windows

| HTML entry | Role |
|------------|------|
| `index.html` | Island (main notch) |
| `mascot.html` | Mochi overlay |
| `settings.html` | Settings |
| `caption.html` | Spoken-answer subtitles |
| `wheel.html` | Tab launcher wheel |

## Notes

- Sounds live under `public/sounds/<character>/` (see `src/core/sound.ts` for character ids).
- Eco mode and visibility helpers throttle work when windows are hidden.
- The UI talks to the Python backend over local HTTP (see CSP in `src-tauri/tauri.conf.json`).

Build the installer from the repo root: `BUILD.md` / `build_exe.ps1`.
