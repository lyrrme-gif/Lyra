"""Precise on-screen element location.

Pass 1: ask the vision model for a bounding box in NORMALISED coordinates
        (0-1000 of image width/height). Normalised coords don't depend on how
        much the image was downscaled, and are converted to physical screen
        pixels with the SAME frame metadata (left/top/width/height) the frame
        was captured with, so DPI scaling can't shift the click.
Pass 2: if the first answer is not clearly confident, or the box is big, crop
        around it and ask again; accept the refined box only if it stays
        inside the first one (guards against the model drifting).

Returns a dict; callers use ["x"], ["y"] as the click point. No clicking here.
"""
from __future__ import annotations

import base64
import json
import re
from io import BytesIO
from typing import Optional

from PIL import Image

import ui

log = ui.log

_BIG_BOX = 0.30        # box wider/taller than 30% of the frame -> refine
_MAX_SIDE_P1 = 1280
_MAX_SIDE_P2 = 896


# ---------- pure helpers (unit-testable) ----------
def parse_box(raw: str) -> Optional[dict]:
    """Extract {found, x1,y1,x2,y2, label, confidence} from model text."""
    if not raw:
        return None
    txt = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{.*\}", txt, flags=re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    found = d.get("found")
    if isinstance(found, str):
        found = found.strip().lower() in ("true", "yes", "1")
    out = {"found": bool(found), "label": str(d.get("label") or "").strip(),
           "confidence": str(d.get("confidence") or "medium").lower()}
    if "high" in out["confidence"]:
        out["confidence"] = "high"
    elif "low" in out["confidence"]:
        out["confidence"] = "low"
    else:
        out["confidence"] = "medium"
    if not out["found"]:
        return out
    try:
        x1, y1, x2, y2 = (float(d[k]) for k in ("x1", "y1", "x2", "y2"))
    except Exception:
        return None
    x1, x2 = sorted((max(0.0, min(1000.0, x1)), max(0.0, min(1000.0, x2))))
    y1, y2 = sorted((max(0.0, min(1000.0, y1)), max(0.0, min(1000.0, y2))))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    out.update(x1=x1, y1=y1, x2=x2, y2=y2)
    return out


def box_to_screen(b: dict, left: int, top: int, width: int, height: int) -> dict:
    """Normalised box -> physical screen px (box + centre)."""
    sx1 = left + b["x1"] / 1000.0 * width
    sx2 = left + b["x2"] / 1000.0 * width
    sy1 = top + b["y1"] / 1000.0 * height
    sy2 = top + b["y2"] / 1000.0 * height
    return {"box": (int(sx1), int(sy1), int(sx2), int(sy2)),
            "x": int((sx1 + sx2) / 2), "y": int((sy1 + sy2) / 2)}


def crop_region(b: dict, pad: float = 0.12, min_frac: float = 0.25) -> tuple[float, float, float, float]:
    """Normalised crop window (x1,y1,x2,y2) around a box, at least min_frac of the frame."""
    cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
    w = max((b["x2"] - b["x1"]) + pad * 2000, min_frac * 1000)
    h = max((b["y2"] - b["y1"]) + pad * 2000, min_frac * 1000)
    x1, y1 = cx - w / 2, cy - h / 2
    x1, y1 = max(0.0, min(x1, 1000 - w)), max(0.0, min(y1, 1000 - h))
    return x1, y1, min(1000.0, x1 + w), min(1000.0, y1 + h)


def crop_box_to_full(sub: dict, region: tuple[float, float, float, float]) -> dict:
    """Map a box in crop-normalised coords back to full-frame normalised coords."""
    rx1, ry1, rx2, ry2 = region
    rw, rh = rx2 - rx1, ry2 - ry1
    return {**sub,
            "x1": rx1 + sub["x1"] / 1000.0 * rw, "x2": rx1 + sub["x2"] / 1000.0 * rw,
            "y1": ry1 + sub["y1"] / 1000.0 * rh, "y2": ry1 + sub["y2"] / 1000.0 * rh}


def _inside(inner: dict, outer: dict, tol: float = 40.0) -> bool:
    cx, cy = (inner["x1"] + inner["x2"]) / 2, (inner["y1"] + inner["y2"]) / 2
    return (outer["x1"] - tol <= cx <= outer["x2"] + tol and
            outer["y1"] - tol <= cy <= outer["y2"] + tol)


# ---------- model calls ----------
def _jpeg_b64(img: Image.Image, max_side: int, quality: int = 82) -> str:
    im = img
    if max(im.size) > max_side:
        im = im.copy()
        im.thumbnail((max_side, max_side))
    buf = BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


_PROMPT = (
    "Find this UI element in the image: {desc}\n"
    "Reply with ONLY one JSON object, no markdown:\n"
    '{{"found": true|false, "x1": n, "y1": n, "x2": n, "y2": n, '
    '"label": "short text of what you found", "confidence": "high|medium|low"}}\n'
    "x1,y1 = top-left and x2,y2 = bottom-right corner of the element's tight bounding box. "
    "All four numbers are 0-1000 fractions of the image: x is horizontal "
    "(0 = left edge, 1000 = right edge), y is vertical (0 = top, 1000 = bottom). "
    "Box only the single clickable element, not its surroundings. "
    "If it is not visible, reply {{\"found\": false}}. Never guess."
)


def _ask(img: Image.Image, desc: str, max_side: int) -> Optional[dict]:
    from actions.apps import _vision_complete   # lazy: avoids a circular import
    b64 = _jpeg_b64(img, max_side)
    raw, err = _vision_complete([{
        "role": "user",
        "content": [
            {"type": "text", "text": _PROMPT.format(desc=desc)},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    }], max_tokens=120)
    if err and not raw:
        raise err
    return parse_box(raw)


def locate(description: str, image: Image.Image, left: int, top: int,
           width: int, height: int, refine: bool = True) -> dict:
    """Locate `description` on a frame captured at (left, top, width, height).

    Returns {"ok": True, x, y, box, label, confidence, refined}
         or {"ok": False, "reason": "not_found" | "vision_error" | "parse_error", ...}
    """
    try:
        b1 = _ask(image, description, _MAX_SIDE_P1)
    except Exception as e:
        return {"ok": False, "reason": "vision_error", "error": str(e)}
    if b1 is None:
        return {"ok": False, "reason": "parse_error"}
    if not b1["found"]:
        return {"ok": False, "reason": "not_found"}

    best, refined = b1, False
    big = ((b1["x2"] - b1["x1"]) / 1000 > _BIG_BOX) or ((b1["y2"] - b1["y1"]) / 1000 > _BIG_BOX)
    if refine and (b1["confidence"] != "high" or big):
        try:
            region = crop_region(b1)
            iw, ih = image.size
            crop = image.crop((int(region[0] / 1000 * iw), int(region[1] / 1000 * ih),
                               int(region[2] / 1000 * iw), int(region[3] / 1000 * ih)))
            sub = _ask(crop, description, _MAX_SIDE_P2)
            if sub and sub["found"]:
                full = crop_box_to_full(sub, region)
                if _inside(full, b1):
                    best, refined = full, True
                    best["confidence"] = "high" if sub["confidence"] == "high" else b1["confidence"]
                else:
                    log.info("vision refine drifted outside first box - keeping first")
        except Exception as e:
            log.info("vision refine skipped: %s", e)

    pt = box_to_screen(best, left, top, width, height)
    return {"ok": True, **pt, "label": best.get("label") or description,
            "confidence": best["confidence"], "refined": refined}
