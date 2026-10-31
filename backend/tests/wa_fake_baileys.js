// Test helper (not shipped logic): `node -r wa_fake_baileys.js bridge.js <auth> <media>` runs the real bridge.js
// against a fake Baileys, so tests/test_wa_bridge_media.py can check what it relays and saves without a network,
// a phone or Node packages. It plays a short scenario about 0.4 s after start (long enough for the test to send
// its {"cmd":"config"} line first), then waits for {"cmd":"quit"}.
"use strict";
const Module = require("module");
const EventEmitter = require("events");

const ev = new EventEmitter();
const JID = "212600000001@s.whatsapp.net";
const GROUP1 = "120363000000000001@g.us";      // its subject comes from groupFetchAllParticipating
const GROUP2 = "120363000000000002@g.us";      // its subject is only found by asking when a message arrives
const OMAR = "212600000009@s.whatsapp.net";    // saved on the phone as "Omar Benali", calls himself "4F6D6172"
const now = () => Math.floor(Date.now() / 1000);
const long = (n) => ({ toNumber: () => n });          // protobuf Long look-alike

const sock = {
  ev: { on: (name, fn) => ev.on(name, fn) },
  user: { id: "1:2@s.whatsapp.net", name: "Me" },
  updateMediaMessage: async (m) => m,
  groupMetadata: async (jid) => { if (jid === GROUP2) return { id: jid, subject: "Lonely group" }; throw new Error("not-authorized"); },
  groupFetchAllParticipating: async () => ({ [GROUP1]: { id: GROUP1, subject: "Study group" } }),
  // what would go out to WhatsApp: the tests read it from stdout
  sendMessage: async (to, content) => {
    process.stdout.write(JSON.stringify({ event: "fake_sent", to, keys: Object.keys(content).sort(),
      bytes: (content.image || content.document || Buffer.alloc(0)).length, fileName: content.fileName || "",
      mimetype: content.mimetype || "", caption: content.caption || "" }) + "\n");
  },
  logout: async () => {},
  end: () => {},
};

const fake = {
  default: () => sock,
  useMultiFileAuthState: async () => ({ state: {}, saveCreds: () => {} }),
  DisconnectReason: { loggedOut: 401 },
  fetchLatestBaileysVersion: async () => { throw new Error("offline"); },
  downloadMediaMessage: async (m) => {
    if (m.key.id === "fail1") throw new Error("media expired");
    const msg = m.message.audioMessage || m.message.imageMessage
      || (m.message.viewOnceMessageV2 && m.message.viewOnceMessageV2.message.imageMessage) || {};
    return Buffer.from("FAKE-" + (msg.mimetype || "?") + "-" + m.key.id);
  },
};

const orig = Module._load;
Module._load = function (request) {
  if (request === "@whiskeysockets/baileys") return fake;
  if (request === "pino") throw new Error("pino is not installed here");   // the bridge has a no-op logger fallback
  return orig.apply(this, arguments);
};

function msg(id, message, extra) {
  return Object.assign({ key: { remoteJid: JID, fromMe: false, id }, pushName: "Alex", messageTimestamp: now(), message }, extra || {});
}

setTimeout(() => {
  ev.emit("connection.update", { connection: "open" });
  ev.emit("contacts.upsert", [
    { id: OMAR, name: "Omar Benali", notify: "4F6D6172" },                 // address-book name must win
    { id: JID, notify: "Alex" },                                           // only a self-chosen name
    { id: "212600000007@s.whatsapp.net", name: "+212 600 000 007" },       // a number is not a name
  ]);
  ev.emit("messages.upsert", {
    type: "notify",
    messages: [
      { key: { remoteJid: GROUP2, participant: OMAR, fromMe: false, id: "g1" }, pushName: "4F6D6172", messageTimestamp: now(),
        message: { conversation: "hello group" } },
    ],
  });
  // delivered while Lyra was closed (not "notify") and an hour old: not fetched on its own...
  ev.emit("messages.upsert", { type: "append", messages: [msg("late1", { imageMessage: { mimetype: "image/jpeg", fileLength: long(1000) } }, { messageTimestamp: now() - 3600 })] });
  // ...but one that is only a minute old is (a delivery type must not decide whether you see a photo you just got)
  ev.emit("messages.upsert", { type: "append", messages: [msg("fresh1", { imageMessage: { mimetype: "image/jpeg", fileLength: long(1000) } }, { messageTimestamp: now() - 60 })] });
  ev.emit("messages.upsert", {
    type: "notify",
    messages: [
      msg("t1", { conversation: "are you coming tonight?" }),
      msg("v1", { audioMessage: { ptt: true, seconds: 12, mimetype: "audio/ogg; codecs=opus", fileLength: long(5000) } }),
      msg("i1", { imageMessage: { mimetype: "image/jpeg", caption: "look at this", width: 640, height: 480, fileLength: long(90000) } }),
      msg("vo1", { viewOnceMessageV2: { message: { imageMessage: { mimetype: "image/jpeg", viewOnce: true } } } }),
      msg("fail1", { imageMessage: { mimetype: "image/jpeg" } }),
      msg("big1", { imageMessage: { mimetype: "image/jpeg", fileLength: long(50 * 1024 * 1024) } }),
      msg("odd1", { audioMessage: { ptt: false, seconds: 3, mimetype: "audio/amr", fileLength: long(100) } }),
    ],
  });
  // History: one recent photo (fetched), one 10 days old (listed only).
  ev.emit("messaging-history.set", {
    chats: [], contacts: [],
    messages: [
      msg("h-new", { imageMessage: { mimetype: "image/png", fileLength: long(1000) } }, { messageTimestamp: now() - 3600 }),
      msg("h-old", { imageMessage: { mimetype: "image/png", fileLength: long(1000) } }, { messageTimestamp: now() - 10 * 86400 }),
    ],
  });
}, 400);
