"""Lyra — math helpers for the Study tab. Pure functions, no Lyra imports, so they are easy to test.

    clean_latex(s)        tidy an LLM / handwriting reply into bare LaTeX (no $ fences, no prose)
    looks_mathy(text)     does a topic or a card look like mathematics?
    equivalent(a, b)      are two answers the same maths?  True / False / None (= cannot tell)
    speakable(text)       LaTeX -> words, so Lyra can read a formula aloud

`equivalent` uses SymPy when it is installed (it is optional: requirements-optional.txt). Without SymPy,
or whenever an answer does not parse, it returns None and the caller falls back to the AI grader.

Nothing here ever hands the raw answer to eval(): the text is converted to a plain expression string,
checked against a whitelist of characters and names, and only then parsed with an empty builtins table.
"""
from __future__ import annotations

import random
import re

MAX_LEN = 400

# ── tidy-up ──────────────────────────────────────────────────────────────────────────────────

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def clean_latex(s: str | None) -> str:
    """Strip code fences, $…$ / \\(…\\) / \\[…\\] wrappers and a leading 'latex:' label."""
    t = _THINK.sub("", s or "").strip()
    t = _FENCE.sub("", t).strip()
    t = re.sub(r"^(?:latex|tex|math)\s*:\s*", "", t, flags=re.I)
    for a, b in (("$$", "$$"), ("$", "$"), ("\\[", "\\]"), ("\\(", "\\)")):
        if t.startswith(a) and t.endswith(b) and len(t) >= len(a) + len(b):
            t = t[len(a):len(t) - len(b)].strip()
            break
    return re.sub(r"\s+", " ", t)[:MAX_LEN]


_MATH_WORDS = re.compile(
    r"\b(?:algebra|calculus|derivative|integral|differentiat\w*|integrat\w*|limit|equation|inequalit\w*|polynomial|"
    r"quadratic|matrix|matrices|determinant|vector|trigonometr\w*|sine|cosine|logarithm\w*|exponential|"
    r"probabilit\w*|statistic\w*|geometry|theorem|factori[sz]e|simplify|solve|fraction|sequence|series|"
    r"complex\s+numbers?|linear\s+algebra|fourier|laplace|taylor|eigen\w*|math\w*|maths)\b", re.I)
_MATH_SYMBOLS = re.compile(r"\\(?:frac|sqrt|int|sum|lim|pi|alpha|beta|theta|cdot|times|le|ge|neq|infty)\b|\$|[∫∑√π≤≥≠∞]")
_MATH_EXPR = re.compile(r"(?:[0-9a-z)\]]\s*[\^=+*/]\s*[0-9a-z(\\])|(?:\b[a-z]\s*\(\s*[a-z0-9]\s*\)\s*=)", re.I)


def looks_mathy(text: str | None) -> bool:
    t = text or ""
    return bool(_MATH_SYMBOLS.search(t) or _MATH_WORDS.search(t) or _MATH_EXPR.search(t))


# LaTeX commands that start with a letter JSON also uses as an escape (\\f \\t \\b \\n \\r \\u). If a model writes
# them with ONE backslash inside a JSON string, json.loads would quietly turn \\frac into a form feed.
_JSON_CLASH = re.compile(
    r"(?<!\\)\\(?=(?:frac|dfrac|tfrac|forall|flat|times|theta|text|textbf|tan|tau|top|to|triangle|tilde|therefore|"
    r"neq|nu|neg|nabla|ne|notin|ni|not|beta|bar|binom|begin|bmod|boldsymbol|bullet|because|"
    r"rho|right|rightarrow|Rightarrow|rangle|real|"
    r"upsilon|underline|uparrow|cup|cap)(?![A-Za-z]))")


def repair_json_latex(raw: str) -> str:
    """Make LaTeX written with single backslashes survive json.loads.

    A model that forgets to double its backslashes writes \\int or \\sqrt (invalid JSON escapes) and \\frac or
    \\times (valid escapes that would silently become a form feed / tab). Every backslash that is not a real JSON
    escape is doubled; \\n, \\t, \\u1234 and friends are left alone unless they start a LaTeX command."""
    s = raw or ""
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c); i += 1
            continue
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if nxt in ("\\", '"', "/"):                         # already a proper escape: keep the pair
            out.append(c + nxt); i += 2
        elif nxt and nxt in "bfnrtu":
            if _JSON_CLASH.match(s, i) or (nxt == "u" and not re.match(r"[0-9a-fA-F]{4}", s[i + 2:i + 6])):
                out.append("\\\\")                          # a LaTeX command: double it
            else:
                out.append(c)                                 # a real \n, \t ...
            i += 1
        else:                                                 # \int, \sqrt, \left ...
            out.append("\\\\"); i += 1
    return "".join(out)


# ── LaTeX -> plain expression string ─────────────────────────────────────────────────────────

_GREEK = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lamda lambda mu nu xi rho sigma tau "
          "upsilon phi chi psi omega").split()
_FUNCS = {"sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot", "sec": "sec", "csc": "csc",
          "arcsin": "asin", "arccos": "acos", "arctan": "atan", "sinh": "sinh", "cosh": "cosh",
          "tanh": "tanh", "ln": "ln", "log": "log", "exp": "exp", "sqrt": "sqrt", "abs": "Abs"}


def _group(s: str, i: int) -> tuple[str, int] | None:
    """Brace group starting at s[i] == '{'. Returns (inside, index after the closing brace)."""
    if i >= len(s) or s[i] != "{":
        return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return None


def _arg(s: str, i: int) -> tuple[str, int] | None:
    """One argument: a {group}, or a single character / digit run (\\frac12, ^2, \\sqrt x)."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return None
    if s[i] == "{":
        return _group(s, i)
    m = re.match(r"\d+(?:\.\d+)?|[A-Za-z]|\\[A-Za-z]+", s[i:])
    return (m.group(0), i + m.end()) if m else (s[i], i + 1)


def _convert(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", s[i:])
            if not m:
                i += 1
                continue
            name, i = m.group(1), i + m.end()
            if name in ("frac", "dfrac", "tfrac"):
                a = _arg(s, i)
                b = _arg(s, a[1]) if a else None
                if not a or not b:
                    raise ValueError("bad fraction")
                out.append(f"(({_convert(a[0])})/({_convert(b[0])}))")
                i = b[1]
            elif name == "sqrt":
                root = None
                if i < len(s) and s[i] == "[":
                    j = s.find("]", i)
                    if j < 0:
                        raise ValueError("bad root")
                    root, i = s[i + 1:j], j + 1
                a = _arg(s, i)
                if not a:
                    raise ValueError("bad root")
                inner = _convert(a[0])
                out.append(f"(({inner})**(1/({_convert(root)})))" if root else f"sqrt({inner})")
                i = a[1]
            elif name == "log" and i < len(s) and s[i] == "_":      # \log_{b}(x) -> log(x, b) form
                a = _arg(s, i + 1)
                if not a:
                    raise ValueError("bad log")
                out.append(f"logb_{re.sub(r'[^0-9A-Za-z]', '', _convert(a[0]))}")
                i = a[1]
            elif name in ("cdot", "times", "ast"):
                out.append("*")
            elif name == "div":
                out.append("/")
            elif name == "pi":
                out.append("pi")
            elif name == "infty":
                out.append("oo")
            elif name in _FUNCS:
                out.append(_FUNCS[name])
            elif name in _GREEK:
                out.append("lamda" if name == "lambda" else name)
            elif name in ("text", "mathrm", "textrm", "mbox"):
                g = _group(s, i)
                if not g:
                    continue
                word = g[0].strip().lower()
                if word in ("or", "and", "et", "ou"):
                    out.append(",")                         # 'x=2 \\text{ or } x=3' -> two answers
                elif name in ("mathrm", "textrm") and re.fullmatch(r"[A-Za-z]", word):
                    out.append(word)
                i = g[1]
            elif name in ("left", "right", ",", ";", ":", "!", " ", "quad", "qquad", "displaystyle"):
                continue
            elif name in ("pm", "mp"):
                out.append("±")
            else:
                raise ValueError(f"unsupported command \\{name}")
        elif c == "^":
            a = _arg(s, i + 1)
            if not a:
                raise ValueError("bad power")
            out.append(f"**({_convert(a[0])})")
            i = a[1]
        elif c == "_":
            a = _arg(s, i + 1)
            if not a:
                raise ValueError("bad subscript")
            out.append("_" + re.sub(r"[^0-9A-Za-z]", "", _convert(a[0])))
            i = a[1]
        elif c == "{":
            g = _group(s, i)
            if not g:
                raise ValueError("unbalanced braces")
            out.append(f"({_convert(g[0])})")
            i = g[1]
        elif c == "}":
            raise ValueError("unbalanced braces")
        elif c == "·" or c == "×":
            out.append("*"); i += 1
        elif c == "÷":
            out.append("/"); i += 1
        elif c == "−":
            out.append("-"); i += 1
        elif c == "π":
            out.append("pi"); i += 1
        elif c == "√":
            a = _arg(s, i + 1)
            if not a:
                raise ValueError("bad root")
            out.append(f"sqrt({_convert(a[0])})"); i = a[1]
        else:
            out.append(c); i += 1
    return "".join(out)


_SAFE_CHARS = re.compile(r"^[0-9A-Za-z_+\-*/^().,=\s±]*$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def to_expression(s: str) -> str | None:
    """LaTeX (or plain linear text like 'x^2+1', 'sqrt(x)') -> a plain SymPy-style string, or None."""
    t = clean_latex(s)
    if not t or "\\begin" in t:
        return None
    try:
        e = _convert(t)
    except ValueError:
        return None
    e = e.replace("^", "**")
    e = re.sub(r"\*\*\s*\*\*", "**", e)
    e = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", e)      # 1,000 -> 1000
    e = re.sub(r"\s+", " ", e).strip()
    if not e or not _SAFE_CHARS.match(e) or "__" in e:
        return None
    return e


# ── equivalence ──────────────────────────────────────────────────────────────────────────────

def _split_top(e: str, seps: str = ",") -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in e:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and ch in seps:
            parts.append(cur); cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _expand_pm(parts: list[str]) -> list[str]:
    out: list[str] = []
    for p in parts:
        if "±" in p:
            out += [p.replace("±", "+", 1), p.replace("±", "-", 1)]
        else:
            out.append(p)
    return out


def _sympy():
    try:
        import sympy  # noqa: F401
        from sympy.parsing import sympy_parser as sp
        return sympy, sp
    except Exception:
        return None


def _parse(e: str):
    """Expression string -> SymPy object, or None. Names are whitelisted; builtins are empty."""
    mods = _sympy()
    if not mods:
        return None
    sympy, sp = mods
    local: dict = {"pi": sympy.pi, "E": sympy.E, "e": sympy.E, "oo": sympy.oo, "Abs": sympy.Abs, "ln": sympy.log,
                   "sqrt": sympy.sqrt, "exp": sympy.exp, "log": lambda x: sympy.log(x, 10)}
    for f in ("sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "sinh", "cosh", "tanh"):
        local[f] = getattr(sympy, f)
    # \log_{b}(x) was written as 'logb_<b>'; turn the base into a function of one argument
    for name in set(re.findall(r"logb_[0-9A-Za-z]+", e)):
        base = name[5:]
        b = sympy.Integer(base) if base.isdigit() else sympy.Symbol(base)
        local[name] = (lambda b_: (lambda x: sympy.log(x, b_)))(b)
    for ident in set(_IDENT.findall(e)):
        if ident in local:
            continue
        if re.fullmatch(r"[A-Za-z](?:_[A-Za-z0-9]+)?", ident) or ident in _GREEK or ident == "lamda":
            local[ident] = sympy.Symbol(ident)
        elif re.fullmatch(r"[A-Za-z]{2,3}", ident):
            continue                                        # 'xy' -> x*y through split_symbols below
        else:
            return None
    tr = sp.standard_transformations + (sp.implicit_multiplication_application, sp.convert_xor)
    try:
        names = {"Symbol": sympy.Symbol, "Integer": sympy.Integer, "Float": sympy.Float, "Rational": sympy.Rational,
                 "__builtins__": {}}
        return sp.parse_expr(e, local_dict=local, global_dict=names, transformations=tr, evaluate=True)
    except Exception:
        return None


def _numeric_diff(a, b) -> bool | None:
    """True if a-b is ~0 at several random points, False if it clearly is not, None if undecidable."""
    mods = _sympy()
    if not mods:
        return None
    sympy, _ = mods
    try:
        diff = a - b
        syms = sorted(diff.free_symbols, key=lambda s: s.name)
        if len(syms) > 6:
            return None
        rng = random.Random(7)
        ok = bad = 0
        for _ in range(10):
            pt = {s: sympy.Rational(rng.randint(3, 40), rng.randint(2, 9)) for s in syms}
            try:
                v = complex(sympy.N(diff.subs(pt), 20))
            except Exception:
                continue
            if v != v or abs(v) == float("inf"):
                continue
            scale = 1.0
            try:
                scale += abs(complex(sympy.N(a.subs(pt), 20)))
            except Exception:
                pass
            if abs(v) <= 1e-8 * scale:
                ok += 1
            else:
                bad += 1
            if ok + bad >= 5:
                break
        if bad:
            return False
        return True if ok >= 3 else None
    except Exception:
        return None


def _same_expr(ea: str, eb: str) -> bool | None:
    a, b = _parse(ea), _parse(eb)
    if a is None or b is None:
        return None
    return _numeric_diff(a, b)


def _same_part(pa: str, pb: str) -> bool | None:
    """Compare one answer part: an expression, or an equation such as 'x = 4' / '2x+1 = 7'."""
    mods = _sympy()
    if not mods:
        return None
    sympy = mods[0]
    ea, eb = pa.count("="), pb.count("=")
    if ea > 1 or eb > 1:
        return None
    if ea == 0 and eb == 0:
        return _same_expr(pa, pb)
    if ea == 1 and eb == 1:
        la, ra = (x.strip() for x in pa.split("="))
        lb, rb = (x.strip() for x in pb.split("="))
        da, db = _parse(f"({la})-({ra})"), _parse(f"({lb})-({rb})")
        if da is None or db is None:
            return None
        try:
            ratio = sympy.simplify(da / db) if db != 0 else None
        except Exception:
            ratio = None
        if ratio is not None and not ratio.free_symbols and ratio != 0:
            return True
        # same sides compared one by one ('x = 4' vs '4 = x')
        r1 = _same_expr(la, lb), _same_expr(ra, rb)
        r2 = _same_expr(la, rb), _same_expr(ra, lb)
        if (r1[0] and r1[1]) or (r2[0] and r2[1]):
            return True
        return False if None not in (r1 + r2) or ratio is not None else None
    # one side is an equation, the other a bare value: 'x = 4' vs '4'
    eq, val = (pa, pb) if ea else (pb, pa)
    lhs, rhs = (x.strip() for x in eq.split("="))
    for side in (rhs, lhs):
        other = lhs if side is rhs else rhs
        if re.fullmatch(r"[A-Za-z](?:_[A-Za-z0-9]+)?", other):
            return _same_expr(side, val)
    return None


def equivalent(expected: str, given: str) -> bool | None:
    """True/False when both answers are clear maths, None when we cannot tell (caller asks the AI)."""
    if not _sympy():
        return None
    ex, gv = to_expression(expected), to_expression(given)
    if not ex or not gv:
        return None
    if re.search(r"[<>]", ex + gv):
        return None
    pa = _expand_pm(_split_top(ex))
    pb = _expand_pm(_split_top(gv))
    if not pa or not pb:
        return None
    if len(pa) == 1 and len(pb) == 1:
        return _same_part(pa[0], pb[0])
    if len(pa) != len(pb):
        # a different number of solutions is a real difference only when everything parsed
        return False if all(_parse(p.split("=")[-1]) is not None for p in pa + pb) else None
    left = list(pb)
    for p in pa:                                             # every expected part needs a distinct match
        hit = None
        for q in left:
            r = _same_part(p, q)
            if r:
                hit = q
                break
            if r is None:
                return None
        if hit is None:
            return False
        left.remove(hit)
    return True


# ── reading a formula aloud ──────────────────────────────────────────────────────────────────

_SPOKEN_SYMBOLS = [
    (r"\\leq?\b|≤", " is at most "), (r"\\geq?\b|≥", " is at least "), (r"\\neq?\b|≠", " is not equal to "),
    (r"\\approx|≈", " is about "), (r"\\cdot|\\times|·|×", " times "), (r"\\div|÷", " divided by "),
    (r"\\pm|±", " plus or minus "), (r"\\infty|∞", " infinity "), (r"\\pi\b|π", " pi "),
    (r"\\theta\b", " theta "), (r"\\alpha\b", " alpha "), (r"\\beta\b", " beta "), (r"\\lambda\b", " lambda "),
    (r"\\sin\b", " sine of "), (r"\\cos\b", " cosine of "), (r"\\tan\b", " tangent of "),
    (r"\\ln\b", " natural log of "), (r"\\log\b", " log of "), (r"\\lim\b", " the limit of "),
    (r"\\sum\b|∑", " the sum of "), (r"\\int\b|∫", " the integral of "), (r"\\to\b|\\rightarrow\b|→", " approaches "),
    (r"\\left|\\right|\\,|\\;|\\!|\\quad|\\displaystyle", " "),
]


def speakable(text: str | None) -> str:
    """Rough spoken form of a card with LaTeX in it. Plain text passes through unchanged."""
    t = text or ""
    if "$" not in t and "\\" not in t and "^" not in t:
        return t
    t = re.sub(r"\$+", " ", t)
    for _ in range(4):                                       # nested fractions / roots
        t = re.sub(r"\\d?t?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r" \1 over \2 ", t)
        t = re.sub(r"\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}", r" the \1 th root of \2 ", t)
        t = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r" the square root of \1 ", t)
    t = re.sub(r"\^\s*\{?\s*2\s*\}?", " squared ", t)
    t = re.sub(r"\^\s*\{?\s*3\s*\}?", " cubed ", t)
    t = re.sub(r"\^\s*\{([^{}]*)\}", r" to the power \1 ", t)
    t = re.sub(r"\^\s*(\w)", r" to the power \1 ", t)
    t = re.sub(r"_\s*\{([^{}]*)\}", r" sub \1 ", t)
    t = re.sub(r"_\s*(\w)", r" sub \1 ", t)
    for pat, word in _SPOKEN_SYMBOLS:
        t = re.sub(pat, word, t)
    t = t.replace("=", " equals ").replace("+", " plus ").replace("{", " ").replace("}", " ")
    t = re.sub(r"(?<=\w)\s*-\s*(?=\w)", " minus ", t).replace("/", " over ").replace("\\", " ")
    return re.sub(r"\s+", " ", t).strip()
