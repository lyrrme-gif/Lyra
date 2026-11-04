# Clean Lyra tree

## Trace / logging
Live `[trace]` (keys, clicks, CPU/GPU, heartbeats) is **OFF by default**.
Enable only when debugging:
```bat
set LYRA_TRACE=1
python main.py
```

## Data path
Always `%APPDATA%\Lyra` (override with `LYRA_DATA_DIR`). Never writes into `backend/`.

## Backend layout
| File | Role |
|------|------|
| main.py | Startup + loops |
| command_pipeline.py | Full command turn |
| command_helpers.py | Pipeline helpers |
| tts.py | TTS |
| voice.py | Listen + re-exports |

## Island UI modules
| File | Role |
|------|------|
| island.ts | Main Island class |
| downloadUi.ts | Download tile SVG helpers |
| islandIcons.ts | Volume / activity / Spotify / battery icons |
| islandSearch.ts | cleanCard normalisation |
| timerChip / toast / pulse / … | Existing feature modules |

## Removed
Spotify library plugin, unused sound packs, hero.ts, dead functions.
