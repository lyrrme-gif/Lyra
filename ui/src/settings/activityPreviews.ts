// Settings → Live Activities: one preview per activity, and each one IS the real island.
//
// activitySnapshots.ts holds the island's own DOM (computed styles inlined) captured from the running
// island while that activity was showing: the same markup, sizes, fonts, icons and colours the notch
// draws. Here each snapshot is dropped on a small desktop and scaled to the tile. Previews are
// decorative and never talk to the backend; they do not change how any activity works.

import "./activityPreviews.css";
import { h } from "../views/dom";
import { SNAPSHOTS } from "./activitySnapshots";
import { snapshotScene } from "./screenPreview";
import type { LiveActivity } from "../core/state";

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
  return snapshotScene(island, snap.w, snap.h);
}
