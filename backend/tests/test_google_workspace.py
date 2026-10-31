"""Tests for google_workspace.py (Step 13). From the backend folder:  python -m unittest tests.test_google_workspace -v

No network and no Google account: the token file lives in a temp folder and `requests` is replaced."""
import json
import logging
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
_ui = types.ModuleType("ui"); _ui.log = logging.getLogger("t"); sys.modules["ui"] = _ui

import google_workspace as gw  # noqa: E402

READ = list(gw.READ_SCOPES)


class Google(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lyra-google-"))
        self._f = gw._file
        gw._file = lambda name: self.tmp / name
        self.posts = []
        fake = types.ModuleType("requests")
        fake.post = lambda url, **kw: (self.posts.append((url, kw)), types.SimpleNamespace(status_code=self.code))[1]
        self._r = sys.modules.get("requests"); sys.modules["requests"] = fake
        self.code = 200
        gw._cache["k"] = (0, {})

    def tearDown(self):
        gw._file = self._f
        if self._r is not None: sys.modules["requests"] = self._r
        else: sys.modules.pop("requests", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def token(self, scopes, **extra):
        (self.tmp / "token.json").write_text(json.dumps({"scopes": scopes, "refresh_token": "r-1", "token": "a-1", **extra}))

    def test_only_read_scopes_are_ever_asked_for(self):
        self.assertTrue(all(s.endswith(".readonly") for s in gw.READ_SCOPES))
        self.assertEqual(len(gw.READ_SCOPES), 2)

    def test_scopes_ok(self):
        self.assertTrue(gw.scopes_ok(READ))
        self.assertTrue(gw.scopes_ok(READ[:1]))
        self.assertFalse(gw.scopes_ok(READ + ["https://mail.google.com/"]))     # anything wider is refused
        self.assertFalse(gw.scopes_ok(["https://www.googleapis.com/auth/gmail.send"]))
        self.assertFalse(gw.scopes_ok([]))

    def test_a_token_with_wider_scopes_does_not_count_as_connected(self):
        self.token(READ + ["https://www.googleapis.com/auth/gmail.modify"])
        self.assertFalse(gw.connected())
        self.token(READ)
        self.assertTrue(gw.connected())

    def test_status(self):
        st = gw.status()
        self.assertFalse(st["connected"]); self.assertFalse(st["has_client_secret"])
        (self.tmp / "client_secret.json").write_text("{}")
        self.token(READ)
        st = gw.status()
        self.assertTrue(st["connected"]); self.assertTrue(st["has_client_secret"])
        self.assertEqual(st["folder"], str(self.tmp))

    def test_disconnect_revokes_at_google_and_forgets_the_token(self):
        self.token(READ)
        msg = gw.disconnect()
        self.assertIn("cancel my access", msg)
        self.assertEqual(self.posts[0][0], "https://oauth2.googleapis.com/revoke")
        self.assertEqual(self.posts[0][1]["params"], {"token": "r-1"})
        self.assertFalse((self.tmp / "token.json").exists())
        self.assertEqual(gw._cache, {})

    def test_disconnect_still_forgets_the_token_when_google_is_unreachable(self):
        self.token(READ); self.code = 500
        msg = gw.disconnect()
        self.assertIn("permissions", msg)
        self.assertFalse((self.tmp / "token.json").exists())

    def test_disconnect_when_never_connected(self):
        self.assertEqual(gw.disconnect(), "Google wasn't connected.")
        self.assertEqual(self.posts, [])

    def test_connect_without_client_secret_explains_setup(self):
        self.assertIn("client_secret.json", gw.connect())

    def test_commands_need_a_connection(self):
        self.assertIn("isn't connected", gw.try_google("read my latest emails"))
        self.assertIsNone(gw.try_google("what's my next event"))     # falls through to the other calendars
        self.assertIsNone(gw.try_google("open spotify"))


if __name__ == "__main__":
    unittest.main()
