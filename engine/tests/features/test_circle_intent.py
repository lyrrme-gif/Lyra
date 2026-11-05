import sys, types, logging, unittest
if "engine.bridge.ui" not in sys.modules:
    m = types.ModuleType("engine.bridge.ui"); m.log = logging.getLogger("t"); sys.modules["engine.bridge.ui"] = m
import engine.features.circle_explain as ce

class Intent(unittest.TestCase):
    def test_plain(self):
        for q in ["what's the name of this song", "who is this", "what does it say", "how many episodes", "translate this", ""]:
            self.assertFalse(ce.wants_explanation(q), q)
    def test_explain(self):
        for q in ["explain this", "how does this work", "why is it red", "what does this mean", "I'm confused about this", "what is this?"]:
            self.assertTrue(ce.wants_explanation(q), q)


class Regions(unittest.TestCase):
    def test_finds_text_lines(self):
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (670, 370), (0, 0, 0)); d = ImageDraw.Draw(im)
        truth = [(100, 100, 330, 125), (100, 150, 380, 175), (450, 152, 600, 172)]
        for x0, y0, x1, y1 in truth:
            for x in range(x0, x1, 8):
                d.rectangle([x, y0, x + 2, y1], fill=(255, 255, 255))
        regs = ce.find_regions(im)
        self.assertEqual(len(regs), 3)
        for t in truth:
            self.assertTrue(any(abs(r[0]-t[0]) < 8 and abs(r[1]-t[1]) < 4 and abs(r[2]-t[2]) < 14 for r in regs), t)

    def test_union_and_parse(self):
        self.assertEqual(ce._union([(0, 0, 10, 10), (20, 20, 40, 40)], [1, 2], (100, 100)), [0.0, 0.0, 0.4, 0.4])
        st = ce._parse('[{"text":"a","draw":{"shape":"box","regions":[1,2]}}]')
        self.assertEqual(st[0]["draw"]["regions"], [1, 2])


class ScreenRouting(unittest.TestCase):
    def test_routes(self):
        import engine.features.guide as g
        self.assertEqual(g._parse("can you explain the exercise on screen please?")["kind"], "screen")
        self.assertEqual(g._parse("explique moi cet exercice")["kind"], "screen")
        self.assertIsNone(g._parse("explain recursion"))
        self.assertIsNone(g._parse("what is the capital of France"))


class Locate(unittest.TestCase):
    def test_locate_box_picks_line_and_grows_to_full_width(self):
        from PIL import Image, ImageDraw
        W, H = 1600, 900
        im = Image.new("RGB", (W, H), (25, 25, 25)); d = ImageDraw.Draw(im)
        d.rectangle([340, 60, 1500, 900], fill=(255, 255, 255))
        for y0, y1, x1 in [(100, 135, 1450), (700, 740, 1450), (760, 800, 900)]:
            for x in range(480, x1, 8):                # thin glyph-like strokes, ~25% ink like real text
                d.rectangle([x, y0, x + 2, y1], fill=(0, 0, 0))
        def fake(prompt, b64, mt):
            if "numbered cells" in prompt:
                return "8", None
            regs = ce.find_regions(im.crop((0, 0, 1, 1))) if False else None
            return "__PICK__", None
        calls = {}
        def fake2(prompt, b64, mt):
            if "numbered cells" in prompt:
                return "8", None
            # pick the box whose number tab is nearest the line at y~700: ask by re-detecting regions
            import base64, io
            return calls["ids"], None
        ce._ask_img = fake2
        # find which region index is the y=700 line inside the cell-8 crop
        cw, ch = W / 3, H / 3
        ox, oy = int(max(0, 1 * cw - cw * .2)), int(max(0, 2 * ch - ch * .2))
        sub = im.crop((ox, oy, int(min(W, 2 * cw + cw * .2)), H))
        regs = ce.find_regions(sub)
        idx = next(i for i, r in enumerate(regs, 1) if r[1] + oy < 720 and r[1] + oy > 680 and r[2] - r[0] > 200)
        calls["ids"] = f"[{idx}]"
        box = ce.locate_box(im, "the question line")
        self.assertIsNotNone(box)
        px = [box[0] * W, box[1] * H, box[2] * W, box[3] * H]
        self.assertLess(abs(px[0] - 480), 12); self.assertLess(abs(px[1] - 700), 12)
        self.assertLess(abs(px[2] - 1450), 20)       # grown past the cell crop to the real end of the line

if __name__ == "__main__":
    unittest.main()
