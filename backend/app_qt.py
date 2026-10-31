"""Lyra — run this instead of main.py to get the floating bubble overlay
+ system tray, instead of the plain console chat transcript.

Usage:
    python app_qt.py

Architecture note:
  main.py's voice/text loop runs completely unchanged on a background
  QThread (see overlay.EngineThread). This file and overlay.py are purely
  additive UI layers. Console mode (`python main.py`) still works exactly
  as before and shares the identical engine code.
"""

import sys
import ctypes  # Windows-only; used to hide the console window when frozen


def _hide_console_if_frozen() -> None:
    """When the app is packaged with PyInstaller (sys.frozen == True),
    hide the black console window instead of building with --windowed.

    Why we keep the console subsystem alive (just invisible):
      - Ctrl+T / text mode still needs a real console for input()
      - ui.py's msvcrt hotkey listener also requires a console
      - A true --windowed build has no console at all and silently
        breaks both of the above features.

    During normal `python app_qt.py` development runs we leave the
    console visible so log output is still readable.
    """
    # sys.frozen is set by PyInstaller; absent in normal interpreter runs
    if getattr(sys, "frozen", False):
        # Obtain the HWND of the console attached to this process
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # SW_HIDE = 0 → make the window invisible but keep it alive
            ctypes.windll.user32.ShowWindow(hwnd, 0)


# Hide the console *before* importing the heavy Qt overlay so the black
# window never flashes on screen in a frozen build.
_hide_console_if_frozen()

# Import the overlay module (which itself imports PyQt6 and main)
import overlay

# Standard entry-point guard
if __name__ == "__main__":
    # overlay.run() creates the QApplication, bubble widget, tray icon,
    # and starts the background EngineThread that runs main.main().
    overlay.run()
