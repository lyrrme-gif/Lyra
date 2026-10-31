// Settings → Live Activities: one preview per activity, and each one IS the real island.
//
// activitySnapshots.ts holds the island's own DOM (computed styles inlined) captured from the running
// island while that activity was showing: the same markup, sizes, fonts, icons and colours the notch
// draws. Here each snapshot is dropped on a small desktop and scaled to the tile. Previews are
// decorative and never talk to the backend; they do not change how any activity works.

import "./activityPreviews.css";
import { h } from "../views/dom";
import { SNAPSHOTS } from "./activitySnapshots";
import type { LiveActivity } from "../core/state";

/** Logical size of the "desktop" stage every snapshot is placed on (scaled to the tile by `fitStage`). */
const STAGE_W = 420;

/** Scales the fixed-size stage to the tile it sits in. */
function fitStage(stage: HTMLElement, host: HTMLElement): void {
  const apply = () => {
    const w = host.clientWidth;
    if (w > 0) stage.style.setProperty("--k", String(w / STAGE_W));
  };
  apply();
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(apply).observe(host);
}

/** The preview scene for one Live Activity (goes inside `.ptile-scene`). */
export function activityScene(id: LiveActivity): HTMLElement {
  const snap = SNAPSHOTS[id];
  const stage = h("div", { class: "ap-stage" });
  if (snap) {
    stage.innerHTML = snap.html;
    const island = stage.firstElementChild as HTMLElement | null;
    if (island) {
      island.classList.add("ap-island");
      island.dataset.act = id;
      island.style.width = `${snap.w}px`;
      island.style.height = `${snap.h}px`;
    }
  }
  const desk = h("span", { class: "mini-desk ap-desk" }, stage);
  // Fit once the tile is in the document and has a width.
  requestAnimationFrame(() => fitStage(stage, desk));
  return desk;
}
