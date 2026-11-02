// Settings → Live Activities: one preview per activity, and each one IS the real island.
//
// activitySnapshots.ts holds the island's own DOM (computed styles inlined) captured from the running
// island while that activity was showing: the same markup, sizes, fonts, icons and colours the notch
// draws. Here each snapshot is dropped on a small desktop and scaled to the tile. Previews are
// decorative and never talk to the backend; they do not change how any activity works.
//
// The light show is the real one too: island/activityFx.ts (the very module the island uses) is attached to
// the snapshot and replays what the notch does for that activity (edge light, shockwaves, sparks, the notch
// buzzing for a call ...). And every tile has a speaker button that plays the activity's real sound
// (core/activitySounds.ts) together with its light.

import "./activityPreviews.css";
import { h } from "../views/dom";
import { SNAPSHOTS } from "./activitySnapshots";
import { snapshotScene } from "./screenPreview";
import type { LiveActivity } from "../core/state";
import { APP_FX_COLOR, buildFx, type FxKind, type FxOpts } from "../island/activityFx";
import { Sound } from "../core/sound";

/** One step of a preview's loop: show `kind` for `ms`. */
type Step = [FxKind, number, FxOpts?];

/** What the notch does for each activity (the same kinds island.ts uses), as a loop. */
const LOOPS: Partial<Record<LiveActivity, Step[]>> = {
  music: [["music", 6000]],
  timer: [["timer", 3600], ["success", 2600, { color: "#ff9f0a", originX: 26, act: "timer" }]],
  volume: [["volume", 1900, { level: 0.85 }], ["volume", 1700, { level: 0.4 }]],
  brightness: [["brightness", 2300, { level: 0.95 }], ["brightness", 1700, { level: 0.3 }]],
  devices: [["devices", 3000, { originX: 24 }], ["disconnect", 2300]],
  downloads: [["download", 3600], ["success", 2800, { originX: 26 }]],
  listening: [["listening", 5200]],
  recognition: [["recognition", 3400], ["found", 3200, { originX: 26 }]],
  notifications: [["message", 3300]],
  calls: [["call", 5200]],
  reminders: [["reminder", 4600]],
};

function appOf(rgb: string): string {
  if (/37,\s*211,\s*102/.test(rgb)) return "whatsapp";
  if (/38,\s*165,\s*225/.test(rgb)) return "telegram";
  if (/88,\s*101,\s*242/.test(rgb)) return "discord";
  return "";
}

/** Replays the light of a preview from its first step (the speaker button). */
const replays = new WeakMap<Element, () => void>();

/** The preview scene for one Live Activity (goes inside `.ptile-scene`): the real island DOM on the new desktop. */
export function activityScene(id: LiveActivity): HTMLElement {
  const snap = SNAPSHOTS[id];
  const holder = h("div", {});
  if (!snap) return snapshotScene(holder, 120, 34);
  holder.innerHTML = snap.html;
  const island = holder.firstElementChild as HTMLElement | null;
  if (!island) return snapshotScene(holder, 120, 34);
  island.classList.add("ap-island");
  island.dataset.act = id;
  for (const [k, v] of [["position", "relative"], ["inset", "auto"], ["margin", "0"], ["left", "0"], ["top", "0"], ["transform", "none"]]) island.style.setProperty(k, v, "important");
  island.style.width = `${snap.w}px`;
  island.style.height = `${snap.h}px`;
  island.style.pointerEvents = "none";
  attachFx(island, id);
  return snapshotScene(island, snap.w, snap.h);
}

function attachFx(island: HTMLElement, id: LiveActivity): void {
  const steps = LOOPS[id];
  if (!steps) return;
  // A message shows in the colour of its app, exactly as the notch does.
  const tile = island.querySelector<HTMLElement>('[data-r="notify-toast-tile"]');
  const msgColor = tile?.style.backgroundColor || "";
  const fx = buildFx(island);
  let i = 0;
  let seen = false;
  let t: number | null = null;
  const stop = () => { if (t != null) { window.clearTimeout(t); t = null; } };
  const running = () => !!island.closest(".sp.run");
  const play = () => {
    stop();
    if (!island.isConnected) {
      if (seen) { fx.hide(); return; }               // gone for good (the panel was rebuilt)
      t = window.setTimeout(play, 400);              // not on screen yet
      return;
    }
    seen = true;
    if (!running()) { t = window.setTimeout(play, 400); return; }   // off screen: wait, do not burn CPU
    const [kind, ms, opts] = steps[i % steps.length];
    fx.show(kind, kind === "message" ? { color: msgColor || APP_FX_COLOR.whatsapp, ...opts } : opts);
    i++;
    t = window.setTimeout(play, ms);
  };
  replays.set(island, () => { i = 0; play(); });
  play();
}

/** Restarts the light of a preview (after its glow switch changed) without playing any sound. */
export function replayActivityFx(scene: HTMLElement): void {
  const island = scene.querySelector<HTMLElement>(".ap-island");
  if (island) replays.get(island)?.();
}

/** The speaker button of a tile: plays this activity's real sound (and restarts its light). */
export function playActivityPreview(id: LiveActivity, scene: HTMLElement): void {
  const island = scene.querySelector<HTMLElement>(".ap-island");
  if (island) replays.get(island)?.();
  const play = (n: Parameters<typeof Sound.activity>[0], o: Parameters<typeof Sound.activity>[1] = {}) => Sound.activity(n, { ...o, preview: true });
  switch (id) {
    case "notifications": {
      const rgb = island?.querySelector<HTMLElement>('[data-r="notify-toast-tile"]')?.style.backgroundColor || "";
      play("message", { app: appOf(rgb) || "whatsapp" });
      break;
    }
    case "calls": play("ring"); window.setTimeout(() => play("answer"), 1500); break;
    case "reminders": play("reminder"); break;
    case "timer": play("timerDone"); break;
    case "volume": [0.2, 0.45, 0.7, 1].forEach((l, k) => window.setTimeout(() => play("volume", { level: l }), k * 150)); break;
    case "brightness": [0.2, 0.55, 0.95].forEach((l, k) => window.setTimeout(() => play("brightness", { level: l }), k * 220)); break;
    case "devices": play("connect"); window.setTimeout(() => play("disconnect"), 1700); break;
    case "downloads": play("downloadStart"); window.setTimeout(() => play("downloadDone"), 1300); break;
    case "listening": play("listenOn"); window.setTimeout(() => play("listenOff"), 1400); break;
    case "recognition": play("listenOn"); window.setTimeout(() => play("found"), 1300); break;
    case "clipboard": play("copy"); break;
    case "music": play("music"); break;
  }
}
