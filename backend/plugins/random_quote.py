"""Example plugin: a random quote. Try: "give me a quote", "random quote", "inspire me"."""
import random

# Short classic quotes (old enough to be public domain). "Attributed" where the source is traditional.
_QUOTES = [
    ("The journey of a thousand miles begins with a single step.", "Lao Tzu"),
    ("It does not matter how slowly you go, as long as you do not stop.", "attributed to Confucius"),
    ("Well begun is half done.", "attributed to Aristotle"),
    ("Fall seven times, stand up eight.", "Japanese proverb"),
    ("The best way out is always through.", "Robert Frost"),
    ("Nothing will come of nothing.", "William Shakespeare"),
    ("Do what you can, with what you have, where you are.", "Theodore Roosevelt"),
    ("The only thing we have to fear is fear itself.", "Franklin D. Roosevelt"),
    ("Knowledge is power.", "Francis Bacon"),
    ("No man ever steps in the same river twice.", "attributed to Heraclitus"),
]
_last = -1


def handler(command: str):
    global _last
    i = random.randrange(len(_QUOTES))
    if i == _last:
        i = (i + 1) % len(_QUOTES)
    _last = i
    text, who = _QUOTES[i]
    return f"\u201c{text}\u201d \u2014 {who}"


PLUGIN = {
    "name": "Random quote",
    "description": "Gives a short classic quote: 'give me a quote', 'random quote', 'inspire me'.",
    "patterns": [
        r"\b(?:random|inspirational|motivational)\s+quote\b",
        r"\b(?:give|tell|read|show)\s+me\s+(?:a\s+)?quote\b",
        r"^\s*inspire\s+me\s*[.!]?\s*$",
    ],
    "handler": handler,
    "permission": "SAFE",
}
