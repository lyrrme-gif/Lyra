"""Example plugin: roll dice. Try: "roll a dice", "roll two dice", "roll a d20", "roll 3d6"."""
import random
import re

_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_NOTATION = re.compile(r"\b(\d{1,2})?\s*d\s*(\d{1,4})\b", re.IGNORECASE)            # 2d6, d20
_COUNT = re.compile(r"\b(a|an|one|two|three|four|five|six|\d{1,2})\s+(?:dice|die)\b", re.IGNORECASE)


def handler(command: str):
    n, sides = 1, 6
    m = _NOTATION.search(command)
    if m:
        n, sides = int(m.group(1) or 1), int(m.group(2))
    else:
        c = _COUNT.search(command)
        if c:
            word = c.group(1).lower()
            n = _WORDS.get(word) or int(word)
    if not (1 <= n <= 20 and 2 <= sides <= 1000):
        return "I can roll 1 to 20 dice with 2 to 1000 sides."
    rolls = [random.randint(1, sides) for _ in range(n)]
    if n == 1:
        return f"You rolled {rolls[0]}."
    return f"You rolled {', '.join(map(str, rolls))}, for a total of {sum(rolls)}."


PLUGIN = {
    "name": "Dice roll",
    "description": "Rolls dice: 'roll a dice', 'roll two dice', 'roll a d20', 'roll 3d6'.",
    "patterns": [
        r"\b(?:roll|throw)\b.*\b(?:dice|die)\b",
        r"\b(?:roll|throw)\s+(?:an?\s+)?\d{0,2}\s*d\s*\d{1,4}\b",
    ],
    "handler": handler,
    "permission": "SAFE",
}
