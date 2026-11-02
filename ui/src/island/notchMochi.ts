// The Lyra Mochi lives in the island's notch. It shows whenever the notch is compact and idle,
// and steps aside when Spotify, the volume HUD, a timer, a toast or Lyra's own turn (listening / thinking /
// speaking, see lyraStatus.ts: there the Mochi moves to the left of the notch next to the text / the wave) own it.
// When Lyra has to explain something on screen, the mascot overlay window (src/mascot) pops the
// Mochi out of the notch and tells us through "mascot-away"; we hide ours until it is back.
//
// Nothing in island.ts changes: this only appends one element to #island and reads its classes.

import { createCharacter, gazeToward, type Character, type EmoteName } from "../mascot/character";
import { AWAY_TIMEOUT_MS, onAway } from "../mascot/dock";
import { carry, emitNest, onNestQuery } from "../mascot/carry";
import { IS_TAURI, onEvent } from "../core/bridge";
import { nowPlaying, onNowPlaying } from "../core/nowPlaying";
import { isReallyVisible } from "../core/visibility";
import { DEFAULT_CONTEXT, autoHidden, watchContext, type AutoHideContext } from "../core/autohide";
// >>> phase-6 begin
import * as startup from "./startup";
import { adaptivePoll, noteEvents } from "../core/pollLoop";
import { getFeatures, onFeatures } from "../core/featuresCache";
// <<< phase-6 end

const BASE = "http://127.0.0.1:8765";
const BODY_PX = 30; // body width in the notch (the mascot engine shrinks to the same size)

const CSS = `
#notch-mochi{position:absolute;left:50%;top:50%;width:${(BODY_PX * 84 / 82).toFixed(1)}px;transform:translate(-50%,-50%);
  pointer-events:none;z-index:3;opacity:0;visibility:hidden;transition:opacity .18s ease,visibility 0s linear .18s}
#island[data-mode="compact"] #notch-mochi{opacity:1;visibility:visible;transition:opacity .18s ease}
#island[data-mode="compact"]:has(#spotify-compact.on,#volume-hud.on,#timer-chip.on,#notify-toast.on) #notch-mochi,
#island.lyra-listening #notch-mochi,#island.lyra-speaking #notch-mochi,#island.lyra-thinking #notch-mochi,#island.lyra-status #notch-mochi,#island.quitting #notch-mochi,
#island #notch-mochi.away,#island #notch-mochi.off{opacity:0;visibility:hidden}
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

  // Drawn only while it is in the notch and the "Lyra mascot" feature is on.
  const sync = () => {
    wrap.classList.toggle("away", away);
    wrap.classList.toggle("off", !enabled || autoHid());
    ch.setActive?.(enabled && !away && !autoHid());
    if (enabled && !away && !autoHid()) aim();
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
    const p = (await getFeatures()) as { feature_mascot?: boolean; mascot_shape?: string } | null;
    if (!p) return;   // backend not up yet: keep the last value
    enabled = p.feature_mascot === true;
    ch.setShape?.(p.mascot_shape ?? "cloud");
    sync();
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
          default: if (performance.now() > until) setEmote("idle");   // a mood (Mochi sound) plays out first
        }
      }
    } catch { /* backend not up yet: keep polling */ }
    if (until && performance.now() > until) setEmote("idle");
  }
  adaptivePoll(poll, 250, 500, false);   // 250 ms while events arrive, 500 ms when quiet
}
