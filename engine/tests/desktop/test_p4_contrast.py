"""Phase 4: WCAG contrast of the design tokens (desktop/src/shared/design/tokens.css), for every accent swatch, both themes.
Offline: parses the CSS, converts OKLCH to sRGB (gamut-clipped) and checks the ratios."""
import math
import re
import unittest
from pathlib import Path

TOKENS = Path(__file__).resolve().parents[3] / "desktop" / "src" / "shared" / "design" / "tokens.css"
SWATCHES = {"violet": "#8f7bff", "blue": "#3b8cff", "mint": "#2fd3a6", "pink": "#ff6fae", "amber": "#ffb020"}


def hex_hue(hexcol: str) -> float:
    r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda v: v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = lin(r), lin(g), lin(b)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s
    bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
    h = math.degrees(math.atan2(bb, a))
    return round(h + 360 if h < 0 else h)


def oklch_to_linear(L, C, H):
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    return tuple(min(1.0, max(0.0, v)) for v in (r, g, bl))


def luminance(rgb):
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def parse_block(css: str, selector: str):
    """Token name -> (L, C, H-or-'var', alpha) for the block whose selector list contains `selector`."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in m.group(1):
            toks = {}
            for name, body in re.findall(r"(--d-[a-z0-9-]+):\s*(oklch\([^;]*\));", m.group(2)):
                mm = re.match(r"oklch\(\s*([\d.]+)\s+([\d.]+|var\(--p4-c\))\s+([\d.]+|var\(--p4-h\))\s*(?:/\s*([\d.]+))?\s*\)", body)
                if mm:
                    toks[name] = mm.groups()
            return toks
    raise AssertionError(f"no block for {selector}")


def resolve(tok, hue):
    L, C, H, A = tok
    C = 0.16 if C.startswith("var") else float(C)  # default chroma for --p4-c; the light theme sets its own numbers
    H = hue if H.startswith("var") else float(H)
    return float(L), C, H, float(A) if A else 1.0


def rgb_of(tok, hue, over=None):
    L, C, H, A = resolve(tok, hue)
    rgb = oklch_to_linear(L, C, H)
    if A < 1 and over is not None:
        rgb = tuple(A * c + (1 - A) * o for c, o in zip(rgb, over))
    return rgb


class TokenContrast(unittest.TestCase):
    css = TOKENS.read_text(encoding="utf-8")

    def check(self, theme_selector, pairs):
        t = parse_block(self.css, theme_selector)
        fails = []
        for name, hexcol in SWATCHES.items():
            hue = hex_hue(hexcol)
            for fg, bg, need in pairs:
                bg_rgb = rgb_of(t[bg], hue)
                # translucent glass sits on the window background
                if bg.endswith("glass") or bg.endswith("glass-2"):
                    bg_rgb = rgb_of(t[bg], hue, over=rgb_of(t["--d-bg1"], hue))
                fg_rgb = rgb_of(t[fg], hue, over=bg_rgb)
                ratio = contrast(fg_rgb, bg_rgb)
                if ratio < need:
                    fails.append(f"{theme_selector} {name}(h{hue}) {fg} on {bg}: {ratio:.2f} < {need}")
        self.assertEqual(fails, [], "\n" + "\n".join(fails))

    TEXT = [
        ("--d-tx", "--d-bg0", 7.0), ("--d-tx", "--d-bg1", 7.0), ("--d-tx", "--d-surface", 7.0), ("--d-tx", "--d-glass-2", 4.5),
        ("--d-tx2", "--d-bg0", 4.5), ("--d-tx2", "--d-surface", 4.5), ("--d-tx2", "--d-glass", 4.5),
        ("--d-tx3", "--d-bg0", 4.5), ("--d-tx3", "--d-bg1", 4.5), ("--d-tx3", "--d-surface", 4.5), ("--d-tx3", "--d-glass", 4.5),
        ("--d-on-ac", "--d-ac", 4.5), ("--d-ac", "--d-bg0", 3.0), ("--d-ac", "--d-surface", 3.0),
        ("--d-ok", "--d-bg0", 4.5), ("--d-warn", "--d-bg0", 4.5), ("--d-danger", "--d-bg0", 4.5),
        ("--d-ok", "--d-surface", 4.5), ("--d-warn", "--d-surface", 4.5), ("--d-danger", "--d-surface", 4.5),
    ]

    def test_dark(self):
        self.check(":root, :root[data-d-theme=\"dark\"]", self.TEXT)

    def test_light(self):
        self.check(":root[data-d-theme=\"light\"]", self.TEXT)

    def test_has_reduced_motion_and_focus(self):
        self.assertIn("prefers-reduced-motion: reduce", self.css)
        self.assertIn('data-d-motion="reduce"', self.css)
        self.assertIn(":focus-visible", self.css)


if __name__ == "__main__":
    unittest.main()
