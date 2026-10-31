"""Lyra — cheap local answers (date/time/math) that skip the LLM entirely.

brain.py calls try_knowledge_shortcut() *before* any Groq API round-trip.
If this function returns a non-empty string, that string is spoken and the
LLM is never contacted — saving latency and tokens for the most common
trivial questions.

Intentionally NOT handled here (they need real knowledge / web search):
  - "define X" / "what does X mean"
  - weather, news, sports scores, etc.
Those fall through to ask_brain(), which may trigger a web search.
"""

import re
import ast          # Used for safe arithmetic evaluation (no eval())
import operator as _op
from datetime import datetime

# ---------------------------------------------------------------------------
# Compiled patterns for the three local shortcuts
# ---------------------------------------------------------------------------

# "what's the date", "what day is it", "what's the day", …
DATE_PATTERN = re.compile(r"\b(what(?:'s)?\s+(?:the\s+)?(?:date|day))\b")

# "what's the time", "what time is it", …
TIME_PATTERN = re.compile(r"\b(what(?:'s)?\s+(?:the\s+)?(?:time|hour))\b")

# Captures simple spoken arithmetic such as:
#   "what's 12 plus 7"
#   "3 times 4 equals"
#   "100 divided by 5"
MATH_PATTERN = re.compile(
    r"^(?:what(?:'s)?\s+)?([\d+\-*/().\s]+?)\s*(?:equals|is|=|plus|minus|times|divided\s+by)\s*([\d+\-*/().\s]+)?$"
)

# ---------------------------------------------------------------------------
# Safe arithmetic evaluator — never uses the built-in eval()
# Only the operators listed below are permitted; anything else raises.
# ---------------------------------------------------------------------------
_SAFE_BIN_OPS = {
    ast.Add: _op.add,   # +
    ast.Sub: _op.sub,   # -
    ast.Mult: _op.mul,  # *
    ast.Div: _op.truediv,  # /
    ast.Pow: _op.pow,   # **
    ast.Mod: _op.mod,   # %
}
_SAFE_UNARY_OPS = {
    ast.UAdd: _op.pos,  # unary +
    ast.USub: _op.neg,  # unary -
}


def _safe_eval_expr(expr: str):
    """Evaluate a purely arithmetic expression without using eval().

    Only numbers, +, -, *, /, %, **, and parentheses are allowed.
    Names, function calls, attribute access, etc. raise ValueError
    and are treated by the caller as "not something we can compute".
    """
    # Parse the expression into an AST (mode="eval" expects a single expression)
    node = ast.parse(expr, mode="eval").body

    def _walk(n):
        # Literal number
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        # Binary operator (+, -, *, /, …)
        if isinstance(n, ast.BinOp) and type(n.op) in _SAFE_BIN_OPS:
            return _SAFE_BIN_OPS[type(n.op)](_walk(n.left), _walk(n.right))
        # Unary operator (+x, -x)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _SAFE_UNARY_OPS:
            return _SAFE_UNARY_OPS[type(n.op)](_walk(n.operand))
        # Anything else is rejected for safety
        raise ValueError(f"unsupported expression node: {n!r}")

    return _walk(node)


def try_knowledge_shortcut(prompt: str) -> str:
    """Handle trivial questions locally.

    Returns a ready-to-speak answer string, or "" if the prompt is not
    something we can answer without the LLM / web search.
    """
    # Normalize: lower-case, strip punctuation that speakers often add
    p = prompt.strip().lower().rstrip("?.!")

    # ----- Local identity / location -----
    if re.search(r"\b(where are you|where do you live|where is lyra)\b", p):
        return "I'm running on this Windows PC."
    if re.search(r"\b(who are you|what are you)\b", p):
        return "I'm Lyra, your Windows assistant."
    if re.search(r"\b(battery|battery level|how much battery)\b", p):
        try:
            import psutil
            b=psutil.sensors_battery()
            return f"Battery is at {b.percent:.0f} percent." if b else "This PC doesn't report a battery level."
        except Exception:
            return "I couldn't read the battery level."
    if re.search(r"\b(volume|loudness)\b", p) and re.search(r"\b(what|how|level)\b", p):
        try:
            from actions.system import get_volume_level
            return f"Volume is at {get_volume_level():.0f} percent."
        except Exception:
            pass

    # ----- Date -----
    if DATE_PATTERN.search(p):
        # %A = full weekday, %B = full month, %d = day, %Y = year
        return datetime.now().strftime("Today is %A, %B %d %Y.")

    # ----- Time -----
    if TIME_PATTERN.search(p):
        # %I = 12-hour clock, %M = minutes, %p = AM/PM
        return datetime.now().strftime("It's about %I:%M %p.")

    # ----- Math -----
    m = MATH_PATTERN.match(p)
    if m:
        # Reassemble the full spoken expression from the original match
        expr = m.group(0)
        # Replace spoken operator words with their symbolic counterparts
        expr = re.sub(
            r"\b(equals|is|=|plus|minus|times|divided\s+by)\b",
            lambda x: {
                "plus": "+", "minus": "-", "times": "*", "divided by": "/",
                "equals": "", "is": "", "=": "",
            }.get(x.group(0), x.group(0)),
            expr,
        )
        # Strip everything that is not a digit or a math operator / paren
        expr = re.sub(r"[^0-9+\-*/().]", "", expr)
        # Final safety check: the cleaned expression must consist only of
        # digits and the allowed operators
        if expr and re.fullmatch(r"[0-9+\-*/().\s]+", expr):
            try:
                result = _safe_eval_expr(expr)
                return f"That's {result}."
            except Exception:
                # Any parse / evaluation error → fall through to the LLM
                pass

    # Nothing matched → tell the caller to continue with the full brain path
    return ""
