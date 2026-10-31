"""Lyra — conversational shortcut builder.

Handles "create a shortcut", "edit shortcut <name>", "delete shortcut
<name>", and "list shortcuts" as multi-turn conversations. State lives in
a single ShortcutWizard instance shared with shortcuts/__init__.py — NOT a
second ShortcutManager, so a shortcut saved here shows up immediately for
matching without needing a restart.

Voice-friendly by design: every step accepts a short spoken phrase rather
than requiring structured input, and action steps are parsed with a small
set of patterns (see _parse_action_phrase) covering the action types the
executor already supports.
"""

import re
import difflib

import ui

log = ui.log

_YES_WORDS = ("yes", "yeah", "yep", "yup", "confirm", "go ahead", "do it", "correct", "save")
_NO_WORDS = ("no", "nope", "cancel", "stop", "don't", "discard", "nevermind")

_DONE_WORDS = ("done", "that's it", "thats it", "that's all", "thats all", "finish", "finished", "nothing else", "no more")

_START_CREATE = re.compile(
    r"^(?:create|make|add|new)\s+(?:a\s+)?shortcut(?:\s+called\s+(.+))?$", re.IGNORECASE
)
_START_EDIT = re.compile(r"^edit\s+(?:the\s+)?shortcut\s+(.+)$", re.IGNORECASE)
_START_DELETE = re.compile(r"^(?:delete|remove)\s+(?:the\s+)?shortcut\s+(.+)$", re.IGNORECASE)
_START_LIST = re.compile(r"^(?:list|show)\s+(?:my\s+)?shortcuts$|^what\s+shortcuts\s+do\s+i\s+have\??$", re.IGNORECASE)

# --- action-phrase parsing -------------------------------------------------
_ACTION_PATTERNS = [
    (re.compile(r"^open\s+(?:the\s+)?spotify(?:\s+and\s+play\s+(.+)|\s+and\s+search\s+(?:for\s+)?(.+))?$", re.IGNORECASE),
     lambda m: {"type": "open_spotify", "query": (m.group(1) or m.group(2) or "").strip()}),
    (re.compile(r"^open\s+(?:the\s+)?folder\s+(.+)$", re.IGNORECASE),
     lambda m: {"type": "open_folder", "path": m.group(1).strip()}),
    (re.compile(r"^(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})%?$", re.IGNORECASE),
     lambda m: {"type": "set_volume", "level": min(100, int(m.group(1)))}),
    (re.compile(r"^(?:increase|raise|turn up|volume)\s*(?:the\s+)?volume?\s*up$|^volume\s+up$|^turn\s+(?:the\s+)?volume\s+up$", re.IGNORECASE),
     lambda m: {"type": "volume_up", "steps": 5}),
    (re.compile(r"^(?:decrease|lower|turn down|volume)\s*(?:the\s+)?volume?\s*down$|^volume\s+down$|^turn\s+(?:the\s+)?volume\s+down$", re.IGNORECASE),
     lambda m: {"type": "volume_down", "steps": 5}),
    (re.compile(r"^mute(?:\s+(?:the\s+)?(?:volume|audio|sound))?$", re.IGNORECASE),
     lambda m: {"type": "mute"}),
    (re.compile(r"^lock(?:\s+the)?\s+screen$", re.IGNORECASE),
     lambda m: {"type": "lock_screen"}),
    (re.compile(r"^(?:take\s+a\s+)?screenshot$", re.IGNORECASE),
     lambda m: {"type": "take_screenshot"}),
    (re.compile(r"^wait\s+(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)?$", re.IGNORECASE),
     lambda m: {"type": "wait", "seconds": float(m.group(1))}),
    (re.compile(r"^open\s+(?:the\s+)?site\s+(.+)$", re.IGNORECASE),
     lambda m: {"type": "open_site", "target": m.group(1).strip()}),
    # Generic "open <app>" catch-all — must come last so more specific
    # patterns above (spotify, folder, site) get first crack.
    (re.compile(r"^open\s+(?:the\s+)?(.+)$", re.IGNORECASE),
     lambda m: {"type": "open_app", "target": m.group(1).strip()}),
]


def _parse_action_phrase(text: str) -> dict | None:
    text = text.strip()
    for pattern, build in _ACTION_PATTERNS:
        m = pattern.match(text)
        if m:
            return build(m)
    return None


def _describe_action(action: dict) -> str:
    t = action.get("type")
    if t == "open_app":
        return f"open {action.get('target')}"
    if t == "open_site":
        return f"open site {action.get('target')}"
    if t == "open_folder":
        return f"open folder {action.get('path')}"
    if t == "open_spotify":
        q = action.get("query")
        return f"open Spotify and play '{q}'" if q else "open Spotify"
    if t == "set_volume":
        return f"set volume to {action.get('level')}%"
    if t == "volume_up":
        return "turn volume up"
    if t == "volume_down":
        return "turn volume down"
    if t == "mute":
        return "mute"
    if t == "lock_screen":
        return "lock the screen"
    if t == "take_screenshot":
        return "take a screenshot"
    if t == "wait":
        return f"wait {action.get('seconds')}s"
    return t or "unknown action"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "shortcut"


def _is_yes(text: str) -> bool:
    t = text.strip().lower()
    return any(w in t for w in _YES_WORDS)


def _is_no(text: str) -> bool:
    t = text.strip().lower()
    return any(w in t for w in _NO_WORDS)


def _is_done(text: str) -> bool:
    t = text.strip().lower()
    return t in _DONE_WORDS or any(w in t for w in _DONE_WORDS)


class ShortcutWizard:
    """One conversational session at a time (voice assistants are
    single-user, single-thread-of-conversation by nature — no need for
    per-user session tracking here)."""

    def __init__(self, manager):
        self.manager = manager
        self.reset()

    def reset(self):
        self.mode = None       # None | "create" | "edit" | "confirm_delete"
        self.stage = None
        self.draft = None      # {id, name, triggers: [], actions: []}
        self.target_id = None  # for delete

    @property
    def active(self) -> bool:
        return self.mode is not None

    # -- entry points --------------------------------------------------
    def try_start(self, command: str) -> str | None:
        """Checks if `command` starts a wizard session. Returns the first
        prompt if so, None if `command` isn't a wizard-starting phrase."""
        if self.active:
            return None  # a session is already running — handle_turn owns this turn

        m = _START_LIST.match(command.strip())
        if m:
            return self._list_shortcuts()

        m = _START_CREATE.match(command.strip())
        if m:
            self.mode = "create"
            self.draft = {"id": None, "name": None, "triggers": [], "actions": []}
            prefilled_name = (m.group(1) or "").strip()
            if prefilled_name:
                self.draft["name"] = prefilled_name
                self.stage = "trigger"
                return (f"Creating '{prefilled_name}'. What should you say to trigger it? "
                        "You can give me more than one phrase, one at a time — say 'done' when you're finished.")
            self.stage = "name"
            return "Sure — what should this shortcut be called?"

        m = _START_EDIT.match(command.strip())
        if m:
            found = self._find_by_name(m.group(1))
            if not found:
                return f"I don't have a shortcut matching '{m.group(1).strip()}', sir. Say 'list shortcuts' to hear what I've got."
            full = self.manager.get_shortcut(found["id"]) or {}
            self.mode = "edit"
            self.stage = "edit_menu"
            self.draft = {
                "id": found["id"], "name": full.get("name", found["name"]),
                "triggers": list(full.get("triggers", [])),
                "actions": [dict(a) for a in full.get("actions", [])],
            }
            return self._edit_menu_prompt()

        m = _START_DELETE.match(command.strip())
        if m:
            found = self._find_by_name(m.group(1))
            if not found:
                return f"I don't have a shortcut matching '{m.group(1).strip()}', sir."
            self.mode = "confirm_delete"
            self.stage = "confirm"
            self.target_id = found["id"]
            self.draft = {"name": found["name"]}
            return f"Delete '{found['name']}'? Are you sure, sir?"

        return None

    def handle_turn(self, command: str) -> str:
        """Processes one turn of an active session. Only call this when
        `self.active` is True."""
        command = command.strip()

        if self.mode == "confirm_delete":
            return self._turn_confirm_delete(command)
        if self.mode == "create":
            return self._turn_create(command)
        if self.mode == "edit":
            return self._turn_edit(command)

        self.reset()
        return "Something went wrong with the shortcut wizard, sir — let's start over."

    # -- shared bits ------------------------------------------------------
    def _find_by_name(self, text: str) -> dict | None:
        text = text.strip().lower()
        best, best_score = None, 0.5  # loose cutoff — spoken names vary
        for s in self.manager.list_shortcuts():
            for candidate in (s["name"], s["id"].replace("_", " ")):
                score = difflib.SequenceMatcher(None, text, candidate.lower()).ratio()
                if score > best_score:
                    best_score, best = score, s
        return best

    def _list_shortcuts(self) -> str:
        shortcuts = self.manager.list_shortcuts()
        if not shortcuts:
            return "You don't have any shortcuts yet, sir. Say 'create a shortcut' to make one."
        lines = [f"{s['name']} — {s['action_count']} action(s), say: \"{s['triggers'][0]}\"" if s["triggers"]
                 else f"{s['name']} — {s['action_count']} action(s)" for s in shortcuts]
        return "Your shortcuts: " + "; ".join(lines)

    # -- delete -------------------------------------------------------
    def _turn_confirm_delete(self, command: str) -> str:
        if _is_yes(command):
            if self.draft is None:
                self.reset()
                return "Something went wrong with the shortcut wizard, sir — let's start over."
            name = self.draft["name"]
            self.manager.remove_shortcut(self.target_id)
            self.reset()
            return f"Deleted '{name}', sir."
        if _is_no(command):
            self.reset()
            return "Cancelled — kept it as is."
        return "Say yes to delete it, or no to cancel."

    # -- create ---------------------------------------------------------
    def _turn_create(self, command: str) -> str:
        if command.strip().lower() in ("cancel", "never mind", "nevermind", "forget it"):
            self.reset()
            return "Okay, cancelled — nothing saved."

        # Keep the draft narrowed for every create stage.  The wizard can be
        # reset externally while a turn is being processed.
        if self.draft is None:
            self.reset()
            return "Something went wrong with the shortcut wizard, sir — let's start over."

        if self.stage == "name":
            self.draft["name"] = command
            self.stage = "trigger"
            return ("Got it. What should you say to trigger it? Give me one phrase at a time — "
                    "say 'done' once you've given them all.")

        if self.stage == "trigger":
            if _is_done(command):
                if not self.draft["triggers"]:
                    return "You need at least one trigger phrase, sir — what should I listen for?"
                self.stage = "actions"
                return ("Now, what should it do? Tell me the steps one at a time — things like "
                        "'open chrome', 'open spotify', 'set volume to 30', or 'wait 2 seconds'. "
                        "Say 'done' when the sequence is complete.")
            self.draft["triggers"].append(command)
            return f"Added. Any other way to trigger it, or say 'done'?"

        if self.stage == "actions":
            if _is_done(command):
                if not self.draft["actions"]:
                    return "It needs at least one action, sir — what should it do?"
                self.stage = "confirm"
                return self._confirm_prompt()
            action = _parse_action_phrase(command)
            if not action:
                return ("I didn't catch an action in that, sir. Try things like 'open chrome', "
                        "'set volume to 30', 'mute', or 'wait 2 seconds' — or say 'done'.")
            self.draft["actions"].append(action)
            return f"Added: {_describe_action(action)}. Next action, or say 'done'?"

        if self.stage == "confirm":
            if _is_yes(command):
                shortcut_id = _slugify(self.draft["name"])
                self.manager.add_shortcut(shortcut_id, self.draft["name"], self.draft["triggers"], self.draft["actions"])
                name = self.draft["name"]
                self.reset()
                return f"Saved '{name}', sir. You can trigger it any time now."
            if _is_no(command):
                self.reset()
                return "Discarded — nothing saved."
            return "Should I save this shortcut? Say yes or no."

        self.reset()
        return "Lost track of that — let's start over. Say 'create a shortcut' when ready."

    def _confirm_prompt(self) -> str:
        draft = self.draft
        if draft is None:
            return "Something went wrong with the shortcut wizard, sir — let's start over."
        triggers = ", ".join(f'"{t}"' for t in draft["triggers"])
        actions = "; ".join(_describe_action(a) for a in draft["actions"])
        return (f"'{draft['name']}', triggered by {triggers}. Actions: {actions}. "
                f"Save this, sir?")

    # -- edit -------------------------------------------------------------
    def _edit_menu_prompt(self) -> str:
        if self.draft is None:
            return "Something went wrong with the shortcut wizard, sir — let's start over."
        draft = self.draft
        actions = "; ".join(f"{i+1}) {_describe_action(a)}" for i, a in enumerate(draft["actions"]))
        return (f"Editing '{draft['name']}'. Current actions: {actions or '(none)'}. "
                f"Say 'add action ...', 'remove action <number>', 'add trigger ...', "
                f"'rename to ...', 'save', or 'cancel'.")

    def _turn_edit(self, command: str) -> str:
        low = command.strip().lower()

        if self.draft is None:
            self.reset()
            return "Something went wrong with the shortcut wizard, sir — let's start over."

        if low in ("cancel", "never mind", "nevermind", "forget it"):
            self.reset()
            return "Cancelled — no changes saved."

        if low in ("save", "done", "that's it", "thats it", "finish", "finished"):
            self.manager.add_shortcut(self.draft["id"], self.draft["name"], self.draft["triggers"], self.draft["actions"])
            name = self.draft["name"]
            self.reset()
            return f"Saved changes to '{name}', sir."

        m = re.match(r"^add\s+action\s+(.+)$", command, re.IGNORECASE)
        if m:
            action = _parse_action_phrase(m.group(1))
            if not action:
                return "I didn't catch an action in that — try something like 'add action open chrome'."
            self.draft["actions"].append(action)
            return f"Added: {_describe_action(action)}. {self._edit_menu_prompt()}"

        m = re.match(r"^remove\s+action\s+(\d+)$", command, re.IGNORECASE)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(self.draft["actions"]):
                removed = self.draft["actions"].pop(idx)
                return f"Removed: {_describe_action(removed)}. {self._edit_menu_prompt()}"
            return f"There's no action {m.group(1)} — {self._edit_menu_prompt()}"

        m = re.match(r"^add\s+trigger\s+(.+)$", command, re.IGNORECASE)
        if m:
            self.draft["triggers"].append(m.group(1).strip())
            return f"Added trigger phrase. {self._edit_menu_prompt()}"

        m = re.match(r"^rename\s+(?:to\s+)?(.+)$", command, re.IGNORECASE)
        if m:
            self.draft["name"] = m.group(1).strip()
            return f"Renamed to '{self.draft['name']}'. {self._edit_menu_prompt()}"

        return ("I didn't catch that. " + self._edit_menu_prompt())
