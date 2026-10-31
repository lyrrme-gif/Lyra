// Mascot window: a transparent, click-through overlay covering one monitor.
// The Rust side (src-tauri/src/mascot.rs) feeds the cursor and decides click-through;
// the backend feeds Lyra's state, speech levels and "mascot" commands through /events,
// exactly like the caption window.

import "./mascot.css";
import { Engine, type MascotCmd } from "./engine";
import { IS_TAURI, native, onNative, type MascotPrefs, type ScreenInfo } from "./native";
import { Overlay } from "./overlay";
import { onCaptionPill, onMusic } from "./dock";
import { adaptivePoll, noteEvents } from "../core/pollLoop";
import { installTracer } from "../core/trace";
installTracer();

const BASE = "http://127.0.0.1:8765";
const stage = document.getElementById("stage") as HTMLElement;

const overlay = new Overlay(stage);
const engine = new Engine(stage, overlay);

// ── Rust → window ────────────────────────────────────────────────────────────
function applyScreen(s: ScreenInfo): void {
  engine.setScreen(s.width, s.height, `${s.x},${s.y},${s.scale}`);
}
function applyPrefs(p: MascotPrefs): void {
  const q = p ?? {};
  engine.setPrefs(q.mascot_size, q.feature_mascot_stay === true);
  engine.setShape(q.mascot_shape);
}

void (async () => {
  await onNative<{ x: number; y: number }>("mascot-cursor", (c) => engine.setCursor(c.x, c.y));
  await onCaptionPill((c) => engine.setCaption(c));
  await onMusic((on) => engine.setMusic(on));
  await onNative<boolean>("mascot-game", (on) => engine.setGaming(on));
  await onNative<ScreenInfo>("mascot-screen", applyScreen);
  await onNative<MascotPrefs>("mascot-prefs", applyPrefs);
  await onNative<boolean>("mascot-suspend", (on) => engine.setSuspended(on));
  const boot = await native.boot();
  if (boot) {
    applyScreen(boot.screen);
    applyPrefs(boot.prefs);
    engine.setSuspended(boot.suspended);
  }
})();

window.addEventListener("resize", () => engine.setScreen(window.innerWidth, window.innerHeight));

// Plain browser (npm run dev → /mascot.html): no Rust, so the page cursor stands in.
if (!IS_TAURI) {
  window.addEventListener("mousemove", (e) => engine.setCursor(e.clientX, e.clientY));
}

// ── Backend → window (same polling pattern as the caption window) ────────────
type Ev = {
  type?: string; state?: string; levels?: number[]; hz?: number; start_ms?: number;
} & Partial<MascotCmd>;

let since = 0;
let backlog = true; // the first batch is history: never replay old commands

async function poll(): Promise<void> {
  try {
    const res = await fetch(`${BASE}/events?since=${since}`);
    if (!res.ok) return;
    const data = (await res.json()) as { events?: Ev[]; next?: number };
    if (typeof data.next === "number") since = data.next; // also follows a restarted backend
    noteEvents(data.events?.length ?? 0);
    if (backlog) { backlog = false; return; }
    for (const ev of data.events || []) {
      if (ev.type === "state" && ev.state) engine.setLyraState(String(ev.state));
      else if (ev.type === "wave") engine.feedWave(ev.levels ?? [], Number(ev.hz) || 25, Number(ev.start_ms) || Date.now());
      else if (ev.type === "mascot" && ev.cmd) engine.command(ev as MascotCmd);
    }
  } catch { /* backend not up yet: keep polling */ }
}

adaptivePoll(poll, 200, 500);   // 200 ms while events arrive, 500 ms when it has been quiet
