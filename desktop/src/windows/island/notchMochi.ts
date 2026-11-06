// The Lyra Mochi lives in the island's notch.
//
//   idle compact notch        ->  the Mochi sits in the middle
//   any Live Activity         ->  (Settings -> Mochi -> "Mochi in Live Activities", on by default; when it is OFF the Mochi simply
//                                 steps aside while an activity is showing and nothing below applies)
//                                 the Mochi slides to its own slot on the LEFT edge and stays there, in the same place for
//                                 every activity (music, timer, volume / brightness / devices / battery, downloads,
//                                 notifications, calls, reminders, "added" pulses ...). The notch is made wider by
//                                 MOCHI_SLOT_W for it (islandGeometry.ts) and every activity layer leaves that room
//                                 (styles/mochiSlot.css), so icons line up the same way in all of them.
//                                 There it reacts to what is happening (rings for a call, munches a download, focuses on
//                                 a timer, dances to the music), see baseEmote().
//   Lyra's own turn           ->  lyraStatus.ts draws its own Mochi (listening / thinking / speaking), so this one steps aside
//
// When Lyra has to explain something on screen, the mascot overlay window (src/mascot) pops the
// Mochi out of the notch and tells us through "mascot-away"; we hide ours (and give the room back) until it is back.
//
// island.ts only draws the activities; this file appends one element to #island, reads which activity layer is showing
// and publishes two things on #island: `mochi-ok` (the Mochi is shown, so the activities leave room for it) and, on the
// Mochi itself, data-pos="center" | "slot".

import { createCharacter, gazeToward, type Character, type EmoteName } from "../../shared/mascot/character";
import { AWAY_TIMEOUT_MS, onAway } from "../../shared/mascot/dock";
import { carry, emitNest, onNestQuery } from "../../shared/mascot/carry";
import { IS_TAURI, onEvent } from "../../shared/core/bridge";
import { nowPlaying, onNowPlaying } from "../../shared/core/nowPlaying";
import { isReallyVisible } from "../../shared/core/visibility";
import { DEFAULT_CONTEXT, autoHidden, watchContext, type AutoHideContext } from "../../shared/core/autohide";
// >>> phase-6 begin
import * as startup from "./startup";
import { adaptivePoll, noteEvents } from "../../shared/core/pollLoop";
import { getFeatures, onFeatures } from "../../shared/core/featuresCache";
// <<< phase-6 end

const BASE = "http://127.0.0.1:8765";
const BODY_PX = 30; // body width in the notch (the mascot engine shrinks to the same size)

const SLOT_X = 14;   // px from the notch's left edge to the Mochi's left edge in an activity (mochiSlot.css keeps the same number)

const CSS = `
#notch-mochi{position:absolute;left:50%;top:50%;width:${(BODY_PX * 84 / 82).toFixed(1)}px;transform:translate(-50%,-50%);
  pointer-events:none;z-index:40;opacity:0;visibility:hidden;
  transition:opacity .18s ease,visibility 0s linear .18s,left .46s cubic-bezier(.34,1.32,.5,1),transform .46s cubic-bezier(.34,1.32,.5,1)}
#island[data-mode="compact"] #notch-mochi{opacity:1;visibility:visible;
  transition:opacity .18s ease,left .46s cubic-bezier(.34,1.32,.5,1),transform .46s cubic-bezier(.34,1.32,.5,1)}
/* in a Live Activity: the left slot (same x for every activity) */
#island #notch-mochi[data-pos="slot"]{left:${SLOT_X}px;transform:translate(0,-50%)}
/* Lyra's own turn draws its own Mochi (lyraStatus.ts); away / switched off / closing: nothing here */
#island.lyra-listening #notch-mochi,#island.lyra-speaking #notch-mochi,#island.lyra-thinking #notch-mochi,#island.lyra-status #notch-mochi,#island.quitting #notch-mochi,
#island #notch-mochi.away,#island #notch-mochi.off,#island #notch-mochi.yield{opacity:0;visibility:hidden}
@media (prefers-reduced-motion: reduce){#notch-mochi,#island[data-mode="compact"] #notch-mochi{transition:opacity .18s ease}}
`;

export function mountNotchMochi(): void {
  const host = document.getElementById("island");
  if (!host || document.getElementById("notch-mochi")) return;

  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  const wrap = document.createElement("div");
  wrap.id = "notch-mochi";
  host.appendChild(wrap);
  const ch: Character = createCharacter(wrap, { tight: true });

  let away = false, enabled = false;
  let inActs = true;   // Settings -> Mochi -> "Mochi in Live Activities": true = slot on the left of every activity, false = steps aside
  // The Mochi has no auto-hide setting any more: it only steps aside while you share your screen.
  let hideCtx: AutoHideContext = DEFAULT_CONTEXT;
  const autoHid = () => autoHidden("always", hideCtx);
  void watchContext((c) => { hideCtx = c; sync(); });

  // ── Music: groove + notes while a track is actually playing (same window as the island) ──
  // The Mochi always dances while music plays.
  const syncMusic = () => ch.setVibing?.(nowPlaying.running && nowPlaying.status === "playing");
  onNowPlaying(syncMusic);
  syncMusic();

  // ── Eyes follow the cursor (window-logical coordinates, same feed the island uses) ──
  // The last position is kept: the island moves / morphs (and the Mochi appears and disappears),
  // so the gaze is re-aimed from it instead of waiting for the next mouse move.
  let cursor: { x: number; y: number } | null = null;
  const aim = () => {
    if (!cursor) { ch.look(0, 0); return; }
    const [gx, gy] = gazeToward(wrap, cursor.x, cursor.y);
    ch.look(gx, gy);
  };
  const look = (x: number, y: number) => {
    // The island sends (-10000, -10000) while a game is in front: that is "no cursor", not a far corner.
    cursor = x > -5000 && y > -5000 ? { x, y } : null;
    if (enabled && !away && !autoHid()) aim();   // hidden Mochi: nothing to aim (the 400 ms tick re-aims on show)
  };
  if (IS_TAURI) void onEvent<{ x: number; y: number }>("cursor", (c) => look(c.x, c.y));
  else window.addEventListener("mousemove", (e) => look(e.clientX, e.clientY));
  // Cursor events already call aim(); this only re-aims after the island itself moved, so a slow tick is enough.
  window.setInterval(() => { if (enabled && !away && !autoHid() && isReallyVisible()) aim(); }, 400);

  // Drawn only while it is in the notch and the "Lyra mascot" feature is on. `lastShown` = the activity slot was in use.
  let lastShown = false;
  const sync = () => {
    const shown = enabled && !away && !autoHid();
    wrap.classList.toggle("away", away);
    wrap.classList.toggle("off", !enabled || autoHid());
    ch.setActive?.(shown);
    if (shown) aim();
    // The activities leave room for the Mochi only while it is really there (styles/mochiSlot.css); the island
    // measures its notch with the same flag (islandGeometry.mochiSlotOn) and re-measures when it flips.
    const slot = shown && inActs;
    host.classList.toggle("mochi-ok", slot);
    if (slot !== lastShown) { lastShown = slot; window.dispatchEvent(new Event("lyra:mochi-slot")); }
    if (shown) refreshEmote();
  };
  sync();
  // >>> phase-6 begin
  // The start-up animation wakes Mochi (sleep -> surprised -> happy); only when Mochi is actually shown.
  startup.attachMascot({ visible: () => enabled && !away && !autoHid(), setEmote: (e) => { if (enabled && !away && !autoHid()) ch.setEmote(e); } });
  // <<< phase-6 end

  // ── Out of the notch? ──
  let awayTimer = 0;
  void onAway((a) => {
    window.clearTimeout(awayTimer);
    away = a;
    sync();
    if (a) awayTimer = window.setTimeout(() => { away = false; sync(); }, AWAY_TIMEOUT_MS);
  });

  // ── Dragging the Mochi between the island and the screen (mascot/carry.ts) ──
  // The overlay asks where the island is while the Mochi is dragged: its shape (so letting go over it flies the
  // Mochi in) and, when the Mochi tab is open, where that tab keeps its Mochi (so it lands there, not in the notch).
  void onNestQuery(() => {
    const r = host.getBoundingClientRect();
    const hidden = host.dataset.mode === "hidden" || r.width < 2;
    emitNest({
      winW: window.innerWidth,
      rect: hidden ? { x: 0, y: 0, w: 0, h: 0 } : { x: r.left, y: r.top, w: r.width, h: r.height },
      spot: hidden ? null : carry.spot(),
    });
  });

  // ── Feature switch: same flag the overlay window follows (Settings → Lyra mascot) ──
  async function readFeature(): Promise<void> {
    const p = (await getFeatures()) as { feature_mascot?: boolean; feature_mascot_activities?: boolean; mascot_shape?: string } | null;
    if (!p) return;   // backend not up yet: keep the last value
    enabled = p.feature_mascot === true;
    inActs = p.feature_mascot_activities !== false;   // a missing value is ON (the default)
    ch.setShape?.(p.mascot_shape ?? "cloud");
    sync();
    syncOwner();   // slot <-> steps aside, right now
  }
  void readFeature();
  // No timer: the shared cache refreshes on focus / visible / after a save and tells us (core/featuresCache.ts).
  onFeatures(() => void readFeature());

  const MOODS = new Set<string>(["love", "wink", "tired", "focus", "hot", "thirsty", "ring", "lowbat", "munch", "shy", "grumpy", "sneeze", "dance", "surprised", "sad", "sleep", "happy", "wave", "idle",
    // Mochi sounds (done, error, question, dizzy ... see core/mochiSound.ts)
    "celebrate", "error", "think", "listen", "dizzy"]);

  // ── Lyra's state → emote (same backend feed the caption window reads) ──
  let since = 0, backlog = true, until = 0;
  // A game is in front: it plays with a controller too (the Rust side tells both windows).
  let gaming = false;
  if (IS_TAURI) void onEvent<boolean>("mascot-game", (on) => { gaming = !!on; ch.setEmote(on ? "gaming" : "idle"); });
  const setEmote = (e: EmoteName, ms = 0) => { until = ms ? performance.now() + ms : 0; ch.setEmote(e); };

  // ── Which Live Activity is on the notch? Read from the layers island.ts shows (the same classes its CSS uses) ──
  type Owner = "" | "toast" | "pulse" | "hud" | "timer" | "music";
  const layer = (id: string) => document.getElementById(id);
  const ownerNow = (): Owner => {
    if (host.dataset.mode !== "compact") return "";
    if (layer("notify-toast")?.classList.contains("on")) return "toast";
    if (layer("island-pulse")?.classList.contains("on")) return "pulse";
    if (layer("volume-hud")?.classList.contains("on")) return "hud";
    if (layer("timer-chip")?.classList.contains("show")) return "timer";
    if (layer("spotify-compact")?.classList.contains("on")) return "music";
    return "";
  };
  /** How the Mochi feels about what is on the notch (idle when it has no opinion). Music has its own groove: setVibing. */
  const baseEmote = (): EmoteName => {
    if (!inActs) return "idle";   // switched off: the Mochi steps aside during activities, it does not react to them
    switch (ownerNow()) {
      case "toast": {
        const k = layer("notify-toast")?.dataset.kind;
        return k === "call" ? "ring" : k === "reminder" ? "wave" : k === "message" ? "surprised" : "happy";
      }
      case "pulse": {
        const k = layer("island-pulse")?.dataset.kind;
        return k === "fail" ? "error" : k === "song" ? "dance" : k === "gameoff" ? "idle" : k === "game" ? "focus" : "happy";
      }
      case "hud": {
        const hud = layer("volume-hud");
        if (hud?.dataset.activityKind === "download") {
          const ph = hud.dataset.downloadPhase;
          return ph === "success" ? "celebrate" : ph === "fail" ? "error" : "munch";   // munches while it loads
        }
        return hud?.classList.contains("recognition-result") ? "happy" : "idle";
      }
      case "timer": return layer("timer-chip")?.classList.contains("urgent") ? "surprised" : "focus";
      default: return "idle";
    }
  };
  const quiet = () => gaming || startup.active();                         // the game / start-up sequences own the mood
  /** Puts the activity's mood on the Mochi, unless a mood from the backend is still playing out. */
  function refreshEmote(): void {
    if (quiet() || (until && performance.now() < until)) return;
    until = 0;
    ch.setEmote(baseEmote());
  }
  async function poll(): Promise<void> {
    try {
      const res = await fetch(`${BASE}/events?since=${since}`);
      if (!res.ok) return;
      const data = (await res.json()) as { events?: { type?: string; state?: string; cmd?: string; name?: string; ms?: number }[]; next?: number };
      if (typeof data.next === "number") since = data.next;
      noteEvents(data.events?.length ?? 0);
      if (backlog) { backlog = false; return; }
      for (const ev of data.events || []) {
        if (gaming || startup.active()) continue;   // phase 6: the start-up sequence owns the mood
        if (ev.type === "mascot" && ev.cmd === "emote" && enabled) {          // a mood sent by the backend (call, low battery, focus …)
          const name = String(ev.name ?? "") as EmoteName;
          if (MOODS.has(name)) setEmote(name, Math.max(500, Number(ev.ms) || 3000));
          continue;
        }
        if (ev.type !== "state") continue;
        switch (String(ev.state || "").toLowerCase()) {
          case "thinking": case "executing": setEmote("think"); break;
          case "success": setEmote("happy", 2200); break;
          case "error": setEmote("error", 2800); break;
          default: if (performance.now() > until) setEmote(baseEmote());   // a mood (Mochi sound) plays out first, then the activity's own
        }
      }
    } catch { /* backend not up yet: keep polling */ }
    if (until && performance.now() > until) { until = 0; if (!quiet()) ch.setEmote(baseEmote()); }
  }
  adaptivePoll(poll, 250, 500, false);   // 250 ms while events arrive, 500 ms when quiet

  // ── Slot ownership: follows the activity layers (no timer: only when one of them changes) ──
  let lastKey = "";
  const syncOwner = () => {
    const o = ownerNow();
    wrap.dataset.pos = o && inActs ? "slot" : "center";
    wrap.classList.toggle("yield", !!o && !inActs);   // switch off: an activity owns the notch, the Mochi fades out (and comes back after)
    const key = `${o}|${o ? baseEmote() : ""}`;
    if (key === lastKey) return;
    lastKey = key;
    if (enabled && !away && !autoHid()) refreshEmote();
  };
  wrap.dataset.pos = "center";
  const watcher = new MutationObserver(syncOwner);
  const FILTER = ["class", "data-kind", "data-activity-kind", "data-download-phase", "data-mode"];
  watcher.observe(host, { attributes: true, attributeFilter: ["class", "data-mode"] });
  for (const id of ["spotify-compact", "volume-hud", "timer-chip", "notify-toast", "island-pulse"]) {
    const el = document.getElementById(id);
    if (el) watcher.observe(el, { attributes: true, attributeFilter: FILTER });
  }
  syncOwner();
}
