"""Lyra — Gmail + Google Calendar, READ-ONLY (Step 13).

Voice/typed: "what's my next event", "today's agenda", "read my latest emails", "any unread emails",
"summarize the first email", "draft a reply to the first email saying ...", "connect google".
The Calendar tab reads GET /gcal/events (ui_events) when it is opened.

Safety: only the two *.readonly scopes are ever requested; a token that carries any other scope is
refused; only GET requests are made; data is fetched on demand (no polling); a reply "draft" is text
only (never saved to Gmail, never sent). Token + client secret live in the data folder
(%APPDATA%\\Lyra\\google), never in the project.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from email.utils import parseaddr

import ui

log = ui.log

READ_SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
)
_CAL = "https://www.googleapis.com/calendar/v3/calendars/primary"
_MAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
_SETUP = "Put your Google client_secret.json in the Lyra google folder first — see GOOGLE_SETUP.md."
_NOT_CONNECTED = "Google isn't connected yet. Say \"connect Google\" to sign in."


class GoogleError(Exception):
    """A problem worth telling the user about (message is spoken as is)."""


# ---------------------------------------------------------------------------
# Token storage / auth
# ---------------------------------------------------------------------------
def _file(name: str):
    from paths import data_path
    return data_path("google", name)


def scopes_ok(scopes) -> bool:
    """True only if the token has read access and nothing beyond the read-only scopes."""
    s = set(scopes or [])
    return bool(s) and s <= set(READ_SCOPES)


def connected() -> bool:
    """Cheap local check (no network): a token file with read-only scopes exists."""
    try:
        tok = json.loads(_file("token.json").read_text("utf-8"))
        return scopes_ok(tok.get("scopes"))
    except Exception:  # noqa: BLE001
        return False


def _creds(force_refresh: bool = False):
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as e:
        raise GoogleError("Google support needs: pip install google-auth google-auth-oauthlib") from e
    if not connected():
        raise GoogleError(_NOT_CONNECTED)
    creds = Credentials.from_authorized_user_file(str(_file("token.json")), list(READ_SCOPES))
    if force_refresh or not creds.valid:
        try:
            creds.refresh(Request())
            _save(creds)
        except Exception as e:  # noqa: BLE001
            raise GoogleError("Your Google sign-in expired. Say \"connect Google\" to sign in again.") from e
    return creds


def _save(creds) -> None:
    p = _file("token.json")
    p.write_text(creds.to_json(), "utf-8")
    try:
        import os
        os.chmod(p, 0o600)
    except Exception:  # noqa: BLE001
        pass


def connect() -> str:
    """Start the one-time browser sign-in (installed-app flow). Returns at once; finishes in a thread."""
    secret = _file("client_secret.json")
    if not secret.exists():
        return _SETUP
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
    except ImportError:
        return "Google support needs: pip install google-auth google-auth-oauthlib"

    def work():
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(str(secret), list(READ_SCOPES))
            creds = flow.run_local_server(port=0, access_type="offline", prompt="consent", timeout_seconds=300)
            if not scopes_ok(creds.scopes):
                log.warning("google: refused token with scopes %s", creds.scopes)
                msg = "Google sign-in gave unexpected permissions, so I didn't keep it."
            else:
                _save(creds)
                _cache.clear()
                msg = "Google is connected. I can read your calendar and email."
        except Exception as e:  # noqa: BLE001
            log.warning("google: sign-in failed: %s", e)
            msg = "Google sign-in didn't finish."
        try:
            import voice
            voice.speak(msg)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=work, name="lyra-google-auth", daemon=True).start()
    return "Opening your browser to sign in to Google."


_libs_cache: list = []


def _libs_installed() -> bool:
    """Are the Google packages installed? Looked up without importing them: importing google-auth and
    oauthlib took ~1 s on every Settings open and stalled the UI thread's requests. Cached for the session."""
    if _libs_cache:
        return _libs_cache[0]
    import importlib.util
    try:
        ok = (importlib.util.find_spec("google.oauth2") is not None
              and importlib.util.find_spec("google_auth_oauthlib") is not None)
    except (ImportError, ValueError):
        ok = False
    _libs_cache.append(ok)
    return ok


def status() -> dict:
    """For GET /google/status (Settings). Local checks only: no network."""
    try:
        has_secret = _file("client_secret.json").exists()
    except Exception:  # noqa: BLE001
        has_secret = False
    libs = _libs_installed()
    return {"connected": connected(), "has_client_secret": has_secret, "libs": libs,
            "scopes": list(READ_SCOPES), "folder": str(_file("client_secret.json").parent)}


def _revoke(token_json: dict) -> bool:
    """Tell Google to invalidate the grant, not just forget it here. Best effort."""
    tok = token_json.get("refresh_token") or token_json.get("token")
    if not tok:
        return False
    try:
        import requests
        r = requests.post("https://oauth2.googleapis.com/revoke", params={"token": tok},
                          headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        log.warning("google: revoke failed: %s", e)
        return False


def disconnect() -> str:
    path = _file("token.json")
    try:
        tok = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return "Google wasn't connected."
    except Exception:  # noqa: BLE001
        tok = {}
    revoked = _revoke(tok)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    _cache.clear()
    _last_mail.clear()
    if revoked:
        return "Google is disconnected, and I told Google to cancel my access."
    return ("Google is disconnected here. I couldn't reach Google to cancel the access, so you can also remove "
            "Lyra at myaccount.google.com/permissions.")


# ---------------------------------------------------------------------------
# HTTP (GET only)
# ---------------------------------------------------------------------------
def _get(url: str, params: dict | None = None) -> dict:
    import requests
    for attempt in (0, 1):
        creds = _creds(force_refresh=attempt == 1)
        try:
            r = requests.get(url, params=params, headers={"Authorization": f"Bearer {creds.token}"}, timeout=15)
        except requests.RequestException as e:
            raise GoogleError("I couldn't reach Google — check your connection.") from e
        if r.status_code == 401 and attempt == 0:
            continue                                   # retry once with a forced token refresh
        if r.status_code == 403:
            raise GoogleError("Google refused the request — enable the Gmail and Calendar APIs in your Google Cloud project.")
        if r.status_code == 401:
            raise GoogleError("Your Google sign-in expired. Say \"connect Google\" to sign in again.")
        if not r.ok:
            raise GoogleError(f"Google returned an error ({r.status_code}).")
        return r.json()
    raise GoogleError("Google didn't answer.")


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
def _parse_event(e: dict) -> dict | None:
    if e.get("status") == "cancelled":
        return None
    for a in e.get("attendees") or []:
        if a.get("self") and a.get("responseStatus") == "declined":
            return None
    s, en = e.get("start") or {}, e.get("end") or {}
    title = (e.get("summary") or "(no title)").strip()
    try:
        if "date" in s:
            d0 = date.fromisoformat(s["date"])
            d1 = date.fromisoformat(en.get("date") or s["date"])      # exclusive
            return {"id": e.get("id", ""), "title": title, "all_day": True, "start": d0, "end": max(d1, d0 + timedelta(days=1))}
        st = datetime.fromisoformat(s["dateTime"].replace("Z", "+00:00")).astimezone()
        et = datetime.fromisoformat((en.get("dateTime") or s["dateTime"]).replace("Z", "+00:00")).astimezone()
        return {"id": e.get("id", ""), "title": title, "all_day": False, "start": st, "end": et}
    except Exception:  # noqa: BLE001
        return None


def _events_between(t0: datetime, t1: datetime, limit: int = 100) -> list[dict]:
    data = _get(f"{_CAL}/events", {
        "timeMin": t0.astimezone().isoformat(), "timeMax": t1.astimezone().isoformat(),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": limit})
    return [ev for ev in (_parse_event(x) for x in data.get("items", [])) if ev]


def _midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day).astimezone()


def _say_time(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}{'' if dt.minute == 0 else f':{dt.minute:02d}'} {'AM' if dt.hour < 12 else 'PM'}"


def _say_day(d: date) -> str:
    today = date.today()
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    if 0 < (d - today).days < 7:
        return d.strftime("on %A")
    return d.strftime("on %B ") + str(d.day)


def next_event() -> str:
    now = datetime.now().astimezone()
    evs = _events_between(now, now + timedelta(days=14), 25)
    evs = [e for e in evs if e["all_day"] or e["end"] > now]
    if not evs:
        return "You have nothing coming up in the next two weeks."
    timed = [e for e in evs if not e["all_day"]]
    e = timed[0] if timed else evs[0]
    if e["all_day"]:
        return f"Next up: {e['title']}, all day {_say_day(e['start'])}."
    if e["start"] <= now:
        return f"You're in {e['title']} right now, until {_say_time(e['end'])}."
    return f"Your next event is {e['title']} {_say_day(e['start'].date())} at {_say_time(e['start'])}."


def agenda_today() -> str:
    today = date.today()
    evs = _events_between(_midnight(today), _midnight(today + timedelta(days=1)))
    evs = [e for e in evs if e["all_day"] or e["end"] > datetime.now().astimezone()]
    if not evs:
        return "Nothing left on your calendar today."
    parts = [f"{e['title']}, all day" if e["all_day"] else f"{e['title']} at {_say_time(e['start'])}" for e in evs[:6]]
    more = f", and {len(evs) - 6} more" if len(evs) > 6 else ""
    n = len(evs)
    return f"You have {n} thing{'s' if n != 1 else ''} today: " + "; ".join(parts) + more + "."


_cache: dict = {}


def ui_events(frm: str, to: str) -> dict:
    """For GET /gcal/events: calendar rows for the Calendar tab. No network unless connected + enabled."""
    try:
        import settings_store
        if not settings_store.feature_on("google"):
            return {"ok": True, "connected": False, "events": []}
    except Exception:  # noqa: BLE001
        pass
    if not connected():
        return {"ok": True, "connected": False, "events": []}
    try:
        d0, d1 = date.fromisoformat(frm), date.fromisoformat(to)
    except ValueError:
        return {"ok": False, "connected": True, "events": [], "error": "bad range"}
    d1 = min(d1, d0 + timedelta(days=120))
    key = (frm, d1.isoformat())
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        evs = _events_between(_midnight(d0), _midnight(d1 + timedelta(days=1)), 250)
    except GoogleError as e:
        return {"ok": False, "connected": True, "events": [], "error": str(e)}
    rows = []
    for e in evs:
        if e["all_day"]:
            d = e["start"]
            while d < e["end"] and d <= d1:
                if d >= d0:
                    rows.append({"id": e["id"], "title": e["title"], "date": d.isoformat(), "time": "", "all_day": True})
                d += timedelta(days=1)
        else:
            rows.append({"id": e["id"], "title": e["title"], "date": e["start"].date().isoformat(),
                         "time": e["start"].strftime("%H:%M"), "all_day": False})
    out = {"ok": True, "connected": True, "events": rows}
    _cache[key] = (time.time(), out)
    return out


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------
_last_mail: list[dict] = []     # what was last read out, so "summarize the first one" works


def _header(msg: dict, name: str) -> str:
    for h in (msg.get("payload") or {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _sender(msg: dict) -> str:
    name, addr = parseaddr(_header(msg, "From"))
    return (name or addr or "someone").strip()


def list_mail(unread: bool, n: int = 5) -> list[dict]:
    params = {"maxResults": n, "labelIds": "INBOX"}
    if unread:
        params["q"] = "is:unread"
    ids = [m["id"] for m in _get(f"{_MAIL}/messages", params).get("messages", [])]
    out = []
    for mid in ids:
        m = _get(f"{_MAIL}/messages/{mid}", {"format": "metadata", "metadataHeaders": ["From", "Subject"]})
        out.append({"id": mid, "sender": _sender(m), "subject": _header(m, "Subject").strip() or "(no subject)"})
    return out


def read_latest(unread: bool) -> str:
    global _last_mail
    mails = list_mail(unread)
    _last_mail = mails
    if not mails:
        return "You have no unread emails." if unread else "Your inbox is empty."
    lead = f"You have {len(mails)} {'unread' if unread else 'latest'} email{'s' if len(mails) != 1 else ''}. "
    return lead + " ".join(f"{i}. {m['sender']}: {m['subject']}." for i, m in enumerate(mails, 1))


def _body_text(payload: dict) -> str:
    def decode(part):
        data = (part.get("body") or {}).get("data")
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace") if data else ""

    def walk(part, want):
        if part.get("mimeType") == want and decode(part):
            return decode(part)
        for sub in part.get("parts") or []:
            r = walk(sub, want)
            if r:
                return r
        return ""

    text = walk(payload, "text/plain")
    if not text:
        html = walk(payload, "text/html")
        text = re.sub(r"\s+", " ", re.sub(r"<(script|style).*?</\1>|<[^>]+>", " ", html, flags=re.S | re.I))
    return text.strip()


def _pick(ref: str | None, n: str | None) -> dict:
    if not _last_mail:
        mails = list_mail(False, 5)
        _last_mail.extend(mails)
    if n:
        idx = int(n) - 1
    else:
        idx = {"first": 0, "latest": 0, "newest": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
               "last": len(_last_mail) - 1}.get((ref or "latest").lower(), None)
        if idx is None and (ref or "").isdigit():
            idx = int(ref) - 1
    if idx is None or not 0 <= idx < len(_last_mail):
        raise GoogleError("I don't have that email — ask me to read your latest emails first.")
    return _last_mail[idx]


def _full_text(mid: str) -> tuple[str, str, str]:
    m = _get(f"{_MAIL}/messages/{mid}", {"format": "full"})
    return _sender(m), _header(m, "Subject").strip(), _body_text(m.get("payload") or {})[:6000]


def _ask_llm(prompt: str) -> str:
    import brain
    return (brain.ask_brain(prompt, [], intent="chat") or "").strip()


def summarize(ref, n) -> str:
    mail = _pick(ref, n)
    sender, subject, body = _full_text(mail["id"])
    if not body:
        return f"That email from {sender} has no readable text."
    out = _ask_llm("Summarize this email in two or three short sentences. Reply with ONLY the summary.\n\n"
                   f"From: {sender}\nSubject: {subject}\n\n{body}")
    return out or "I couldn't summarize it."


def draft_reply(ref, n, gist: str | None) -> str:
    """Reply text only. Not saved to Gmail, never sent (no write scope is held)."""
    mail = _pick(ref, n)
    sender, subject, body = _full_text(mail["id"])
    say = f" The reply should say: {gist}." if gist else ""
    out = _ask_llm("Write a short, polite reply to the email below, from the recipient's point of view."
                   f"{say} Reply with ONLY the body of the reply - no subject line, no placeholders.\n\n"
                   f"From: {sender}\nSubject: {subject}\n\n{body}")
    return f"Here's a draft reply to {sender}. It isn't saved or sent: {out}" if out else "I couldn't write a draft."


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
def _rc(p: str):
    return re.compile(p, re.I)


_ORD = r"first|second|third|fourth|fifth|latest|newest|last"
_LEAD = r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?"
_NEXT = _rc(_LEAD + r"(?:what(?:'s|\s+is)|tell\s+me|show\s+me|when\s+is)\s+(?:my\s+)?next\s+(?:event|meeting|appointment)"
                   r"(?:\s+(?:on|in)\s+(?:my\s+)?(?:google\s+)?calendar)?$")
_AGENDA = _rc(_LEAD + r"(?:what(?:'s|\s+is)\s+(?:on\s+)?(?:my\s+)?(?:google\s+)?(?:calendar|agenda|schedule)(?:\s+(?:for\s+)?today)?"
                     r"|(?:read|show|give)\s+(?:me\s+)?(?:my\s+)?(?:today'?s\s+)?agenda|(?:today'?s|my)\s+agenda(?:\s+today)?"
                     r"|what\s+do\s+i\s+have\s+(?:on\s+)?today)$")
_MAILS = _rc(_LEAD + r"(?:(?:read|check|show|list)\s+(?:me\s+)?(?:my\s+)?(?P<w>latest|last|recent|new|unread)\s+(?:e-?mails?|mail|gmail)"
                    r"|(?:do\s+i\s+have|are\s+there|any)\s+(?:any\s+)?(?P<w2>new|unread)\s+(?:e-?mails?|mail)"
                    r"|what(?:'s|\s+is)\s+in\s+my\s+inbox|check\s+my\s+(?:e-?mails?|inbox|gmail|mail))$")
_SUM = _rc(_LEAD + r"summari[sz]e\s+(?:the\s+|that\s+|my\s+)?(?:(?P<o>" + _ORD + r"|\d)(?:st|nd|rd|th)?\s+)?"
                  r"(?:one|e-?mail|mail)(?:\s+(?:number\s+)?(?P<n>\d))?$")
_DRAFT = _rc(_LEAD + r"(?:(?:draft|write|compose)\s+(?:a\s+)?(?:reply|response)\s+to|reply\s+to)\s+(?:the\s+|that\s+|my\s+)?"
                    r"(?:(?P<o>" + _ORD + r"|\d)(?:st|nd|rd|th)?\s+)?(?:one|e-?mail|mail)(?:\s+(?:number\s+)?(?P<n>\d))?"
                    r"(?:\s+(?:saying|that\s+says|telling\s+them|with)\s+(?P<g>.+))?$")
_CONNECT = _rc(_LEAD + r"(?:connect|sign\s+in\s+to|log\s+in\s+to|link)\s+(?:my\s+)?google(?:\s+(?:account|calendar|workspace|mail|gmail))?$")
_DISCONNECT = _rc(_LEAD + r"(?:disconnect|sign\s+out\s+of|log\s+out\s+of|unlink)\s+(?:my\s+)?google(?:\s+(?:account|calendar|workspace|mail|gmail))?$")


def try_google(command: str, engine_context=None) -> str | None:
    """Router entry. Returns the reply to speak, or None if this isn't a Google command. Never raises.
    "next event" / "agenda" fall through to the other handlers while Google isn't connected."""
    try:
        cmd = re.sub(r"\s+", " ", (command or "").strip()).rstrip("?.! ")
        low = cmd.lower()
        if not cmd:
            return None
        m = None
        for kind, rx in (("connect", _CONNECT), ("disconnect", _DISCONNECT), ("next", _NEXT), ("agenda", _AGENDA),
                         ("mails", _MAILS), ("sum", _SUM), ("draft", _DRAFT)):
            m = rx.match(cmd)
            if m:
                break
        else:
            return None
        try:
            import settings_store
            if not settings_store.feature_on("google"):
                return None
        except Exception:  # noqa: BLE001
            pass
        if kind == "connect":
            return connect()
        if kind == "disconnect":
            return disconnect()
        if not connected():
            return None if kind in ("next", "agenda") else _NOT_CONNECTED
        if kind == "next":
            return next_event()
        if kind == "agenda":
            return agenda_today()
        if kind == "mails":
            return read_latest("unread" in low or "new" in low)
        if kind == "sum":
            return summarize(m.group("o"), m.group("n"))
        return draft_reply(m.group("o"), m.group("n"), m.group("g"))
    except GoogleError as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        log.warning("google: %s", e)
        return f"I couldn't reach Google — {e}"


if __name__ == "__main__":      # python google_workspace.py connect
    import sys
    if sys.argv[1:] == ["connect"]:
        print(connect())
        while threading.active_count() > 1:
            time.sleep(0.5)
    else:
        print("usage: python google_workspace.py connect")
