"""Lyra — Circle to ask.

Tap Ctrl, then press it again and KEEP HOLDING it (either Ctrl key): a glowing flame follows your
cursor and leaves a trail. Circle anything with the mouse: the screen stays live, nothing freezes. When the
circle closes, Lyra grabs that part of the screen and answers ("What is this?").

  Let go of Ctrl early ............ cancels (so does Esc)
  A single tap, Ctrl+C, Ctrl+Click  untouched

New: when the circle closes, Lyra asks "Are you confused about something? What do you want to know?",
listens by herself, and (with the Lyra mascot on) the Mochi walks over, points, draws on the screen and
explains it (circle_explain.py). config.CIRCLE_ASK_MODE = "auto" brings back the old instant "What is this?".

After the answer you can still ask your own question about the same picture by
turning auto-answer off (config.CIRCLE_AUTO_ANSWER = False): then Lyra just says
"Circled — ask about it" and the next thing you say (hold Right Ctrl) or type is the question.

No extra installs: the overlay is a click-through, per-pixel-alpha Win32 window drawn
with Pillow, and the tap-then-hold is found by watching the real key state.
"""

from __future__ import annotations

import base64
import ctypes
import io
import math
import re
import threading
import time
from collections import deque
from ctypes import wintypes

import ui

log = ui.log

TAP_MAX_SECONDS = 0.30    # the first tap: Ctrl down and up again within this long, with nothing else pressed
TAP_GAP_SECONDS = 0.40    # the second press must start within this long after the first tap ended
HOLD_SECONDS = 0.15       # ...and be held this long (nothing else pressed) before the flame starts
PENDING_TTL = 90.0        # seconds the circled picture waits for a question
MIN_STROKE_PX = 28        # smaller scribbles are ignored so a stray click doesn't fire
SESSION_MAX_SECONDS = 25.0   # give up if nobody circles anything
TRAIL_SECONDS = 2.6          # how long the glowing trail lasts
LOCK_SECONDS = 0.5           # "got it" flash after the circle closes
DEFAULT_QUESTION = "What is this? Explain it briefly."
MAX_SIDE = 1280           # longest side sent to the vision model

# Virtual-key codes
_CTRL = (0xA2, 0xA3)           # Left and Right Ctrl: tap then hold of either one starts it
_VK_ESCAPE = 0x1B

# ── the circled picture waiting for a question ───────────────────────────────────

_lock = threading.Lock()
_pending: dict | None = None


def has_pending() -> bool:
    with _lock:
        return bool(_pending) and time.time() - _pending["ts"] <= PENDING_TTL


def clear() -> None:
    global _pending
    with _lock:
        _pending = None


_last_shot = None        # (full screenshot, (vx, vy)) of the circle session that just ended


def _set_pending(img, rect=None) -> None:
    global _pending
    big = img
    if min(big.size) < 512:                       # small crops: enlarge so text and coordinates stay precise
        k = 512 / max(1, min(big.size))
        big = big.resize((max(1, int(big.width * k)), max(1, int(big.height * k))))
    if max(big.size) > MAX_SIDE:
        big = big.copy()
        big.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    big.convert("RGB").save(buf, format="PNG")
    with _lock:
        _pending = {"b64": base64.b64encode(buf.getvalue()).decode("ascii"), "ts": time.time(),
                    "size": img.size, "rect": rect}
        if rect and _last_shot:                   # lets circle_explain OCR whole lines around the circle
            _pending["screen"], _pending["screen_origin"] = _last_shot


def pending_from_image(img, rect=None, side: int = MAX_SIDE) -> dict:
    """A 'circled picture' dict (what circle_explain.plan() reads) built from any PIL image."""
    big = img.convert("RGB")
    if max(big.size) > side:
        big = big.copy()
        big.thumbnail((side, side))
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    return {"b64": base64.b64encode(buf.getvalue()).decode("ascii"), "ts": time.time(),
            "size": img.size, "rect": rect}


def _mascot_on() -> bool:
    try:
        import settings_store
        return settings_store.get("feature_mascot") is True
    except Exception:
        return False


_CANCEL = re.compile(r"^(?:never\s*mind|cancel|forget\s+it|dismiss|clear)\b", re.I)


def answer(question: str) -> str | None:
    """Answer `question` about the circled picture. Returns the reply, or None when nothing
    is waiting (or the user said "never mind") so normal command handling continues."""
    q = (question or "").strip()
    with _lock:
        p = dict(_pending) if _pending else None
    if not p or time.time() - p["ts"] > PENDING_TTL:
        return None
    clear()                                       # one question per circle
    if _CANCEL.match(q):
        return None
    try:
        from actions import apps
    except Exception as e:
        return f"I can't reach the vision helper — {e}"
    if not apps._vision_candidates():
        return "Vision isn't configured — add an API key in Lyra settings."
    import circle_explain
    if _mascot_on() and p.get("rect") and circle_explain.wants_explanation(q):
        try:
            line = circle_explain.explain(p, q)
            if line:
                return line
        except Exception as e:  # noqa: BLE001 - fall back to the plain spoken answer
            log.warning("Circle to ask: guided explanation failed: %s", e)
    task = (f'The user circled this part of their screen and asks: "{q}".' if q else
            "The user circled this part of their screen. Say what it is and explain it briefly.")
    prompt = (task + " Answer exactly what was asked, nothing more: if it is a name, title, number or "
              "a yes/no, give just that in one short sentence; add an explanation only if they asked for one. "
              "Reply in the language of the question. Use only what is visible; if you can't tell, say so.")
    raw, err = apps._vision_complete([{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{p['b64']}"}},
        ],
    }], max_tokens=160)
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    if text:
        return text
    return f"I couldn't read that — {err}" if err else "I looked at it but couldn't put it into words."


# ── geometry (pure, tested) ──────────────────────────────────────────────────────

def crop_box(points, canvas_size, image_size, min_px: int = MIN_STROKE_PX):
    """Bounding box of a drawn stroke, padded a little, in canvas AND image pixels.
    Returns ((cx0, cy0, cx1, cy1), (ix0, iy0, ix1, iy1)) or None if the stroke is too small."""
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w < min_px or h < min_px:
        return None
    cw, ch = canvas_size
    pad = max(10, int(0.06 * max(w, h)))
    cx0, cy0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
    cx1, cy1 = min(cw, max(xs) + pad), min(ch, max(ys) + pad)
    sx, sy = image_size[0] / cw, image_size[1] / ch
    box_i = (int(cx0 * sx), int(cy0 * sy), max(int(cx1 * sx), int(cx0 * sx) + 1), max(int(cy1 * sy), int(cy0 * sy) + 1))
    return (int(cx0), int(cy0), int(cx1), int(cy1)), box_i


def virtual_screen(root=None) -> tuple[int, int, int, int]:
    """(left, top, width, height) of all monitors together."""
    try:
        g = ctypes.windll.user32.GetSystemMetrics
        w, h = int(g(78)), int(g(79))
        if w > 0 and h > 0:
            return int(g(76)), int(g(77)), w, h
    except Exception:
        pass
    if root is not None:
        return 0, 0, int(root.winfo_screenwidth()), int(root.winfo_screenheight())
    return 0, 0, 1920, 1080


# ── circle gesture ───────────────────────────────────────────────────────────────

class CircleDetector:
    """Spots a closed, roughly round loop in the last few seconds of cursor movement.
    `find(points)` takes [(x, y, t), ...] and returns the points of the tightest loop that
    just closed, or None. Straight lines, zig-zags, tiny jitter and open arcs don't count."""

    def __init__(self, min_points: int = 14, min_radius: float = 28.0, min_sweep: float = 0.92 * 2 * math.pi):
        self.min_points, self.min_radius, self.min_sweep = min_points, min_radius, min_sweep

    def _is_loop(self, pts) -> bool:
        n = len(pts)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        cx, cy = sum(xs) / n, sum(ys) / n
        radii = [math.hypot(x - cx, y - cy) for x, y in zip(xs, ys)]
        rbar = sum(radii) / n
        if rbar < self.min_radius:
            return False
        std = math.sqrt(sum((r - rbar) ** 2 for r in radii) / n)
        if std / rbar > 0.55:
            return False
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if min(w, h) < 0.4 * max(w, h):                      # a thin squiggle, not a circle
            return False
        total = travelled = 0.0
        prev = math.atan2(ys[0] - cy, xs[0] - cx)
        for x, y in zip(xs[1:], ys[1:]):
            a = math.atan2(y - cy, x - cx)
            d = (a - prev + math.pi) % (2 * math.pi) - math.pi
            total += d
            travelled += abs(d)
            prev = a
        if abs(total) < self.min_sweep:                      # not all the way round
            return False
        if travelled > 1.3 * abs(total):                     # wobbling back and forth
            return False
        return math.hypot(xs[-1] - xs[0], ys[-1] - ys[0]) <= 1.0 * rbar   # the loop is closed

    def find(self, points):
        n = len(points)
        for start in range(n - self.min_points, -1, -2):     # newest start first = tightest loop
            sub = points[start:]
            if self._is_loop(sub):
                return sub
        return None


# ── drawing the flame + trail (pure Pillow; tested) ──────────────────────────────

_orb_cache: dict = {}


def _orb_sprite(size: int = 110):
    """A soft blue flame with a white-hot centre — the cursor 'hitodama'."""
    if size in _orb_cache:
        return _orb_cache[size]
    from PIL import Image, ImageChops
    inv = ImageChops.invert(Image.radial_gradient("L").resize((size, size)))   # 255 centre -> 0 edge
    glow = Image.new("RGBA", (size, size), (70, 150, 255, 0))
    glow.putalpha(inv.point(lambda v: int(255 * (max(0, v - 90) / 165) ** 2.2 * 0.85)))   # 0 before the sprite edge
    core = Image.new("RGBA", (size, size), (240, 248, 255, 0))
    core.putalpha(inv.point(lambda v: int(255 * max(0.0, (v - 175) / 80) ** 1.4)))
    glow.alpha_composite(core)
    _orb_cache[size] = glow
    return glow


def smooth_path(points, step: float = 5.0, max_sub: int = 8):
    """Turn raw cursor samples [(x, y, t)] into a smooth curve for drawing: a Catmull-Rom spline passes
    through the samples with extra points between them (one per ~`step` px, at most `max_sub`), so fast
    strokes are curves instead of straight chords.
    Only used for drawing — circle detection still looks at the raw samples. The newest point is kept as-is
    so the trail always ends exactly under the flame."""
    n = len(points)
    if n < 3:
        return list(points)
    sm = list(points)
    out = [sm[0]]
    last = len(sm) - 1
    for i in range(last):
        p0, p1, p2, p3 = sm[max(0, i - 1)], sm[i], sm[i + 1], sm[min(last, i + 2)]
        k = max(1, min(max_sub, int(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / step)))
        for j in range(1, k + 1):
            u = j / k
            u2, u3 = u * u, u * u * u
            x = 0.5 * (2 * p1[0] + (p2[0] - p0[0]) * u + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * u2
                       + (3 * p1[0] - p0[0] - 3 * p2[0] + p3[0]) * u3)
            y = 0.5 * (2 * p1[1] + (p2[1] - p0[1]) * u + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * u2
                       + (3 * p1[1] - p0[1] - 3 * p2[1] + p3[1]) * u3)
            out.append((x, y, p1[2] + (p2[2] - p1[2]) * u))
    out[-1] = points[-1]
    return out


def render_frame(points, cursor, now, lock=None, ttl: float = TRAIL_SECONDS):
    """One overlay frame. points = [(x, y, t)] in screen px, cursor = (x, y), lock = ((x0,y0,x1,y1), t0)
    for the 'got it' flash. Returns (RGBA image, (left, top)) — the window is just big enough for it."""
    from PIL import Image, ImageDraw, ImageFilter
    pad = 72
    points = smooth_path(points)
    xs = [p[0] for p in points] + [cursor[0]]
    ys = [p[1] for p in points] + [cursor[1]]
    if lock:
        xs += [lock[0][0], lock[0][2]]
        ys += [lock[0][1], lock[0][3]]
    left, top = int(min(xs)) - pad, int(min(ys)) - pad
    w, h = int(max(xs)) + pad - left, int(max(ys)) + pad - top
    s = 2 if w * h <= 700 * 700 else 1                       # smooth core edges (skipped for huge circles)
    glow = Image.new("RGBA", (w, h), (70, 150, 255, 0))      # soft halo: drawn at 1x, it gets blurred anyway
    core = Image.new("RGBA", (w * s, h * s), (215, 235, 255, 0))   # bright line: supersampled for clean edges
    dg, dc = ImageDraw.Draw(glow, "RGBA"), ImageDraw.Draw(core, "RGBA")

    def pt(x, y, k=1):
        return ((x - left) * k, (y - top) * k)

    for i in range(1, len(points)):
        x0, y0, _ = points[i - 1]
        x1, y1, t1 = points[i]
        f = 1.0 - (now - t1) / ttl
        if f <= 0:
            continue
        f = f ** 1.2
        width = 2.2 + 6.0 * f
        dg.line([pt(x0, y0), pt(x1, y1)], fill=(70, 150, 255, int(160 * f)), width=max(1, int(width * 3.4)))
        a, b = pt(x0, y0, s), pt(x1, y1, s)
        col = (205, 230, 255, int(255 * f))
        dc.line([a, b], fill=col, width=max(1, int(width * s)))
        r = width * s / 2
        dc.ellipse([b[0] - r, b[1] - r, b[0] + r, b[1] + r], fill=col)
    if lock:
        (bx0, by0, bx1, by1), t0 = lock
        p = min(1.0, max(0.0, (now - t0) / LOCK_SECONDS))
        g = 10 * p
        dg.rounded_rectangle([*pt(bx0 - g, by0 - g), *pt(bx1 + g, by1 + g)], radius=22,
                             outline=(70, 150, 255, int(190 * (1 - p))), width=12)
        dc.rounded_rectangle([*pt(bx0 - g, by0 - g, s), *pt(bx1 + g, by1 + g, s)], radius=22 * s,
                             outline=(235, 245, 255, int(255 * (1 - p))), width=max(1, 3 * s))

    # (both layers start with their own colour at alpha 0, so blur/resize never darkens the edges)
    if s != 1:
        core = core.reduce(s)                                # exact box downscale: ~5x faster than LANCZOS here
    out = Image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(5)), core)
    orb = _orb_sprite(110)
    k = 0.82 + 0.18 * math.sin(now * 9)                      # gentle flicker
    if k < 1:
        orb = orb.copy()
        orb.putalpha(orb.getchannel("A").point(lambda v: int(v * k)))
    ox, oy = pt(cursor[0], cursor[1])                        # the pad keeps the whole flame inside the frame
    out.alpha_composite(orb, (int(ox - orb.width / 2), int(oy - orb.height / 2)))
    return out, (left, top)


def to_premultiplied_bgra(img) -> bytes:
    """Windows layered windows want premultiplied BGRA."""
    from PIL import Image
    r, g, b, a = img.convert("RGBa").split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


# ── the click-through overlay window (Windows) ───────────────────────────────────

class _Overlay:
    """A borderless, always-on-top, click-through window with per-pixel alpha.
    It is only as big as the flame/trail needs and moves with it, so the real screen
    stays fully live and nothing steals focus."""

    WS_POPUP = 0x80000000
    EX = 0x00080000 | 0x00000020 | 0x00000008 | 0x08000000 | 0x00000080   # layered|transparent|topmost|noactivate|toolwindow

    class _BMIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32)]

    class _BLEND(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    def __init__(self):
        H = ctypes.c_void_p
        self.u = u = ctypes.WinDLL("user32", use_last_error=True)
        self.g = g = ctypes.WinDLL("gdi32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetModuleHandleW.argtypes, k.GetModuleHandleW.restype = [wintypes.LPCWSTR], H
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, H, H, ctypes.c_void_p]
        u.CreateWindowExW.restype = H
        u.DestroyWindow.argtypes = [H]
        u.GetDC.argtypes, u.GetDC.restype = [H], H
        u.ReleaseDC.argtypes = [H, H]
        u.ShowWindow.argtypes = [H, ctypes.c_int]
        u.UpdateLayeredWindow.argtypes = [H, H, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), H,
                                          ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
                                          ctypes.POINTER(self._BLEND), wintypes.DWORD]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), H, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        g.CreateCompatibleDC.argtypes, g.CreateCompatibleDC.restype = [H], H
        g.CreateDIBSection.argtypes = [H, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), H, wintypes.DWORD]
        g.CreateDIBSection.restype = H
        g.SelectObject.argtypes, g.SelectObject.restype = [H, H], H
        g.DeleteObject.argtypes = [H]
        g.DeleteDC.argtypes = [H]
        self.hwnd = u.CreateWindowExW(self.EX, "STATIC", "Lyra circle", self.WS_POPUP, 0, 0, 1, 1,
                                      None, None, k.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise OSError(f"CreateWindowEx failed (error {ctypes.get_last_error()})")
        self._shown = False

    def present(self, img, x: int, y: int) -> None:
        w, h = img.size
        data = to_premultiplied_bgra(img)
        bmi = self._BMIH(ctypes.sizeof(self._BMIH), w, -h, 1, 32, 0, w * h * 4, 0, 0, 0, 0)   # top-down 32-bit
        screen = self.u.GetDC(None)
        mem = self.g.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        bmp = self.g.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        try:
            if not bmp or not bits.value:
                raise OSError("CreateDIBSection failed")
            old = self.g.SelectObject(mem, bmp)
            ctypes.memmove(bits, data, len(data))
            ok = self.u.UpdateLayeredWindow(
                self.hwnd, screen, ctypes.byref(wintypes.POINT(x, y)), ctypes.byref(wintypes.SIZE(w, h)),
                mem, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(self._BLEND(0, 0, 255, 1)), 2)  # ULW_ALPHA
            self.g.SelectObject(mem, old)
            if not ok:
                raise OSError(f"UpdateLayeredWindow failed (error {ctypes.get_last_error()})")
            if not self._shown:
                self.u.ShowWindow(self.hwnd, 4)          # SW_SHOWNOACTIVATE
                self._shown = True
        finally:
            if bmp:
                self.g.DeleteObject(bmp)
            self.g.DeleteDC(mem)
            self.u.ReleaseDC(None, screen)

    def pump(self) -> None:
        msg = wintypes.MSG()
        while self.u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            self.u.TranslateMessage(ctypes.byref(msg))
            self.u.DispatchMessageW(ctypes.byref(msg))

    def close(self) -> None:
        try:
            self.u.DestroyWindow(self.hwnd)
        except Exception:
            pass


def _cursor() -> tuple[int, int]:
    p = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return int(p.x), int(p.y)


def _keep_going() -> bool:
    """A Ctrl key is still held and Esc is not: the circle session carries on."""
    gk = ctypes.windll.user32.GetAsyncKeyState
    return any(gk(v) & 0x8000 for v in _CTRL) and not (gk(_VK_ESCAPE) & 0x8000)


# ── one circling session ─────────────────────────────────────────────────────────

_session_lock = threading.Lock()
_on_ask = None          # callback(text): run `text` as a Lyra command (set by start())


def _notify(title: str, subtitle: str = "", phase: str = "info", ms: int = 6000) -> None:
    try:
        import ui_bridge
        ui_bridge.push_activity("circle", title, subtitle=subtitle or None, phase=phase, duration_ms=ms)
    except Exception:
        pass


def _circle_session():
    """Show the flame while Ctrl is held (Esc cancels); return (circled crop (PIL), its rectangle in screen
    pixels) or None."""
    import screen_capture
    try:                                                 # cursor, capture and mascot must all use physical px
        from computer.mouse import ensure_dpi_aware
        ensure_dpi_aware()
    except Exception:  # noqa: BLE001
        pass
    shot = screen_capture.capture_full()                 # taken before the overlay exists
    vx, vy, vw, vh = virtual_screen()
    try:
        overlay = _Overlay()
    except Exception as e:                               # no visuals, but circling still works
        log.warning("Circle to ask: overlay unavailable (%s)", e)
        overlay = None
    pts: deque = deque()
    det = CircleDetector()
    lock = None
    loop = None
    drew = overlay is not None
    end = time.monotonic() + SESSION_MAX_SECONDS

    # The cursor is sampled by its own thread at ~240 Hz, so a fast circle gets plenty of points even
    # when a frame takes a while to draw; the window timer is raised to 1 ms so sleeps are accurate.
    plock = threading.Lock()
    stop_sampling = threading.Event()

    def _sample() -> None:
        while not stop_sampling.is_set():
            t = time.monotonic()
            c = _cursor()
            with plock:
                if not pts or math.hypot(c[0] - pts[-1][0], c[1] - pts[-1][1]) >= 1.5:
                    pts.append((c[0], c[1], t))
                while pts and t - pts[0][2] > TRAIL_SECONDS:
                    pts.popleft()
            time.sleep(1 / 240)

    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
        timer_raised = True
    except Exception:  # noqa: BLE001
        timer_raised = False
    sampler = threading.Thread(target=_sample, name="circle-sampler", daemon=True)
    sampler.start()
    try:
        while time.monotonic() < end and _keep_going():
            now = time.monotonic()
            cur = _cursor()
            with plock:
                snap = list(pts)
            if loop is None:
                loop = det.find(snap)
                if loop:
                    xs, ys = [p[0] for p in loop], [p[1] for p in loop]
                    lock = ((min(xs), min(ys), max(xs), max(ys)), now)
            if drew:
                try:
                    img, (ox, oy) = render_frame(snap, cur, now, lock)
                    overlay.present(img, ox, oy)
                    overlay.pump()
                except Exception as e:
                    log.warning("Circle to ask: drawing stopped (%s)", e)
                    drew = False
            if lock and now - lock[1] >= LOCK_SECONDS:
                break
            time.sleep(1 / 60)
    finally:
        stop_sampling.set()
        sampler.join(timeout=0.5)
        if timer_raised:
            try:
                ctypes.windll.winmm.timeEndPeriod(1)
            except Exception:  # noqa: BLE001
                pass
        if overlay is not None:
            overlay.close()
    if not loop:
        return None
    pts_canvas = [(x - vx, y - vy) for x, y, _ in loop]
    boxes = crop_box(pts_canvas, (vw, vh), shot.size)
    if not boxes:
        return None
    cx0, cy0, cx1, cy1 = boxes[0]
    global _last_shot
    _last_shot = (shot.convert("RGB"), (vx, vy))
    return _last_shot[0].crop(boxes[1]), (cx0 + vx, cy0 + vy, cx1 + vx, cy1 + vy)


QUESTION = "Are you confused about something here? What do you want to know about it?"


def _listen_once(max_wait: float = 7.0, max_len: float = 14.0) -> str | None:
    """Open the microphone, wait for the user to speak, stop after a pause, return the transcript.
    None when nobody spoke or the microphone / speech-to-text is unavailable."""
    try:
        import numpy as np
        import pyaudio
        import voice
    except Exception as e:  # noqa: BLE001
        log.warning("Circle to ask: cannot listen (%s)", e)
        return None
    ptt = None
    try:
        import ptt as _ptt
        ptt = _ptt
    except Exception:
        pass
    busy = getattr(ptt, "_busy", None)
    got_busy = False
    if busy is not None:
        try:
            got_busy = busy.acquire(timeout=2.0)
        except Exception:
            got_busy = False
    paused = False
    pcm = b""
    rate, chunk = 16000, 1024
    try:
        before = getattr(ptt, "_before_record", None)
        if before:
            try:
                before()
                paused = True
            except Exception as e:  # noqa: BLE001
                log.warning("Circle to ask: could not pause the wake-word microphone: %s", e)
        time.sleep(0.25)                                   # let the end of Lyra's own voice die away
        ui.set_state("listening")
        index = None
        try:
            import mic_devices
            index = mic_devices.preferred_input_index()
        except Exception:
            pass
        pa = pyaudio.PyAudio()
        kw = dict(format=pyaudio.paInt16, channels=1, rate=rate, input=True, frames_per_buffer=chunk)
        if index is not None:
            kw["input_device_index"] = index
        stream = pa.open(**kw)
        frames, noise = [], []
        speech, quiet, t0 = False, 0.0, time.monotonic()
        try:
            while True:
                data = stream.read(chunk, exception_on_overflow=False)
                frames.append(data)
                a = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(a * a)) + 1e-6)
                el = time.monotonic() - t0
                if el < 0.35:                              # measure the room first
                    noise.append(rms)
                    continue
                thr = max(450.0, (sum(noise) / max(1, len(noise))) * 2.8)
                if rms > thr:
                    speech, quiet = True, 0.0
                elif speech:
                    quiet += chunk / rate
                if speech and quiet >= 1.1:
                    break
                if not speech and el > max_wait:
                    break
                if el > max_len:
                    break
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
        if speech:
            pcm = b"".join(frames)
    except Exception as e:  # noqa: BLE001
        log.warning("Circle to ask: listening failed: %s", e)
    finally:
        if paused:
            after = getattr(ptt, "_after_record", None)
            if after:
                try:
                    after()
                except Exception:
                    pass
        if got_busy:
            try:
                busy.release()
            except Exception:
                pass
    if len(pcm) < rate * 2 * 0.3:
        try:
            if getattr(ui, "_last_state", "idle") == "listening":
                ui.set_state("idle")
        except Exception:
            pass
        return None
    ui.set_state("thinking")
    try:
        import voice
        status, text = voice._stt_engine.transcribe(pcm, sample_rate=rate)
    except Exception as e:  # noqa: BLE001
        log.warning("Circle to ask: transcription failed: %s", e)
        ui.set_state("idle")
        return None
    text = (text or "").strip()
    if status != "ok" or not text:
        ui.set_state("idle")
        return None
    return text


def _ask_what_to_know() -> None:
    """Lyra asks what the user wants to know about the circle, listens, and runs the answer."""
    text = None
    try:
        import voice
        voice.speak(QUESTION)
        text = _listen_once()
    except Exception as e:  # noqa: BLE001
        log.warning("Circle to ask: could not ask: %s", e)
    if text and _on_ask is not None:
        try:
            _on_ask(text)
        except Exception as e:  # noqa: BLE001
            log.warning("Circle to ask: answer failed: %s", e)
    else:
        _notify("Circled — ask about it", "Hold Right Ctrl and speak, or type", ms=7000)


def _session() -> None:
    res = None
    try:
        res = _circle_session()
    except Exception as e:
        log.exception("Circle to ask failed: %s", e)
        _notify("Circle to ask couldn't start", str(e)[:80], phase="fail")
    finally:
        _session_lock.release()
    if res is None:
        return
    crop, rect = res
    _set_pending(crop, rect)
    log.info("Circle to ask: captured %sx%s", *crop.size)
    auto, mode = True, "ask"
    try:
        import config
        auto = bool(getattr(config, "CIRCLE_AUTO_ANSWER", True))
        mode = str(getattr(config, "CIRCLE_ASK_MODE", "ask")).strip().lower()
    except Exception:
        pass
    if auto and _on_ask is not None and mode == "auto":
        _notify("Looking at it…", phase="start", ms=4000)
        try:
            _on_ask(DEFAULT_QUESTION)
        except Exception as e:
            log.warning("Circle to ask: answer failed: %s", e)
    elif auto and _on_ask is not None:
        _ask_what_to_know()
    else:
        _notify("Circled — ask about it", "Hold Right Ctrl and speak, or type", ms=7000)


def launch() -> bool:
    """Start a circling session (ignored if one is already open)."""
    if not _session_lock.acquire(blocking=False):
        return False
    threading.Thread(target=_session, daemon=True, name="lyra-circle").start()
    return True


# ── hotkey: tap Ctrl, then hold it
# Polls the real key state ~65x/s instead of using a keyboard hook: nothing can get
# stuck or silently stop firing, and it never touches the keys it watches.

POLL_SECONDS = 0.015
_OTHER_KEYS = [v for v in range(0x08, 0xFF) if v not in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C, 0xE8)]   # every key that is not a modifier


class DoubleTapDetector:
    """True once, when Ctrl has been tapped and then pressed again and HELD: the first press is a quick tap (down and up
    within `tap` seconds), the second starts within `gap` seconds of it and is held `hold` seconds, and no other
    key is pressed meanwhile (Ctrl+C, Ctrl+Click ... never count). The caller keeps circling while Ctrl stays down."""

    def __init__(self, tap: float | None = None, gap: float | None = None, hold: float | None = None):
        self.tap = TAP_MAX_SECONDS if tap is None else tap
        self.gap = TAP_GAP_SECONDS if gap is None else gap
        self.hold = HOLD_SECONDS if hold is None else hold
        self.down_at: float | None = None      # when the current press started
        self.tainted = False                   # something else was pressed during this press (or it was held too long)
        self.first_end: float | None = None    # when the first tap ended (None = no first tap waiting)
        self.is_second = False                 # this press follows a clean first tap
        self.fired = False

    def update(self, now: float, ctrl: bool, other: bool) -> bool:
        if ctrl:
            if self.down_at is None:
                self.down_at, self.tainted, self.fired = now, False, False
                self.is_second = self.first_end is not None and now - self.first_end <= self.gap
                if not self.is_second:
                    self.first_end = None
            if other:
                self.tainted = True
            if self.is_second:
                if not self.tainted and not self.fired and now - self.down_at >= self.hold:
                    self.fired, self.first_end = True, None
                    return True
            elif now - self.down_at > self.tap:
                self.tainted = True               # held too long to be a first tap
            return False
        if self.down_at is None:
            if self.first_end is not None and now - self.first_end > self.gap:
                self.first_end = None
            return False
        first_tap = not self.is_second and not self.tainted and now - self.down_at <= self.tap
        self.down_at, self.is_second = None, False
        self.first_end = now if first_tap else None
        return False


_started = False
_poll_thread: threading.Thread | None = None


def _poll_loop() -> None:
    gk = ctypes.windll.user32.GetAsyncKeyState

    def down(vks) -> bool:
        return any(gk(v) & 0x8000 for v in vks)

    det = DoubleTapDetector()
    warned = False
    while _started:
        try:
            ctrl = down(_CTRL)
            other = ctrl and down(_OTHER_KEYS)
            if det.update(time.monotonic(), ctrl, other):
                log.info("Circle to ask: Ctrl tapped, then held: flame on")
                launch()
        except Exception as e:
            if not warned:
                log.warning("Circle to ask hotkey error: %s", e)
                warned = True
        time.sleep(POLL_SECONDS)


def start(on_ask=None) -> bool:
    """Arm the tap-then-hold-Ctrl hotkey. `on_ask(text)` runs `text` as a Lyra command when a
    circle is finished. Safe to call once; never raises."""
    global _started, _on_ask, _poll_thread
    if on_ask is not None:
        _on_ask = on_ask
    if _started:
        return True
    try:
        import game_mode
        if game_mode.is_active():          # a game is running: no key polling until it ends
            game_mode.defer("circle_ask")
            return False
    except Exception:
        pass
    try:
        ctypes.windll.user32.GetAsyncKeyState(0x11)      # Windows only
    except Exception as e:
        log.warning("Circle to ask: hotkey unavailable (%s)", e)
        return False
    _started = True
    _poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="lyra-circle-hotkey")
    _poll_thread.start()
    log.info("Circle to ask: tap Ctrl, then hold it, to circle anything")
    return True


def is_running() -> bool:
    """True while the tap-then-hold-Ctrl watcher thread is alive."""
    return bool(_started and _poll_thread is not None and _poll_thread.is_alive())


def stop() -> None:
    """End the watcher thread (it is gone when this returns), so start() can arm it again."""
    global _started, _poll_thread
    _started = False
    t, _poll_thread = _poll_thread, None
    if t is not None and t is not threading.current_thread():
        t.join(timeout=1.0)


# ── self-test:  python circle_ask.py ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print("Circle to ask self-test")
    try:
        from PIL import Image  # noqa: F401
        print("  Pillow ......... ok")
    except Exception as e:
        print(f"  Pillow ......... MISSING: {e}\n  -> pip install --upgrade Pillow")
        sys.exit(1)
    try:
        ctypes.windll.user32.GetAsyncKeyState(0x11)
        print("  key state ...... ok")
    except Exception as e:
        print(f"  key state ...... FAILED: {e}")
        sys.exit(1)
    try:
        ov = _Overlay()
        img, (ox, oy) = render_frame([], _cursor(), time.monotonic())
        ov.present(img, ox, oy)
        time.sleep(0.4)
        ov.close()
        print("  overlay window . ok (you should have seen a blue flame at your cursor)")
    except Exception as e:
        print(f"  overlay window . FAILED: {e}")
        sys.exit(1)
    import screen_capture
    shot = screen_capture.capture_full()
    print(f"  screenshot ..... ok ({shot.width}x{shot.height})")

    def demo(text):
        print(f"\n>>> Circle finished! Lyra would now run: {text!r}")
        print(f"    circled picture: {_pending['size'][0]}x{_pending['size'][1]} px" if _pending else "    (no picture)")

    start(on_ask=demo)
    print("\nNow tap Ctrl, press it again and hold it while you circle something (Ctrl+C to quit)...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
