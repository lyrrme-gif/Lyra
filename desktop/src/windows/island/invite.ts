// Hover invite: the pointer rests on the compact notch without clicking and the island "asks" to be opened.
// It swells a little (the spring lives in island.ts), lights its rim and a soft sheen follows the
// cursor. All of it is CSS, switched by
// the `hovering` class that island.ts puts on #island while the pointer is on the compact notch.
// (Just the swell, a lit rim, a gentle float and a soft sheen under the cursor: no rings, no label.)

import { h } from "../../shared/views/dom";
import "./invite.css";

export interface Invite {
  /** Elements to put inside #island. */
  els: HTMLElement[];
  /** Pointer position inside the island, in px (drives the sheen). */
  track(x: number, y: number, w: number, hh: number): void;
}

export function buildInvite(): Invite {
  const sheen = h("div", { id: "island-sheen", "aria-hidden": "true" });
  let raf = 0;
  let nx = 50, ny = 50;
  return {
    els: [sheen],
    track(x, y, w, hh) {
      nx = w > 0 ? Math.max(0, Math.min(100, (x / w) * 100)) : 50;
      ny = hh > 0 ? Math.max(0, Math.min(100, (y / hh) * 100)) : 50;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        sheen.style.setProperty("--mx", `${nx.toFixed(1)}%`);
        sheen.style.setProperty("--my", `${ny.toFixed(1)}%`);
      });
    },
  };
}
