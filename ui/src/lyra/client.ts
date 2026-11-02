import type { Island } from "../island/island";
import { lyraStateToHint, type LyraState } from "./bridge";
import { pushReply } from "./chatBus";
import { isMochiKind, playMochi, playMochiIfOn } from "../core/mochiSound";
import { State } from "../core/state";
import { tourSignal } from "../core/tour";
import { Chats } from "./chatStore";
import { setTranslatePrefill } from "../views/translate";
import type { TimersPayload } from "../island/timerChip";
import { adaptivePoll, noteEvents } from "../core/pollLoop";
import { visiblePoll } from "../core/visiblePoll";
import { trace, traceBackendEvent } from "../core/trace";

const BASE = "http://127.0.0.1:8765";
let since = 0;
/** The first batch after (re)start is history: state yes, old chat/toasts no. */
let backlog = true;
/** A slow answer makes the Mochi think out loud (once, and only if it is still thinking after a moment). */
let thinkTimer = 0;
const THINK_SOUND_AFTER_MS = 1500;

export function sendChat(text: string): void {
  trace("chat", `message sent to Lyra (${text.length} chars, text not logged)`);
  fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "chat", text }),
  }).catch(() => {
    window.dispatchEvent(new CustomEvent("lyra:typed-failed"));
    pushReply("Backend offline — run python main.py in backend/");
  });
}




export function connectLyra(island: Island): void {
  // Seed battery (and privacy) once so the expanded Island can show it immediately.
  void (async () => {
    try {
      const res = await fetch(`${BASE}/status`);
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.battery && typeof data.battery.percent === "number") {
        State.battery = {
          percent: Number(data.battery.percent),
          charging: data.battery.charging === true,
        };
        State.notify();
      }
    } catch { /* offline */ }
  })();

  const poll = async () => {
    try {
      const res = await fetch(`${BASE}/events?since=${since}`);
      if (!res.ok) throw new Error(String(res.status));
      const data = (await res.json()) as {
        events?: Array<{
          type: string; state?: string; role?: string; text?: string; _id?: number; local?: boolean;
          levels?: number[]; hz?: number; start_ms?: number; final?: boolean;
          app?: string; kind?: string; sender?: string; ts?: number;
          items?: unknown[]; now?: number;
          percent?: number; charging?: boolean; level?: number; muted?: boolean; mic?: boolean;
          mochi?: string; mochi_subtitle?: string; mochi_silent?: boolean; mochi_ms?: number;
          title?: string; subtitle?: string; phase?: string; icon_url?: string; progress?: number; duration_ms?: number; soft?: boolean; available?: boolean; ok?: boolean; answer?: string; query?: string; provider?: string; results?: unknown[]; card?: unknown; artist?: string; album?: string; artwork_url?: string; spotify_url?: string; youtube_url?: string; message?: string; source?: string; open?: boolean; view?: string; action?: string; on?: boolean; wake?: string;
        }>;
        next?: number;
      };
      if (typeof data.next === "number") since = data.next;
      noteEvents(data.events?.length ?? 0);
      const replaying = backlog;
      if (backlog) {
        backlog = false;
        const keepClip = State.extras.keepClipboard;
        const post = (path: string, body: object) => fetch(`${BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => undefined);
        void post("/clipboard/keep", { keep: keepClip });
        if (!keepClip) void post("/clipboard/clear", {});
      }
      for (const msg of data.events || []) {
        traceBackendEvent(msg as Record<string, unknown>, replaying);
        if (replaying && ((msg.type === "chat" && !State.extras.keepChat) || msg.type === "notify" || msg.type === "reply")) continue;
        if (msg.type === "state" && msg.state) {
          applyState(island, msg.state as LyraState);
        }
        // The words you are saying, live (a fast guess), then the accurate text; and what dictation is doing.
        if (msg.type === "live_text") {
          if (!replaying && (!msg.ts || Date.now() - Number(msg.ts) < 4_000)) island.onLiveText(String(msg.text || ""), msg.final === true, String(msg.source || ""));
          continue;
        }
        if (msg.type === "circle_pill") {
          if (!replaying && (!msg.ts || Date.now() - Number(msg.ts) < 10_000)) island.onCirclePill(String(msg.state || ""));
          continue;
        }
        if (msg.type === "dictation_pill") {
          if (!replaying && (!msg.ts || Date.now() - Number(msg.ts) < 10_000)) island.onDictationPill(String(msg.state || ""), String(msg.app || ""));
          continue;
        }
        // Loudness of the sentence Lyra is saying: the white wave on the island follows it. A Mochi sound never sends one.
        if (msg.type === "wave") {
          if (!replaying) island.feedWave(Array.isArray(msg.levels) ? msg.levels : [], Number(msg.hz) || 25, Number(msg.start_ms) || Date.now());
          continue;
        }
        // A Mochi sound that is not a reply (tired, overheating ...): the backend already checked the setting.
        if (msg.type === "mochi_sound") {
          if (!replaying && isMochiKind(msg.kind) && (!msg.ts || Date.now() - Number(msg.ts) < 10_000)) playMochi(msg.kind);
          continue;
        }
        if (msg.type === "notify") {
          // Windows toast / reminder → the notch.
          const kind = msg.kind === "call" || msg.kind === "reminder" || msg.kind === "dictation" ? msg.kind : "message";
          island.onNotify({
            app: String(msg.app || ""),
            kind,
            sender: String(msg.sender || ""),
            text: String(msg.text || ""),
            ts: Number(msg.ts) || Date.now(),
            durationMs: Number(msg.duration_ms) || undefined,
            chat: String(msg.chat || "") || undefined,
          });
          continue;
        }
        if (msg.type === "status" && msg.kind === "brightness") {
          island.onBrightness(Number(msg.percent) || 0, Number(msg.ts) || undefined);
          continue;
        }
        if (msg.type === "status" && msg.kind === "bluetooth") {
          island.onBluetooth({ text: String(msg.text || ""), ts: Number(msg.ts) || undefined });
          continue;
        }
        if (msg.type === "status" && msg.kind === "battery") {
          if (msg.available === false) {
            State.battery = null;
            State.notify();
            continue;
          }
          const pct = Math.max(0, Math.min(100, Number(msg.percent) || 0));
          const charging = msg.charging === true;
          State.battery = { percent: pct, charging };
          State.notify();
          // Soft percent drifts update the expanded chip only — no compact HUD.
          if (!(msg as { soft?: boolean }).soft) {
            island.onStatus({
              text: String(msg.text || ""),
              percent: pct,
              charging,
              ts: Number(msg.ts) || undefined,
            });
          }
          continue;
        }
        if (msg.type === "activity") {
          island.onActivity({
            kind: String(msg.kind || "info"),
            phase: String(msg.phase || "info"),
            title: String(msg.title || ""),
            subtitle: msg.subtitle ? String(msg.subtitle) : undefined,
            app: msg.app ? String(msg.app) : undefined,
            iconUrl: msg.icon_url ? String(msg.icon_url) : undefined,
            url: msg.url ? String(msg.url) : undefined,
            progress: Number.isFinite(Number(msg.progress)) ? Number(msg.progress) : undefined,
            durationMs: Number(msg.duration_ms) || undefined,
            ts: Number(msg.ts) || undefined,
          });
          continue;
        }
        if (msg.type === "music_recognition") {
          island.onMusicRecognition({
            ok: msg.ok !== false,
            title: String(msg.title || ""),
            artist: String(msg.artist || ""),
            album: String(msg.album || ""),
            artworkUrl: String(msg.artwork_url || ""),
            spotifyUrl: String(msg.spotify_url || ""),
            youtubeUrl: String(msg.youtube_url || ""),
            message: String(msg.message || ""),
            source: String(msg.source || ""),
            ts: Number(msg.ts) || undefined,
          });
          continue;
        }
        if (msg.type === "web_search") {
          island.onWebSearch({ query: String(msg.query || ""), provider: String(msg.provider || ""), results: Array.isArray(msg.results) ? msg.results as any[] : [], card: msg.card, related: Array.isArray((msg as any).related) ? (msg as any).related : [], more: Array.isArray((msg as any).more) ? (msg as any).more : [], moreTitle: String((msg as any).more_title || ""), ts: Number(msg.ts) || undefined });
          continue;
        }
        if (msg.type === "search_dismiss") {
          // A result was opened by voice: the results panel is finished.
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.dismissSearchResults();
          continue;
        }
        if (msg.type === "appvolume") {
          island.onAppVolume({
            app: String(msg.app || "App"),
            level: Number(msg.level) || 0,
            muted: msg.muted === true,
            ts: Number(msg.ts) || undefined,
          });
          continue;
        }
        if (msg.type === "privacy") {
          island.onPrivacy(msg.mic === true);
          continue;
        }
        if (msg.type === "wallpaper") {
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.onWallpaper(msg.open === true);
          continue;
        }
        if (msg.type === "appearance") {
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) {
            const f = ((msg as unknown as { fields?: Record<string, string> }).fields) || {};
            const it = f.island_theme;
            if (it && ["pure-black", "midnight", "aurora", "dynamic", "frost"].includes(it)) {
              try { window.localStorage.setItem("lyra-island-theme", it); } catch { /* not persisted */ }
              State.reloadAppearance();
            }
          }
          continue;
        }
        if (msg.type === "controls") {
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.onControls(msg.open === true);
          continue;
        }
        if (msg.type === "record") {
          const rm = msg as unknown as { recording?: boolean; started_at?: number; paused?: boolean; elapsed_ms?: number };
          island.onRecord(!!rm.recording, rm.started_at, rm.paused ? Math.max(0, Number(rm.elapsed_ms) || 0) : null);
          continue;
        }
        if (msg.type === "call_record") {
          const cm = msg as unknown as { recording?: boolean; started_at?: number };
          island.onCallRecord(!!cm.recording, cm.started_at);
          continue;
        }
        if (msg.type === "translate") {
          // A voice translation ("translate hello to French"): the Translate tab opens with the result.
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) {
            const tm = msg as unknown as Record<string, unknown>;
            setTranslatePrefill({ src: String(tm.src || ""), dst: String(tm.dst || ""), text: String(tm.text || ""), result: String(tm.result || "") });
            island.onOpenView("translate");
          }
          continue;
        }
        if (msg.type === "open_view") {
          // Lyra asks for a tab (voice, or a backend feature that needs one).
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.onOpenView(String(msg.view || ""));
          continue;
        }
        if (msg.type === "added") {
          // Lyra added something by voice (a to-do, a reminder, a timer): open its tab and play the animation.
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.onAdded(String(msg.view || ""), String(msg.text || ""), String((msg as unknown as Record<string, unknown>).kind || ""), (msg as unknown as Record<string, unknown>).status === "fail" ? "fail" : "ok");
          continue;
        }
        if (msg.type === "wheel") {
          // Held shortcut / voice: only fresh events count, so a late "commit" can never open a tab by itself.
          if (!msg.ts || Date.now() - Number(msg.ts) < 5_000) island.onWheel(String(msg.action || ""));
          continue;
        }
        if (msg.type === "stats") {
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.onStats(msg.open === true);
          continue;
        }
        if (msg.type === "todos") {
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.onTodos(msg.open === true);
          continue;
        }
        if (msg.type === "study") {
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.onStudy(msg.open === true);
          continue;
        }
        if (msg.type === "game_mode") {
          island.onGameMode(msg.on === true, String(msg.wake || ""));   // a game is running: shortcuts off
          continue;
        }
        if (msg.type === "schedule") {
          if (!msg.ts || Date.now() - Number(msg.ts) < 20_000) island.onSchedule(msg.open === true);
          continue;
        }
        if (msg.type === "timers") {
          island.onTimers({ items: (msg.items ?? []) as TimersPayload["items"], now: Number(msg.now) || Date.now() });
          continue;
        }
        if (msg.type === "chat" && msg.text) {
          const role = String(msg.role || "").toLowerCase();
          // What Lyra heard / answered is drawn by the caption window; the island
          // keeps no conversation. A spoken or typed question needs nothing here.
          if (role === "user" || role === "you") {
            tourSignal("said", String(msg.text));
            Chats.user(String(msg.text));
            // The accurate transcript of what you said replaces the live guess on the island (typed messages have none).
            if (!msg.local) island.onLiveText(String(msg.text), true, "ptt");
            continue;
          }
          // An assistant reply is also a definitive end to the thinking phase.
          // Clear it here in case the following speaking/idle state event was
          // lost while this window was reconnecting to the backend.
          island.setThinking(false);
          window.clearTimeout(thinkTimer);
          State.stateOverride = null;
          State.notify();
          console.log("[lyra] reply:", msg.text);
          Chats.assistant(String(msg.text));
          // "Done" / "Opening ..." can be a Mochi sound instead of speech (Settings -> Voice).
          const mochi = isMochiKind(msg.mochi) ? msg.mochi : null;
          island.onTypedReply(!!mochi);
          pushReply(String(msg.text), !!mochi);
          if (mochi && !replaying) playMochi(mochi);
        }
      }
    } catch {
      // backend not up yet — keep polling
    }
  };

  // 150 ms (fast enough for the voice bars) while events arrive, 350 ms when it has been quiet.
  adaptivePoll(poll, 150, 350);


  // Seed any timers that were already running before the UI connected.
  const refreshTimers = async () => {
    try {
      const r = await fetch(`${BASE}/timers`);
      if (!r.ok) return;
      const data = await r.json();
      if (Array.isArray(data.items)) {
        island.onTimers({ items: data.items, now: Number(data.now) || Date.now() });
      }
    } catch { /* backend offline */ }
  };
  // Timers are pushed by the backend; this only seeds at start and re-seeds at most every 30 s while visible.
  visiblePoll(refreshTimers, { ms: 30_000, floorMs: 30_000 });

}

function applyState(island: Island, state: LyraState): void {
  console.log("[lyra] state:", state, "→", lyraStateToHint(state));
  trace("lyra", `Lyra state is now '${state}'`);

  // Listening and speaking are mutually exclusive compact-notch owners.
  island.setListening(state === "listening");
  island.setSpeaking(state === "speaking");
  // "Thinking" is shown on the small island itself. It must never pull another tab open.
  island.setThinking(state === "thinking");
  window.clearTimeout(thinkTimer);
  if (state === "thinking") thinkTimer = window.setTimeout(() => void playMochiIfOn("thinking"), THINK_SOUND_AFTER_MS);

  if (state === "thinking") {
    // Thinking is presented in the subtitle window; keep the island visuals unchanged.
    State.stateOverride = null;
    State.notify();
  } else if (state === "speaking" || state === "idle") {
    // The thinking override belongs only to the thinking phase.  If it is
    // left behind, the island can keep the old Lyra visual state after TTS
    // finishes instead of returning to normal/Spotify presentation.
    State.stateOverride = null;
    State.notify();
  }

  if (state !== "idle") {
    try {
      island.reveal();
    } catch {
      /* */
    }
  }

  if (state === "speaking") {
    // Speech lives on the small island with the voice wave, except while the chat tab is open:
    // there the chat stays put and the wave shows in the top row of the panel.
    try {
      if (State.mode === "expanded" && !island.hasSearchResultsOpen() && State.view !== "prompt") island.collapse("Lyra started speaking");
      else if (State.mode === "hidden") island.reveal();
    } catch {
      /* */
    }
    return;
  }

  if (state === "error") {
    try {
      island.fsm.forceHome(`Lyra state: ${state}`);
    } catch {
      /* */
    }
  }
}



export async function searchWeb(query: string, limit = 5): Promise<any> {
  try { const res = await fetch(`${BASE}/search`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query, limit }) }); return await res.json().catch(() => ({ ok: false, results: [] })); }
  catch { return { ok: false, results: [], error: "Could not reach Lyra — is the backend running?" }; }
}

/** Trigger music recognition (Island / Music tab). */
export async function recognizeMusic(forceAudio = true): Promise<any> {
  try {
    const res = await fetch(`${BASE}/music/recognize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force_audio: forceAudio }),
    });
    return await res.json().catch(() => ({ ok: false, message: "Invalid response from Lyra." }));
  } catch {
    return { ok: false, message: "Could not reach Lyra — is the backend running?" };
  }
}

/** History plus the backend session id (changes on every backend restart). */
export async function musicRecognitionHistoryFull(): Promise<{ items: any[]; session: string } | null> {
  try {
    const res = await fetch(`${BASE}/music/history`);
    if (!res.ok) return null;
    const data = await res.json();
    return { items: Array.isArray(data.items) ? data.items : [], session: String(data.session || "") };
  } catch {
    return null;
  }
}

export async function musicRecognitionHistory(): Promise<any[]> {
  try {
    const res = await fetch(`${BASE}/music/history`);
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.items) ? data.items : [];
  } catch {
    return [];
  }
}
