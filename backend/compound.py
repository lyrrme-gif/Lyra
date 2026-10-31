"""Rule-based splitting of a compound command into ordered steps. No LLM, no I/O.

    "open discord and open anogashi's chat"  ->  ["open discord", "open anogashi's chat"]
    "open spotify, play lofi then mute"      ->  ["open spotify", "play lofi", "mute"]
    "play salt and pepper"                   ->  ["play salt and pepper"]

split_compound() cuts at:
  * always:  ";"  ", then"  "and then"  "then"  "after that"  "afterwards"
  * only when what follows starts with an action verb:  ","  "and"

It never cuts inside "double quotes", and never inside the text of a message
("message Sara saying open the door and close it" stays one step). Chain keeps
track of a running compound command: which step is next, which app an earlier step
named, and what to say when a step fails.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Words that start a command. Used only to decide whether "and"/"," begins a new step.
ACTION_VERBS = (
    "open", "close", "launch", "start", "quit", "play", "pause", "resume", "stop", "skip",
    "mute", "unmute", "message", "dm", "text", "send", "tell", "call", "chat", "search",
    "google", "find", "look", "set", "turn", "switch", "go", "show", "take", "lock",
    "minimize", "maximize", "restart", "click", "press", "type", "write", "remind", "add",
    "create", "delete", "remove", "save", "run", "enable", "disable", "increase", "decrease",
    "raise", "lower", "check", "read", "scroll", "copy", "paste", "translate", "record",
    "screenshot", "ask",
)
_VERB = "(?:" + "|".join(ACTION_VERBS) + ")"
_FILLER = r"(?:(?:also|please|just|now)\s+)*"

_HARD = re.compile(
    r"\s*;\s*|\s*,?\s*\b(?:and\s+then|and\s+after\s+that|then|after\s+that|afterwards?)\b\s*,?\s*",
    re.I,
)
_SOFT = re.compile(r"(?:\s*,\s*(?:and\s+)?|\s+and\s+)(?=" + _FILLER + _VERB + r"\b)", re.I)

# Steps whose remaining words are free text: "and"/"," inside them are not step breaks.
_FREE_TEXT = re.compile(
    r"^(?:(?:hey\s+)?lyra[,\s]+)?(?:please\s+)?"
    r"(?:(?:send|message|dm|text|tell|say|type|write|remind|translate|ask|dictate)\b"
    r"|(?:take|make|add|save)\s+(?:a\s+)?note\b|note\b)",
    re.I,
)
# Where the text of a message starts: "send Sara: hi", "message Sara saying hi", "tell Sara that hi".
_PAYLOAD_MARK = re.compile(
    r":\s*|\s+(?:saying|says|that\s+says|to\s+say|and\s+say|telling\s+\w+|that)\s+", re.I
)
_CHAT_APP_END = re.compile(r"\b(?:on|in|via|using|through)\s+(?:discord|whatsapp|telegram)\s*$", re.I)
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”|«[^»]*»')
_MASK = "\x00"


def _masked(segment: str) -> str:
    """`segment` with quoted text and message text blanked out, so no separator matches there."""
    chars = list(segment)

    def blank(a: int, b: int) -> None:
        for i in range(a, b):
            chars[i] = _MASK

    for m in _QUOTED.finditer(segment):
        blank(m.start(), m.end())
    if _FREE_TEXT.match(segment):
        m = _PAYLOAD_MARK.search("".join(chars))
        if m and not (m.end() < len(segment) and segment[m.end()] in '"“«'):
            blank(m.end(), len(segment))          # unquoted message text runs to the end
    return "".join(chars)


def _next_cut(segment: str):
    """(start, end) of the first split point in `segment`, or None."""
    masked = _masked(segment)
    free_text = bool(_FREE_TEXT.match(segment))
    best = None
    h = _HARD.search(masked)
    if h:
        best = (h.start(), h.end())
    for s in _SOFT.finditer(masked):
        if best and s.start() >= best[0]:
            break
        before = segment[:s.start()]
        if not before.strip():
            continue
        if free_text and not _CHAT_APP_END.search(before):
            continue                               # "message Sara ... and ..." is message text
        best = (s.start(), s.end())
        break
    return best


def _tidy(step: str) -> str:
    step = step.strip(" \t,;.")
    step = re.sub(r"^(?:(?:and|then|also|just|now)\s+)+", "", step, flags=re.I)
    return re.sub(r"\s+(?:and|then)$", "", step, flags=re.I).strip(" \t,;.")


def split_compound(text: str) -> list[str]:
    """Split `text` into ordered steps. A command that isn't compound comes back as [text]."""
    s = re.sub(r"\s+", " ", text or "").strip()
    if not s:
        return []
    steps: list[str] = []
    rest = s
    while True:
        cut = _next_cut(rest)
        if cut is None:
            steps.append(rest)
            break
        steps.append(rest[:cut[0]])
        rest = rest[cut[1]:]
    steps = [t for t in (_tidy(x) for x in steps) if t]
    return steps or [s]


# --------------------------------------------------------------------------------------------
# Reusing the app of an earlier step
# --------------------------------------------------------------------------------------------
CHAT_APPS = ("discord", "whatsapp", "telegram")
MUSIC_APPS = ("spotify", "youtube")

_ON_APP = re.compile(r"\b(?:on|in|via|using|through|with)\s+(?:the\s+)?(?P<app>[a-z][\w.+-]*(?:\s[a-z][\w.+-]*)?)\s*$", re.I)
_OPEN_APP = re.compile(
    r"^(?:please\s+)?(?:open|launch|start|switch\s+to|go\s+to)\s+(?:the\s+)?(?P<app>[a-z0-9][\w.+-]*(?:\s[a-z0-9][\w.+-]*){0,2}?)(?:\s+app)?$",
    re.I,
)
_NOT_AN_APP = re.compile(r"\b(?:chat|dm|dms|conversation|messages?)\b|['’]s\b", re.I)
_CHAT_STEP = re.compile(
    r"^(?:please\s+)?(?:(?:open|show|go\s+to|switch\s+to)\s+(?:the\s+|my\s+)?.+?\s+(?:chat|dm|dms|conversation|messages?)"
    r"|(?:open|show|go\s+to)\s+(?:my\s+|the\s+)?(?:chat|dm|conversation)\s+(?:with|from)\s+.+"
    r"|(?:message|dm|text|chat\s+with)\s+\w+(?:\s\w+)?)$",
    re.I,
)
_HAS_APP = re.compile(r"\b(?:on|in|via|using|through)\s+(?:discord|whatsapp|telegram|spotify|youtube)\b", re.I)
_PLAY_STEP = re.compile(r"^(?:please\s+)?play\s+\S.*$", re.I)
_PLAY_WITH_APP = re.compile(r"\b(?:on|in|using|with|via)\s+\S+\s*$", re.I)


def app_of(step: str) -> str | None:
    """The app a step names: "open discord" / "open Sara's chat on discord" -> "discord"."""
    s = (step or "").strip().rstrip(".!? ")
    m = _ON_APP.search(s)
    if m:
        return m.group("app").lower()
    m = _OPEN_APP.match(s)
    if m and not _NOT_AN_APP.search(m.group("app")):
        return m.group("app").lower()
    return None


def inherit_app(step: str, app: str | None) -> str:
    """Give `step` the app of an earlier step when it names none and one is needed.

        "open anogashi's chat"  + discord  ->  "open anogashi's chat on discord"
        "play lofi"             + spotify  ->  "play lofi on spotify"
    Anything else comes back unchanged."""
    if not app:
        return step
    s = step.strip().rstrip(".!? ")
    if app in CHAT_APPS and _CHAT_STEP.match(s) and not _HAS_APP.search(s):
        return f"{s} on {app}"
    if app in MUSIC_APPS and _PLAY_STEP.match(s) and not _PLAY_WITH_APP.search(s):
        return f"{s} on {app}"
    return step


# --------------------------------------------------------------------------------------------
# Running a compound command
# --------------------------------------------------------------------------------------------
_FAIL = re.compile(
    r"\b(?:couldn['’]?t|could\s+not|can['’]?t|cannot|unable|failed|failure|error|sorry|didn['’]?t|"
    r"don['’]?t\s+know|do\s+not\s+know|lost\s+focus|i\s+stopped|no\s+(?:such|match))\b|\bnot\s+found\b",
    re.I,
)


def looks_failed(reply: str) -> bool:
    """Handlers return plain text, not a status. A reply that apologises, says it couldn't, or asks
    a question (so the chain can't go on without an answer) counts as "the step did not finish"."""
    r = (reply or "").strip()
    if not r:
        return False
    return r.endswith("?") or bool(_FAIL.search(r[:160]))


@dataclass
class Chain:
    """Progress through the steps of one compound command."""
    steps: list[str]
    pending: list[str] = field(default_factory=list)   # steps still to run (the caller may alias this list)
    ran: list[str] = field(default_factory=list)       # steps started so far, as they were run
    app: str | None = None                             # app named by an earlier step

    def __post_init__(self) -> None:
        self.pending = list(self.steps)

    @property
    def total(self) -> int:
        return len(self.steps)

    def next_step(self) -> str:
        """Start the next step: the text to run, with an earlier step's app filled in."""
        if self.ran:
            self.app = app_of(self.ran[-1]) or self.app
        step = inherit_app(self.pending.pop(0), self.app)
        self.ran.append(step)
        return step

    def failure_message(self, reply: str = "", include_reply: bool = True) -> str:
        """Which step stopped the command, which ones finished, which never ran."""
        n = len(self.ran)
        parts = [f"Stopped at step {n} of {self.total} ({self.ran[-1]})."]
        if include_reply and reply:
            parts.append(reply.strip())
        done = self.ran[:-1]
        parts.append(("Finished: " + "; ".join(done) + ".") if done else "Nothing finished before it.")
        if self.pending:
            parts.append("Not run: " + "; ".join(self.pending) + ".")
        return " ".join(parts)
