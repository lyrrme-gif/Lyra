# Lyra

A desktop AI assistant for Windows.

- `desktop/` – the app you see: Tauri (Rust) shell + Vite/TypeScript UI (island, settings, mascot, caption, wheel windows)
- `engine/` – the Python backend: voice, brain, features, and the local HTTP bridge the app talks to
- `scripts/` – `build_exe.ps1` (whole installer) and `build_backend.ps1` (just the backend)
- `PROJECT_MAP.md` – a map of every folder and file (read this first, human or AI)
- `docs/` – start with `docs/BUILD.md`; `docs/MOVES.md` lists where every file went

Run from source: see `docs/ENGINE_README.md` (backend) and `docs/DESKTOP_README.md` (app).
