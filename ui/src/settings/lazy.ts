// Run a card's first load only when the card is actually on screen.
//
// Settings builds every tab up front (so search can look through all of them), and each card used to fire
// its own request while it was built: ~25 requests at once, several of them slow probes, all competing
// with the animation that opens the window. A card inside a hidden tab has no layout box, so an
// IntersectionObserver reports it only once its tab is shown. Each card then loads once, on first view.

import { lazyFired, lazyWaiting, tabOf, withOwner } from "../core/loadOwner";

/** `label` is optional and only names the card in the Settings log ("who loaded this?"). */
export function whenVisible(el: Element, load: () => void, label = ""): void {
  let done = false;
  const waiting = lazyWaiting(el, label);
  const run = () => {
    if (done) return;
    done = true;
    io?.disconnect();
    const tab = lazyFired(waiting) || tabOf(el);
    withOwner(`lazy:${tab || "?"}${label ? ":" + label : ""}`, load);
  };
  if (typeof IntersectionObserver === "undefined") { run(); return; }
  const io: IntersectionObserver | null = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) run();
  }, { rootMargin: "80px" });
  io.observe(el);
}
