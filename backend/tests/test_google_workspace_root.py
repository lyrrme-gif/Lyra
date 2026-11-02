"""Offline tests for google_workspace.py: python -m unittest test_google_workspace -v
Google's HTTP API, the token and the LLM are stubbed; no network."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))  # backend/ on the path

import base64, json, logging, sys, tempfile, types, unittest
from datetime import datetime, timedelta
from pathlib import Path


def b64(s): return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.calls = []; self.feature = True; self.llm = []
        ui = types.ModuleType("ui"); ui.log = logging.getLogger("t")
        paths = types.ModuleType("paths")
        def dp(*p):
            f = self.tmp.joinpath(*p); f.parent.mkdir(parents=True, exist_ok=True); return f
        paths.data_path = dp
        ss = types.ModuleType("settings_store"); ss.feature_on = lambda n: self.feature
        brain = types.ModuleType("brain")
        brain.ask_brain = lambda prompt, hist, intent="": (self.llm.append(prompt) or "LLM RESULT")
        sys.modules.update(ui=ui, paths=paths, settings_store=ss, brain=brain)
        sys.modules.pop("google_workspace", None)
        import google_workspace as g, requests
        self.g = g
        self.routes = {}
        def fake_get(url, params=None, headers=None, timeout=0):
            self.calls.append(("GET", url, params))
            for k, v in self.routes.items():
                if url.endswith(k):
                    return types.SimpleNamespace(status_code=200, ok=True, json=lambda v=v: v)
            return types.SimpleNamespace(status_code=404, ok=False, json=lambda: {})
        def forbidden(*a, **k): self.calls.append(("WRITE",)); raise AssertionError("write call")
        self._orig = (requests.get, requests.post, requests.put, requests.patch, requests.delete)
        requests.get = fake_get; requests.post = requests.put = requests.patch = requests.delete = forbidden
        g._creds = lambda force_refresh=False: types.SimpleNamespace(token="tok")
        self.token(list(g.READ_SCOPES))

    def tearDown(self):
        import requests
        requests.get, requests.post, requests.put, requests.patch, requests.delete = self._orig

    def token(self, scopes):
        (self.tmp / "google").mkdir(exist_ok=True)
        (self.tmp / "google" / "token.json").write_text(json.dumps({"scopes": scopes}))

    def ev(self, title, start, end=None, **kw):
        end = end or start + timedelta(hours=1)
        return {"id": title, "summary": title, "start": {"dateTime": start.astimezone().isoformat()},
                "end": {"dateTime": end.astimezone().isoformat()}, **kw}

    def test_only_read_only_scopes(self):
        self.assertTrue(all(s.endswith(".readonly") for s in self.g.READ_SCOPES))
        self.assertTrue(self.g.scopes_ok(self.g.READ_SCOPES))
        self.assertFalse(self.g.scopes_ok(list(self.g.READ_SCOPES) + ["https://www.googleapis.com/auth/gmail.send"]))
        self.assertFalse(self.g.scopes_ok([]))

    def test_token_with_write_scope_is_not_connected(self):
        self.token(list(self.g.READ_SCOPES) + ["https://www.googleapis.com/auth/gmail.compose"])
        self.assertFalse(self.g.connected())
        self.assertIn("connect Google", self.g.try_google("read my latest emails"))

    def test_not_connected_falls_through_for_calendar(self):
        (self.tmp / "google" / "token.json").unlink()
        self.assertIsNone(self.g.try_google("what's my next event"))
        self.assertIsNone(self.g.try_google("today's agenda"))
        self.assertIn("connect Google", self.g.try_google("any unread emails"))
        self.assertEqual(self.calls, [])

    def test_next_event_skips_declined_and_past(self):
        now = datetime.now()
        self.routes["/events"] = {"items": [
            self.ev("Old", now - timedelta(hours=3), now - timedelta(hours=2)),
            self.ev("Declined", now + timedelta(hours=1), attendees=[{"self": True, "responseStatus": "declined"}]),
            self.ev("Standup", now + timedelta(hours=2))]}
        r = self.g.try_google("What's my next event?")
        self.assertIn("Standup", r); self.assertNotIn("Declined", r); self.assertNotIn("Old", r)

    def test_agenda_today(self):
        now = datetime.now()
        self.routes["/events"] = {"items": [
            {"id": "a", "summary": "Holiday", "start": {"date": now.date().isoformat()}, "end": {"date": (now.date() + timedelta(days=1)).isoformat()}},
            self.ev("Dentist", now + timedelta(hours=1))]}
        r = self.g.try_google("what's on my calendar today")
        self.assertIn("2 things", r); self.assertIn("Holiday, all day", r); self.assertIn("Dentist at", r)

    def test_ui_events_rows_and_cache(self):
        now = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0)
        d = now.date()
        self.routes["/events"] = {"items": [
            self.ev("Meet", now),
            {"id": "t", "summary": "Trip", "start": {"date": d.isoformat()}, "end": {"date": (d + timedelta(days=2)).isoformat()}}]}
        out = self.g.ui_events((d - timedelta(days=1)).isoformat(), (d + timedelta(days=5)).isoformat())
        self.assertTrue(out["connected"])
        rows = {(r["title"], r["date"]): r for r in out["events"]}
        self.assertEqual(rows[("Meet", d.isoformat())]["time"], "15:30")
        self.assertIn(("Trip", d.isoformat()), rows); self.assertIn(("Trip", (d + timedelta(days=1)).isoformat()), rows)
        self.assertNotIn(("Trip", (d + timedelta(days=2)).isoformat()), rows)       # end date is exclusive
        n = len(self.calls)
        self.g.ui_events((d - timedelta(days=1)).isoformat(), (d + timedelta(days=5)).isoformat())
        self.assertEqual(len(self.calls), n)                                        # served from cache

    def test_ui_events_no_network_when_not_connected_or_off(self):
        self.feature = False
        self.assertEqual(self.g.ui_events("2026-10-01", "2026-10-30")["events"], [])
        self.feature = True; (self.tmp / "google" / "token.json").unlink()
        self.assertFalse(self.g.ui_events("2026-10-01", "2026-10-30")["connected"])
        self.assertEqual(self.calls, [])

    def _mail_routes(self):
        def msg(i, frm, sub, body=None):
            m = {"id": i, "payload": {"headers": [{"name": "From", "value": frm}, {"name": "Subject", "value": sub}]}}
            if body: m["payload"].update(mimeType="multipart/alternative", parts=[{"mimeType": "text/plain", "body": {"data": b64(body)}}])
            return m
        self.msgs = {"m1": msg("m1", "Ahmed Z <ahmed@x.ma>", "Exam schedule", "The exam is on Monday at 9."), "m2": msg("m2", "bob@y.com", "Hi")}
        self.routes["/messages"] = {"messages": [{"id": "m1"}, {"id": "m2"}]}
        orig = self.routes
        import requests
        base = requests.get
        def get(url, params=None, headers=None, timeout=0):
            for mid, m in self.msgs.items():
                if url.endswith("/messages/" + mid):
                    self.calls.append(("GET", url, params))
                    return types.SimpleNamespace(status_code=200, ok=True, json=lambda m=m: m)
            return base(url, params, headers, timeout)
        requests.get = get

    def test_read_summarize_draft_never_writes(self):
        self._mail_routes()
        r = self.g.try_google("read my latest emails")
        self.assertIn("1. Ahmed Z: Exam schedule.", r); self.assertIn("2. bob@y.com: Hi.", r)
        self.assertEqual(self.g.try_google("summarize the first email"), "LLM RESULT")
        self.assertIn("The exam is on Monday", self.llm[-1])
        d = self.g.try_google("draft a reply to the first email saying I will be there")
        self.assertIn("isn't saved or sent", d); self.assertIn("I will be there", self.llm[-1])
        self.assertIn("no readable text", self.g.try_google("summarize email 2"))
        self.assertNotIn(("WRITE",), self.calls)
        self.assertTrue(all(c[0] == "GET" for c in self.calls))
        self.assertTrue(any(c[2] and c[2].get("labelIds") == "INBOX" for c in self.calls if c[2]))

    def test_unread_query(self):
        self._mail_routes()
        self.g.try_google("any unread emails")
        self.assertTrue(any(c[2] and c[2].get("q") == "is:unread" for c in self.calls))

    def test_phrases_ignore_unrelated(self):
        for c in ("what is the capital of France", "summarize this", "reply to John on whatsapp", "next", "open my calendar app"):
            self.assertIsNone(self.g.try_google(c), c)

    def test_feature_off(self):
        self.feature = False
        self.assertIsNone(self.g.try_google("connect google"))

    def test_connect_needs_client_secret(self):
        self.assertIn("client_secret.json", self.g.try_google("connect Google"))

    def test_disconnect_removes_token(self):
        self.assertEqual(self.g.try_google("disconnect google"), "Google is disconnected.")
        self.assertFalse(self.g.connected())


if __name__ == "__main__":
    unittest.main()
