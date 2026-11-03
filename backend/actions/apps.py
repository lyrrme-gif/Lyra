"""Lyra — opening websites, local apps, and Spotify (including the
vision-model grid-click workaround for hitting the Play button, via Groq)."""

import os
import re
import time
import base64
import difflib
import subprocess
import webbrowser
from io import BytesIO
from urllib.parse import quote_plus
from typing import Optional

from lazy_imports import OpenAI          # the openai package is imported on first use (it costs ~0.9 s at launch)
from PIL import ImageGrab, ImageDraw, ImageFont
import pygetwindow as gw
import pyautogui

import ui
from config import LLM_API_KEY, LLM_BASE_URL, LLM_VISION_MODEL
import app_scanner

log = ui.log
trace = ui.trace


_app_icon_cache: dict[str, str | None] = {}


def _app_icon_data_url(path: str | None) -> str | None:
    """Extract the real Windows app icon from an EXE/LNK using Shell32.

    We do this at runtime instead of bundling/downloading brand assets. Windows
    already exposes the icon associated with an executable/shortcut through its
    shell APIs, so the Island can show the same icon the user sees in Explorer.
    """
    key = os.path.normcase(os.path.abspath(path)) if path else ""
    if not key:
        return None
    if key in _app_icon_cache:
        return _app_icon_cache[key]
    try:
        import ctypes
        from ctypes import wintypes
        from PIL import Image
        from io import BytesIO

        if not os.path.exists(key):
            _app_icon_cache[key] = None
            return None

        class SHFILEINFOW(ctypes.Structure):
            _fields_ = [
                ("hIcon", wintypes.HICON),
                ("iIcon", ctypes.c_int),
                ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260),
                ("szTypeName", wintypes.WCHAR * 80),
            ]

        info = SHFILEINFOW()
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        flags = 0x000000100 | 0x000000000  # SHGFI_ICON
        # Prefer a large shell icon; SHGetFileInfo also resolves .lnk icons.
        flags |= 0x000000000
        result = shell32.SHGetFileInfoW(
            ctypes.c_wchar_p(key), 0, ctypes.byref(info), ctypes.sizeof(info), flags
        )
        if not result or not info.hIcon:
            _app_icon_cache[key] = None
            return None

        import app_icons
        value = app_icons.hicon_to_data_url(info.hIcon, 48)
        user32.DestroyIcon(info.hIcon)
        _app_icon_cache[key] = value
        return value
    except Exception as e:
        log.debug("App icon extraction failed for %r: %s", path, e)
        _app_icon_cache[key] = None
        return None


def _find_app_icon_path(app_name: str) -> str | None:
    """Resolve an installed-app target suitable for Windows shell icon lookup."""
    try:
        entry = app_scanner.find(app_name)
        if entry and entry.kind == "file" and entry.target:
            target = os.path.expandvars(os.path.expanduser(entry.target))
            if os.path.exists(target):
                return target
    except Exception:
        pass
    return None


_name_icon_cache: dict = {}


def app_icon_for_name(name: str) -> str | None:
    """Icon (data URL) of an installed app by its display name, e.g. 'WhatsApp'. Cached."""
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in _name_icon_cache:
        return _name_icon_cache[key]
    icon = None
    try:
        entry = app_scanner.find(key)
        if entry and entry.target:
            import app_icons
            icon = app_icons.icon_for_target(entry.target, entry.kind)
    except Exception as e:  # noqa: BLE001
        log.debug("icon lookup for %r failed: %s", name, e)
    if icon:
        _name_icon_cache[key] = icon
    return icon


def _activity_app_launch(app_name: str, phase: str, icon_url: str | None = None) -> None:
    """Show Island feedback for app launches. Never reports success unless true.

    Format on the notch:  [app icon]  Opening Discord…
    Success is delayed ~0.45s so the "Opening…" state is visible.
    """
    name = (app_name or "app").strip() or "app"
    display = " ".join(p[:1].upper() + p[1:] for p in name.replace("_", " ").split()) if name else "App"

    def _push(p: str, title: str, ms: int) -> None:
        try:
            import ui_bridge
            ui_bridge.push_activity("app_launch", title, phase=p, app=name, duration_ms=ms, icon_url=icon_url)
        except Exception:
            pass

    if icon_url is None:
        icon_url = _app_icon_data_url(_find_app_icon_path(name))
    if phase == "start":
        _push("start", f"Opening {display}…", 6000)
    elif phase == "success":
        # Non-blocking delay so "Opening…" lands first on the Island.
        import threading
        def _later():
            import time
            time.sleep(0.65)
            _push("success", f"{display} opened", 2200)
        threading.Thread(target=_later, daemon=True, name="lyra-app-launch-hud").start()
    else:
        _push("fail", f"Couldn't open {display}", 3000)


# Separate client from brain.py's (importing brain here would create a
# circular import). Vision follows the currently configured LLM provider instead
# of always sending the user's key to Groq.
_vision_last_ok = None   # provider that answered last; tried first next time


def _vision_candidates():
    """Ordered [(provider, key, base_url, model)] for every provider that has a key.

    Configured provider first, so a dead/renamed model on one provider falls
    through to the next instead of silently killing describe/click/screen watch.
    """
    import os
    try:
        import llm_store
        stored = llm_store.get() or {}
    except Exception:
        stored = {}

    def _key(name, env):
        return str(stored.get(name) or os.environ.get(env, "") or "").strip()

    table = {
        "groq": (_key("groq_api_key", "GROQ_API_KEY"),
                 "https://api.groq.com/openai/v1",
                 os.environ.get("GROQ_VISION_MODEL_NAME", "qwen/qwen3.8-27b")),
        "gemini": (_key("gemini_api_key", "GEMINI_API_KEY"),
                   "https://generativelanguage.googleapis.com/v1beta/openai/",
                   os.environ.get("GEMINI_VISION_MODEL", "gemini-3.8-flash")),
        "openrouter": (_key("openrouter_api_key", "OPENROUTER_API_KEY"),
                       "https://openrouter.ai/api/v1",
                       os.environ.get("OPENROUTER_VISION_MODEL", "google/gemini-2.5-flash")),
    }
    try:
        import ollama_support
        _ov = ollama_support.vision_candidate()      # (key, base_url, model) when a local vision model is set
        if _ov:
            table["ollama"] = _ov
    except Exception:
        pass
    primary = str(stored.get("provider") or "").lower().strip()
    order = []
    for name in (_vision_last_ok, primary, "groq", "gemini", "openrouter", "ollama"):
        if name in table and name not in order:
            order.append(name)
    out = [(n, *table[n]) for n in order if table[n][0]]
    if not out and LLM_API_KEY:
        out.append(("config", LLM_API_KEY, LLM_BASE_URL, LLM_VISION_MODEL))
    return out


_vision_cooldown: dict = {}   # provider -> time.monotonic() until which it is skipped (rate limited)


def _rate_limit_wait(err) -> float:
    """Seconds a 429 asks us to wait ("try again in 18.25s" / "291ms"); 20s when unknown."""
    m = re.search(r"try again in\s+([\d.]+)\s*(ms|s)\b", str(err))
    if not m:
        return 20.0
    v = float(m.group(1))
    return min(60.0, v / 1000.0 if m.group(2) == "ms" else v)


def _vision_complete(messages, max_tokens=180):
    """Run one vision request, falling through providers. Returns (text, error).

    No SDK-level retries (max_retries=0): the OpenAI client otherwise sleeps 6-19 s per 429 *inside*
    the call, which stalled every screen explanation for 30 s. A rate-limited provider is skipped
    for as long as it asked, and the next provider answers immediately."""
    import time as _time
    global _vision_last_ok
    last_err = None
    cands = _vision_candidates()
    now = _time.monotonic()
    ready = [c for c in cands if _vision_cooldown.get(c[0], 0) <= now]
    if not ready:                                     # all cooling down: try the soonest one anyway
        ready = sorted(cands, key=lambda c: _vision_cooldown.get(c[0], 0))[:1]
    for name, key, base_url, model in ready:
        client = OpenAI(base_url=base_url, api_key=key, max_retries=0,
                        timeout=(90.0 if name == "ollama" else 25.0))
        for extra in ({"reasoning_effort": "none"}, None):
            try:
                kwargs = dict(model=model, messages=messages, max_tokens=max_tokens)
                if extra:
                    kwargs["extra_body"] = extra
                resp = client.chat.completions.create(**kwargs)
                _vision_last_ok = name
                return (resp.choices[0].message.content or ""), None
            except Exception as e:
                last_err = e
                if type(e).__name__ == "BadRequestError":      # often just the extra param - retry bare
                    continue
                # 404 / auth / rate limit -> next provider
                if getattr(e, "status_code", None) == 429 or "rate limit" in str(e).lower():
                    _vision_cooldown[name] = _time.monotonic() + _rate_limit_wait(e)
                    log.warning("Vision via %s/%s rate limited - skipping it for %.0fs", name, model,
                                _vision_cooldown[name] - _time.monotonic())
                else:
                    log.warning("Vision via %s/%s failed: %s", name, model, e)
                break
    return "", last_err


def screen_contains(image, phrase: str) -> bool:
    """Cheap yes/no vision check used by 'tell me when X appears' alerts."""
    img = image
    if max(img.size) > 1024:
        img = img.copy()
        img.thumbnail((1024, 1024))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    prompt = (f'Does this screen currently show or mention: "{phrase}"? '
              "Answer with exactly one word: YES or NO.")
    raw, err = _vision_complete([{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    }], max_tokens=8)
    if err and not raw:
        log.warning("screen_contains vision failed: %s", err)
        return False
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip().upper()
    return clean.startswith("YES")

# Fail-safe: moving mouse to corner won't abort mid-automation
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

# ---------------------------------------------------------------------------
# App / task control
# ---------------------------------------------------------------------------

SEARCHABLE_SITES = {
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "google": "https://www.google.com/search?q={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
    "github": "https://github.com/search?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    # Useful additions:
    "stack overflow": "https://stackoverflow.com/search?q={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
    "imdb": "https://www.imdb.com/find?q={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "google maps": "https://www.google.com/maps/search/{q}",
    "news": "https://news.google.com/search?q={q}",
}

PLAIN_SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    # `twitter` and `x` both point to x.com — the rebrand.
    "twitter": "https://www.x.com",
    "x": "https://www.x.com",
    "netflix": "https://www.netflix.com",
    "reddit": "https://www.reddit.com",
    "github": "https://github.com",
    "linkedin": "https://www.linkedin.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "gemini": "https://gemini.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "discord": "https://discord.com/channels/@me",
    "twitch": "https://www.twitch.tv",
    "tiktok": "https://www.tiktok.com",
}

LOCAL_APPS = {
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "file explorer": "explorer",
    "explorer": "explorer",
    "paint": "mspaint",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "outlook": "outlook",
    "chrome": "chrome",
    # Keep executable and arguments separate. Passing the combined string as
    # one argv item makes Windows look for a file literally named
    # "Update.exe --processStart Discord.exe" (WinError 2).
    "discord": [os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe"), "--processStart", "Discord.exe"],
    "vscode": "code",
    "vs code": "code",
    "spotify": "spotify",
    "steam": "steam",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "control panel": "control",
    "terminal": "wt",
    "powershell": "powershell",
    "cmd": "cmd",
    "snipping tool": "snippingtool",
    "magnifier": "magnify",
}

KNOWN_SITE_KEYS = sorted(set(SEARCHABLE_SITES) | set(PLAIN_SITES), key=len, reverse=True)
KNOWN_APP_KEYS = sorted(LOCAL_APPS.keys(), key=len, reverse=True)

# ---------------------------------------------------------------------------

_KNOWN_TARGET_KEYS = sorted(set(KNOWN_SITE_KEYS) | set(KNOWN_APP_KEYS) | {"spotify"}, key=len, reverse=True)

ACTION_SEARCH_PATTERNS = [
    re.compile(r"(?:open|go to)\s+(?P<app>[\w\s]+?)\s+(?:and\s+)?search(?:\s+for)?\s+(?P<query>.+)", re.IGNORECASE),
    re.compile(r"search(?:\s+for)?\s+(?P<query>.+?)\s+on\s+(?P<app>[\w\s]+)", re.IGNORECASE),
    re.compile(r"play\s+(?P<query>.+?)\s+on\s+(?P<app>spotify)", re.IGNORECASE),
    re.compile(r"(?:search|find|look\s+up)\s+(?P<app>spotify)\s+for\s+(?P<query>.+)", re.IGNORECASE),
    re.compile(r"(?:open|go to|launch|start)\s+(?P<app>spotify)\b.*?\bplay\s+(?P<query>.+)", re.IGNORECASE),
]

ACTION_OPEN_PATTERN = re.compile(
    r"(?:open|go to|launch|start)\s+(?:the\s+)?(?P<app>"
    + "|".join(re.escape(k) for k in _KNOWN_TARGET_KEYS)
    + r")\b",
    re.IGNORECASE,
)

# Loose fallback for when the target doesn't exactly match a known key — e.g.
# STT mangles "Spotify" into "spitofy". Captures whatever free-form phrase
# follows the verb; fuzzy_match_target() below decides if it's close enough
# to a real target to count. Only used as a fallback AFTER the exact
# ACTION_OPEN_PATTERN fails, so it never overrides a clean exact match.
ACTION_OPEN_FUZZY_PATTERN = re.compile(
    r"(?:open|go to|launch|start)\s+(?:the\s+)?(?P<app>[a-zA-Z][a-zA-Z\s]{1,30}?)"
    r"(?:\s+(?:please|for me|now|sir))?\s*[.!?]?\s*$",
    re.IGNORECASE,
)


def fuzzy_match_target(text: str, keys, cutoff: float = 0.68):
    """Return the closest known key to `text` (e.g. an app/site name) if
    it's a close-enough match, else None. Checks the whole phrase first
    (handles multi-word keys like "file explorer"), then falls back to
    word-by-word (handles STT mangling a single word inside a longer
    phrase). Cheap, local, no API call — meant to catch mic mishearings
    like "spitofy" -> "spotify" or "note pad" -> "notepad" without adding
    LLM latency to every single command."""
    text = (text or "").strip().lower()
    if not text:
        return None
    keys = list(keys)
    whole = difflib.get_close_matches(text, keys, n=1, cutoff=cutoff)
    if whole:
        return whole[0]
    for word in text.split():
        match = difflib.get_close_matches(word, keys, n=1, cutoff=cutoff)
        if match:
            return match[0]
    return None

# General "click/play X on whatever's currently on screen" command — e.g.
# "play the first video there", "click the subscribe button on the app I'm
# in". Requires an anchor phrase so it doesn't fire on every "play ..." —
# those without an anchor should mean Spotify or the media-key play/pause
# toggle instead.
SCREEN_CLICK_PATTERN = re.compile(
    r"\b(?:play|click(?:\s+on)?|press|select|open|hit|tap)\s+(?P<desc>.+?)\s+"
    r"(?:here|there|on\s+it|"
    r"(?:on|in)\s+(?:this\s+|the\s+|my\s+)?(?:screen|page|tab|app|website|site))\b",
    re.IGNORECASE,
)



def _find_window(title_substring: Optional[str]):
    """Find a window whose title contains title_substring (case-insensitive).
    pygetwindow's getWindowsWithTitle is exact-substring but case-sensitive on
    some builds, so we scan all titles ourselves."""
    if not title_substring:
        return None
    needle = title_substring.lower()
    try:
        for w in gw.getAllWindows():
            title = (w.title or "").strip()
            if title and needle in title.lower() and w.width > 100 and w.height > 100:
                return w
    except Exception as e:
        log.warning("Window scan failed: %s", e)
    # Fallback to library helper
    try:
        wins = gw.getWindowsWithTitle(title_substring)
        if wins:
            return wins[0]
    except Exception:
        pass
    return None


# A dense single-pass grid avoids a second, slower vision request while keeping
# the click point close to the target centre.
GRID_COLS = 24
GRID_ROWS = 16
CELL_LABEL_PATTERN = re.compile(r"\b([A-Xa-x])\s?(1[0-6]|[1-9])\b")


def _physical_window_rect(win) -> tuple[int, int, int, int]:
    """Return (left, top, width, height) in *physical* screen pixels.

    pygetwindow often reports logical (DPI-scaled) coords on Windows while
    ImageGrab / pyautogui use physical pixels — that mismatch is why clicks
    land on the wrong video. Prefer Win32 GetWindowRect when available.
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = getattr(win, "_hWnd", None) or getattr(win, "hWnd", None)
        if hwnd:
            rect = wintypes.RECT()
            if user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
                left, top = int(rect.left), int(rect.top)
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width > 50 and height > 50:
                    return left, top, width, height
    except Exception as e:
        log.warning("GetWindowRect failed (%s) — falling back to pygetwindow.", e)

    # Fallback: scale logical → physical using system DPI
    left, top, width, height = win.left, win.top, win.width, win.height
    try:
        import ctypes
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        scale = dpi / 96.0
        if abs(scale - 1.0) > 0.05:
            left = int(left * scale)
            top = int(top * scale)
            width = int(width * scale)
            height = int(height * scale)
            log.info("Vision DPI scale applied: %.2f", scale)
    except Exception:
        pass
    return left, top, width, height


def _acquire_frame(window_title: Optional[str] = None, full_screen: bool = False, fresh: bool = False):
    """Prefer a fresh ScreenWatch buffer frame; otherwise grab now.

    Returns (PIL.Image, left, top, width, height) or (None, 0, 0, 0, 0).
    """
    if full_screen:
        # Desktop icons are outside the bounds of the foreground application
        # window. Capture the virtual desktop, including its actual origin on
        # multi-monitor setups, so the returned point remains a screen point.
        try:
            import ctypes
            user32 = ctypes.windll.user32
            left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            top = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
            width = user32.GetSystemMetrics(78) # SM_CXVIRTUALSCREEN
            height = user32.GetSystemMetrics(79)# SM_CYVIRTUALSCREEN
            shot = ImageGrab.grab(
                bbox=(left, top, left + width, top + height), all_screens=True
            ).convert("RGB")
            return shot, left, top, shot.width, shot.height
        except Exception as e:
            log.warning("Virtual desktop capture failed: %s", e)
            return None, 0, 0, 0, 0

    try:
        import screen_watch
        if screen_watch.is_watching():
            frame = screen_watch.latest_frame()
            if frame is not None:
                m = frame.meta
                log.info(
                    "Vision: using live buffer (%sx%s, “%s”)",
                    m.width, m.height, m.window_title,
                )
                return frame.image, m.left, m.top, m.width, m.height
            frame = screen_watch.grab_now()
            if frame is not None:
                m = frame.meta
                return frame.image, m.left, m.top, m.width, m.height
    except Exception as e:
        log.warning("Vision: screen_watch unavailable (%s) — falling back", e)

    win = _find_window(window_title) if window_title else None
    if not win:
        win = gw.getActiveWindow()
    if not win:
        log.warning("Vision: no window available to screenshot.")
        return None, 0, 0, 0, 0
    try:
        win.activate()
    except Exception:
        pass
    time.sleep(0.35)

    left, top, width, height = _physical_window_rect(win)
    if width < 80 or height < 80:
        log.warning("Vision: window too small (%sx%s)", width, height)
        return None, 0, 0, 0, 0
    try:
        screenshot = ImageGrab.grab(
            bbox=(left, top, left + width, top + height),
            all_screens=True,
        ).convert("RGB")
    except TypeError:
        screenshot = ImageGrab.grab(bbox=(left, top, left + width, top + height)).convert("RGB")
    img_w, img_h = screenshot.size
    if abs(img_w - width) > 2 or abs(img_h - height) > 2:
        log.info(
            "Vision: grab size %sx%s != bbox %sx%s — using image size for grid",
            img_w, img_h, width, height,
        )
        width, height = img_w, img_h
    return screenshot, left, top, width, height


REFINE_COLS = 6
REFINE_ROWS = 6


def _refine_point(clean, width, height, col, row, cell_w, cell_h, description):
    """Second, zoomed-in vision pass.

    The coarse 12x8 grid only says which neighbourhood holds the target; clicking the
    middle of such a big cell often misses a small button. Crop around the chosen cell,
    draw a finer grid on the crop, and ask again. Returns (x, y) in window pixels,
    or None to keep the coarse position.
    """
    try:
        sx = clean.width / float(width)
        sy = clean.height / float(height)
        x0 = max(0.0, col * cell_w - cell_w * 0.5)
        y0 = max(0.0, row * cell_h - cell_h * 0.5)
        x1 = min(float(width), (col + 1) * cell_w + cell_w * 0.5)
        y1 = min(float(height), (row + 1) * cell_h + cell_h * 0.5)
        crop = clean.crop((int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)))
        zoom = max(1, min(4, int(900 / max(crop.width, crop.height, 1))))
        if zoom > 1:
            crop = crop.resize((crop.width * zoom, crop.height * zoom))
        cw, ch = crop.size
        d = ImageDraw.Draw(crop)
        fw, fh = cw / REFINE_COLS, ch / REFINE_ROWS
        fpx = max(12, int(min(fw, fh) * 0.28))
        try:
            fnt = ImageFont.truetype("arial.ttf", fpx)
        except Exception:
            fnt = ImageFont.load_default()
        for c in range(REFINE_COLS):
            for r in range(REFINE_ROWS):
                xa, ya = c * fw, r * fh
                d.rectangle([xa, ya, xa + fw, ya + fh], outline="red", width=1)
                d.text((xa + 2, ya + 1), f"{chr(65 + c)}{r + 1}", fill="red", font=fnt)
        buf = BytesIO()
        crop.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        prompt = (
            "This is a zoomed-in crop of a screen with a fine red grid. Cell labels are "
            f"A1..{chr(64 + REFINE_COLS)}{REFINE_ROWS} (columns left to right, rows top to bottom).\n"
            f"Target to click: {description}\n"
            "Pick the ONE cell whose centre sits on the middle of that target "
            "(for a round button, the cell at the middle of the circle).\n"
            "Reply with only the cell label, e.g. C4. If the target is not in this crop, reply NONE."
        )
        raw, verr = _vision_complete([{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }], max_tokens=20)
        if not raw:
            return None
        clean_txt = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        if clean_txt.upper().startswith("NONE"):
            return None
        m = re.search(r"\b([A-F])\s?([1-6])\b", clean_txt.upper())
        if not m:
            return None
        c = ord(m.group(1)) - 65
        r = int(m.group(2)) - 1
        fx = (c + 0.5) / REFINE_COLS
        fy = (r + 0.5) / REFINE_ROWS
        return x0 + fx * (x1 - x0), y0 + fy * (y1 - y0)
    except Exception as e:
        log.warning("Vision refinement skipped: %s", e)
        return None


def _find_click_target(description: str, window_title: Optional[str] = None) -> dict:
    """Locate a UI target via grid vision. Does not click.

    Returns dict:
      ok: bool
      cell, label, confidence ('high'|'medium'|'low'), x, y, reason
    """
    ui.set_state("looking")
    desktop_target = bool(re.search(
        r"\b(desktop|recycle\s*bin|trash|desktop\s+icon)\b",
        description, re.I,
    ))
    screenshot, left, top, width, height = _acquire_frame(
        window_title, full_screen=desktop_target
    )
    if screenshot is None:
        return {"ok": False, "reason": "no_frame"}

    clean_shot = screenshot.copy()  # untouched copy for the zoomed refinement pass
    draw = ImageDraw.Draw(screenshot)
    cell_w, cell_h = width / GRID_COLS, height / GRID_ROWS
    font_px = max(14, int(min(cell_w, cell_h) * 0.22))
    try:
        font = ImageFont.truetype("arial.ttf", font_px)
    except Exception:
        font = ImageFont.load_default()

    for col in range(GRID_COLS):
        for row in range(GRID_ROWS):
            x0, y0 = col * cell_w, row * cell_h
            x1, y1 = x0 + cell_w, y0 + cell_h
            draw.rectangle([x0, y0, x1, y1], outline="red", width=2)
            label = f"{chr(65 + col)}{row + 1}"
            tw = max(28, int(font_px * 1.6))
            th = max(16, font_px + 6)
            draw.rectangle([x0, y0, x0 + tw, y0 + th], fill="red")
            draw.text((x0 + 2, y0 + 1), label, fill="white", font=font)

    buf = BytesIO()
    screenshot.save(buf, format="PNG")
    img_bytes = buf.getvalue()

    if not _vision_candidates():
        log.warning("Vision: no configured vision API key.")
        trace("vision: skipped, no vision API key")
        return {"ok": False, "reason": "no_api_key"}

    prompt = (
        "This screenshot has a dense red grid. Cell labels are A1..X16 "
        "(columns A-X left to right, rows 1-16 top to bottom).\n"
        f"User wants to click: {description}\n"
        "Choose the actual named item, not a nearby category, heading, or neighboring result. "
        "For a video, click inside its thumbnail, preferably near the center.\n"
        "Reply in exactly this format on one line:\n"
        "CELL|short label of target|confidence\n"
        "Example: Q8|blue Subscribe button|high\n"
        "If you cannot find it, reply: NONE|not found|low"
    )

    b64_img = base64.b64encode(img_bytes).decode("utf-8")
    raw_response = ""
    try:
        raw_response, verr = _vision_complete([{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
            ],
        }], max_tokens=40)
        if verr and not raw_response:
            raise verr
        clean = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL).strip()
        # Parse CELL|label|confidence
        parts = [p.strip() for p in clean.replace("\n", " ").split("|")]
        cell_token = parts[0] if parts else ""
        target_label = parts[1] if len(parts) > 1 else description
        conf_raw = (parts[2] if len(parts) > 2 else "medium").lower()
        if "high" in conf_raw:
            confidence = "high"
        elif "low" in conf_raw:
            confidence = "low"
        else:
            confidence = "medium"

        if cell_token.upper().startswith("NONE"):
            return {"ok": False, "reason": "not_found", "raw": raw_response}
        if not CELL_LABEL_PATTERN.search(cell_token):
            # Fallback: maybe model only returned a cell
            match = CELL_LABEL_PATTERN.search(clean)
            if not match:
                log.warning("Vision: no cell in response: %r", raw_response)
                return {"ok": False, "reason": "not_found", "raw": raw_response}
            cell_token = match.group(0)
            confidence = "low"

        # A vision model can confidently name an unrelated item when several
        # clickable results are visible. Refuse that coordinate unless the
        # returned label shares a meaningful word with the requested target.
        # This catches cases such as asking for "Planet Killer" and receiving
        # a coordinate labelled "Grand Theft Auto VI trailer".
        ignored = {"click", "on", "the", "a", "an", "button", "icon", "video", "result", "link"}
        requested_words = set(re.findall(r"[a-z0-9]+", description.lower())) - ignored
        label_words = set(re.findall(r"[a-z0-9]+", target_label.lower())) - ignored
        if len(requested_words) >= 2 and len(label_words) >= 2 and not (requested_words & label_words):
            log.warning("Vision target mismatch: requested %r, model labelled %r", description, target_label)
            return {"ok": False, "reason": "target_mismatch", "raw": raw_response}
        if re.search(r"\bvideo\b", description, re.I) and re.search(r"\b(category|channel|playlist|heading)\b", target_label, re.I):
            log.warning("Vision target type mismatch: requested video %r, model labelled %r", description, target_label)
            return {"ok": False, "reason": "target_mismatch", "raw": raw_response}

        match = CELL_LABEL_PATTERN.search(cell_token)
        if not match:
            log.warning("Vision: invalid cell token: %r", cell_token)
            return {"ok": False, "reason": "not_found", "raw": raw_response}
        col = ord(match.group(1).upper()) - ord("A")
        row = int(match.group(2)) - 1
        col = max(0, min(col, GRID_COLS - 1))
        row = max(0, min(row, GRID_ROWS - 1))
        cell = f"{chr(65 + col)}{row + 1}"
        trace(f"vision: cell={cell} label={target_label!r} conf={confidence} for {description!r}")
    except Exception as e:
        log.error("Vision request failed (got %r): %s", raw_response, e)
        return {"ok": False, "reason": "vision_error", "error": str(e)}

    click_x = left + int((col + 0.5) * cell_w)
    click_y = top + int((row + 0.5) * cell_h)
    # The dense grid supplies an accurate point without a second API request.
    try:
        screen = pyautogui.size()
        margin = 8
        click_x = max(margin, min(click_x, screen.width - margin))
        click_y = max(margin, min(click_y, screen.height - margin))
    except Exception:
        pass

    return {
        "ok": True,
        "cell": cell,
        "label": target_label.strip() or description,
        "confidence": confidence,
        "x": click_x,
        "y": click_y,
    }


def _preview_cursor(x: int, y: int) -> None:
    """Move mouse to target so the user can see what would be clicked."""
    try:
        pyautogui.moveTo(x, y, duration=0.18)
    except Exception as e:
        log.warning("Cursor preview failed: %s", e)


def _perform_click_at(x: int, y: int, double_click: bool = False) -> bool:
    try:
        if double_click:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y)
        return True
    except Exception as e:
        log.error("Click failed: %s", e)
        return False


def _click_via_vision(description: str, window_title: Optional[str] = None, double_click: bool = False) -> bool:
    """Legacy helper: find + click with no confirmation (used by Spotify path)."""
    target = _find_click_target(description, window_title=window_title)
    if not target.get("ok"):
        return False
    return _perform_click_at(target["x"], target["y"], double_click=double_click)


def right_click_on_screen(description: str, engine_context=None) -> str:
    """Find target, confirm, then right-click."""
    target = _find_click_target(description)
    if not target.get("ok"):
        return (
            f"I couldn't click “{description}” because I couldn't find it on screen. "
            "Could you describe where it is or what it looks like?"
        )
    if target.get("confidence") == "low":
        return (
            f"I found a possible match for “{description}”. "
            f"Candidate: “{target.get('label')}”. "
            "I did not click because the match is uncertain."
        )
    _preview_cursor(target["x"], target["y"])
    from permissions import PermissionManager
    allowed, reason = PermissionManager().check_permission(
        "click_on_screen",
        f"right-click “{target['label']}”",
        engine_context,
    )
    if not allowed:
        return reason or "Okay, I won't click."
    try:
        pyautogui.rightClick(target["x"], target["y"])
        return f"Right-clicked “{target['label']}”."
    except Exception as e:
        return f"Right-click failed — {e}"


def double_click_on_screen(description: str, engine_context=None) -> str:
    target = _find_click_target(description)
    if not target.get("ok"):
        return f"I couldn't find “{description}” on screen."
    if target.get("confidence") == "low":
        return f"I found a possible match for “{description}”. I did not click because the match is uncertain."
    _preview_cursor(target["x"], target["y"])
    from permissions import PermissionManager
    allowed, reason = PermissionManager().check_permission(
        "click_on_screen",
        f"double-click “{target['label']}”",
        engine_context,
    )
    if not allowed:
        return reason or "Okay, I won't click."
    if _perform_click_at(target["x"], target["y"], double_click=True):
        return f"Double-clicked “{target['label']}”."
    return "Double-click failed."


def scroll_screen(direction: str = "down", amount: int = 3) -> str:
    """Scroll the focused window. direction: up|down, amount: notches."""
    clicks = max(1, min(20, int(amount)))
    delta = clicks if direction == "up" else -clicks
    try:
        pyautogui.scroll(delta * 100)
        return f"Scrolled {direction}."
    except Exception as e:
        return f"Couldn't scroll — {e}"


def type_text(text: str, interval: float = 0.02) -> str:
    """Type text into the focused field."""
    text = (text or "").strip()
    if not text:
        return "Nothing to type."
    try:
        pyautogui.write(text, interval=interval)
        return "Typed it."
    except Exception:
        # Fallback for non-ASCII: clipboard paste
        try:
            from actions.system import set_clipboard_text
            if set_clipboard_text(text):
                pyautogui.hotkey("ctrl", "v")
                return "Pasted it."
        except Exception as e:
            return f"Couldn't type — {e}"
        return "Couldn't type that text."


def focus_window(title_substring: str) -> bool:
    """Bring a window whose title contains title_substring to the front."""
    win = _find_window(title_substring)
    if not win:
        return False
    try:
        if getattr(win, "isMinimized", False):
            win.restore()
        win.activate()
        time.sleep(0.3)
        return True
    except Exception as e:
        log.warning("focus_window(%r) failed: %s", title_substring, e)
        return False


def click_on_screen(
    description: str,
    engine_context=None,
    require_confirm: bool = True,
    window_title: Optional[str] = None,
) -> str:
    """Phase 2 public entry: find target, preview cursor, confirm, then click.

    Returns a spoken reply string (success, cancel, or not-found).
    """
    if window_title:
        focus_window(window_title)
    target = _find_click_target(description, window_title=window_title)
    if not target.get("ok"):
        reason = target.get("reason")
        if reason == "no_api_key":
            return "Vision isn't configured — set GROQ_API_KEY first."
        if reason == "no_frame":
            return "I couldn't capture the screen, sir."
        return (
            f"I couldn't click “{description}” because I couldn't find it on screen. "
            "Could you describe where it is or what it looks like?"
        )

    label = target["label"]
    confidence = target["confidence"]
    x, y = target["x"], target["y"]

    # Unsure → do not offer a silent click; ask for a better description
    if confidence == "low":
        return (
            f"I found a possible match for “{description}”. "
            f"Candidate: “{label}”. "
            "I did not click because the match is uncertain."
        )

    # Screen clicks are ordinary UI actions; shutdown is the only action that
    # requires confirmation. Keep the argument for compatibility with callers.
    do_confirm = False
    if do_confirm:
        confirm_desc = f"click “{label}”"
        from permissions import PermissionManager
        pm = PermissionManager()
        allowed, reason = pm.check_permission(
            "click_on_screen",
            confirm_desc,
            engine_context,
        )
        if not allowed:
            return reason or "Okay, I won't click."

    if _perform_click_at(x, y):
        try:
            import session_context
            session_context.get().note_click(label, description)
        except Exception:
            pass
        return f"Clicked '{label}'."
    return "I found it but the click failed, sir."


def _png_b64(img, max_side: int) -> str:
    """A picture shrunk to `max_side` and encoded for a vision request (the original is left alone)."""
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _all_screen_images():
    """[(label, image), ...] for every screen plus a one-line layout, or ([], "") with a single screen."""
    try:
        import screen_capture
        mons = screen_capture.monitors()
        if len(mons) < 2:
            return [], ""
        return ([(screen_capture.screen_label(m, mons), screen_capture.capture_monitor(m["index"]).convert("RGB")) for m in mons],
                screen_capture.describe_layout())
    except Exception as e:
        log.warning("Vision: could not grab every screen (%s) — using the focused window", e)
        return [], ""


def describe_screen(question: str = "") -> str:
    """Describe what is currently on screen using the live buffer (or a fresh grab).

    Vision API is called only here — not on every capture tick.
    """
    # Respect the three privacy modes: Ask captures only for this request;
    # Always reuses the local buffer when fresh; Off still allows explicit visual
    # requests because the user just asked for context.
    # Two or more screens: every screen goes in as its own labelled picture, so Lyra sees all of them
    # (the focused-window frame below only ever covers one).
    shots, layout = _all_screen_images()
    if shots:
        screenshot = shots[0][1]
    else:
        screenshot, left, top, width, height = _acquire_frame()
    if screenshot is None:
        return "I couldn't capture the screen, sir."

    if not _vision_candidates():
        # Still report window title if we can.
        try:
            import screen_watch
            frame = screen_watch.latest_frame(max_age=10.0)
            if frame and frame.meta.window_title:
                return (
                    f"Vision isn't configured, but the focused window looks like "
                    f"“{frame.meta.window_title}”."
                )
        except Exception:
            pass
        return "Vision isn't configured — add an API key in Lyra settings."

    prompt = ((question or "").strip() or
        "Describe what is visible on this computer screen in 2-4 short sentences. "
        "Name the main app or page if clear, and the important UI elements or content. "
        "Do not invent details you cannot see. Speak as if telling a user what's on screen.")
    if shots:
        content = [{"type": "text", "text": f"{prompt}\n\n{layout} Each picture below is one screen, labelled. "
                                            "Say which screen something is on when it matters."}]
        for label, shot in shots:
            content += [{"type": "text", "text": f"{label}:"},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_png_b64(shot, 1024)}"}}]
    else:
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_png_b64(screenshot, 1280)}"}}]
    try:
        raw, verr = _vision_complete([{
            "role": "user",
            "content": content,
        }], max_tokens=180 if not shots else 260)
        if verr and not raw:
            raise verr
        raw = raw.strip()
        clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        return clean or "I can see the screen, but I couldn't put it into words."
    except Exception as e:
        log.error("describe_screen vision failed: %s", e)
        return f"I captured the screen but vision failed — {e}"


def describe_screen_change() -> str:
    """Explain the difference between the latest local screen frames using vision only once."""
    try:
        import screen_watch
        watch = screen_watch.get_watch()
        current = watch.get_latest(max_age=5.0) or watch.grab_now()
        previous = watch.previous_image()
    except Exception:
        current = previous = None
    if current is None or previous is None:
        return "I don't have an earlier screen frame to compare yet."
    if not _vision_candidates():
        return "Vision isn't configured — add an API key in Lyra settings."
    a, b = previous.copy(), current.image.copy()
    max_side = 1100
    for img in (a, b):
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
    parts = []
    for img in (a, b):
        buf = BytesIO(); img.save(buf, format="JPEG", quality=72)
        parts.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    prompt = ("Compare these two screenshots. The first is BEFORE and the second is NOW. "
              "In 2-4 short sentences, say what visibly changed. Do not invent changes. "
              "If essentially nothing changed, say that clearly.")
    try:
        raw, err = _vision_complete([{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{parts[0]}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{parts[1]}"}},
            ],
        }], max_tokens=180)
        if err and not raw:
            raise err
        return re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip() or "I couldn't describe the change."
    except Exception as e:
        log.warning("screen change vision failed: %s", e)
        return f"I couldn't analyze the screen change — {e}"


def analyze_image_bytes(image_bytes: bytes, prompt: str = "Describe this image clearly and briefly.") -> str:
    """Send one explicitly captured image to the configured vision provider."""
    if not image_bytes:
        return "I don't have a captured image to look at."
    if len(image_bytes) > 12 * 1024 * 1024:
        return "That image is too large to analyze."
    if not _vision_candidates():
        return "Vision isn't configured — add an API key in Lyra settings."
    b64_img = base64.b64encode(image_bytes).decode("ascii")
    try:
        raw, err = _vision_complete([{
            "role": "user",
            "content": [
                {"type": "text", "text": (prompt or "Describe this image clearly and briefly.").strip()[:2000]},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
            ],
        }], max_tokens=260)
        if err and not raw:
            raise err
        clean = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
        return clean or "I can see the image, but I couldn't put it into words."
    except Exception as e:
        log.error("image vision failed: %s", e)
        return f"I captured the image but vision failed — {e}"


def open_spotify(query: str = "") -> None:
    """Open Spotify; if a search query is given, search and try to play the top result.

    Strategy:
      1. Launch via spotify: URI (desktop app)
      2. Wait for the window + search results
      3. Vision-click the Play button on the top result
      4. If vision fails, keyboard fallback (Enter / Space on the results list)
    """
    exact_track = False
    try:
        if query:
            try:
                from media_resolver import spotify_track_uri
                uri = spotify_track_uri(query)
                if uri:
                    # Unofficial but widely used: ":play" makes the desktop app start the track.
                    uri += ":play"
                    exact_track = True
                else:
                    uri = f"spotify:search:{quote_plus(query)}"
            except Exception:
                uri = f"spotify:search:{quote_plus(query)}"
        else:
            uri = "spotify:"
        os.startfile(uri)
    except Exception:
        url = f"https://open.spotify.com/search/{quote_plus(query)}" if query else "https://open.spotify.com"
        webbrowser.open(url)
        if query:
            log.warning("Opened Spotify in browser; vision-click only targets the desktop app.")
        return

    if not query:
        return

    if exact_track:
        # We launched the exact track with ":play". Do NOT run the vision click / Tab+Enter
        # fallbacks below: on a track or album page they start the first row of the album
        # instead of the requested song.
        log.info("Spotify: launched exact track for query=%r; skipping UI fallbacks.", query)
        return

    # Give the desktop app time to focus + render search results
    time.sleep(4.0)

    # Make sure Spotify is focused
    win = _find_window("Spotify")
    if win:
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.6)
        except Exception as e:
            log.warning("Could not activate Spotify window: %s", e)
    else:
        log.warning("Spotify window not found after launch — vision click may miss.")

    clicked = _click_via_vision(
        "the Play button for the top search result "
        "(green or circular button with a triangle/play icon next to the first song or track)",
        window_title="Spotify",
        double_click=False,
    )

    if clicked:
        log.info("Spotify: vision-click succeeded for query=%r", query)
        return

    # Keyboard fallback — Spotify search results usually play the top track with Enter
    log.warning("Spotify: vision-click failed, trying keyboard fallback (Enter).")
    try:
        if win:
            try:
                win.activate()
            except Exception:
                pass
            time.sleep(0.3)
        # Tab into the results list, then Enter to play
        pyautogui.press("tab")
        time.sleep(0.15)
        pyautogui.press("enter")
        time.sleep(0.4)
        # Some Spotify layouts need a second Enter / Space on the play control
        pyautogui.press("space")
        log.info("Spotify: keyboard fallback sent Enter/Space for query=%r", query)
    except Exception as e:
        log.error("Spotify keyboard fallback failed: %s", e)


def open_site(site_key: str, query: str = "") -> None:
    if query and site_key in SEARCHABLE_SITES:
        webbrowser.open(SEARCHABLE_SITES[site_key].format(q=quote_plus(query)))
    elif site_key in PLAIN_SITES:
        webbrowser.open(PLAIN_SITES[site_key])


def open_url(url: str) -> None:
    """Opens an arbitrary URL directly — used for previously-learned sites
    and for router-guessed URLs that aren't in the SEARCHABLE_SITES/
    PLAIN_SITES dicts above. Unlike open_site(), no key lookup: the caller
    is responsible for having a real https URL already (router.classify()
    validates the scheme before handing one back)."""
    webbrowser.open(url)


def find_installed_app(app_name: str):
    """Find an installed Windows app outside the hard-coded LOCAL_APPS table."""
    try:
        return app_scanner.find(app_name)
    except Exception as e:
        log.warning("Installed-app lookup failed for %r: %s", app_name, e)
        return None


def open_installed_app(app_name: str) -> bool:
    """Fuzzy-find and launch any indexed Windows application."""
    entry = find_installed_app(app_name)
    if not entry:
        _activity_app_launch(app_name or "app", "fail")
        return False

    _activity_app_launch(entry.name, "start")
    if not app_scanner.launch(entry):
        _activity_app_launch(entry.name, "fail")
        return False

    try:
        import session_context
        session_context.get().note_app(entry.name)
    except Exception:
        pass

    trace(f"action: app_scanner.launch('{entry.name}')")
    _activity_app_launch(entry.name, "success")
    return True


def scan_installed_apps(force: bool = False) -> int:
    """Build/refresh the installed-app index."""
    try:
        count = app_scanner.scan(force=force)
        trace(f"action: app_scanner.scan(force={force}) -> {count} apps")
        return count
    except Exception as e:
        log.warning("Installed-app scan failed: %s", e)
        return 0


def scan_installed_apps_startup() -> int:
    """Launch-time index: from the saved copy when there is one (instant), refreshed in the background if old."""
    try:
        count = app_scanner.scan_startup()
        trace(f"action: app_scanner.scan_startup() -> {count} apps")
        return count
    except Exception as e:
        log.warning("Installed-app scan failed: %s", e)
        return 0


def list_installed_apps(limit: int | None = None) -> list[str]:
    try:
        return app_scanner.names(limit)
    except Exception:
        return []


def open_local_app(app_key: str) -> bool:
    """Launch a known app, then fall back to the installed-app scanner.

    The scanner covers Start Menu apps, packaged Windows apps, registry
    App Paths/uninstall entries, and executables on PATH.
    """
    key = (app_key or "").strip().lower()
    display = key or (app_key or "app")

    # Existing explicit mappings remain fastest and preserve Lyra's
    # hand-tuned launch behavior (e.g. Discord, Spotify, Settings).
    exe = LOCAL_APPS.get(key) if key else None
    if key == "spotify":
        # There is no `spotify` command on PATH, so Popen("spotify", shell=True)
        # "succeeds" while cmd prints "'spotify' is not recognized" and nothing
        # opens. The spotify: URI handler launches the desktop app (or falls
        # back to the web player) and reports honestly.
        _activity_app_launch("spotify", "start")
        try:
            open_spotify()
            try:
                import session_context
                session_context.get().note_app("spotify")
            except Exception:
                pass
            _activity_app_launch("spotify", "success")
            return True
        except Exception as e:
            log.error("Failed to launch Spotify: %s", e)
            _activity_app_launch("spotify", "fail")
            return False
    if exe:
        _activity_app_launch(display, "start")
        try:
            target = os.path.expandvars(os.path.expanduser(str(exe)))
            if isinstance(exe, (list, tuple)):
                proc = subprocess.Popen(list(exe), shell=False)
            elif os.path.isfile(target):
                proc = subprocess.Popen([target], shell=False)
            else:
                proc = subprocess.Popen([str(exe)], shell=False)
            # Do not claim success if the child immediately exits.
            time.sleep(0.20)
            if proc.poll() is not None:
                raise RuntimeError(f"{app_key} exited immediately with code {proc.returncode}")
            try:
                import session_context
                session_context.get().note_app(key or str(app_key))
            except Exception:
                pass
            _activity_app_launch(display, "success")
            return True
        except Exception as e:
            log.error("Failed to launch %s: %s", app_key, e)
            _activity_app_launch(display, "fail")
            # Discord installs vary (per-user, system-wide, Store). If the
            # conventional per-user updater is absent, try the indexed app,
            # then open Discord in the default browser as a useful fallback.
            if key == "discord":
                if open_installed_app("Discord"):
                    return True
                try:
                    open_site("discord")
                    _activity_app_launch(display, "success")
                    return True
                except Exception as fallback_error:
                    log.error("Discord browser fallback failed: %s", fallback_error)
            return False

    # New: discover apps that Lyra does not know by name.
    # open_installed_app already emits activity feedback.
    if open_installed_app(app_key):
        return True

    # Preserve custom shortcut/path behavior.
    raw = (app_key or "").strip()
    if not raw:
        _activity_app_launch(display, "fail")
        return False

    _activity_app_launch(display, "start")
    try:
        # A raw command is intentionally conservative: shell=True can report success
        # even when the requested executable was not found. Only accept a real executable
        # path here; otherwise the action is a verified failure rather than a false success.
        target = os.path.expandvars(os.path.expanduser(raw))
        if not os.path.isfile(target):
            raise FileNotFoundError(target)
        proc = subprocess.Popen([target], shell=False)
        time.sleep(0.20)
        if proc.poll() is not None:
            raise RuntimeError(f"process exited immediately with code {proc.returncode}")
        try:
            import session_context
            session_context.get().note_app(key or str(app_key))
        except Exception:
            pass
        _activity_app_launch(display, "success")
        return True
    except Exception as e:
        log.error("Failed to launch %s: %s", app_key, e)
        _activity_app_launch(display, "fail")
        return False


def send_message_in_focused_chat(message: str) -> str:
    """Send a message only in the currently focused chat/composer.

    This intentionally does not guess a recipient: the user must open the
    intended conversation first, then explicitly ask Lyra to send the text.
    """
    text = (message or "").strip()
    if not text:
        return "Tell me the message text to send."
    if len(text) > 4000:
        return "That message is too long to send in one action (limit: 4,000 characters)."
    typed = type_text(text)
    if typed not in ("Typed it.", "Pasted it."):
        return f"I couldn't enter the message: {typed}"
    try:
        pyautogui.press("enter")
        return "Message sent in the currently focused conversation."
    except Exception as e:
        return f"The message was entered, but I couldn't press Send: {e}"


def control_call(action: str, app_name: str, engine_context=None) -> str:
    """Use vision to control a visible call UI only after an explicit command."""
    app = (app_name or "").strip()
    action = (action or "").strip().lower()
    target_map = {
        "answer": "the Answer or Accept incoming call button",
        "decline": "the Decline or Reject incoming call button",
        "end": "the End call or Hang up button",
    }
    if action not in target_map:
        return "I didn't recognize that call action."
    if app and not focus_window(app):
        return f"I couldn't find an open {app} window. Open the call in {app} first."
    return click_on_screen(target_map[action], engine_context=engine_context,
                           require_confirm=True, window_title=app or None)


def play_youtube(query: str) -> tuple[bool,str]:
    from media_resolver import open_youtube_video
    return open_youtube_video(query)
