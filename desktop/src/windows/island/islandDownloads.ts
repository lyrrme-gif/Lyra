/** IslandDownloads — collaborator extracted from island.ts */
import type { Island } from "./island";

import { DL_C } from "./downloadUi";
import { Sound } from "../../shared/core/sound";
import { State } from "../../shared/core/state";
import { h } from "../../shared/views/dom";
import { clockText, colorFor, getTimers, pickPrimary } from "./timerChip";
import { LIVE_REC_CLOCK, fmtClock, type SecSlot } from "./islandConstants";

export class IslandDownloads {
  constructor(private readonly i: Island) {}

  stopDownloadSpinner() {
    if (this.i.downloadSpinnerRaf != null) {
      cancelAnimationFrame(this.i.downloadSpinnerRaf);
      this.i.downloadSpinnerRaf = null;
    }
    const ring = this.i.volumeHud.querySelector<SVGCircleElement>(".download-spinner-ring");
    if (ring) {
      ring.style.animation = "none";
      ring.style.strokeDashoffset = "0";
    }
  }

  startDownloadSpinner() {
    // One loop for the whole download. Its angle comes from the clock, not from "when this
    // event arrived", so a re-rendered icon keeps turning smoothly and always completes its turn.
    if (this.i.downloadSpinnerRaf != null) { this.i.paintDownloadSpinner(performance.now()); return; }
    const tick = (now: number) => {
      if (!this.i.volumeHudOn || this.i.volumeHud.dataset.activityKind !== "download" || !this.i.volumeHud.classList.contains("on")) {
        this.i.downloadSpinnerRaf = null;
        return;
      }
      this.i.paintDownloadSpinner(now);
      this.i.downloadSpinnerRaf = requestAnimationFrame(tick);
    };
    this.i.paintDownloadSpinner(performance.now());
    this.i.downloadSpinnerRaf = requestAnimationFrame(tick);
  }

  paintDownloadSpinner(now: number) {
    const ring = this.i.volumeHud.querySelector<SVGCircleElement>(".download-spinner-ring");
    if (!ring) return;
    ring.style.strokeDashoffset = `${-((now % 950) / 950) * DL_C}`;
  }

  /** Monochrome app / phase icons for the activity HUD. */


  /**
   * Activity HUD that always surfaces the compact notch (from hidden or expanded).
   * Layout: [icon] label  — matches volume HUD (icon left, bold white text).
   */
  downloadIsSecondary(): boolean {
    return this.i.volumeHudOn && this.i.volumeHud.dataset.activityKind === "download" && this.i.volumeHud.dataset.downloadCompact === "1";
  }

  takeDownloadAsIsland(s: Parameters<Island["onActivity"]>[0]): boolean {
    if (s.phase === "fail") { this.i.endDownloadIsland(); Sound.activity("downloadFail"); return true; }
    if (s.phase === "success" && !this.i.dlSecDoneSound) { this.i.dlSecDoneSound = true; Sound.activity("downloadDone"); window.setTimeout(() => { this.i.dlSecDoneSound = false; }, 3500); }
    const pct = Number.isFinite(s.progress) ? Math.max(0, Math.min(100, Number(s.progress))) : null;
    this.i.dlSec = { phase: s.phase, pct: s.phase === "success" ? 100 : pct };
    this.i.dlLast = s;
    this.i.activities.set("download", true);
    if (this.i.dlSecTimer != null) window.clearTimeout(this.i.dlSecTimer);
    // Progress reports keep it alive; if they stop (or it just finished) the island leaves.
    this.i.dlSecTimer = window.setTimeout(() => this.i.endDownloadIsland(), s.phase === "success" ? 2600 : 6500);
    this.i.paintSecondary();
    return true;
  }

  endDownloadIsland() {
    if (this.i.dlSecTimer != null) { window.clearTimeout(this.i.dlSecTimer); this.i.dlSecTimer = null; }
    this.i.dlLast = null;
    this.i.activities.set("download", false);
    this.i.paintSecondary();
  }

  /** Draws the secondary island for the activity that is not main. */
  paintSecondary() {
    this.i.refreshActivities();
    const lay = this.i.activities.layout();
    // The thing that owned the notch is gone but the download goes on: let it take over as the main HUD.
    if (!lay.main && this.i.activities.isActive("download") && this.i.dlLast) {
      const last = { ...this.i.dlLast, ts: Date.now() };
      this.i.endDownloadIsland();
      this.i.onActivity(last);
      return;
    }
    // While the download owns the notch (after a click) the other activity may still sit beside it.
    const dlIsMain = this.i.dlMain && this.i.volumeHudOn && this.i.volumeHud.dataset.activityKind === "download";
    const busy = this.i.appLaunchHudOn || this.i.lyraSpeaking || this.i.lyraListening || this.i.lyraStatusKind !== null || (this.i.volumeHudOn && !dlIsMain);
    const canShow = State.mode === "compact" && !busy && !this.i.promoting;
    // One island per side (the layout hands out at most two). A recording that has no island of its own
    // rides along inside the first one (timer | red dot + clock).
    const recHasOwn = lay.secondary.some((x) => x.id === "record");
    const carrier = lay.secondary[0]?.side ?? null;
    for (const side of ["left", "right"] as const) {
      const sec = lay.secondary.find((x) => x.side === side) ?? null;
      this.i.paintSlot(this.i.slots[side], sec, canShow && !!sec, !recHasOwn && this.i.recordingSince !== null && carrier === side);
    }
  }

  /** Draws one small island (left or right of the main one). */
  paintSlot(slot: SecSlot, sec: { id: string; side: "left" | "right" } | null, show: boolean, withRec: boolean) {
    const el = slot.el;
    if (show && sec) {
      let key = "";
      if (sec.id === "music") {
        const s = this.i.spotifyState;
        key = `music|${this.i.spotifyArtUrl}|${s?.status}|${s?.title ?? ""}`;
        if (key !== slot.key.replace(/\|rec\d+$/, "") || slot.id !== "music") {
          el.replaceChildren(
            h("img", { class: "sec-art", src: this.i.spotifyArtUrl || "", alt: "" }),
            h("span", { class: "sec-eq" }, h("i"), h("i"), h("i"), h("i")),
          );
        }
        el.classList.toggle("paused", s?.status === "paused");
      } else if (sec.id === "download") {
        const d = this.i.dlSec;
        const p = d.pct == null ? null : Math.round(d.pct);
        key = `dl|${d.phase}|${p ?? "x"}`;
        if (slot.id !== "download" || !el.querySelector(".sec-ring")) {
          el.replaceChildren(h("i", { class: "sec-ring" }), h("span", { class: "sec-text sec-dl-text" }));
        }
        const ring = el.querySelector<HTMLElement>(".sec-ring");
        const txt = el.querySelector(".sec-dl-text");
        if (ring) {
          ring.style.setProperty("--p", String(p ?? 28));
          ring.classList.toggle("indet", p == null && d.phase !== "success");
          ring.classList.toggle("done", d.phase === "success");
        }
        if (txt) txt.textContent = d.phase === "success" ? "Done" : p == null ? "" : `${p}%`;
        el.classList.remove("paused");
      } else if (sec.id === "record" && this.i.recordingSince !== null) {
        const secs = Math.max(0, Math.floor(this.i.recElapsedMs() / 1000));
        const text = LIVE_REC_CLOCK ? fmtClock(secs) : (this.i.recPaused() ? "Paused" : "REC");
        key = `rec|${text.length}|${this.i.recPaused() ? "p" : "r"}`;
        if (slot.id !== "record" || !el.querySelector(".sec-text")) {
          el.replaceChildren(h("i", { class: "sec-dot sec-rec" }), h("span", { class: "sec-text" }));
        }
        el.style.setProperty("--sec-accent", "#ff453a");
        const t = el.querySelector(".sec-text");
        if (t && t.textContent !== text) t.textContent = text;
        el.classList.toggle("paused", this.i.recPaused());
      } else if (sec.id === "timer") {
        const it = pickPrimary(getTimers().items, Date.now());
        if (it) {
          const text = clockText(it, Date.now());
          key = `timer|${it.id}|${it.state}|${text.length}`;
          if (slot.id !== "timer" || !el.querySelector(".sec-text")) {
            el.replaceChildren(h("i", { class: "sec-dot" }), h("span", { class: "sec-text" }));
          }
          el.style.setProperty("--sec-accent", colorFor(it));
          const t = el.querySelector(".sec-text");
          if (t && t.textContent !== text) t.textContent = text;
          el.classList.toggle("paused", it.state === "paused");
        }
      }
      let chip = el.querySelector<HTMLElement>(".sec-extra");
      if (withRec) {
        const rs = Math.max(0, Math.floor(this.i.recElapsedMs() / 1000));
        const rt = LIVE_REC_CLOCK ? fmtClock(rs) : (this.i.recPaused() ? "Paused" : "REC");
        if (!chip) {
          chip = h("span", { class: "sec-extra", title: "Recording" }, h("i", { class: "sec-dot sec-rec" }), h("span", { class: "sec-text" }));
          el.append(chip);
        }
        const rtEl = chip.querySelector(".sec-text");
        if (rtEl && rtEl.textContent !== rt) rtEl.textContent = rt;
        key += `|rec${rt.length}`;
      } else if (chip) chip.remove();
      if (key !== slot.key || slot.id !== sec.id) {
        slot.key = key;
        slot.id = sec.id;
        el.dataset.activity = sec.id;
        slot.w = el.offsetWidth; // content-aware width, measured once per content change
        this.i.dirty = true;
        this.i.ensureRunning();
      }
    }
    if (show !== slot.shown) {
      slot.shown = show;
      el.classList.toggle("show", show);
      this.i.dirty = true;
      this.i.ensureRunning();
    }
  }

  /** Small island rect in window coordinates (also its click-through hit shape). */
  secondaryRect(slot: SecSlot, side: "left" | "right", islandX: number, islandW: number, islandTop: number, islandH: number) {
    const h2 = 34;
    const gap = 10;
    const w = Math.max(h2, slot.w || h2);
    const x = side === "right" ? islandX + islandW + gap : islandX - gap - w;
    return { x, y: islandTop + (islandH - h2) / 2, w, h: h2 };
  }

  /** Click on a small island: it becomes main, the old main shrinks to a small island. */
  promoteSecondary(side: "left" | "right") {
    const slot = this.i.slots[side];
    const id = slot.id;
    if (!id || this.i.promoting) return;
    this.i.promoting = true;
    const el = slot.el;
    el.classList.add("morph-out");              // travels toward the main island
    this.i.islandEl.classList.add("activity-swap"); // main content yields
    window.setTimeout(() => {
      // try/finally: if anything in here throws, the flags and classes must still be released,
      // otherwise the small islands stay hidden and the notch looks frozen.
      try {
        if (id === "download") {
          // The download takes the notch; whatever owned it (Spotify, timer) steps aside as the small island.
          if (this.i.dlSecTimer != null) { window.clearTimeout(this.i.dlSecTimer); this.i.dlSecTimer = null; }
          const last = this.i.dlLast;
          this.i.activities.promote("download");
          this.i.dlMain = true;
          if (last) this.i.onActivity({ ...last, ts: Date.now() });
        } else {
          const dlWasMain = this.i.dlMain && this.i.volumeHudOn && this.i.volumeHud.dataset.activityKind === "download";
          this.i.activities.promote(id);
          if (dlWasMain) {
            // Clicking the small island while the download owns the notch swaps them back.
            const last = this.i.dlLast;
            this.i.dlMain = false;
            this.i.endVolumeHud();
            if (last) this.i.takeDownloadAsIsland(last);
          }
        }
        this.i.promoting = false;
        this.i.refreshActivities();
        this.i.syncSpotifyCompact();
        this.i.syncTimerChip();
        // The demoted activity enters on its own side, growing out of the main island.
        el.classList.remove("show");
        el.classList.add("enter");
        el.classList.remove("morph-out");
        slot.shown = false;
        slot.key = "";
        this.i.paintSecondary();
        void el.offsetWidth;
        el.classList.remove("enter");
      } finally {
        this.i.promoting = false;
        el.classList.remove("morph-out", "enter");
        this.i.islandEl.classList.remove("activity-swap");
        this.i.dirty = true;
        this.i.ensureRunning();
      }
    }, 200);
  }

  /** The last three seconds of a countdown tick, one soft tick per second. */
}
