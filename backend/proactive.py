"""Proactive heads-ups: Lyra speaks up (as a quiet island notification) without being asked.

* low battery  - once at 20 % and once at 10 %, only while running on battery
* long session - a game has been running for 2 hours (then every hour after that)

Everything can be switched off with the setting  proactive_enabled = false.
Games are recognised by process name; add your own in the setting  game_processes  (list).
"""
from __future__ import annotations

import threading
import time

import ui

log = ui.log

_DEFAULT_GAMES = {
    "valorant-win64-shipping.exe": "Valorant", "leagueclient.exe": "League of Legends",
    "league of legends.exe": "League of Legends", "cs2.exe": "Counter-Strike 2",
    "fortniteclient-win64-shipping.exe": "Fortnite", "minecraft.exe": "Minecraft",
    "gta5.exe": "GTA V", "eldenring.exe": "Elden Ring", "overwatch.exe": "Overwatch",
    "apexlegends.exe": "Apex Legends", "rocketleague.exe": "Rocket League",
}

_started = False


def _setting(key, default):
    try:
        import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:
        return default


def _notify(sender: str, text: str) -> None:
    try:
        import ui_bridge
        ui_bridge.push_notification("reminder", "reminder", sender, text)
    except Exception as e:  # noqa: BLE001
        log.debug("proactive notify failed: %s", e)


def _games() -> dict:
    games = dict(_DEFAULT_GAMES)
    extra = _setting("game_processes", [])
    if isinstance(extra, (list, tuple)):
        for name in extra:
            n = str(name).strip().lower()
            if n:
                games[n] = n.replace(".exe", "").title()
    return games


def _loop() -> None:
    import psutil
    warned_20 = warned_10 = False
    playing: dict[str, float] = {}      # game name -> when we first saw it
    reminded: dict[str, int] = {}       # game name -> hours already announced
    while True:
        try:
            if _setting("proactive_enabled", True):
                # battery
                b = psutil.sensors_battery()
                if b is not None:
                    if b.power_plugged or b.percent > 25:
                        warned_20 = warned_10 = False
                    elif b.percent <= 10 and not warned_10:
                        warned_10 = warned_20 = True
                        _notify("Battery", f"Battery is at {int(b.percent)} percent - plug in soon.")
                    elif b.percent <= 20 and not warned_20:
                        warned_20 = True
                        _notify("Battery", f"Battery is at {int(b.percent)} percent.")
                # long gaming session
                games = _games()
                running = set()
                for p in psutil.process_iter(["name"]):
                    n = (p.info.get("name") or "").lower()
                    if n in games:
                        running.add(games[n])
                now = time.time()
                for g in running:
                    if g not in playing:
                        try:
                            from actions import game_volume
                            game_volume.on_game_start(g, _notify)
                        except Exception as e:  # noqa: BLE001
                            log.debug("game volume presets: %s", e)
                    playing.setdefault(g, now)
                for g in list(playing):
                    if g not in running:
                        playing.pop(g, None)
                        reminded.pop(g, None)
                        continue
                    hours = int((now - playing[g]) // 3600)
                    first = float(_setting("gaming_reminder_hours", 2) or 2)
                    if hours >= first and hours > reminded.get(g, 0):
                        reminded[g] = hours
                        _notify("Break time?", f"You've been playing {g} for {hours} hours. Stretch and drink some water.")
        except Exception as e:  # noqa: BLE001
            log.debug("proactive loop: %s", e)
        time.sleep(60)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="lyra-proactive", daemon=True).start()
