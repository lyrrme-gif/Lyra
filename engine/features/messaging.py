"""Lyra — send messages through desktop chat apps.

LLM decides WHAT (contact, text, app); this module decides HOW, in code:
  open chat -> UIA / paste / vision -> verify recipient -> draft -> CONFIRM -> send -> verify.

Safety rules that live in code, not in a prompt:
  * `_press_send` needs a one-time token that only `_confirm_send` can mint
    (read back recipient + text, wait for a yes). The LLM/agent has no way to
    skip it, and there is no "confirm=False" parameter.
  * The recipient is verified from the chat header before anything is drafted.
  * Text pasted into an unverified search box is cleared, never sent.
  * Screen text is only ever read as data.
  * Logs contain recipient + length, never the message body.
"""
from __future__ import annotations

import difflib
import json
import re
import time
from pathlib import Path
from typing import Optional

from engine.core import paths
from engine.bridge import ui

log = ui.log
_CONTACTS = paths.data_path("contacts.json")
_PROFILES = paths.data_path("app_profiles.json")

KNOWN_APPS = ("whatsapp", "discord", "telegram", "messenger", "instagram", "signal", "slack", "teams")
_APP_PAT = "|".join(KNOWN_APPS)
MAX_LEN = 1500
_TOKEN_TTL = 45.0
_SEND_TIMES: list[float] = []     # loop guard: max 5 sends / minute


# ---------------- contacts ----------------
def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\u0600-\u06ff ]+", "", (s or "").lower()).strip()


def resolve_contact(who: str, contacts: Optional[dict] = None):
    """-> (status, entry|None, candidates). status: 'ok' | 'ambiguous' | 'unknown'."""
    contacts = contacts if contacts is not None else _load_json(_CONTACTS, {})
    q = _norm(re.sub(r"^(my|the)\s+", "", who or ""))
    if not q:
        return "unknown", None, []
    index: dict[str, list[str]] = {}
    for key, entry in contacts.items():
        if key.startswith("_") or not isinstance(entry, dict):
            continue
        for n in {_norm(key), *(_norm(x) for x in entry.get("names", [])), _norm(entry.get("display", ""))}:
            if n:
                index.setdefault(n, []).append(key)
    if q in index:
        keys = sorted(set(index[q]))
        return ("ok", contacts[keys[0]], keys) if len(keys) == 1 else ("ambiguous", None, keys)
    # Short names must match exactly: a mis-heard 3-letter name is too easy to confuse.
    close = [] if len(q) <= 3 else difflib.get_close_matches(q, list(index), n=3, cutoff=0.84)
    keys = sorted({k for c in close for k in index[c]})
    if len(keys) == 1:
        return "ok", contacts[keys[0]], keys
    if len(keys) > 1:
        return "ambiguous", None, keys
    return "unknown", None, []


# ---------------- parsing ----------------
_SEP = r"(?:\s*[:,\-]\s*|(?:\s+|(?<=\s))(?:saying|that\s+says|to\s+say|and\s+say|telling\s+(?:him|her|them)|that)\s+)"
_SEND_PATTERNS = [
    # send Sara a message on whatsapp saying hi
    re.compile(
        rf"^(?:please\s+)?(?:send|text|message|dm)\s+(?P<who>[^:,]+?)\s+(?:a\s+)?(?:text\s+|message\s+)"
        rf"(?:(?:on|via|in|using|through)\s+(?P<app>{_APP_PAT})\s*)?{_SEP}(?P<msg>.+)$", re.I | re.S),
    # send a message to Sara on discord: hi   /   text Sara on telegram that hi
    re.compile(
        rf"^(?:please\s+)?(?:send|text|message|dm)\s+(?:a\s+)?(?:(?:text\s+)?message\s+)?(?:to\s+)?"
        rf"(?P<who>[^:,]+?)(?:\s+(?:on|via|in|using|through)\s+(?P<app>{_APP_PAT}))?{_SEP}(?P<msg>.+)$",
        re.I | re.S),
]
_TELL = re.compile(r"^(?:please\s+)?tell\s+(?P<who>\w+(?:\s\w+)?)\s+(?:that\s+)?(?P<msg>.+)$", re.I | re.S)
_BARE_APP = re.compile(rf"^(?:on\s+|use\s+|in\s+)?(?P<app>{_APP_PAT})[\s.!]*$", re.I)


_NOT_A_PERSON = re.compile(
    r"^(?:me|myself|us|you|it|this|that|them|everyone|everybody|an?|the|my\s+(?:self|phone|computer)|"
    r"(?:an?\s+)?(?:e-?mail|mail|reminder|note|notification|request|file|link|picture|photo|screenshot)\b.*|"
    r"me\s+an?\b.*)$", re.I)


def parse_send_command(command: str, contacts: Optional[dict] = None) -> Optional[dict]:
    """-> {'who','text','app'|None} or None. Pure; no side effects."""
    c = (command or "").strip()
    c = re.sub(r"^(?:hey\s+)?lyra[,\s]+", "", c, flags=re.I)
    # Natural voice phrasing: "can you send Anogashi on Discord? hello?"
    m = re.match(r"^(?:can you |could you |please )?(?:send|message|text|dm)\s+(?P<who>[^?]+?)\s+(?:on|via|in|using|through)\s+(?P<app>discord|whatsapp|telegram)\s*[:,-]?\s*(?P<msg>.+)$", c, re.I|re.S)
    if m:
        who=m.group("who").strip(); msg=m.group("msg").strip(' ?!.,"“”')
        if who and msg and len(who.split())<=3:
            return {"who":who,"text":msg,"app":m.group("app").lower()}
    for pat in _SEND_PATTERNS:
        m = pat.match(c)
        if m:
            who, msg = m.group("who").strip(), m.group("msg").strip()
            who = re.sub(r"\s+a\s+(?:text\s+)?message$", "", who, flags=re.I).strip()
            if _NOT_A_PERSON.match(who):
                return None          # "send me a reminder", "send an email to ..." are not chat messages
            if who and msg and len(who.split()) <= 3:
                return {"who": who, "text": msg.strip(' "“”'), "app": (m.group("app") or "").lower() or None}
    m = _TELL.match(c)
    if m:   # "tell X ..." is too generic: only for known contacts ("tell me ..." never qualifies)
        rest = re.sub(r"^(?:please\s+)?tell\s+", "", c, flags=re.I)
        words = rest.split()
        for n in (1, 2):
            if len(words) <= n:
                break
            who = " ".join(words[:n])
            if resolve_contact(who, contacts)[0] == "ok":
                msg = re.sub(r"^that\s+", "", " ".join(words[n:]), flags=re.I)
                return {"who": who, "text": msg.strip(' "“”'), "app": None}
    return None


# ---------------- confirm gate ----------------
class _SendToken:
    """Minted only by _confirm_send; single use, short-lived."""
    __slots__ = ("born", "used", "text")

    def __init__(self, text: str):
        self.born, self.used, self.text = time.time(), False, text

    def consume(self, text: str) -> bool:
        ok = (not self.used) and (time.time() - self.born <= _TOKEN_TTL) and text == self.text
        self.used = True
        return ok


_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|send it|send|go ahead|confirm|do it|ok|okay)\b", re.I)
_NO = re.compile(r"\b(no|nope|nah|cancel|stop|don'?t|never ?mind|abort)\b", re.I)
_EDIT = re.compile(r"^(?:change|make|edit|replace|update)\s+(?:it|the message|that)?\s*(?:to|so it says|into)\s+(?P<t>.+)$"
                   r"|^(?:say|write|send)\s+(?P<t2>.+)$", re.I | re.S)


def parse_confirmation(reply: str):
    """-> ('no'|'edit'|'yes'|'unclear', new_text|None). 'no' beats 'yes'."""
    r = (reply or "").strip()
    if not r:
        return "unclear", None
    if _NO.search(r):
        return "no", None
    m = _EDIT.match(r)
    if m:
        return "edit", (m.group("t") or m.group("t2")).strip(' "“”')
    if _YES.search(r):
        return "yes", None
    return "unclear", None


def _listen(prompt: str, engine_context) -> Optional[str]:
    ui.print_chat("lyrr", prompt)
    try:
        from engine.voice import voice
        voice.speak(prompt)
    except Exception:
        pass
    if ui.INPUT_MODE == "text":
        try:
            return input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
    if not engine_context or "recognizer" not in engine_context:
        return None
    from engine.voice import voice
    status, heard = voice.listen(engine_context["recognizer"], engine_context["source"], timeout_seconds=8)
    return heard if status == "ok" else ""


def _confirm_send(display: str, app: str, text: str, engine_context, apply_edit):
    """Read back and wait. Returns (_SendToken|None, final_text). apply_edit(new) re-drafts."""
    for _ in range(3):
        prompt = f"To {display} on {app.title()}: “{text}”. Send it?"
        reply = _listen(prompt, engine_context)
        if reply is None:
            return None, text
        kind, new = parse_confirmation(reply)
        if kind == "yes":
            return _SendToken(text), text
        if kind == "no":
            return None, text
        if kind == "edit" and new:
            if len(new) > MAX_LEN or not apply_edit(new):
                return None, text
            text = new
            continue
        # unclear -> one more chance
    return None, text


def _press_send(token: Optional[_SendToken], text: str) -> bool:
    if token is None or not token.consume(text):
        log.warning("send blocked: no valid confirmation token")
        return False
    from engine.computer import keyboard
    keyboard.press("enter")
    return True


# ---------------- app helpers ----------------
# Built-in profiles, so chats open without any setup. app_profiles.json (data folder) is optional:
# whatever it sets for an app overrides these keys.
_DEFAULT_PROFILES = {
    "discord": {"window": ["discord"], "launch": "discord", "search_hints": ["search", "find or start a conversation"],
                "input_hints": ["message"], "search_hotkey": ["ctrl", "k"], "hotkey_trusted": True},
    "whatsapp": {"window": ["whatsapp"], "launch": "whatsapp", "search_hints": ["search"],
                 "input_hints": ["type a message", "message"], "search_hotkey": ["ctrl", "f"], "hotkey_trusted": False},
    "telegram": {"window": ["telegram"], "launch": "telegram", "search_hints": ["search"],
                 "input_hints": ["write a message", "message"], "search_hotkey": ["ctrl", "f"], "hotkey_trusted": False},
}


def _profile(app: str) -> Optional[dict]:
    mine = _load_json(_PROFILES, {}).get(app)
    base = _DEFAULT_PROFILES.get(app)
    if base is None and not isinstance(mine, dict):
        return mine or None
    return {**(base or {}), **(mine if isinstance(mine, dict) else {})}


def _learn(app: str, key: str, value: str) -> None:
    data = _load_json(_PROFILES, {})
    if app in data:
        data[app].setdefault("learned", {})[key] = value
        try:
            _PROFILES.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


def _foreground_title() -> str:
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        return (w.title or "") if w else ""
    except Exception:
        return ""


def _ensure_app_focused(app: str, prof: dict) -> bool:
    from engine.actions import apps
    for t in prof.get("window", [app]):
        if apps.focus_window(t):
            return True
    try:
        apps.open_local_app(prof.get("launch", app))
    except Exception as e:
        log.warning("could not launch %s: %s", app, e)
        return False
    for _ in range(16):
        time.sleep(0.5)
        for t in prof.get("window", [app]):
            if apps.focus_window(t):
                time.sleep(0.6)
                return True
    return False


def _in_app(prof: dict) -> bool:
    t = _foreground_title().lower()
    return any(w.lower() in t for w in prof.get("window", []))


def _frame():
    """Fresh frame of the focused window: (image, left, top, w, h) or None."""
    from engine.actions import apps
    img, l, t, w, h = apps._acquire_frame(None, fresh=True)
    return None if img is None else (img, l, t, w, h)


def _vision_locate(desc: str):
    f = _frame()
    if not f:
        return None
    from engine.computer import vision
    hit = vision.locate(desc, *f)
    return hit if hit.get("ok") and hit.get("confidence") != "low" else None


def _vision_yes(question: str) -> bool:
    f = _frame()
    if not f:
        return False
    from engine.actions.apps import screen_contains
    return screen_contains(f[0], question)


# ---------------- steps ----------------
def _focus_field(kind: str, prof: dict, win) -> tuple[Optional[object], str]:
    """Focus the 'input' or 'search' field. -> (uia_ctrl|None, method) ; method '' = failed."""
    from engine.computer import uia, mouse
    hints = prof.get(f"{kind}_hints", [])
    if win is not None:
        ctrl = uia.find_input(win, hints, exclude=(["search"] if kind == "input" else []))
        if ctrl is not None and uia.focus(ctrl):
            return ctrl, "uia"
    if kind == "search" and prof.get("hotkey_trusted") and prof.get("search_hotkey"):
        from engine.computer import keyboard
        keyboard.press(*prof["search_hotkey"])
        time.sleep(0.35)
        return None, "hotkey"
    desc = ("the message text input box at the bottom of the open chat" if kind == "input"
            else "the search box that filters chats or contacts")
    hit = _vision_locate(desc)
    if hit and mouse.click(hit["x"], hit["y"]):
        time.sleep(0.2)
        return None, "vision"
    return None, ""


def _clear_field() -> None:
    from engine.computer import keyboard
    keyboard.press("ctrl", "a")
    time.sleep(0.05)
    keyboard.press("delete")


def _finish_quick_switcher() -> None:
    """A click or Enter sometimes leaves Discord's quick switcher open on the highlighted result.
    If the screen still shows it, press Enter once more to jump to that chat."""
    from engine.computer import keyboard
    if _vision_yes('The Discord quick switcher popup ("Select a result and press ENTER to jump to it") is still open.'):
        keyboard.press("enter")
        time.sleep(1.0)


def _open_chat(chat_name: str, display: str, prof: dict, win) -> tuple[bool, str]:
    from engine.computer import keyboard, uia
    # Discord's normal search box searches the current server. Ctrl+K opens
    # its global DM/quick switcher, which is the right place to find a person.
    if "discord" in [str(x).lower() for x in prof.get("window", [])]:
        keyboard.press("ctrl", "k")
        time.sleep(0.35)
        if not keyboard.enter_text(chat_name):
            return False, "I couldn't type the contact name into Discord's quick switcher."
        time.sleep(0.8)
        # Prefer clicking the visible matching result. Discord's quick switcher
        # can leave focus in the query field, where Enter may select nothing (or
        # a different item); using the screen also handles builds with different
        # keyboard behavior.
        try:
            hit = _vision_locate(
                f'the Discord quick switcher result for the person named "{chat_name}"'
            )
        except Exception as e:
            log.warning("discord switcher: screen reader unavailable (%s), pressing Enter", e)
            hit = None
        if hit:
            from engine.computer import mouse
            if mouse.click(hit["x"], hit["y"]):
                time.sleep(0.9)
                _finish_quick_switcher()
                return True, "discord_quick_switcher_click"
        try:
            seen = _vision_yes(f'Discord quick switcher shows a result matching "{chat_name}".')
        except Exception:
            seen = True            # cannot look: the switcher highlights its best match, Enter opens it
        if not seen:
            _clear_field()
            keyboard.press("esc")
            return False, f"I couldn't find {display} in Discord's quick switcher."
        keyboard.press("enter")
        time.sleep(1.0)
        try:
            _finish_quick_switcher()
        except Exception:
            pass
        return True, "discord_quick_switcher"

    ctrl, how = _focus_field("search", prof, win)
    if not how:
        return False, "I couldn't find the search box."
    if how == "uia" and ctrl is not None and uia.set_text(ctrl, chat_name):
        pass
    else:
        if not keyboard.enter_text(chat_name):
            return False, "I couldn't type the contact name."
    time.sleep(0.9)
    if how in ("vision", "hotkey"):
        # Never press Enter blind: confirm the name really landed in a search box.
        if not _vision_yes(f'The text "{chat_name}" is typed inside a SEARCH box (not a message box).'):
            _clear_field()
            return False, "I wasn't sure the search box was focused, so I stopped."
    keyboard.press("enter")
    time.sleep(1.1)
    return True, how


def _verify_recipient(display: str, chat_name: str, win) -> bool:
    from engine.computer import uia
    names = {_norm(display), _norm(chat_name)} - {""}
    if win is not None:
        blob = " | ".join(_norm(t) for t in uia.visible_texts(win, 80)) + " | " + _norm(_foreground_title())
        if any(n and n in blob for n in names):
            return True
    return _vision_yes(f'The open chat is with "{chat_name}" — their name is shown in the chat header at the top.')


def _draft(text: str, prof: dict, win) -> tuple[bool, str]:
    """Put `text` in the message box. -> (ok, method). Replaces any existing draft."""
    from engine.computer import keyboard, uia
    ctrl, how = _focus_field("input", prof, win)
    if not how:
        return False, ""
    if how == "uia" and ctrl is not None and uia.set_text(ctrl, text):
        got = uia.read_text(ctrl)
        if got is not None and _norm(got)[:30] == _norm(text)[:30]:
            return True, "uia"
    _clear_field()
    time.sleep(0.05)
    if not keyboard.enter_text(text):
        return False, ""
    time.sleep(0.2)
    if how == "uia" and ctrl is not None:
        got = uia.read_text(ctrl)
        if got is not None:
            return (_norm(got)[:30] == _norm(text)[:30]), "paste"
    return _vision_yes(f'The message box contains the draft text "{text[:60]}".'), "paste"


# ---------------- public API ----------------
def send_message(contact: str, text: str, app: Optional[str] = None, engine_context=None) -> str:
    """Draft -> confirm -> send. Returns a short spoken reply."""
    text = (text or "").strip()
    if not text:
        return "What should the message say?"
    if len(text) > MAX_LEN:
        return "That message is too long for me to send safely."

    now = time.time()
    _SEND_TIMES[:] = [t for t in _SEND_TIMES if now - t < 60]
    if len(_SEND_TIMES) >= 5:
        return "That's a lot of messages in a minute — I'm pausing sends. Try again shortly."

    contacts = _load_json(_CONTACTS, {})
    status, entry, cands = resolve_contact(contact, contacts)
    if status == "ambiguous":
        names = ", ".join(contacts[k].get("display", k) for k in cands[:3])
        return f"Which one — {names}?"
    display = (entry or {}).get("display") or contact.strip().title()
    app = (app or (entry or {}).get("default_app") or contacts.get("_default_app") or "").lower()
    if not app:
        return f"Which app should I use for {display} — WhatsApp, Discord or Telegram?"
    prof = _profile(app)
    if not prof:
        return f"I don't know how to message on {app.title()} yet — add it to app_profiles.json."
    chat_name = ((entry or {}).get("chat_names") or {}).get(app) or display
    log.info("send_message: to=%r app=%s len=%d", display, app, len(text))

    try:
        from engine.computer import uia
        ui.set_state("thinking")
        if not _ensure_app_focused(app, prof):
            return f"I couldn't open {app.title()}."
        win = uia.foreground_window()
        ok, info = _open_chat(chat_name, display, prof, win)
        if not ok:
            return info
        if not _in_app(prof):
            return f"{app.title()} lost focus, so I stopped."
        win = uia.foreground_window()
        if not _verify_recipient(display, chat_name, win):
            return f"I couldn't confirm that’s {display}’s chat, so I didn't write anything."
        ok, method = _draft(text, prof, win)
        if not ok:
            return "I couldn't put the message in the box, so I stopped."

        def _redraft(new: str) -> bool:
            return _draft(new, prof, uia.foreground_window())[0]

        token, text = _confirm_send(display, app, text, engine_context, _redraft)
        if token is None:
            if _in_app(prof):
                _clear_field()
            return "Okay, I didn't send it."
        if not _in_app(prof):
            return f"{app.title()} isn't in front anymore, so I didn't send it."
        if not _press_send(token, text):
            return "I couldn't confirm the send, so nothing was sent."
        _SEND_TIMES.append(time.time())
        time.sleep(0.9)
        _learn(app, "draft", method)
        field_empty = _vision_yes("The message input box is now empty and the message appears in the chat above it.")
        return (f"Sent to {display} on {app.title()}." if field_empty
                else f"I pressed send to {display}, but couldn't verify it went through.")
    except Exception as e:
        log.error("send_message failed: %s", e)
        return f"Sending failed — {e}"
    finally:
        ui.set_state("idle")


def try_handle(command: str, engine_context=None) -> Optional[str]:
    """Deterministic entry for 'send Sara: ...'. None = not a message command."""
    p = parse_send_command(command)
    if not p:
        return None
    ui.trace(f"action: send_message(to={p['who']!r}, app={p['app']})")
    return send_message(p["who"], p["text"], p["app"], engine_context)


def open_chat(contact: str, app: str, engine_context=None) -> str:
    contacts=_load_json(_CONTACTS,{})
    status,entry,cands=resolve_contact(contact,contacts)
    if status=="ambiguous":
        names=", ".join(contacts[k].get("display",k) for k in cands[:3])
        return f"Which one — {names}?"
    display=(entry or {}).get("display") or contact.strip().title()
    app=(app or (entry or {}).get("default_app") or "").lower()
    prof=_profile(app)
    if not prof: return f"I don't know how to open chats on {app.title()}."
    chat_name=((entry or {}).get("chat_names") or {}).get(app) or display
    if not _ensure_app_focused(app,prof): return f"I couldn't open {app.title()}."
    from engine.computer import uia
    win=uia.foreground_window()
    ok,info=_open_chat(chat_name,display,prof,win)
    if not ok: return info
    if not _verify_recipient(display,chat_name,uia.foreground_window()):
        return f"I opened {app.title()}, but couldn't confirm {display}'s chat."
    return f"Opened {display}'s chat on {app.title()}."

_OPEN_CHAT_PATTERNS = (
    # The app can be omitted when the contact has a configured default app.
    r"^(?:please\s+)?(?:open|show|go\s+to|switch\s+to)\s+(?:the\s+)?(?P<who>(?:(?!\band\b|\bthen\b).)+?)\s+(?:chat|dm|conversation)$",
    # Natural phrasing: "open Anogashi chat on Discord".
    r"^(?:please\s+)?(?:open|show|go\s+to|switch\s+to)\s+(?:the\s+)?(?P<who>(?:(?!\band\b|\bthen\b).)+?)\s+(?:chat|dm|conversation)\s+(?:on|in)\s+(?P<app>discord|whatsapp|telegram)$",
    # "open Sara's chat on discord", "show my Sara's dm in whatsapp"
    r"^(?:please\s+)?(?:open|show|go\s+to|switch\s+to)\s+(?:my\s+)?(?P<who>(?:(?!\band\b|\bthen\b).)+?)['’]s\s+(?:chat|dm|dms|conversation|messages?)\s+(?:on|in|with)\s+(?P<app>discord|whatsapp|telegram)$",
    # "open the chat with Sara on whatsapp"
    r"^(?:please\s+)?(?:open|show|go\s+to)\s+(?:my\s+|the\s+)?(?:chat|dm|conversation)\s+(?:with|from)\s+(?P<who>(?:(?!\band\b|\bthen\b).)+?)\s+(?:on|in)\s+(?P<app>discord|whatsapp|telegram)$",
    # "message Sara on discord" / "dm Sara on discord" (no text = just open the chat)
    r"^(?:please\s+)?(?:message|dm|text|chat\s+with)\s+(?P<who>(?!and\b|then\b)\w+(?:\s(?!and\b|then\b)\w+)?)\s+(?:on|in)\s+(?P<app>discord|whatsapp|telegram)$",
)
_OPEN_LAST = re.compile(r"^(?:please\s+)?open\s+(?:that|the\s+(?:last|latest)|my\s+(?:last|latest))\s+(?:chat|message|notification|dm)$", re.I)


def _last_chat_notification():
    from engine.features import notification_history
    rows=[x for x in notification_history.get_notifications(None,None,50) if x["app"] in ("discord","whatsapp","telegram")]
    if not rows:
        try:
            from engine.features import notifications
            rows=[x for x in notifications.recent(30) if x["app"] in ("discord","whatsapp","telegram")]
        except Exception:
            rows=[]
    return rows[0] if rows else None


def try_open_chat(command: str, engine_context=None) -> Optional[str]:
    text=(command or '').strip().rstrip('.!? ').strip()
    if _OPEN_LAST.match(text):
        top=_last_chat_notification()
        if not top: return "I don't have a recent chat notification to open."
        # Discord server toasts look like "name (#channel, Server)"; the person is the first part.
        who=re.sub(r"\s*\(.*\)\s*$","",top["sender"]).strip() or top["sender"]
        return open_chat(who,top["app"],engine_context)
    for pat in _OPEN_CHAT_PATTERNS:
        m=re.match(pat,text,re.I)
        if m:
            who=re.sub(r"['’]s$","",m.group('who').strip())      # "open Sara's chat" -> Sara
            return open_chat(who,(m.groupdict().get('app') or '').lower(),engine_context)
    return None
