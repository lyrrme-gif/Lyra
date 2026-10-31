"""Tests for the shipped app plugin: plugins/whatsapp.py.
From the backend folder:

    python -m unittest tests.test_apps_plugins -v

No network, no WhatsApp, no Spotify: WhatsApp runs against a fake helper process (a few lines of Python that
speak the same JSON lines as bridge.js) and Spotify against a fake HTTP function. They check the plugins'
own logic (events -> views, login URL, token refresh, Feb-2026 playlist shape), not the real services.
"""
import json
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.request
from pathlib import Path

from plugins import apps, whatsapp

FAKE_BRIDGE = textwrap.dedent('''
    import json, sys
    def out(o): print(json.dumps(o), flush=True)
    out({"event": "qr", "qr": "2@abcdefghijklmnop,QRDATA,xyz"})
    for line in sys.stdin:
        cmd = json.loads(line)
        if cmd["cmd"] == "go":
            out({"event": "open", "me": {"id": "1@s.whatsapp.net", "name": "Lyrr"}})
            out({"event": "chat", "id": "212600000001@s.whatsapp.net", "name": "Sara", "ts": 100})
            out({"event": "message", "chat": "212600000001@s.whatsapp.net", "id": "old1", "from": "Sara", "name": "Sara",
                 "text": "old message", "ts": 100, "mine": False, "live": False})
            out({"event": "message", "chat": "212600000001@s.whatsapp.net", "id": "new1", "from": "Sara", "name": "Sara",
                 "text": "are you coming?", "ts": 200, "mine": False, "live": True})
        elif cmd["cmd"] == "send":
            out({"event": "message", "chat": cmd["to"], "id": "me1", "from": "me", "name": "Sara",
                 "text": cmd["text"], "ts": 300, "mine": True, "live": True})
        elif cmd["cmd"] in ("quit", "logout"):
            break
''')


def wait_for(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class WhatsAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        script = Path(self.tmp.name) / "fake_bridge.py"
        script.write_text(FAKE_BRIDGE)
        self.store = apps.AppStore("whatsapp", Path(self.tmp.name) / "data")
        self.wa = whatsapp.WhatsApp(self.store, command=[sys.executable, str(script)])
        self.wa.folder = lambda: Path(self.tmp.name) / "data"      # keep files in the temp folder
        self.addCleanup(self.wa.stop)

    def connect(self):
        self.wa.start()
        self.assertTrue(wait_for(lambda: self.wa.qr))
        self.wa.send_cmd({"cmd": "go"})
        self.assertTrue(wait_for(lambda: self.wa.open))

    def test_qr_screen_before_linking(self):
        self.wa.start()
        self.assertTrue(wait_for(lambda: self.wa.qr))
        v = apps.sanitize_view(self.wa.view(""))
        self.assertEqual(v["type"], "qr")
        self.assertTrue(v["image"].startswith("data:image/svg+xml;base64,"))
        self.assertFalse(self.wa.status()["connected"])

    def test_qr_is_small_and_matches_the_code(self):
        import base64
        import re
        import secrets
        import qrcode
        text = "2@" + secrets.token_urlsafe(90) + "," + secrets.token_urlsafe(32) + "," + secrets.token_urlsafe(32) + "," + secrets.token_urlsafe(24)
        uri = whatsapp.qr_svg(text, qrcode)
        self.assertLess(len(uri), 20000)                      # far under the 60 000 the view cleaner allows
        svg = base64.b64decode(uri.split(",", 1)[1]).decode()
        qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(text)
        qr.make(fit=True)
        want = qr.get_matrix()
        n = len(want)
        got = [[False] * n for _ in range(n)]                 # read the dark squares back out of the path
        for x, y, w in re.findall(r"M(\d+) (\d+)h(\d+)v1h-\d+z", svg):
            for i in range(int(w)):
                got[int(y)][int(x) + i] = True
        self.assertEqual(got, want)

    def test_chat_list_then_thread(self):
        self.connect()
        self.assertEqual(self.wa.status(), {"connected": True, "account": "Lyrr", "message": ""})
        lst = apps.sanitize_view(self.wa.view(""))
        self.assertEqual(lst["type"], "list")
        self.assertEqual(lst["items"][0]["title"], "Sara")
        self.assertEqual(lst["items"][0]["badge"], "1")                    # one live message is unread
        self.assertEqual(lst["items"][0]["action"]["name"], "open")
        opened = self.wa.action("open", {"chat": "212600000001@s.whatsapp.net"})["view"]
        th = apps.sanitize_view(opened)
        self.assertEqual([m["text"] for m in th["messages"]], ["old message", "are you coming?"])
        self.assertEqual(th["route"], "chat:212600000001@s.whatsapp.net")
        self.assertEqual(th["compose"]["action"], "send")
        self.assertEqual(apps.sanitize_view(self.wa.view(""))["items"][0]["badge"], "")   # opening clears unread

    def test_history_is_not_a_toast_but_live_messages_are_once(self):
        self.connect()
        first = self.wa.poll()
        self.assertEqual([(p["sender"], p["text"]) for p in first], [("Sara", "are you coming?")])
        self.assertEqual(self.wa.poll(), [])

    def test_send_goes_to_the_helper_and_comes_back(self):
        self.connect()
        jid = "212600000001@s.whatsapp.net"
        self.assertTrue(self.wa.action("send", {"chat": jid, "text": "on my way"})["ok"])
        self.assertTrue(wait_for(lambda: any(m["text"] == "on my way" for m in self.wa.chats[jid]["messages"])))
        self.assertTrue(self.wa.chats[jid]["messages"][-1]["mine"])
        self.assertEqual(self.wa.poll()[-1:] and [], [])            # my own message never becomes a toast

    def test_send_needs_text_and_a_connection(self):
        self.assertFalse(self.wa.action("send", {"chat": "x", "text": "hi"})["ok"])
        self.connect()
        self.assertFalse(self.wa.action("send", {"chat": "x", "text": "  "})["ok"])

    def test_duplicate_message_ids_are_ignored(self):
        self.connect()
        ev = {"event": "message", "chat": "c@s.whatsapp.net", "id": "dup", "from": "A", "name": "A", "text": "t",
              "ts": 5, "mine": False, "live": True}
        self.wa.handle(ev)
        self.wa.handle(ev)
        self.assertEqual(len(self.wa.chats["c@s.whatsapp.net"]["messages"]), 1)

    def group_message(self, mid, mentioned=False):
        self.wa.handle({"event": "message", "chat": "g@g.us", "id": mid, "from": "Omar", "name": "Study group",
                        "text": "exam moved", "ts": 9, "mine": False, "live": True, "mentioned": mentioned})

    def test_groups_are_quiet_by_default_but_still_unread(self):
        self.group_message("1")
        self.assertEqual(self.wa.poll(), [])
        self.assertEqual(self.wa.chats["g@g.us"]["unread"], 1)

    def test_a_mention_gets_through_a_muted_group_and_names_the_sender(self):
        self.group_message("2", mentioned=True)
        self.assertEqual(self.wa.poll(), [{"id": "2", "sender": "Study group", "text": "Omar: exam moved", "chat": "g@g.us"}])

    def test_unmuted_groups_and_the_master_switch(self):
        self.store.set("settings", {"mute_groups": False})
        self.group_message("3")
        self.assertEqual(len(self.wa.poll()), 1)
        self.store.set("settings", {"toasts": False, "mute_groups": False})
        self.group_message("4")
        self.assertEqual(self.wa.poll(), [])
        self.assertEqual(self.wa.chats["g@g.us"]["unread"], 2)      # still counted

    def test_chat_rows_carry_a_time_and_the_list_an_unread_chip(self):
        self.connect()
        lst = apps.sanitize_view(self.wa.view(""))
        self.assertEqual(lst["chip"], "1 unread")
        self.assertTrue(lst["items"][0]["time"])

    def test_relative_times(self):
        now = 1_000_000_000
        self.assertEqual(whatsapp.rel_time(now - 20, now), "now")
        self.assertEqual(whatsapp.rel_time(now - 300, now), "5m")
        self.assertEqual(whatsapp.rel_time(now - 7300, now), "2h")
        self.assertEqual(whatsapp.rel_time(0, now), "")

    def test_cache_survives_a_restart_and_unlink_wipes_it(self):
        self.connect()
        self.wa.flush()
        again = whatsapp.WhatsApp(self.store, command=["x"])
        self.assertEqual(again.chats["212600000001@s.whatsapp.net"]["messages"][-1]["text"], "are you coming?")
        self.wa.folder = lambda: Path(self.tmp.name) / "data"
        self.wa.handle({"event": "close", "loggedOut": True})
        self.assertEqual(self.wa.chats, {})
        self.assertIsNone(self.store.get("cache"))

    def test_without_node_the_user_is_told_what_to_do(self):
        wa = whatsapp.WhatsApp(self.store)
        wa._node = lambda: None
        self.assertFalse(wa._prepare_helper())
        self.assertIn("Node.js", wa.note)

    def test_old_node_is_refused_and_a_missing_bundle_is_reported(self):
        wa = whatsapp.WhatsApp(self.store)
        wa._node = lambda: "node"
        wa._node_major = lambda node: 18
        self.assertFalse(wa._prepare_helper())
        self.assertIn("too old", wa.note)
        wa._node_major = lambda node: 22
        wa.bundle = lambda: Path(self.tmp.name) / "nope.js"
        self.assertFalse(wa._prepare_helper())
        self.assertIn("missing", wa.note)

    def test_shipped_bundle_exists_and_needs_no_npm(self):
        wa = whatsapp.WhatsApp(self.store)
        self.assertTrue(wa.bundle().is_file())
        wa._node = lambda: "node"
        wa._node_major = lambda node: 22
        self.assertTrue(wa._prepare_helper())          # no npm, no git, no network


class Shipped(unittest.TestCase):
    def test_the_plugin_file_loads_as_an_app_plugin(self):
        from plugins import loader
        for mod, app_id in ((whatsapp, "whatsapp"),):
            plugin = loader._validate(mod.PLUGIN, Path(mod.__file__), "bundled")
            self.assertEqual(plugin.app["id"], app_id)


if __name__ == "__main__":
    unittest.main()
