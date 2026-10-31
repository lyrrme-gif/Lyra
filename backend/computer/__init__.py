"""Lyra computer-control layer.

keyboard.py  clipboard paste / hotkeys (Unicode-safe typing)
uia.py       Windows UI Automation: find a field, set its text, read it back
vision.py    locate a UI element on a frame -> precise screen coordinates

The agent never cares HOW something is done; callers try UIA first, then
paste/keyboard, then vision, and fall down the chain on failure.
"""
