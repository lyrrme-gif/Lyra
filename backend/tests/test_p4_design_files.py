"""Phase 4: structural checks on the design layer (offline, no browser).

- every window links the tokens, its skin and boot.ts
- island / wheel / caption skins never touch geometry (click-through rects depend on it)
- design CSS makes no network requests and stays inside @supports (old webviews keep the legacy look)
- the hot-file edit in settings/main.ts is exactly the two marked blocks
"""
import re
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[2] / "ui"
DESIGN = UI / "src" / "design"

WINDOWS = {
    "index.html": "skin-island.css",
    "settings.html": "skin-settings.css",
    "wheel.html": "skin-wheel.css",
    "caption.html": "skin-caption.css",
}
GEOMETRY = re.compile(r"(?<![-\w])(width|height|min-width|max-width|min-height|max-height|margin[-\w]*|padding[-\w]*|top|left|right|bottom|inset|position|display|gap|flex[-\w]*|grid[-\w]*|float|overflow[-\w]*)\s*:")


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


class WindowsAreWired(unittest.TestCase):
    def test_each_html_links_tokens_skin_and_boot(self):
        for html, skin in WINDOWS.items():
            text = (UI / html).read_text(encoding="utf-8")
            self.assertIn("/src/design/tokens.css", text, html)
            self.assertIn(f"/src/design/{skin}", text, html)
            self.assertIn("/src/design/boot.ts", text, html)
            self.assertLess(text.index("tokens.css"), text.index(skin), f"{html}: tokens must load before the skin")


class SkinsKeepGeometry(unittest.TestCase):
    def test_no_layout_properties_in_window_skins(self):
        for name in ("skin-island.css", "skin-wheel.css", "skin-caption.css"):
            css = strip_comments((DESIGN / name).read_text(encoding="utf-8"))
            bad = [m.group(0) for m in GEOMETRY.finditer(css)]
            self.assertEqual(bad, [], f"{name} must not change layout: {bad}")


class DesignCssHygiene(unittest.TestCase):
    def test_no_network_and_supports_guard(self):
        for css_file in DESIGN.glob("*.css"):
            css = strip_comments(css_file.read_text(encoding="utf-8"))
            self.assertNotRegex(css, r"url\(\s*['\"]?https?:", css_file.name)
            self.assertNotIn("@import", css, css_file.name)
            if css_file.name.startswith("skin-"):
                self.assertIn("@supports (color: oklch(from red l c h))", css, css_file.name)

    def test_tokens_use_the_documented_names(self):
        css = (DESIGN / "tokens.css").read_text(encoding="utf-8")
        for name in ("--d-bg0", "--d-surface", "--d-tx", "--d-tx2", "--d-tx3", "--d-ac", "--d-on-ac", "--d-ok", "--d-warn", "--d-danger",
                     "--d-t-fast", "--d-t-base", "--d-t-slow", "--d-spring", "--p4-h"):
            self.assertIn(name + ":", css)
        for ms in re.findall(r"--d-t-(?:fast|base|slow):\s*(\d+)ms", css):
            self.assertTrue(150 <= int(ms) <= 300, f"motion timing {ms}ms outside 150-300")


class HotFileEdit(unittest.TestCase):
    def test_settings_main_only_has_marked_phase4_lines(self):
        text = (UI / "src" / "settings" / "main.ts").read_text(encoding="utf-8")
        blocks = re.findall(r"// >>> phase-4 begin\n(.*?)\n// <<< phase-4 end", text, flags=re.S)
        self.assertEqual(len(blocks), 2)
        self.assertTrue(all(len(b.splitlines()) <= 2 for b in blocks))
        self.assertIn("registerP4", blocks[1])


class KitHasEveryComponent(unittest.TestCase):
    def test_exports(self):
        kit = (UI / "src" / "settings" / "uiKit.ts").read_text(encoding="utf-8")
        for fn in ("Card", "Row", "Segmented", "KeyCap", "StatusPill", "EmptyState", "Toggle", "Slider", "Section", "Banner"):
            self.assertRegex(kit, rf"export function {fn}\b", fn)
        # the old public API must still be there
        self.assertIn("export function enhanceSelects", kit)
        self.assertIn("export function segmented<", kit)


if __name__ == "__main__":
    unittest.main()
