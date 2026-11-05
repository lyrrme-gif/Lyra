"""Tests for WhatsApp photos, voice messages, toasts, reply suggestions and the voice commands.
From the backend folder:

    python -m unittest tests.test_wa_media -v

No network and no WhatsApp: events are fed in the way bridge.js sends them; speech-to-text and the AI are fakes.
The last class runs the real plugins/whatsapp_bridge/bridge.js against a fake Baileys (needs `node`, else skipped).
"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from engine.plugins import apps, whatsapp

SARA = "212600000001@s.whatsapp.net"
ALEX = "212600000002@s.whatsapp.net"
HERE = Path(__file__).resolve().parent


def wait_for(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = apps.AppStore("whatsapp", self.root / "data")
        self.heard = []
        self.sent = []
        self.wa = whatsapp.WhatsApp(self.store, command=["unused"],
                                    transcriber=self.fake_transcribe, suggester=self.fake_suggest)
        self.wa.folder = lambda: self.root / "data"
        self.wa.open = True
        self.wa.send_cmd = lambda obj: (self.sent.append(obj), True)[1]     # records everything it would send
        self.suggest_calls = []

    def fake_transcribe(self, path):
        self.heard.append(path.name)
        return "can you call me when you are free"

    def fake_suggest(self, name, convo):
        self.suggest_calls.append((name, convo))
        return ["Sure, calling in 5", "Can't right now, later?", "Yes!"]

    def msg(self, mid, text="", chat=SARA, name="Sara", live=True, mine=False, media=None, ts=None, **kw):
        ev = {"event": "message", "chat": chat, "id": mid, "from": "me" if mine else name, "name": name,
              "text": text, "ts": ts or int(time.time()), "mine": mine, "live": live}
        if media:
            ev["media"] = media
        ev.update(kw)
        self.wa.handle(ev)

    def voice(self, mid, chat=SARA, name="Sara", seconds=12):
        self.msg(mid, "[voice message]", chat=chat, name=name,
                 media={"kind": "voice", "mime": "audio/ogg", "seconds": seconds, "size": 5000})

    def saved(self, mid, name="aaaaaaaaaaaaaaaaaaaaaaaa.ogg", chat=SARA, data=b"OggS-fake"):
        d = self.wa.media_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
        self.wa.handle({"event": "media", "chat": chat, "id": mid, "file": name, "bytes": len(data)})


class SpokenTextTests(unittest.TestCase):
    def test_single_message(self):
        self.assertEqual(whatsapp.spoken_summary("Alex", [{"kind": "text", "text": "see you at 8?"}]),
                         "You received a message from Alex saying: “see you at 8?”.")

    def test_two_voice_messages(self):
        t = whatsapp.spoken_summary("Alex", [{"kind": "voice", "seconds": 5}, {"kind": "voice", "seconds": 9}])
        self.assertEqual(t, "You received two voice messages from Alex.")

    def test_voice_with_words_and_photo(self):
        t = whatsapp.spoken_summary("Alex", [{"kind": "voice", "transcript": "call me"},
                                            {"kind": "image", "text": "my new bike"}])
        self.assertIn("You received a voice message from Alex. It says: “call me”.", t)
        self.assertIn("Alex sent you a photo with the caption: “my new bike”.", t)

    def test_voice_without_words_gives_length(self):
        self.assertEqual(whatsapp.spoken_summary("Alex", [{"kind": "voice", "seconds": 12}]),
                         "You received a voice message from Alex, 12 seconds long.")

    def test_helpers(self):
        self.assertEqual(whatsapp.fmt_seconds(75), "1:15")
        self.assertEqual(whatsapp.short("a b c d e f", 5), "a b…")
        self.assertEqual(whatsapp.parse_suggestions('```json\n["a", "b", "a", "c", "d"]\n```'), ["a", "b", "c"])
        self.assertEqual(whatsapp.parse_suggestions("1. yes\n2. no"), ["yes", "no"])


class MediaFlowTests(Base):
    def test_voice_message_is_saved_transcribed_and_shown(self):
        self.voice("v1")
        m = self.wa.chats[SARA]["messages"][0]
        self.assertEqual((m["text"], m["media"]["state"]), ("", "loading"))
        self.saved("v1")
        self.assertTrue(wait_for(lambda: self.wa.chats[SARA]["messages"][0]["media"]["tstate"] == "done"))
        view = apps.sanitize_view(self.wa.view("chat:" + SARA))
        media = view["messages"][0]["media"]
        self.assertEqual((media["kind"], media["state"], media["seconds"]), ("voice", "ready", 12))
        self.assertEqual(media["transcript"], "can you call me when you are free")
        self.assertRegex(media["url"], r"^/apps/media\?id=whatsapp&f=a{24}\.ogg$")

    def test_photo_shows_with_caption_as_text(self):
        self.msg("i1", "[photo] look", media={"kind": "image", "mime": "image/jpeg", "caption": "look", "w": 640, "h": 480})
        self.saved("i1", "bbbbbbbbbbbbbbbbbbbbbbbb.jpg", data=b"\xff\xd8jpeg")
        row = self.wa.view("chat:" + SARA)["messages"][0]
        self.assertEqual(row["text"], "look")
        self.assertEqual((row["media"]["kind"], row["media"]["state"], row["media"]["w"]), ("image", "ready", 640))

    def test_view_once_is_never_loaded(self):
        self.msg("o1", "[view once photo]", media={"kind": "image", "mime": "image/jpeg", "viewOnce": True})
        row = self.wa.view("chat:" + SARA)["messages"][0]
        self.assertEqual((row["text"], row["media"]["state"], row["media"]["url"]), ("[view once photo]", "viewonce", ""))
        self.assertEqual(whatsapp.last_label(self.wa.chats[SARA]["messages"][-1]), "[view once photo]")

    def test_loading_switch_off_means_nothing_is_fetched(self):
        self.store.set("settings", {"media": False})
        self.voice("v1")
        self.assertEqual(self.wa.chats[SARA]["messages"][0]["media"]["state"], "off")
        self.wa._cfg_sent = None
        self.wa._sync_config()
        self.assertIn({"cmd": "config", "media": False}, self.sent)

    def test_transcribe_switch_off_leaves_a_button(self):
        self.store.set("settings", {"transcribe": False})
        self.voice("v1")
        self.saved("v1")
        time.sleep(0.2)
        self.assertEqual(self.heard, [])
        self.assertEqual(self.wa.view("chat:" + SARA)["messages"][0]["media"]["tstate"], "off")
        self.assertTrue(self.wa.action("transcribe", {"chat": SARA, "id": "v1"})["ok"])
        self.assertTrue(wait_for(lambda: self.heard))

    def test_failed_load_is_reported(self):
        self.voice("v1")
        self.wa.handle({"event": "media", "chat": SARA, "id": "v1", "file": "", "error": "media expired"})
        media = self.wa.view("chat:" + SARA)["messages"][0]["media"]
        self.assertEqual((media["state"], media["note"]), ("failed", "media expired"))

    def test_bad_file_names_are_refused(self):
        self.voice("v1")
        self.wa.handle({"event": "media", "chat": SARA, "id": "v1", "file": "../../secret.ogg", "bytes": 1})
        self.assertEqual(self.wa.chats[SARA]["messages"][0]["media"]["state"], "failed")

    def test_stuck_loading_shows_as_failed(self):
        self.voice("v1")
        self.wa.chats[SARA]["messages"][0]["media"]["since"] = time.time() - 500
        self.assertEqual(self.wa.view("chat:" + SARA)["messages"][0]["media"]["state"], "failed")

    def test_restart_does_not_leave_things_loading(self):
        self.voice("v1")
        self.wa.flush()
        again = whatsapp.WhatsApp(self.store, command=["unused"])
        self.assertEqual(again.chats[SARA]["messages"][0]["media"]["state"], "failed")

    def test_chat_list_subtitle(self):
        self.voice("v1")
        row = self.wa.view("")["items"][0]
        self.assertEqual(row["subtitle"], "🎤 Voice message (0:12)")


class MediaFilesTests(Base):
    def test_media_path_only_serves_saved_hex_names(self):
        self.voice("v1")
        self.saved("v1")
        self.assertIsNotNone(self.wa.media_path("aaaaaaaaaaaaaaaaaaaaaaaa.ogg"))
        for bad in ("../data/whatsapp.json", "aaaaaaaaaaaaaaaaaaaaaaaa.exe", "..\\x.ogg", "", "AAAAAAAAAAAAAAAAAAAAAAAA.ogg",
                    "cccccccccccccccccccccccc.ogg"):
            self.assertIsNone(self.wa.media_path(bad), bad)

    def test_sanitizer_only_lets_the_media_url_form_through(self):
        view = apps.sanitize_view({"type": "thread", "title": "x", "messages": [
            {"id": "1", "text": "", "media": {"kind": "image", "state": "ready", "url": "https://evil.example/x.png"}},
            {"id": "2", "text": "", "media": {"kind": "voice", "state": "ready",
                                                "url": "/apps/media?id=whatsapp&f=aaaaaaaaaaaaaaaaaaaaaaaa.ogg"}},
            {"id": "3", "text": "", "media": {"kind": "exe", "state": "ready"}}]})
        a, b, c = view["messages"]
        self.assertEqual((a["media"]["url"], a["media"]["state"]), ("", "failed"))
        self.assertTrue(b["media"]["url"].startswith("/apps/media?"))
        self.assertNotIn("media", c)

    def test_prune_removes_old_orphans_and_marks_expired(self):
        self.voice("v1")
        self.saved("v1")
        old = self.wa.media_dir() / "dddddddddddddddddddddddd.jpg"
        old.write_bytes(b"x")
        long_ago = time.time() - 3 * 3600
        import os
        os.utime(old, (long_ago, long_ago))
        self.assertEqual(self.wa.prune_media(), 1)               # the orphan only
        self.assertFalse(old.exists())
        self.assertEqual(self.wa.prune_media(time.time() + 15 * 86400), 1)      # now the real one is too old
        self.assertEqual(self.wa.chats[SARA]["messages"][0]["media"]["state"], "expired")

    def test_disconnect_deletes_saved_media(self):
        self.voice("v1")
        self.saved("v1")
        self.wa.proc = None
        self.wa.disconnect()
        self.assertFalse(self.wa.media_dir().exists())
        self.assertEqual(self.wa.chats, {})


class ToastTests(Base):
    def test_plain_text_toast_is_the_text(self):
        self.msg("t1", "are you coming?")
        self.assertEqual([(p["sender"], p["text"], p["chat"]) for p in self.wa.poll()], [("Sara", "are you coming?", SARA)])

    def test_voice_toast_waits_for_its_words(self):
        self.voice("v1")
        self.assertEqual(self.wa.poll(), [])                      # still loading: held back
        self.saved("v1")
        self.assertTrue(wait_for(lambda: self.wa.chats[SARA]["messages"][0]["media"]["tstate"] == "done"))
        (toast,) = self.wa.poll()
        self.assertEqual(toast["text"], "🎤 Voice message · “can you call me when you are free”")

    def test_voice_toast_does_not_wait_forever(self):
        self.voice("v1")
        self.wa.incoming[0]["hold"] = time.time() - 1
        (toast,) = self.wa.poll()
        self.assertEqual(toast["text"], "🎤 Voice message (0:12)")

    def test_voice_toast_right_away_when_transcribing_is_off(self):
        self.store.set("settings", {"transcribe": False})
        self.voice("v1")
        self.assertEqual(self.wa.poll()[0]["text"], "🎤 Voice message (0:12)")

    def test_several_messages_become_one_toast_per_chat(self):
        self.store.set("settings", {"transcribe": False})
        self.voice("v1", name="Alex", chat=ALEX)
        self.voice("v2", name="Alex", chat=ALEX)
        self.msg("t1", "hi", chat=SARA)
        by = {p["sender"]: p["text"] for p in self.wa.poll()}
        self.assertEqual(by, {"Alex": "2 voice messages", "Sara": "hi"})

    def test_mixed_toast_and_photo(self):
        self.msg("t1", "look", media={"kind": "image", "mime": "image/jpeg", "caption": "look"})
        self.msg("t2", "also this")
        self.assertEqual(self.wa.poll()[0]["text"], "1 message, 1 photo")

    def test_history_and_own_messages_never_toast(self):
        self.msg("h1", "old", live=False)
        self.msg("m1", "mine", mine=True)
        self.assertEqual(self.wa.poll(), [])

    def test_spoken_text_from_unread(self):
        self.msg("t1", "see you at 8?", chat=ALEX, name="Alex")
        self.assertEqual(self.wa.say_unread(),
                         "You received a message from Alex saying: “see you at 8?”. Say “draft a reply to Alex” if you want a suggestion.")


class ReplyAndVoiceCommandTests(Base):
    def test_suggestions_never_send_anything(self):
        self.msg("t1", "can you call me?")
        res = self.wa.action("suggest", {"chat": SARA})
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["suggestions"]), 3)
        self.assertFalse([c for c in self.sent if c.get("cmd") == "send"])
        self.assertEqual(self.wa.view("chat:" + SARA)["compose"]["draft"], "Sure, calling in 5")      # waits in the reply box
        self.assertEqual(self.wa.view("chat:" + SARA)["compose"]["draft"], "")                       # handed over once

    def test_only_the_send_action_sends_and_needs_text(self):
        self.msg("t1", "hi")
        self.assertFalse(self.wa.action("send", {"chat": SARA, "text": "  "})["ok"])
        self.assertTrue(self.wa.action("send", {"chat": SARA, "text": "on my way"})["ok"])
        self.assertEqual([c for c in self.sent if c.get("cmd") == "send"], [{"cmd": "send", "to": SARA, "text": "on my way"}])

    def test_no_voice_command_sends(self):
        self.msg("t1", "can you call me?", chat=ALEX, name="Alex")
        for phrase in ("any new whatsapp messages", "what did Alex say", "draft a reply to Alex",
                       "reply to Alex on whatsapp", "write a response to Alex on whatsapp"):
            whatsapp.client = lambda wa=self.wa: wa
            self.assertIsNotNone(whatsapp.handler(phrase), phrase)
        self.assertFalse([c for c in self.sent if c.get("cmd") == "send"])

    def test_suggestion_prompt_uses_transcripts_not_audio(self):
        self.voice("v1")
        self.saved("v1")
        self.assertTrue(wait_for(lambda: self.wa.chats[SARA]["messages"][0]["media"]["tstate"] == "done"))
        self.wa.action("suggest", {"chat": SARA})
        name, convo = self.suggest_calls[0]
        self.assertEqual(convo[0]["text"], "[voice message] can you call me when you are free")

    def test_ai_switch_off(self):
        self.store.set("settings", {"ai_replies": False})
        self.msg("t1", "hi")
        self.assertFalse(self.wa.action("suggest", {"chat": SARA})["ok"])
        self.assertEqual(self.suggest_calls, [])
        self.assertNotIn("suggest", self.wa.view("chat:" + SARA)["compose"])

    def test_nothing_to_reply_to(self):
        self.msg("m1", "hello", mine=True)
        self.assertFalse(self.wa.action("suggest", {"chat": SARA})["ok"])

    def test_a_failing_ai_is_a_message_not_a_crash(self):
        def boom(name, convo):
            raise RuntimeError("no AI key is set up yet")
        self.wa.suggester = boom
        self.msg("t1", "hi")
        self.assertFalse(self.wa.action("suggest", {"chat": SARA})["ok"])

    def test_draft_command_opens_the_chat_with_the_draft(self):
        self.msg("t1", "can you call me?", chat=ALEX, name="Alex")
        say = self.wa.say_draft("alex")
        self.assertIn("I haven't sent anything", say)
        v = self.wa.view("")
        self.assertEqual((v["type"], v["compose"]["draft"]), ("thread", "Sure, calling in 5"))
        self.assertEqual(self.wa.view("")["type"], "list")        # only once

    def test_commands_ignore_what_is_not_whatsapp(self):
        whatsapp.client = lambda: self.wa
        self.assertIsNone(whatsapp.handler("what did Einstein say"))
        self.assertIsNone(whatsapp.handler("send Sara a message on whatsapp saying hi"))
        self.assertIsNone(whatsapp.handler("open the chat with Sara on whatsapp"))
        self.assertIsNone(whatsapp.handler("what's the weather"))

    def test_what_did_x_say_and_voice_only(self):
        self.msg("t1", "first", chat=ALEX, name="Alex")
        self.voice("v1", chat=ALEX, name="Alex")
        self.saved("v1", chat=ALEX)
        self.assertTrue(wait_for(lambda: self.wa.chats[ALEX]["messages"][-1]["media"]["tstate"] == "done"))
        whatsapp.client = lambda: self.wa
        say = whatsapp.handler("what did alex say")
        self.assertIn("“first”", say.replace("a message from Alex saying: ", ""))
        self.assertIn("voice message from Alex. It says: “can you call me when you are free”", say)
        self.assertIn("It says", whatsapp.handler("transcribe Alex's voice message"))

    def test_not_connected(self):
        self.wa.open = False
        self.assertIn("isn't connected", self.wa.say_unread())
        self.assertIsNone(self.wa.say_chat("alex"))

    def test_find_chat(self):
        self.msg("t1", "x", chat=ALEX, name="Alex Martin")
        self.assertEqual(self.wa.find_chat("alex"), ALEX)
        self.assertEqual(self.wa.find_chat("my Alex"), ALEX)
        self.assertEqual(self.wa.find_chat("alexx"), ALEX)
        self.assertIsNone(self.wa.find_chat("bob"))


GROUP = "120363405889172332@g.us"
OMAR = "212600000009@s.whatsapp.net"


class NameTests(Base):
    def test_unknown_group_is_not_shown_as_a_number(self):
        self.msg("g1", "hi", chat=GROUP, name="120363405889172332", sender=OMAR)
        self.assertEqual(self.wa.view("")["items"][0]["title"], "Group")
        self.assertEqual(self.wa.view("chat:" + GROUP)["title"], "Group")
        self.assertEqual(whatsapp.display_name("212600000001@s.whatsapp.net", ""), "+212600000001")
        self.assertEqual(whatsapp.display_name("212600000001@s.whatsapp.net", "Sara"), "Sara")

    def test_group_subject_arrives_later_and_is_remembered(self):
        self.msg("g1", "hi", chat=GROUP, name="120363405889172332", sender=OMAR)
        self.wa.handle({"event": "name", "id": GROUP, "name": "Study group"})
        self.assertEqual(self.wa.view("")["items"][0]["title"], "Study group")
        self.assertEqual(self.wa.view("chat:" + GROUP)["title"], "Study group")
        self.wa.flush()
        again = whatsapp.WhatsApp(self.store, command=["unused"])
        again.folder = self.wa.folder
        self.assertEqual(again._title(GROUP), "Study group")           # survives a restart

    def test_sender_names_follow_the_phone_even_for_old_messages(self):
        self.msg("g1", "hi", chat=GROUP, name="Study group", sender=OMAR, **{"from": "4F6D6172"})
        self.assertEqual(self.wa.view("chat:" + GROUP)["messages"][0]["from"], "4F6D6172")
        self.wa.handle({"event": "name", "id": OMAR, "name": "Omar Benali"})
        self.assertEqual(self.wa.view("chat:" + GROUP)["messages"][0]["from"], "Omar Benali")
        self.msg("g2", "again", chat=GROUP, name="Study group", sender=OMAR, **{"from": "4F6D6172"})
        self.assertEqual(self.wa.chats[GROUP]["messages"][-1]["from"], "Omar Benali")

    def test_a_guess_never_replaces_a_name_from_the_phone(self):
        self.wa.handle({"event": "name", "id": SARA, "name": "Sara Benali"})
        self.msg("t1", "hi", name="sara_92")
        self.assertEqual(self.wa.view("")["items"][0]["title"], "Sara Benali")

    def test_numbers_are_not_names_and_forget_clears_them(self):
        self.wa.handle({"event": "name", "id": SARA, "name": "212600000001"})
        self.assertNotIn(SARA, self.wa.names)
        self.wa.handle({"event": "name", "id": SARA, "name": "Sara"})
        self.wa.proc = None
        self.wa.disconnect()
        self.assertEqual(self.wa.names, {})


class LoadMediaTests(Base):
    def test_load_button_asks_the_bridge_and_shows_loading(self):
        self.store.set("settings", {"media": False})
        self.msg("i1", "", media={"kind": "image", "mime": "image/jpeg", "w": 640, "h": 480})
        self.assertEqual(self.wa.view("chat:" + SARA)["messages"][0]["media"]["state"], "off")
        self.assertTrue(self.wa.action("load_media", {"chat": SARA, "id": "i1"})["ok"])
        self.assertIn({"cmd": "download", "chat": SARA, "id": "i1"}, self.sent)
        self.assertEqual(self.wa.view("chat:" + SARA)["messages"][0]["media"]["state"], "loading")
        self.saved("i1", "bbbbbbbbbbbbbbbbbbbbbbbb.jpg", data=b"\xff\xd8x")
        self.assertEqual(self.wa.view("chat:" + SARA)["messages"][0]["media"]["state"], "ready")

    def test_view_once_cannot_be_loaded(self):
        self.msg("o1", "[view once photo]", media={"kind": "image", "mime": "image/jpeg", "viewOnce": True})
        self.assertFalse(self.wa.action("load_media", {"chat": SARA, "id": "o1"})["ok"])
        self.assertFalse([c for c in self.sent if c.get("cmd") == "download"])


class AttachTests(Base):
    PNG = b"\x89PNG\r\n\x1a\n-fake"

    def test_picked_file_is_kept_and_only_sent_on_send(self):
        up = self.wa.save_outgoing("holiday photo.PNG", self.PNG)
        self.assertEqual((up["ok"], up["kind"], up["name"], up["size"]), (True, "image", "holiday photo.PNG", len(self.PNG)))
        self.assertFalse([c for c in self.sent if c.get("cmd") in ("send", "send_media")])       # picking sends nothing
        self.msg("t1", "hi")
        res = self.wa.action("send", {"chat": SARA, "text": "look", "attach": up["token"]})
        self.assertTrue(res["ok"])
        (cmd,) = [c for c in self.sent if c.get("cmd") == "send_media"]
        self.assertEqual((cmd["to"], cmd["caption"], cmd["kind"], cmd["mime"], cmd["name"]), (SARA, "look", "image", "image/png", "holiday photo.PNG"))
        self.assertTrue(cmd["path"].startswith(str(self.wa.outbox_dir())))
        self.assertFalse(self.wa.action("send", {"chat": SARA, "text": "", "attach": up["token"]})["ok"])   # a token works once

    def test_a_file_alone_can_be_sent_without_text(self):
        up = self.wa.save_outgoing("notes.pdf", b"%PDF-1.4 fake")
        self.assertEqual(up["kind"], "file")
        self.assertTrue(self.wa.action("send", {"chat": SARA, "text": "", "attach": up["token"]})["ok"])
        self.assertEqual([c["kind"] for c in self.sent if c.get("cmd") == "send_media"], ["file"])
        self.assertFalse(self.wa.action("send", {"chat": SARA, "text": "", "attach": ""})["ok"])          # nothing to send

    def test_bad_uploads(self):
        self.assertFalse(self.wa.save_outgoing("x.png", b"")["ok"])
        self.assertFalse(self.wa.save_outgoing("x.png", b"0" * (whatsapp.MAX_UPLOAD + 1))["ok"])
        up = self.wa.save_outgoing("..\\..\\evil<>.exe", b"MZ")
        self.assertEqual(up["name"], "evil.exe")
        self.assertEqual(Path(self.wa.outbox[up["token"]]["path"]).parent, self.wa.outbox_dir())    # never leaves the outbox

    def test_unsent_files_are_cleaned_up(self):
        up = self.wa.save_outgoing("a.png", self.PNG)
        path = Path(self.wa.outbox[up["token"]]["path"])
        self.wa._prune_outbox()
        self.assertTrue(path.exists())                                  # fresh: kept for the Send press
        self.wa._prune_outbox(time.time() + whatsapp.OUTBOX_KEEP_S + 5)
        self.assertEqual(self.wa.outbox, {})
        self.assertFalse(path.exists())                                 # nobody sent it within the hour: deleted

    def test_files_left_by_an_earlier_run_are_cleaned_up(self):
        import os
        self.wa.outbox_dir().mkdir(parents=True)
        stray = self.wa.outbox_dir() / "ffffffffffffffff.png"
        stray.write_bytes(b"x")
        os.utime(stray, (1, 1))
        self.wa._prune_outbox()
        self.assertFalse(stray.exists())

    def test_failed_send_is_shown_as_a_notice(self):
        self.msg("t1", "hi")
        self.assertEqual(self.wa.view("chat:" + SARA)["notice"], "")
        self.wa.handle({"event": "sent", "to": SARA, "ok": False, "error": "too large"})
        self.assertEqual(apps.sanitize_view(self.wa.view("chat:" + SARA))["notice"], "Not sent: too large")
        self.assertTrue(self.wa.action("send", {"chat": SARA, "text": "again"})["ok"])
        self.assertEqual(self.wa.view("chat:" + SARA)["notice"], "")

    def test_thread_offers_the_plus_button(self):
        self.msg("t1", "hi")
        self.assertTrue(apps.sanitize_view(self.wa.view("chat:" + SARA))["compose"]["attach"])

    def test_disconnect_removes_picked_files(self):
        self.wa.save_outgoing("a.png", self.PNG)
        self.wa.proc = None
        self.wa.disconnect()
        self.assertFalse(self.wa.outbox_dir().exists())


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class RealBridgeTests(unittest.TestCase):
    """plugins/whatsapp_bridge/bridge.js itself, against a fake Baileys."""

    def run_bridge(self, media_on, extra=(), setup=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = Path(tmp)
        if setup:
            setup(self.tmp)
        bridge = HERE.parent.parent / "plugins" / "whatsapp_bridge" / "bridge.js"
        proc = subprocess.Popen(["node", "-r", str(HERE / "wa_fake_baileys.js"), str(bridge), tmp + "/auth", tmp + "/media", tmp + "/outbox"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        proc.stdin.write(json.dumps({"cmd": "config", "media": media_on}) + "\n")
        proc.stdin.flush()
        time.sleep(2.5)
        for cmd in (extra(self.tmp) if callable(extra) else extra):
            proc.stdin.write(json.dumps(cmd) + "\n")
            proc.stdin.flush()
            time.sleep(0.6)
        proc.stdin.write('{"cmd":"quit"}\n')
        proc.stdin.flush()
        out, _ = proc.communicate(timeout=15)
        events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
        return events, Path(tmp) / "media"

    def test_media_on(self):
        events, media = self.run_bridge(True)
        msgs = {e["id"]: e for e in events if e["event"] == "message"}
        self.assertEqual(msgs["v1"]["media"], {"kind": "voice", "mime": "audio/ogg", "seconds": 12, "size": 5000, "viewOnce": False})
        self.assertEqual(msgs["i1"]["media"]["caption"], "look at this")
        self.assertTrue(msgs["vo1"]["media"]["viewOnce"])
        self.assertEqual(msgs["vo1"]["text"], "[view once photo]")
        self.assertNotIn("media", msgs["t1"])
        got = {e["id"]: e for e in events if e["event"] == "media"}
        self.assertTrue(got["v1"]["file"].endswith(".ogg") and got["i1"]["file"].endswith(".jpg"))
        self.assertEqual(got["fail1"]["error"], "media expired")
        self.assertEqual(got["big1"]["error"], "too large")
        self.assertEqual(got["odd1"]["error"], "unsupported type")
        self.assertEqual(got["h-old"]["error"], "older message")
        self.assertNotIn("vo1", got)                                  # view-once: nothing saved, nothing pending
        self.assertTrue(got["h-new"]["file"])
        self.assertEqual(got["late1"]["error"], "older message")       # an hour old, delivered late: not fetched on its own
        self.assertTrue(got["fresh1"]["file"])                         # a minute old: fetched whatever the delivery type
        self.assertEqual(len(list(media.iterdir())), 4)                # v1, i1, h-new, fresh1
        for e in got.values():
            self.assertRegex(e["file"], r"^([0-9a-f]{24}\.[a-z0-9]{3,4})?$")

    def test_tapping_load_fetches_an_old_one_even_with_loading_off(self):
        events, media = self.run_bridge(False, extra=[{"cmd": "download", "chat": "212600000001@s.whatsapp.net", "id": "late1"},
                                                      {"cmd": "download", "chat": "212600000001@s.whatsapp.net", "id": "nope"}])
        got = {e["id"]: e for e in events if e["event"] == "media"}
        self.assertTrue(got["late1"]["file"].endswith(".jpg"))
        self.assertEqual(got["nope"]["error"], "not available any more")
        self.assertEqual(len(list(media.iterdir())), 1)

    def test_names_come_from_the_phone_first(self):
        events, _ = self.run_bridge(False)
        names = {}
        for e in events:
            if e["event"] == "name":
                names.setdefault(e["id"], []).append(e["name"])
        self.assertEqual(names["212600000009@s.whatsapp.net"][-1], "Omar Benali")    # not "4F6D6172"
        self.assertEqual(names["212600000001@s.whatsapp.net"][-1], "Alex")             # only a push name: better than a number
        self.assertNotIn("212600000007@s.whatsapp.net", names)                         # a phone number is not a name
        self.assertEqual(names["120363000000000001@g.us"], ["Study group"])            # from the group list
        self.assertEqual(names["120363000000000002@g.us"], ["Lonely group"])           # asked for when its first message came
        msg = next(e for e in events if e["event"] == "message" and e["id"] == "g1")
        self.assertEqual((msg["from"], msg["sender"]), ("Omar Benali", "212600000009@s.whatsapp.net"))

    def test_send_media_sends_an_outbox_file_then_deletes_it(self):
        sara = "212600000001@s.whatsapp.net"
        outside = {}

        def setup(tmp):
            (tmp / "outbox").mkdir()
            (tmp / "outbox" / "a1.png").write_bytes(b"\x89PNG-fake")
            (tmp / "outbox" / "d1.pdf").write_bytes(b"%PDF-fake-bytes")
            (tmp / "precious.txt").write_text("not for WhatsApp")
            outside["path"] = tmp / "precious.txt"

        def cmds(tmp):
            o = tmp / "outbox"
            return [
                {"cmd": "send_media", "to": sara, "path": str(o / "a1.png"), "name": "a1.png", "mime": "image/png", "kind": "image", "caption": "look"},
                {"cmd": "send_media", "to": sara, "path": str(o / "d1.pdf"), "name": "d1.pdf", "mime": "application/pdf", "kind": "file"},
                {"cmd": "send_media", "to": sara, "path": str(tmp / "precious.txt"), "name": "p.txt", "mime": "text/plain", "kind": "file"},
                {"cmd": "send_media", "to": sara, "path": str(o / ".." / "precious.txt"), "name": "p.txt", "mime": "text/plain", "kind": "file"},
            ]
        events, _ = self.run_bridge(False, setup=setup, extra=cmds)
        sent = [e for e in events if e["event"] == "fake_sent"]
        self.assertEqual(len(sent), 2)
        self.assertEqual((sent[0]["keys"], sent[0]["caption"], sent[0]["bytes"]), (["caption", "image"], "look", 9))
        self.assertEqual((sent[1]["keys"], sent[1]["fileName"], sent[1]["mimetype"]), (["caption", "document", "fileName", "mimetype"], "d1.pdf", "application/pdf"))
        answers = [e for e in events if e["event"] == "sent"]
        self.assertEqual([a["ok"] for a in answers], [True, True, False, False])
        self.assertEqual(answers[2]["error"], "not an outgoing file")
        self.assertEqual(list((self.tmp / "outbox").iterdir()), [])          # what was sent is gone
        self.assertTrue(outside["path"].exists())                            # a path outside the outbox is never read or deleted

    def test_media_off_downloads_nothing(self):
        events, media = self.run_bridge(False)
        self.assertFalse([e for e in events if e["event"] == "media"])
        self.assertFalse(media.exists())
        self.assertTrue([e for e in events if e["event"] == "message" and e.get("media")])


if __name__ == "__main__":
    unittest.main()
