// Lyra <-> WhatsApp bridge. Links to your account as a linked device, exactly like WhatsApp Web:
// you scan a QR code once in Lyra and this process then relays chats and messages.
//
// Talks to plugins/whatsapp.py in JSON lines. Usage:  node bridge.js <auth folder> [media folder]
//
//   out (stdout):  {"event":"qr","qr":"..."}   {"event":"open","me":{"id","name"}}
//                  {"event":"close","loggedOut":bool}   {"event":"chat","id","name","ts"}
//                  {"event":"name","id","name"}   a better name for a chat, group or person (saved on the phone > their own)
//                  {"event":"message","chat","id","from","sender","name","text","ts","mine","live","media"?}
//                      media = {"kind":"voice"|"audio"|"image","mime","seconds","size","w","h","caption","viewOnce"}
//                  {"event":"media","chat","id","file","bytes"}   the photo / voice message was saved as <media folder>/<file>
//                  {"event":"media","chat","id","file":"","error":"..."}   it could not be loaded
//                  {"event":"error","message":"..."}
//   in  (stdin):   {"cmd":"send","to":"<jid>","text":"..."}   {"cmd":"config","media":bool}
//                  {"cmd":"download","chat":"<jid>","id":"<message id>"}   load one photo / voice message now (a tap on "Load")
//                  {"cmd":"send_media","to","path","name","mime","kind","caption"}   send one file from the outbox folder
//                  (answered with {"event":"sent","to","ok":bool,"error"?})
//                  {"cmd":"logout"}   {"cmd":"quit"}
//
// Media: photos and voice messages are only downloaded when a media folder is given AND Lyra has sent
// {"cmd":"config","media":true} (the user's "Load photos and voice messages" switch). View-once media is never
// saved: the sender chose that it can be seen once, so Lyra only says that one arrived.
//
// Unofficial: WhatsApp does not allow third-party clients. Use it to read and answer your own chats,
// not for bulk or automated sending (that is what gets accounts banned).
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");

const authDir = process.argv[2];
if (!authDir) {
  console.error("usage: node bridge.js <auth folder> [media folder]");
  process.exit(2);
}
const mediaDir = process.argv[3] || "";   // where photos and voice messages are saved; "" = never download
const outboxDir = process.argv[4] || "";  // where Lyra puts a file the user chose to send; the helper deletes it after sending

function out(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

let baileys;
try {
  baileys = require("@whiskeysockets/baileys");
} catch (e) {
  out({ event: "error", message: "Baileys is not installed: " + e.message });
  process.exit(3);
}
const makeWASocket = baileys.default || baileys.makeWASocket;
const { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = baileys;

let logger;
try {
  logger = require("pino")({ level: "silent" });
} catch (_) {
  const noop = () => {};
  logger = { level: "silent", trace: noop, debug: noop, info: noop, warn: noop, error: noop, fatal: noop, child: () => logger };
}

const book = new Map();      // jid -> the name saved on the phone (address book), a group's subject, a business name
const push = new Map();      // jid -> the name the person chose for themselves in WhatsApp
const told = new Map();      // jid -> the last name sent to Lyra (so each change is sent once)
let sock = null;
let stopping = false;
let retry = 0;   // reconnect delay grows while offline: 2 s, 4 s ... 30 s
let mediaOn = false;        // the user's "Load photos and voice messages" switch (Lyra sends it with {"cmd":"config"})
let historyBudget = 0;      // how many older photos / voice messages one history sync may still download

function textOf(m) {
  const { msg, viewOnce } = unwrap(m);
  if (!msg) return null;
  if (viewOnce && msg.imageMessage) return "[view once photo]";
  if (viewOnce && msg.videoMessage) return "[view once video]";
  if (viewOnce && msg.audioMessage) return "[view once voice message]";
  if (msg.conversation) return msg.conversation;
  if (msg.extendedTextMessage && msg.extendedTextMessage.text) return msg.extendedTextMessage.text;
  if (msg.imageMessage) return msg.imageMessage.caption ? "[photo] " + msg.imageMessage.caption : "[photo]";
  if (msg.videoMessage) return msg.videoMessage.caption ? "[video] " + msg.videoMessage.caption : "[video]";
  if (msg.audioMessage) return msg.audioMessage.ptt ? "[voice message]" : "[audio]";
  if (msg.documentMessage) return "[file] " + (msg.documentMessage.fileName || "");
  if (msg.stickerMessage) return "[sticker]";
  if (msg.contactMessage) return "[contact]";
  if (msg.locationMessage) return "[location]";
  return null;   // protocol messages, reactions, receipts...: nothing to show
}

// ── Photos and voice messages ───────────────────────────────────────────────────────────────────────

const MAX_BYTES = { image: 12 * 1024 * 1024, voice: 12 * 1024 * 1024, audio: 25 * 1024 * 1024 };
const HISTORY_MAX_AGE_S = 3 * 86400;    // older chat history is listed, but its media is not fetched
const EXT = {
  "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
  "audio/ogg": ".ogg", "audio/opus": ".ogg", "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
  "audio/mp4": ".m4a", "audio/aac": ".m4a", "audio/x-m4a": ".m4a",
};

function num(v) {   // protobuf numbers can be Long objects
  if (v && typeof v.toNumber === "function") return v.toNumber();
  return Number(v) || 0;
}

/** The message without its wrappers, and whether it was "view once". */
function unwrap(m) {
  let msg = m && m.message;
  let viewOnce = false;
  for (let i = 0; i < 5 && msg; i++) {
    if (msg.ephemeralMessage) msg = msg.ephemeralMessage.message;
    else if (msg.viewOnceMessage) { viewOnce = true; msg = msg.viewOnceMessage.message; }
    else if (msg.viewOnceMessageV2) { viewOnce = true; msg = msg.viewOnceMessageV2.message; }
    else if (msg.viewOnceMessageV2Extension) { viewOnce = true; msg = msg.viewOnceMessageV2Extension.message; }
    else if (msg.documentWithCaptionMessage) msg = msg.documentWithCaptionMessage.message;
    else break;
  }
  if (msg && ((msg.imageMessage && msg.imageMessage.viewOnce) || (msg.audioMessage && msg.audioMessage.viewOnce))) viewOnce = true;
  return { msg: msg || null, viewOnce };
}

/** What kind of media a message carries (photos and voice messages / audio files), or null. */
function mediaOf(m) {
  const { msg, viewOnce } = unwrap(m);
  if (!msg) return null;
  if (msg.audioMessage) {
    const a = msg.audioMessage;
    return { kind: a.ptt ? "voice" : "audio", mime: String(a.mimetype || "audio/ogg").split(";")[0].trim().toLowerCase(),
             seconds: num(a.seconds), size: num(a.fileLength), viewOnce };
  }
  if (msg.imageMessage) {
    const i = msg.imageMessage;
    return { kind: "image", mime: String(i.mimetype || "image/jpeg").split(";")[0].trim().toLowerCase(),
             size: num(i.fileLength), w: num(i.width), h: num(i.height), caption: String(i.caption || "").slice(0, 1000), viewOnce };
  }
  return null;
}

const dlQueue = [];
const inflight = new Set();          // "chat|id" being fetched or waiting: never fetch one twice
const recentMedia = new Map();       // "chat|id" -> {m, media}: the newest 300, for the on-demand "Load" button
const FRESH_S = 600;                 // a message this young was just received, however WhatsApp delivered it
let dlRunning = false;

function withTimeout(promise, ms) {
  let timer;
  const limit = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("timed out")), ms); });
  return Promise.race([promise, limit]).finally(() => clearTimeout(timer));
}

async function runDownload(job) {
  const { m, chat, media } = job;
  const id = (m.key && m.key.id) || "";
  try {
    const ext = EXT[media.mime];
    if (!ext) throw new Error("unsupported type " + media.mime);
    const buf = await withTimeout(baileys.downloadMediaMessage(m, "buffer", {},
      { logger, reuploadRequest: sock && sock.updateMediaMessage }), 60000);
    if (!buf || !buf.length) throw new Error("empty");
    if (buf.length > (MAX_BYTES[media.kind] || MAX_BYTES.image)) throw new Error("too large");
    fs.mkdirSync(mediaDir, { recursive: true });
    const file = crypto.randomBytes(12).toString("hex") + ext;   // random name: nothing to guess
    fs.writeFileSync(path.join(mediaDir, file), buf, { mode: 0o600 });
    out({ event: "media", chat, id, file, bytes: buf.length });
  } catch (e) {
    out({ event: "media", chat, id, file: "", error: String((e && e.message) || e).slice(0, 120) });
  } finally {
    inflight.delete(chat + "|" + id);
  }
}

async function pumpDownloads() {
  if (dlRunning) return;
  dlRunning = true;
  try {
    while (dlQueue.length) await runDownload(dlQueue.shift());
  } finally {
    dlRunning = false;
  }
}

/** Fetch a photo / voice message in the background when allowed. Never for view-once media.
 *  While loading is on, every other media message gets exactly one {"event":"media"} follow-up: the saved file,
 *  or the reason it was not loaded, so the notch never waits on something that is not coming.
 *  `force` = the user tapped "Load" for this one: allowed even when automatic loading is off or the message is old. */
function maybeDownload(m, chat, media, live, ts, force) {
  if (!mediaDir || !media || media.viewOnce || !m.key || !m.key.id) return;
  if (!force && !mediaOn) return;
  const id = m.key.id;
  const key = chat + "|" + id;
  const skip = (why) => out({ event: "media", chat, id, file: "", error: why });
  if (inflight.has(key)) return;
  if (!EXT[media.mime]) return skip("unsupported type");
  if (media.size && media.size > (MAX_BYTES[media.kind] || MAX_BYTES.image)) return skip("too large");
  if (!force && !live) {
    const age = Math.floor(Date.now() / 1000) - ts;
    if (age > HISTORY_MAX_AGE_S) return skip("older message");
    if (age > FRESH_S) {
      if (historyBudget <= 0) return skip("older message");
      historyBudget--;
    }
  }
  if (dlQueue.length >= 100) {
    const dropped = dlQueue.shift();
    inflight.delete(dropped.chat + "|" + dropped.m.key.id);
    out({ event: "media", chat: dropped.chat, id: dropped.m.key.id, file: "", error: "too many waiting" });
  }
  inflight.add(key);
  dlQueue.push({ m, chat, media });
  void pumpDownloads();
}

/** A chat's "name" is sometimes just its phone number: that is not a name. */
function isRealName(nm) {
  return typeof nm === "string" && /[^\d\s+()\-.]/.test(nm);
}

function bestName(jid) {
  return book.get(jid) || push.get(jid) || "";
}

const MAX_SEND = 16 * 1024 * 1024;

/** Send one file the user picked. Only files inside the outbox folder are read, and each is deleted afterwards. */
async function sendMedia(cmd) {
  const to = String(cmd.to);
  let file = "";          // set only once the path is known to be inside the outbox: only then may it be deleted
  try {
    if (!sock) throw new Error("not connected");
    if (!outboxDir) throw new Error("sending files is not set up");
    const wanted = path.resolve(String(cmd.path || ""));
    if (!wanted.startsWith(path.resolve(outboxDir) + path.sep)) throw new Error("not an outgoing file");
    file = wanted;
    const buf = fs.readFileSync(file);
    if (!buf.length || buf.length > MAX_SEND) throw new Error("too large");
    const mime = String(cmd.mime || "application/octet-stream").toLowerCase();
    const caption = String(cmd.caption || "").slice(0, 1000) || undefined;
    const content = cmd.kind === "image" && /^image\/(jpeg|png|webp)$/.test(mime)
      ? { image: buf, caption }
      : { document: buf, mimetype: mime, fileName: String(cmd.name || "file").slice(0, 120), caption };
    await sock.sendMessage(to, content);
    out({ event: "sent", to, ok: true });
  } catch (e) {
    out({ event: "sent", to, ok: false, error: String((e && e.message) || e).slice(0, 120) });
  } finally {
    if (file) { try { fs.unlinkSync(file); } catch (_) { /* already gone */ } }
  }
}

function nameOf(jid) {
  return bestName(jid) || String(jid || "").split("@")[0].split(":")[0];
}

/** Tell Lyra when we learn (or improve) the name of a jid. Only real names: never the bare number. */
function announceName(jid) {
  const nm = bestName(jid);
  if (nm && told.get(jid) !== nm) {
    told.set(jid, nm);
    out({ event: "name", id: jid, name: String(nm).slice(0, 80) });
  }
}

const groupAsked = new Set();
/** A group whose subject we do not know yet: ask WhatsApp once (again after 10 minutes if that failed). */
function ensureGroup(chat) {
  if (!sock || !chat.endsWith("@g.us") || book.has(chat) || groupAsked.has(chat)) return;
  groupAsked.add(chat);
  Promise.resolve().then(() => sock.groupMetadata(chat)).then((g) => {
    if (g && g.subject) { book.set(chat, g.subject); announceName(chat); }
  }).catch(() => {}).finally(() => setTimeout(() => groupAsked.delete(chat), 600000));
}

async function fetchGroups() {
  if (!sock) return;
  try {
    const all = await sock.groupFetchAllParticipating();
    for (const g of Object.values(all || {})) {
      if (g && g.id && g.subject) { book.set(g.id, g.subject); announceName(g.id); }
    }
  } catch (_) { /* rate limited or offline: the next open tries again */ }
}

function relay(m, live) {
  if (!m || !m.key || !m.key.remoteJid) return;
  const chat = m.key.remoteJid;
  if (chat === "status@broadcast" || chat.endsWith("@newsletter")) return;
  const text = textOf(m);
  if (text === null) return;
  const mine = !!m.key.fromMe;
  // Was I @-mentioned? (used to let a muted group through). Best effort: ids can come as phone or LID form.
  const ctx = (m.message && m.message.extendedTextMessage && m.message.extendedTextMessage.contextInfo) || {};
  const me = String((sock && sock.user && sock.user.id) || "").split(":")[0].split("@")[0];
  const mentioned = !!me && (ctx.mentionedJid || []).some((j) => String(j).split(":")[0].split("@")[0] === me);
  const sender = m.key.participant || chat;
  if (!mine && m.pushName) { push.set(sender, m.pushName); announceName(sender); }   // group members too
  ensureGroup(chat);
  const ts = Number(m.messageTimestamp && m.messageTimestamp.toNumber ? m.messageTimestamp.toNumber() : m.messageTimestamp) || Math.floor(Date.now() / 1000);
  const media = mediaOf(m);
  out({
    event: "message", chat, id: m.key.id || "", from: mine ? "me" : nameOf(sender), sender,
    name: nameOf(chat), text: String(text).slice(0, 2000), ts, mine, mentioned, live: !!live,
    media: media || undefined,
  });
  if (media && !media.viewOnce && m.key.id) {      // kept for a while so a tap on "Load" can still fetch it
    recentMedia.set(chat + "|" + m.key.id, { m, media });
    if (recentMedia.size > 300) recentMedia.delete(recentMedia.keys().next().value);
  }
  maybeDownload(m, chat, media, live, ts, false);
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  let version;
  try {
    version = (await fetchLatestBaileysVersion()).version;
  } catch (_) { /* offline lookup failed: Baileys falls back to its built-in version */ }
  sock = makeWASocket({
    auth: state, logger, version, browser: ["Lyra", "Desktop", "1.0"],
    printQRInTerminal: false, syncFullHistory: false, markOnlineOnConnect: false,
  });
  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (u) => {
    if (u.qr) out({ event: "qr", qr: u.qr });
    if (u.connection === "open") {
      retry = 0;
      const me = sock.user || {};
      out({ event: "open", me: { id: me.id || "", name: me.name || "" } });
      setTimeout(() => void fetchGroups(), 1500);     // the subjects of all your groups
    }
    if (u.connection === "close") {
      const code = u.lastDisconnect && u.lastDisconnect.error && u.lastDisconnect.error.output
        ? u.lastDisconnect.error.output.statusCode : 0;
      const loggedOut = code === DisconnectReason.loggedOut;
      out({ event: "close", loggedOut, code });
      if (loggedOut) {
        try { fs.rmSync(authDir, { recursive: true, force: true }); } catch (_) {}
        process.exit(0);
      }
      const wait = Math.min(30000, 2000 * Math.pow(2, retry++));
      if (!stopping) setTimeout(() => start().catch((e) => out({ event: "error", message: String(e && e.message || e) })), wait);
    }
  });

  // Names: what is saved on the phone wins over what the person calls themselves. Contacts may also come
  // under a second id (a LID or phone number form), so the name is kept for each.
  const learn = (list) => {
    for (const c of list || []) {
      if (!c || !c.id) continue;
      const saved = [c.name, c.verifiedName].find(isRealName);
      for (const id of [c.id, c.lid, c.phoneNumber].filter((x) => typeof x === "string" && x)) {
        if (saved) book.set(id, saved);
        if (c.notify) push.set(id, c.notify);
        announceName(id);
      }
    }
  };
  const learnGroups = (list) => {
    for (const g of list || []) {
      if (g && g.id && g.subject) { book.set(g.id, g.subject); announceName(g.id); }
    }
  };
  sock.ev.on("groups.upsert", learnGroups);
  sock.ev.on("groups.update", learnGroups);
  sock.ev.on("contacts.upsert", learn);
  sock.ev.on("contacts.update", learn);
  sock.ev.on("chats.upsert", (chats) => {
    learn(chats);
    for (const c of chats || []) if (c.id) out({ event: "chat", id: c.id, name: nameOf(c.id), ts: Number(c.conversationTimestamp) || 0 });
  });
  sock.ev.on("messaging-history.set", ({ chats, contacts, messages }) => {
    learn(contacts); learn(chats);
    historyBudget = 30;                                       // a few recent photos / voice messages, not the whole archive
    for (const m of (messages || []).slice(0, 400)) relay(m, false);   // history: shown in the list, never as a toast
  });
  sock.ev.on("messages.upsert", ({ messages, type }) => {
    for (const m of messages || []) relay(m, type === "notify");
  });
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  let cmd;
  try { cmd = JSON.parse(line); } catch (_) { return; }
  try {
    if (cmd.cmd === "send" && sock && cmd.to && cmd.text) {
      await sock.sendMessage(String(cmd.to), { text: String(cmd.text).slice(0, 4000) });
    } else if (cmd.cmd === "send_media" && cmd.to) {
      await sendMedia(cmd);
    } else if (cmd.cmd === "config") {
      if (typeof cmd.media === "boolean") mediaOn = cmd.media;
    } else if (cmd.cmd === "download") {
      const chat = String(cmd.chat || ""), id = String(cmd.id || "");
      const hit = recentMedia.get(chat + "|" + id);
      if (!hit) out({ event: "media", chat, id, file: "", error: "not available any more" });
      else maybeDownload(hit.m, chat, hit.media, true, 0, true);
    } else if (cmd.cmd === "logout" && sock) {
      await sock.logout();
    } else if (cmd.cmd === "quit") {
      stopping = true;
      try { sock && sock.end(undefined); } catch (_) {}
      process.exit(0);
    }
  } catch (e) {
    out({ event: "error", message: String(e && e.message || e) });
  }
});
rl.on("close", () => process.exit(0));   // Lyra went away: do not stay behind

start().catch((e) => {
  out({ event: "error", message: String(e && e.message || e) });
  process.exit(1);
});
