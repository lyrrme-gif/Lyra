"""Lyra — circle to explain.

After Ctrl+Shift + circling something, Lyra asks what the user wants to know (circle_ask), then
this module turns the answer into a short guided explanation: the vision model looks at the
circled picture and returns up to MAX_STEPS steps, each a spoken sentence plus (optionally) a shape
to draw on the screen (circle / underline / arrow / box / line) around the thing it is talking about.

The steps are played by guide.py (speech in sync with the Mochi, "next / repeat / stop" work); the
mascot window (ui/src/mascot) walks to each spot, draws the shape with its hand and points.
Nothing here clicks or types: it only looks, speaks and draws.
"""

from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import ui

log = ui.log

MAX_STEPS = 4

# The Mochi only walks over and draws when the user wants something EXPLAINED. A question that just
# wants a fact (a name, a title, a number, "what does it say", yes/no) gets a short spoken answer.
_EXPLAIN = re.compile(
    r"\b(explain|explanation|how (?:does|do|it|this|to|can|come)|why|walk me|show me|teach|understand|"
    r"confus\w*|what (?:does|do) (?:this|that|it|these|those) (?:mean|do)|what(?:'s| is) (?:going on|happening|this for)|"
    r"what is it for|break (?:it )?down|step by step|"
    r"expliqu\w*|pourquoi|comment|je (?:ne )?comprends|montre|"
    r"اشرح|شرح|علاش|لماذا|كيف|فهمني|وضح)\b", re.I)
_EXPLAIN_PLAIN_DEFAULT = re.compile(r"^\s*(?:what(?:'s| is)?\s+(?:this|that|it)\??|what\s+is\s+this\s*\??|explain\s+it\s*\??)\s*$", re.I)


def wants_explanation(question: str) -> bool:
    """True when the question asks for an explanation (walk-through with drawing). Name / fact /
    yes-no questions return False and are simply answered."""
    q = (question or "").strip()
    if not q:
        return False
    if _EXPLAIN.search(q):
        return True
    return bool(_EXPLAIN_PLAIN_DEFAULT.match(q))


SHAPES = ("circle", "underline", "arrow", "box", "line")

_PROMPT = (
    "The image is a crop of the user's screen that they just circled. They ask: \"{q}\"\n"
    "You explain it with a small friendly character that walks over, points and DRAWS on the screen.\n"
    "Reply with ONLY a JSON array of 1 to {n} steps, in the order you would explain them. Each step is\n"
    "{{\"text\": \"<what you say aloud: one short sentence, at most 22 words>\", "
    "\"draw\": {{\"shape\": \"circle|underline|arrow|box\", \"regions\": [ids]}} or null}}\n"
    "{regions_help}"
    "Rules: only draw on things you can really see; \"regions\" lists the ids of the numbered cyan boxes that "
    "hold EXACTLY the thing the step talks about (several ids when it spans several boxes, e.g. a whole block of "
    "lines); never pick a box that is not about the step; use circle for a single item, underline for a word or "
    "line of text, arrow to say 'look here', box for an area; use null when a step has nothing to draw; the "
    "first step answers the question directly; what you say must match what you draw; no markdown; write the "
    "spoken text in the language of the question."
)

_PROMPT_BIG = (
    "The image is the user's screen. They ask: \"{q}\"\n"
    "You explain it with a small friendly character that walks over, points and DRAWS on the screen.\n"
    "Reply with ONLY a JSON array of 1 to {n} steps, in the order you would explain them. Each step is\n"
    "{{\"text\": \"<what you say aloud: one short sentence, at most 22 words>\", "
    "\"draw\": {{\"shape\": \"circle|underline|arrow|box\", \"what\": \"<precise description of the ONE thing on screen this "
    "step talks about: quote its first words if it is text, or say its colour, icon and position>\"}} or null}}\n"
    "Rules: only the user's actual content matters (the document, exercise, code, message), not browser or window chrome "
    "unless asked; describe things you can really see; use circle for a single item, underline for a word or line of "
    "text, arrow to say 'look here', box for an area; null when a step has nothing to draw; the first step answers the "
    "question directly; what you say must match what you draw; no markdown; write the spoken text in the language of the question."
)

_GRID_HELP = (
    "The image has a thin red grid with labels (0.1 ... 0.9) along the top edge (x) and left edge (y): fractions of "
    "the image width and height. For \"draw\" give \"box\": [x0, y0, x1, y1] (two decimals, tight around the "
    "thing) instead of \"regions\".\n"
)
_REGION_HELP = (
    "Parts of the image are outlined in cyan boxes with a yellow number tab (1, 2, 3 ...). Use those numbers, "
    "never coordinates.\n"
)


# ── numbered regions: the model PICKS what to draw on instead of guessing coordinates ───────────
MAX_REGIONS = 28


def _local_ink(a):
    """Boolean mask of pixels that differ from their LOCAL background (a 24px block's median), so text
    is found on any uniform colour and big flat panels / pages don't count as ink."""
    import numpy as np
    h, w = a.shape
    B = 48
    ph, pw = (-h) % B, (-w) % B
    p = np.pad(a, ((0, ph), (0, pw)), mode="edge")
    bg = np.median(p.reshape(p.shape[0] // B, B, p.shape[1] // B, B), axis=(1, 3))
    bg = np.repeat(np.repeat(bg, B, axis=0), B, axis=1)[:h, :w]
    return np.abs(a - bg) > 34


def find_regions(img) -> list[tuple[int, int, int, int]]:
    """Boxes (px) of the separate words-lines / buttons / pictures in the picture, from the pixels
    alone (no OCR): local-contrast ink, joined into blobs, one box per blob."""
    import numpy as np
    a = np.asarray(img.convert("L"), dtype=np.int16)
    H, W = a.shape
    if H < 8 or W < 8:
        return []
    ink = _local_ink(a)
    if ink.mean() > 0.45:                                   # busy photo: coarse cells instead
        return [(int(c * W / 3), int(r * H / 3), int((c + 1) * W / 3), int((r + 1) * H / 3))
                for r in range(3) for c in range(3)]
    f = max(1, int(round(max(W, H) / 480)))                 # work on a pooled copy (fast)
    ph, pw = (-H) % f, (-W) % f
    pooled = np.pad(ink, ((0, ph), (0, pw))).reshape((H + ph) // f, f, (W + pw) // f, f).any(axis=(1, 3))
    hh, ww = pooled.shape
    dx = 3                                                  # words of a line join; lines stay apart
    for _ in range(5):
        m = pooled.copy()
        for k in range(1, dx + 1):
            m[:, k:] |= pooled[:, :-k]
            m[:, :-k] |= pooled[:, k:]
        v = m.copy()
        v[1:] |= m[:-1]
        v[:-1] |= m[1:]
        lab = np.zeros((hh, ww), dtype=np.int32)
        parent = [0]

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        ys, xs = np.nonzero(v)
        for y, x in zip(ys.tolist(), xs.tolist()):
            up = lab[y - 1, x] if y else 0
            left = lab[y, x - 1] if x else 0
            if up and left:
                ra, rb = find(up), find(left)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
                lab[y, x] = min(ra, rb)
            elif up or left:
                lab[y, x] = up or left
            else:
                parent.append(len(parent))
                lab[y, x] = len(parent) - 1
        roots = np.array([find(i) for i in range(len(parent))])
        lab = roots[lab]
        boxes = []
        for r in np.unique(lab):
            if r == 0:
                continue
            yy, xx = np.nonzero((lab == r) & pooled)
            if yy.size == 0:
                continue
            x0, x1 = int(xx.min()) * f, min(W, (int(xx.max()) + 1) * f)
            y0, y1 = int(yy.min()) * f, min(H, (int(yy.max()) + 1) * f)
            sub = ink[y0:y1, x0:x1]
            ry, rx = np.nonzero(sub)
            if ry.size == 0:
                continue
            x0, x1, y0, y1 = x0 + int(rx.min()), x0 + int(rx.max()) + 1, y0 + int(ry.min()), y0 + int(ry.max()) + 1
            w_, h_ = x1 - x0, y1 - y0
            if w_ < 6 or h_ < 5:
                continue
            if min(w_, h_) < 4 and max(w_, h_) / max(1, min(w_, h_)) > 12:   # thin border line
                continue
            if w_ * h_ > 0.9 * W * H and len(np.unique(lab)) > 2:
                continue
            boxes.append((x0, y0, x1, y1))
        if len(boxes) <= MAX_REGIONS:
            break
        dx += 3
    boxes.sort(key=lambda b: (b[1] // max(8, H // 40), b[0]))   # reading order
    return boxes[:MAX_REGIONS]


def _marked_b64(img, regions) -> str:
    """The picture with each region outlined and numbered (1, 2, 3 ...)."""
    from PIL import Image, ImageDraw, ImageFont
    im = img.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fs = max(12, min(22, min(im.size) // 45))
    try:
        font = ImageFont.truetype("arialbd.ttf", fs)
    except Exception:  # noqa: BLE001
        try:
            font = ImageFont.truetype("arial.ttf", fs)
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
    for i, (x0, y0, x1, y1) in enumerate(regions, 1):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(im.width - 1, x1), min(im.height - 1, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        d.rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], outline=(0, 220, 255, 230), width=2)
        t = str(i)
        tw = int(d.textlength(t, font=font)) + 6
        ty = max(0, y0 - fs - 3)
        d.rectangle([x0 - 1, ty, x0 - 1 + tw, ty + fs + 3], fill=(255, 215, 0, 235))
        d.text((x0 + 2, ty + 1), t, fill=(0, 0, 0, 255), font=font)
    return _img_to_b64(Image.alpha_composite(im, layer))


def _union(regions, ids, size):
    """Union of the numbered regions as 0-1 fractions of the picture, or None."""
    w, h = size
    picked = [regions[i - 1] for i in ids if isinstance(i, int) and 1 <= i <= len(regions)]
    if not picked:
        return None
    x0, y0 = min(r[0] for r in picked), min(r[1] for r in picked)
    x1, y1 = max(r[2] for r in picked), max(r[3] for r in picked)
    return [x0 / w, y0 / h, x1 / w, y1 / h]



# ── OCR locating: exact text-line boxes from Windows OCR beat asking a vision model to pick boxes ──
_ocr_cache: dict = {}


async def _ocr_lines_async(png: bytes):
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(bytearray(png))
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    bitmap = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return []
    result = await engine.recognize_async(bitmap)
    out = []
    for ln in result.lines:
        xs0, ys0, xs1, ys1 = [], [], [], []
        for w in ln.words:
            r = w.bounding_rect
            xs0.append(r.x); ys0.append(r.y); xs1.append(r.x + r.width); ys1.append(r.y + r.height)
        if xs0:
            out.append((ln.text, (min(xs0), min(ys0), max(xs1), max(ys1))))
    return out


def ocr_lines(img) -> list:
    """[(text, (x0, y0, x1, y1) px)] per text line, from Windows OCR; [] when unavailable. Cached per image."""
    key = (img.size, hash(img.convert('L').resize((48, 48)).tobytes()))   # by content: ids get reused
    if key in _ocr_cache:
        return _ocr_cache[key]
    lines: list = []
    try:
        import asyncio
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        lines = asyncio.run(_ocr_lines_async(buf.getvalue()))
    except Exception as e:  # noqa: BLE001
        log.debug("circle_explain: OCR unavailable: %s", e)
    if len(_ocr_cache) > 4:
        _ocr_cache.clear()
    _ocr_cache[key] = lines
    return lines


def _norm_tokens(t: str) -> list[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 2]


def ocr_locate(img, what: str):
    """Box [x0,y0,x1,y1] (0-1 fractions) of the text line best matching the words quoted in `what`
    (or None). The vision model is asked to quote the first words of the thing, so match those."""
    lines = ocr_lines(img)
    if not lines:
        return None
    m = re.search(r"[\"'\u2018\u2019\u201c\u201d\u00ab]([^\"\u201d\u00bb]{3,}?)[\"'\u2019\u201d\u00bb](?:\s|$|[,.;:)])", what or "")
    q = _norm_tokens(m.group(1) if m else what)[:7]
    if len(q) < 2 and not (q and len(q[0]) >= 6):
        return None
    best, best_score = None, 0.0
    for text, box in lines:
        toks = set(_norm_tokens(text))
        if not toks:
            continue
        hits = sum(1 for w in q if w in toks)
        score = hits / len(q)
        if score > best_score:
            best, best_score = box, score
    if not best or best_score < 0.6:
        return None
    W, H = img.size
    x0, y0, x1, y1 = best
    return [max(0.0, x0 / W), max(0.0, y0 / H), min(1.0, x1 / W), min(1.0, y1 / H)]


# ── hierarchical locating: coarse cell first, then exact pixel regions inside it ───────────────
def _ask_img(prompt: str, img_b64: str, max_tokens: int):
    from actions import apps
    raw, err = apps._vision_complete([{
        "role": "user",
        "content": [{"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}],
    }], max_tokens=max_tokens)
    return re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip(), err


def _cells_b64(img, cols: int = 3, rows: int = 3) -> str:
    """The picture split into cols x rows big numbered cells (1..9, left to right, top to bottom)."""
    from PIL import Image, ImageDraw, ImageFont
    im = img.convert("RGBA")
    w, h = im.size
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fs = max(28, min(w, h) // 8)
    try:
        font = ImageFont.truetype("arialbd.ttf", fs)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    for r in range(rows):
        for c in range(cols):
            x0, y0, x1, y1 = c * w / cols, r * h / rows, (c + 1) * w / cols, (r + 1) * h / rows
            d.rectangle([x0, y0, x1, y1], outline=(255, 40, 40, 255), width=3)
            n = r * cols + c + 1
            d.text((x0 + 10, y0 + 6), str(n), fill=(255, 40, 40, 255), font=font, stroke_width=3, stroke_fill=(255, 255, 255, 255))
    return _img_to_b64(Image.alpha_composite(im, layer))


def locate_box(img, what: str, cols: int = 3, rows: int = 3):
    """Where is `what` on the picture? Returns [x0, y0, x1, y1] as 0-1 fractions of `img`, or None.
    Two small questions instead of one hard one: (1) which of 9 big cells holds it, (2) which of the
    numbered pixel-exact regions inside that cell is it. Never raises."""
    try:
        W, H = img.size
        what = (what or "").replace('"', "'")[:200]
        if not what:
            return None
        hit = ocr_locate(img, what)
        if hit:
            log.info("circle_explain: OCR located %r at %s", what[:50], [round(v, 3) for v in hit])
            return hit
        sub, ox, oy = img, 0, 0
        if min(W, H) > 520 and W * H > 500_000:
            raw, _ = _ask_img(
                f"The picture is split into {cols * rows} numbered cells ({cols} columns x {rows} rows, numbered left to "
                f"right then top to bottom). Which ONE cell contains the main part of: \"{what}\"? "
                "Reply with only the cell number, or 0 if it is not visible.",
                _cells_b64(img, cols, rows), 8)
            m = re.search(r"\d+", raw or "")
            n = int(m.group(0)) if m else 0
            if not 1 <= n <= cols * rows:
                return None
            r, c = divmod(n - 1, cols)
            cw, ch = W / cols, H / rows
            mx, my = cw * 0.2, ch * 0.2                      # margin: the thing may cross the cell border
            ox, oy = int(max(0, c * cw - mx)), int(max(0, r * ch - my))
            x1, y1 = int(min(W, (c + 1) * cw + mx)), int(min(H, (r + 1) * ch + my))
            sub = img.crop((ox, oy, x1, y1))
        regions = find_regions(sub)
        sw, sh = sub.size
        if len(regions) < 1:
            return None
        raw, _ = _ask_img(
            "Parts of the picture are outlined in cyan boxes with a yellow number tab. Which boxes contain EXACTLY: "
            f"\"{what}\"? Reply with only a JSON array of box numbers, e.g. [3] or [4,5]; [] if none fit.",
            _marked_b64(sub, regions), 30)
        a, b = raw.find("["), raw.rfind("]")
        ids = [int(x) for x in json.loads(raw[a:b + 1]) if isinstance(x, (int, float))] if 0 <= a < b else []
        box = _union(regions, ids, (sw, sh))
        if not box:
            return None
        px = (ox + box[0] * sw, oy + box[1] * sh, ox + box[2] * sw, oy + box[3] * sh)
        if sub is not img:
            px = _grow_x(img, px)
        return [px[0] / W, px[1] / H, px[2] / W, px[3] / H]
    except Exception as e:  # noqa: BLE001
        log.debug("circle_explain: locate_box failed: %s", e)
        return None


def _grow_x(img, box):
    """The crop around a cell can cut a long line short: re-measure that line's band over the full
    width and extend the box sideways to the true ends of the text. box = (x0, y0, x1, y1) px."""
    import numpy as np
    W, H = img.size
    x0, y0, x1, y1 = [int(v) for v in box]
    y0, y1 = max(0, y0 - 1), min(H, y1 + 1)
    if y1 - y0 < 3:
        return box
    band = np.asarray(img.convert("L"), dtype=np.int16)[y0:y1]
    bg = int(np.median(band))
    cols = (np.abs(band - bg) > 38).any(axis=0)
    gap = max(10, int((y1 - y0) * 0.9))
    best, start, last = None, None, None
    for i in range(W + gap + 1):
        v = bool(cols[i]) if i < W else False
        if v:
            if start is None:
                start = i
            last = i
        elif start is not None and i - last > gap:
            if min(last + 1, x1) > max(start, x0):          # overlaps the picked box
                ov = min(last + 1, x1) - max(start, x0)
                if best is None or ov > best[0]:
                    best = (ov, start, last + 1)
            start = None
    if not best:
        return box
    return (min(x0, best[1]), box[1], max(x1, best[2]), box[3])


def _locate_steps(img, items: list[dict]) -> None:
    """Fill items' 'draw' boxes from their 'what' descriptions (in place, in parallel)."""
    todo = [it for it in items if it.get("draw") and it["draw"].get("what")]
    if not todo:
        return

    def one(it):
        d = it["draw"]
        box = locate_box(img, d["what"])
        it["draw"] = {"shape": d["shape"], "box": box} if box else None

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(one, todo))


def _b64_to_img(b64: str):
    from PIL import Image
    return Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")


def _img_to_b64(img) -> str:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _grid_b64(img, box=None, line=None) -> str:
    """The picture with a thin labelled 0.1-step grid (so the model can read positions precisely) and,
    optionally, a red rectangle (`box`) or red line (`line`) the model is asked to check."""
    from PIL import Image, ImageDraw, ImageFont
    im = img.convert("RGBA")
    w, h = im.size
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fs = max(10, min(w, h) // 26)
    try:
        font = ImageFont.truetype("arial.ttf", fs)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    for i in range(1, 10):
        t = i / 10
        x, y = int(t * w), int(t * h)
        d.line([(x, 0), (x, h)], fill=(255, 40, 40, 85), width=1)
        d.line([(0, y), (w, y)], fill=(255, 40, 40, 85), width=1)
        lab = f"{t:.1f}"
        d.rectangle([x + 1, 0, x + 1 + fs * 2, fs + 3], fill=(255, 255, 255, 190))
        d.text((x + 2, 0), lab, fill=(200, 0, 0, 255), font=font)
        d.rectangle([0, y + 1, fs * 2, y + fs + 4], fill=(255, 255, 255, 190))
        d.text((1, y + 2), lab, fill=(200, 0, 0, 255), font=font)
    if box:
        x0, y0, x1, y1 = box
        d.rectangle([x0 * w, y0 * h, x1 * w, y1 * h], outline=(255, 0, 255, 255), width=max(2, w // 200))
    if line:
        x0, y0, x1, y1 = line
        d.line([(x0 * w, y0 * h), (x1 * w, y1 * h)], fill=(255, 0, 255, 255), width=max(2, w // 200))
    return _img_to_b64(Image.alpha_composite(im, layer))


_REFINE = (
    "The magenta rectangle was drawn on this picture to mark: \"{what}\".\n"
    "A thin red grid with labels (fractions 0-1 of the image width along the top, of the height down the left) is "
    "drawn on it; ignore the grid. Look at where the magenta rectangle really is.\n"
    "Reply with ONLY a JSON array [x0, y0, x1, y1] (fractions, two decimals): the TIGHT box around exactly the "
    "thing described, correcting the rectangle if it is off, or the same numbers if it is already right."
)


def _refine_one(img, item: dict) -> None:
    """Show the model its own box and let it correct it (in place). Never raises."""
    from actions import apps
    d = item.get("draw")
    if not d or d["shape"] == "line":
        return
    try:
        raw, _ = apps._vision_complete([{
            "role": "user",
            "content": [
                {"type": "text", "text": _REFINE.format(what=item["text"].replace('"', "'")[:160])},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_grid_b64(img, box=d['box'])}"}},
            ],
        }], max_tokens=60)
        raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
        a, b = raw.find("["), raw.rfind("]")
        if a < 0 or b <= a:
            return
        v = [_num(x) for x in json.loads(raw[a:b + 1])]
        if len(v) != 4 or any(x is None for x in v):
            return
        v = [min(1.0, max(0.0, x)) for x in v]
        x0, y0, x1, y1 = min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3])
        if x1 - x0 < 0.01 or y1 - y0 < 0.01:
            return
        d["box"] = [x0, y0, x1, y1]
    except Exception as e:  # noqa: BLE001
        log.debug("circle_explain: refine skipped: %s", e)


def _refine_all(img, items: list[dict]) -> None:
    try:
        import config
        if not bool(getattr(config, "CIRCLE_REFINE", True)):
            return
    except Exception:  # noqa: BLE001
        pass
    todo = [it for it in items if it.get("draw") and it["draw"]["shape"] != "line"]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda it: _refine_one(img, it), todo))


def _num(v) -> float | None:
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return None
    return f if f == f else None


def _load_steps(raw: str):
    """The model's reply as a list of step dicts. Accepts a JSON array, a single step object, or
    {"steps": [...]} (models often ignore 'ONLY a JSON array'), with or without ``` fences."""
    raw = re.sub(r"```(?:json)?", "", raw or "").strip()
    for open_c, close_c in (("[", "]"), ("{", "}")):
        a, b = raw.find(open_c), raw.rfind(close_c)
        if a < 0 or b <= a:
            continue
        try:
            data = json.loads(raw[a:b + 1])
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            inner = data.get("steps")
            data = inner if isinstance(inner, list) else [data]
        if isinstance(data, list):
            return data
    return []


def _parse(raw: str) -> list[dict]:
    data = _load_steps(raw)
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        draw = None
        d = item.get("draw")
        if isinstance(d, dict):
            shape = str(d.get("shape") or "").strip().lower()
            box = d.get("box")
            ids = d.get("regions")
            what = d.get("what")
            if shape in SHAPES and isinstance(what, str) and what.strip():
                draw = {"shape": shape, "what": what.strip()}
            elif shape in SHAPES and isinstance(ids, list):
                ids = [int(x) for x in ids if isinstance(x, (int, float)) and x == x]
                if ids:
                    draw = {"shape": shape, "regions": ids}
            elif shape in SHAPES and isinstance(box, (list, tuple)) and len(box) == 4:
                v = [_num(x) for x in box]
                if all(x is not None for x in v):
                    v = [min(1.0, max(0.0, x)) for x in v]
                    draw = {"shape": shape, "box": v}
        out.append({"text": text[:240], "draw": draw})
        if len(out) >= MAX_STEPS:
            break
    return out



# ── debug dump: what the model saw / answered / where the boxes went (folder: debug_explain/) ─────
def _dump(stamp: str, shown_b64: str, raw: str, q: str, items=None, img=None, regions=None) -> None:
    try:
        import os
        from pathlib import Path
        from PIL import ImageDraw
        d = Path(__file__).resolve().parent / "debug_explain"
        d.mkdir(exist_ok=True)
        if shown_b64:
            (d / f"{stamp}_1_sent.png").write_bytes(base64.b64decode(shown_b64))
        lines = [f"question: {q}", f"regions found: {len(regions or [])}", "", "RAW MODEL REPLY:", raw or "(empty)"]
        if items is not None:
            lines += ["", "PARSED STEPS (after locating):"]
            lines += [f"{i}. draw={it.get('draw')} | {it['text']}" for i, it in enumerate(items, 1)]
        (d / f"{stamp}_2_reply.txt").write_text("\n".join(lines), encoding="utf-8")
        if items is not None and img is not None:
            im = img.convert("RGB").copy()
            dr = ImageDraw.Draw(im)
            for i, it in enumerate(items, 1):
                dd = it.get("draw")
                if dd and dd.get("box"):
                    x0, y0, x1, y1 = dd["box"]
                    dr.rectangle([x0 * im.width, y0 * im.height, x1 * im.width, y1 * im.height], outline=(255, 0, 0), width=4)
                    dr.text((x0 * im.width + 4, y0 * im.height + 2), str(i), fill=(255, 0, 0))
            im.save(d / f"{stamp}_3_boxes.png")
        files = sorted(d.glob("*"), key=lambda f: f.stat().st_mtime)
        for f in files[:-24]:                     # keep the last ~8 runs
            f.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        log.debug("circle_explain: dump failed: %s", e)



_LYRA_UI = re.compile(r"^\W*(lyra|thinking|listening|speaking|executing)\b", re.I)
OCR_MAX = 70


def ocr_regions(img):
    """(boxes px, texts) for the text lines of the picture from Windows OCR (Lyra's own pill removed),
    or ([], []) when OCR is unavailable / finds too little. Lines are the right unit to point at."""
    W, H = img.size
    keep = []
    for text, (x0, y0, x1, y1) in ocr_lines(img):
        t = (text or "").strip()
        if not t or _LYRA_UI.match(t) or y1 - y0 < 6 or x1 - x0 < 8:
            continue
        keep.append((t, (int(max(0, x0)), int(max(0, y0)), int(min(W, x1)), int(min(H, y1)))))
    if len(keep) < 3:
        return [], []
    keep = keep[:OCR_MAX]
    return [b for _, b in keep], [t for t, _ in keep]



def screen_ocr_regions(screen, origin, rect, img_size):
    """Like ocr_regions(), but for a circled area: OCR the screenshot AROUND the circle (30% margin) so
    lines that stick out of the circle are read whole, keep the lines mostly inside it, and return their
    full boxes in the circled picture's pixels (they may stick out of it; that is the point)."""
    try:
        vx, vy = origin
        rx0, ry0, rx1, ry1 = [float(v) for v in rect]
        w, h = rx1 - rx0, ry1 - ry0
        if w < 8 or h < 8:
            return [], []
        bx0, by0 = int(max(0, rx0 - w * 0.3 - vx)), int(max(0, ry0 - h * 0.3 - vy))
        bx1, by1 = int(min(screen.width, rx1 + w * 0.3 - vx)), int(min(screen.height, ry1 + h * 0.3 - vy))
        sub = screen.crop((bx0, by0, bx1, by1))
        iw, ih = img_size
        keep = []
        for text, (x0, y0, x1, y1) in ocr_lines(sub):
            t = (text or "").strip()
            if not t or _LYRA_UI.match(t) or y1 - y0 < 6 or x1 - x0 < 8:
                continue
            X0, Y0, X1, Y1 = x0 + bx0 + vx, y0 + by0 + vy, x1 + bx0 + vx, y1 + by0 + vy      # virtual px
            ix = max(0.0, min(X1, rx1) - max(X0, rx0))
            iy = max(0.0, min(Y1, ry1) - max(Y0, ry0))
            if ix * iy < 0.4 * (X1 - X0) * (Y1 - Y0):
                continue                                                  # mostly outside the circle
            keep.append((t, ((X0 - rx0) / w * iw, (Y0 - ry0) / h * ih, (X1 - rx0) / w * iw, (Y1 - ry0) / h * ih)))
        if len(keep) < 1:
            return [], []
        keep = keep[:OCR_MAX]
        return [b for _, b in keep], [t for t, _ in keep]
    except Exception as e:  # noqa: BLE001
        log.debug("circle_explain: screen OCR failed: %s", e)
        return [], []


def _monitor_for(cx: float, cy: float) -> tuple[float, float, float, float]:
    """(left, top, width, height) of the monitor holding the screen point (cx, cy)."""
    try:
        import screen_capture
        for m in screen_capture.monitors():
            if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                return float(m["left"]), float(m["top"]), float(m["width"]), float(m["height"])
    except Exception as e:  # noqa: BLE001
        log.debug("circle_explain: monitors unavailable: %s", e)
    try:
        import circle_ask
        vx, vy, vw, vh = circle_ask.virtual_screen()
        return float(vx), float(vy), float(vw), float(vh)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0, 1920.0, 1080.0


def plan(pending: dict, question: str):
    """Steps for guide._start(): [{text, ms, x, y, draw?}] with x/y/draw in 0-1 fractions of the
    monitor the circle is on. Returns (steps, error)."""
    import guide
    from actions import apps

    rect = pending.get("rect")
    if not rect:
        return None, "I lost track of where you circled."
    rx0, ry0, rx1, ry1 = [float(v) for v in rect]
    mx, my, mw, mh = _monitor_for((rx0 + rx1) / 2, (ry0 + ry1) / 2)

    def to_mon(px: float, py: float) -> tuple[float, float]:
        return (min(0.99, max(0.01, (px - mx) / mw)), min(0.99, max(0.01, (py - my) / mh)))

    def crop_to_mon(fx: float, fy: float) -> tuple[float, float]:
        return to_mon(rx0 + fx * (rx1 - rx0), ry0 + fy * (ry1 - ry0))

    q = (question or "").strip() or "What is this? Explain it."
    regions: list = []
    sz = pending.get("size") or (0, 0)
    big = sz[0] * sz[1] > 900_000                           # a whole screen / big area: locate step by step
    prompt_tpl = _PROMPT_BIG if big else _PROMPT
    try:
        img = _b64_to_img(pending["b64"])
        ocr_boxes, ocr_texts = [], []
        if pending.get("screen") is not None and pending.get("screen_origin") is not None:
            ocr_boxes, ocr_texts = screen_ocr_regions(pending["screen"], pending["screen_origin"], rect, img.size)
        if not ocr_boxes:
            ocr_boxes, ocr_texts = ocr_regions(img)
        if ocr_boxes:                                       # text lines from OCR: exact boxes + their text
            big, prompt_tpl = False, _PROMPT
            regions = ocr_boxes
            listing = "\n".join(f"{i}: {t[:70]}" for i, t in enumerate(ocr_texts, 1))
            shown = _marked_b64(img, regions)
            help_ = (_REGION_HELP + "Every numbered box is one LINE of text; this is what each line says "
                     "(OCR, may contain small mistakes; ignore lines that are browser, window or taskbar "
                     "text):\n" + listing + "\nOnly describe what is really written there; never invent numbers.\n")
            log.info("circle_explain: using %d OCR text lines as regions", len(regions))
        else:
            try:
                regions = [] if big else find_regions(img)
            except Exception as e:  # noqa: BLE001
                log.debug("circle_explain: no regions (%s)", e)
        if ocr_boxes:
            pass
        elif big:
            shown, help_ = pending["b64"], ""
        elif len(regions) >= 2:
            shown, help_ = _marked_b64(img, regions), _REGION_HELP
        else:
            regions, shown, help_ = [], _grid_b64(img), _GRID_HELP
    except Exception as e:  # noqa: BLE001
        log.debug("circle_explain: no grid (%s)", e)
        img, shown, help_ = None, pending["b64"], _GRID_HELP
    raw, err = apps._vision_complete([{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt_tpl.format(q=q.replace('"', "'"), n=MAX_STEPS, regions_help=help_)},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{shown}"}},
        ],
    }], max_tokens=700)
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    import time as _t
    stamp = _t.strftime("%H%M%S")
    _dump(stamp, shown, raw, q, regions=regions)
    items = _parse(raw)
    if not items:
        if not raw:
            return None, (f"I couldn't read that — {err}" if err else "I looked at it but couldn't put it into words.")
        # The model answered in plain prose: say it (never raw JSON), and circle the whole region.
        if re.match(r"\s*[\[{]", raw) or '"text"' in raw:
            m = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            prose = m.group(1) if m else ""
        else:
            prose = raw
        prose = re.sub(r"\s+", " ", prose).strip()
        if not prose:
            return None, "I looked at it but couldn't put it into words."
        items = [{"text": prose[:300], "draw": {"shape": "circle", "box": [0.0, 0.0, 1.0, 1.0]}}]

    if img is not None and big:                             # descriptions -> exact boxes (cell, then region)
        _locate_steps(img, items)
    for it in items:                                        # numbered regions -> exact pixel boxes
        d = it.get("draw")
        if d and "regions" in d:
            box = _union(regions, d["regions"], img.size) if (img is not None and regions) else None
            it["draw"] = {"shape": d["shape"], "box": box} if box else None
    if img is not None and not regions and not big:         # only guessed coordinates need a second look
        _refine_all(img, items)
    for i, it in enumerate(items, 1):
        log.info("circle_explain: step %d draw=%s | %s", i, it.get("draw"), it["text"][:70])
    _dump(stamp, "", raw, q, items=items, img=img, regions=regions)
    cx, cy = to_mon((rx0 + rx1) / 2, (ry0 + ry1) / 2)
    steps = []
    for it in items:
        st = {"text": it["text"], "ms": guide._step_ms(it["text"]), "x": cx, "y": cy}
        d = it["draw"]
        if d:
            x0, y0, x1, y1 = d["box"]
            if d["shape"] != "line":                       # boxes: order the corners
                x0, x1 = min(x0, x1), max(x0, x1)
                y0, y1 = min(y0, y1), max(y0, y1)
            a = crop_to_mon(x0, y0)
            b = crop_to_mon(x1, y1)
            st["draw"] = {"shape": d["shape"], "x0": round(a[0], 4), "y0": round(a[1], 4),
                          "x1": round(b[0], 4), "y1": round(b[1], 4)}
            st["x"], st["y"] = round((a[0] + b[0]) / 2, 4), round((a[1] + b[1]) / 2, 4)
        steps.append(st)
    return steps, None


def explain(pending: dict, question: str) -> str | None:
    """Start the guided explanation. Returns the short line for Lyra to say now, or None when it
    could not start (the caller then answers in plain text)."""
    try:
        import guide
        steps, err = plan(pending, question)
        if not steps:
            log.warning("circle_explain: %s", err)
            return None
        guide._start(steps, stay=True)                      # the Mochi stays out on the screen afterwards
        return "Okay, let me show you."
    except Exception as e:  # noqa: BLE001
        log.warning("circle_explain failed: %s", e)
        return None
