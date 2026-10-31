"""Study tab maths: LaTeX tidy-up, symbolic answer checking, spoken formulas, the handwriting entry point.

Offline: no AI, no network. The symbolic tests skip themselves when SymPy is not installed (it is optional).
"""
import base64
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import study          # noqa: E402
import study_math as m  # noqa: E402

try:
    import sympy  # noqa: F401
    HAVE_SYMPY = True
except Exception:
    HAVE_SYMPY = False


class CleanLatex(unittest.TestCase):
    def test_strips_wrappers_and_fences(self):
        self.assertEqual(m.clean_latex("```latex\n$$\\frac{a}{b}$$\n```"), "\\frac{a}{b}")
        self.assertEqual(m.clean_latex("$x^2$"), "x^2")
        self.assertEqual(m.clean_latex("\\(x+1\\)"), "x+1")
        self.assertEqual(m.clean_latex("latex: x+1"), "x+1")
        self.assertEqual(m.clean_latex("<think>hmm</think>  y = 2"), "y = 2")
        self.assertEqual(m.clean_latex(None), "")

    def test_json_backslash_repair(self):
        import json
        raw = '[{"q":"Compute $\\frac{1}{2}\\times 4$","a":"$\\neq$"}]'     # single backslashes: json would eat them
        item = json.loads(m.repair_json_latex(raw))[0]
        self.assertEqual(item["q"], "Compute $\\frac{1}{2}\\times 4$")
        self.assertEqual(item["a"], "$\\neq$")
        bad = '[{"q":"$\\int_0^1 x\\,dx$ and $\\sqrt{2}\\left(1\\right)$","a":"caf\\u00e9"}]'   # \\int, \\, \\sqrt, \\left: invalid escapes
        item = json.loads(m.repair_json_latex(bad))[0]
        self.assertEqual(item["q"], "$\\int_0^1 x\\,dx$ and $\\sqrt{2}\\left(1\\right)$")
        self.assertEqual(item["a"], "café")                                # a real \\u escape survives
        ok = '[{"q":"line\\nnext","a":"$\\\\frac{1}{2}$"}]'                # already correct: unchanged
        self.assertEqual(json.loads(m.repair_json_latex(ok))[0]["a"], "$\\frac{1}{2}$")
        self.assertEqual(json.loads(m.repair_json_latex(ok))[0]["q"], "line\nnext")


class Detect(unittest.TestCase):
    def test_looks_mathy(self):
        for yes in ("the Fourier transform", "x^2 = 4", "$\\frac{1}{2}$", "integration by parts", "solve for x"):
            self.assertTrue(m.looks_mathy(yes), yes)
        for no in ("French history", "the water cycle", ""):
            self.assertFalse(m.looks_mathy(no), no)


class Speakable(unittest.TestCase):
    def test_reads_formulas(self):
        self.assertEqual(m.speakable("Solve $\\frac{x^2}{3} = \\sqrt{2}$ for $x$"),
                         "Solve x squared over 3 equals the square root of 2 for x")
        self.assertEqual(m.speakable("plain words"), "plain words")
        self.assertIn("pi", m.speakable("$2\\pi r$"))


@unittest.skipUnless(HAVE_SYMPY, "SymPy not installed")
class Equivalence(unittest.TestCase):
    def test_same(self):
        for a, b in (("\\frac{1}{2}", "0.5"), ("x^2+2x+1", "(x+1)^2"), ("x=4", "4"), ("\\sqrt{x}", "x^{1/2}"),
                     ("\\frac{x^2-1}{x-1}", "x+1"), ("2x+1=7", "x=3"), ("\\sin^2 x", "1-\\cos^2 x"),
                     ("e^{2x}", "\\exp(2x)"), ("\\log_{2}(8)", "3"), ("x = \\pm 2", "x=2, x=-2"),
                     ("x=2 \\text{ or } x=3", "x=3, x=2")):
            self.assertIs(m.equivalent(a, b), True, (a, b))

    def test_different(self):
        for a, b in (("3", "4"), ("x^2", "x^3"), ("x+1", "x+2"), ("x=2, x=3", "x=2, x=4"), ("x=2 \\text{ or } x=3", "x=3")):
            self.assertIs(m.equivalent(a, b), False, (a, b))

    def test_cannot_tell_returns_none(self):
        self.assertIsNone(m.equivalent("\\begin{pmatrix}1\\end{pmatrix}", "1"))
        self.assertIsNone(m.equivalent("x<3", "x<4"))
        self.assertIsNone(m.equivalent("", "1"))
        self.assertIsNone(m.equivalent("the derivative", "3x^2"))

    def test_hostile_input_is_never_executed(self):
        for bad in ("__import__('os').system('echo hi')", "x.__class__", "open('f')", "a;b", "x[0]"):
            self.assertIsNone(m.equivalent(bad, "1"), bad)


class Grading(unittest.TestCase):
    def test_math_card_uses_symbolic_check(self):
        if not HAVE_SYMPY:
            self.skipTest("SymPy not installed")
        card = {"q": "Expand $(x+1)^2$", "a": "$x^2+2x+1$", "math": True}
        self.assertEqual(study.grade(card, "(x+1)(x+1)")[0], "correct")
        self.assertEqual(study.grade(card, "x^2+1")[0], "wrong")


class InkEntry(unittest.TestCase):
    def setUp(self):
        self.png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
        self.apps = types.ModuleType("actions.apps")
        pkg = types.ModuleType("actions")
        pkg.apps = self.apps
        self._saved = {k: sys.modules.get(k) for k in ("actions", "actions.apps")}
        sys.modules["actions"], sys.modules["actions.apps"] = pkg, self.apps

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_rejects_non_png_and_oversize(self):
        self.assertTrue(study.recognize_ink("hello")["error"])
        self.assertTrue(study.recognize_ink("data:image/png;base64,@@@")["error"])
        text = "data:image/png;base64," + base64.b64encode(b"not a png at all").decode()
        self.assertTrue(study.recognize_ink(text)["error"])
        big = "data:image/png;base64," + "A" * (study.INK_MAX_B64 + 4)
        self.assertIn("too large", study.recognize_ink(big)["error"])

    def test_no_vision_key(self):
        self.apps._vision_candidates = lambda: []
        r = study.recognize_ink(self.png)
        self.assertEqual(r["latex"], "")
        self.assertIn("AI key", r["error"])

    def test_reads_latex(self):
        self.apps._vision_candidates = lambda: [("x", "k", "u", "m")]
        self.apps._vision_complete = lambda msgs, max_tokens=180: ("```latex\n$\\frac{1}{2}x^{2}$\n```", None)
        self.assertEqual(study.recognize_ink(self.png), {"latex": "\\frac{1}{2}x^{2}", "error": ""})

    def test_unreadable(self):
        self.apps._vision_candidates = lambda: [("x", "k", "u", "m")]
        self.apps._vision_complete = lambda msgs, max_tokens=180: ("???", None)
        r = study.recognize_ink(self.png)
        self.assertEqual(r["latex"], "")
        self.assertIn("couldn't read", r["error"])


if __name__ == "__main__":
    unittest.main()
