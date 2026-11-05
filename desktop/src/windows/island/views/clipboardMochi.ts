// Mochi's reaction to files on the Clipboard tab.
//
//   hovering   Mochi pops up in the drop hint, eyes wide, following the file with its eyes
//   dropped    the file(s) fly into Mochi, which gulps and munches ("Saving …")
//   saved      Mochi celebrates, green tick badge, "Saved"
//   failed     Mochi shakes its head, red cross badge and the reason
//
// This only listens (see core/fileDropFx.ts) and is driven by the hint's own hover state: it never
// touches how a drop is received or saved. It is NOT tied to Settings → Lyra mascot (that switch is the
// little desktop Lyra, off by default): the drop reaction is part of the Clipboard tab itself. Only the
// Mochi's shape is taken from settings, so it matches the rest of the app. If the canvas cannot be
// created, the original "Drop to add" hint is shown unchanged.

import { createCharacter, gazeToward, type Character } from "../../../shared/mascot/character";
import { getFeatures, onFeatures } from "../../../shared/core/featuresCache";
import { Sound } from "../../../shared/core/sound";
import { FileDropFx, type DropFxFile, type DropFxOrigin } from "../../../shared/core/fileDropFx";
import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import "./clipboardMochi.css";

type Phase = "idle" | "hover" | "feeding" | "result";

export interface DropMochi {
  /** A file drag is over the tab (idempotent: called on every dragover). `count` = files in the drag, if known. */
  hover(on: boolean, count?: number): void;
  /** Where the drag is, in client coordinates: Mochi's eyes follow it. */
  look(clientX: number, clientY: number): void;
  destroy(): void;
}

const FLY_MS = 540;          // one file flying into Mochi
const STAGGER_MS = 120;      // gap between several files
const MIN_MUNCH_MS = 700;    // Mochi always gets to chew a moment, even when the save was instant
const HOLD_OK_MS = 1000;     // how long the "Saved" reaction stays
const HOLD_FAIL_MS = 1500;   // an error stays a little longer so it can be read
const WATCHDOG_MS = 30_000;  // a save that never reports back must not leave Mochi stuck
const MAX_FLYING = 3;        // more than this and it is just a blur

const extOf = (n: string) => (n.includes(".") ? (n.split(".").pop() || "") : "").slice(0, 4).toUpperCase() || "FILE";
const shorten = (n: string) => (n.length > 24 ? `${n.slice(0, 14)}…${n.slice(-8)}` : n);
const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Adds Mochi to the Clipboard tab's drop hint.
 * `hint` is the existing `.cbx-drop-hint`; `label` is its text (restored when Mochi is done).
 */
export function mountDropMochi(card: HTMLElement, hint: HTMLElement, label: HTMLElement): DropMochi {
  const defaultLabel = label.textContent || "Drop to add";

  const host = h("div", { class: "cbx-drop-mochi" });
  const badge = h("span", { class: "cbx-drop-badge" });
  const stage = h("div", { class: "cbx-drop-stage", "aria-hidden": "true" }, host, badge);
  const fly = h("div", { class: "cbx-drop-fly", "aria-hidden": "true" });
  hint.insertBefore(stage, hint.firstChild);   // above the original doc icon, which the CSS hides while Mochi shows
  hint.append(fly);
  // Screen readers get the same words the label shows.
  label.setAttribute("role", "status");
  label.setAttribute("aria-live", "polite");

  // ── The character: created on first use, drawn only while the hint is up ──
  let ch: Character | null = null;
  let enabled = true;   // only turns false if the character cannot be created at all
  let shape = "cloud";

  function ensure(): Character | null {
    if (!enabled) return null;
    if (!ch) {
      try {
        ch = createCharacter(host, { tight: true, sharp: 1.5 });
        ch.setShape?.(shape);
        ch.setActive?.(false);
      } catch (err) {
        console.warn("[clipboard] Mochi could not be created, showing the plain drop hint:", err);
        enabled = false;   // canvas unavailable: fall back to the plain hint
        ch = null;
      }
    }
    return ch;
  }

  async function readShape(): Promise<void> {
    const p = (await getFeatures()) as { mascot_shape?: string } | null;
    if (!p) return;   // backend not up yet: keep the last value
    shape = p.mascot_shape ?? "cloud";
    ch?.setShape?.(shape);
  }
  void readShape();
  const offFeatures = onFeatures(() => void readShape());

  // ── State ──
  let phase: Phase = "idle";
  let restT = 0, emoteT = 0, parkT = 0;     // pending un-hover, delayed hover emote, stop-drawing
  let seq: number[] = [];                   // timers of the current eat / result sequence
  let munchDone = false;
  let pending: { ok: boolean; message?: string } | null = null;   // result that arrived while Mochi still chews
  let fedCount = 0;

  const later = (fn: () => void, ms: number) => { seq.push(window.setTimeout(fn, ms)); };
  const clearSeq = () => { for (const t of seq) window.clearTimeout(t); seq = []; };
  const clearIdle = () => { window.clearTimeout(restT); window.clearTimeout(emoteT); window.clearTimeout(parkT); };

  /** Make the Mochi visible and drawing; null when it is switched off. */
  function show(): Character | null {
    const c = ensure();
    if (!c) return null;
    window.clearTimeout(parkT);
    hint.classList.add("has-mochi");
    c.setActive?.(true);
    return c;
  }

  function bump() {   // one squash: a gulp
    stage.classList.remove("gulp");
    void stage.offsetWidth;   // restart the animation
    stage.classList.add("gulp");
  }

  /** Back to the plain hint. */
  function rest() {
    clearSeq();
    clearIdle();
    phase = "idle";
    pending = null;
    munchDone = false;
    FileDropFx.busy = false;
    card.classList.remove("feeding");
    stage.classList.remove("eager", "gulp", "shake");
    badge.classList.remove("on");
    delete hint.dataset.phase;
    label.textContent = defaultLabel;
    fly.replaceChildren();
    // Let the hint fade out before Mochi stops drawing.
    parkT = window.setTimeout(() => {
      if (phase !== "idle") return;
      ch?.setEmote("idle");
      ch?.look(0, 0);
      ch?.setActive?.(false);
      hint.classList.remove("has-mochi");
    }, 320);
  }

  // ── Hovering ──
  function hover(on: boolean, count = 0): void {
    if (phase === "feeding" || phase === "result") return;   // the drop already happened: the sequence owns the hint
    if (on) {
      window.clearTimeout(restT);
      if (count > 1) label.textContent = `Drop ${count} files to add`;
      if (phase === "hover") return;
      const c = show();
      if (!c) return;
      phase = "hover";
      hint.dataset.phase = "hover";
      stage.classList.add("eager");
      c.look(0, 0.5);
      c.setEmote("surprised");                                    // wide eyes: something is coming
      emoteT = window.setTimeout(() => { if (phase === "hover") c.setEmote("happy"); }, 480);   // then eager
    } else if (phase === "hover") {
      window.clearTimeout(restT);
      restT = window.setTimeout(rest, 140);                       // small delay: dragleave flickers between child elements
    }
  }

  function look(x: number, y: number): void {
    if (phase !== "hover" || !ch) return;
    const [gx, gy] = gazeToward(host, x, y);
    ch.look(gx, gy);
  }

  // ── The drop: the file(s) fly into Mochi ──
  function spawnChip(name: string, i: number, n: number, from?: DropFxOrigin): void {
    const hr = hint.getBoundingClientRect();
    const sr = stage.getBoundingClientRect();
    const cx = sr.left + sr.width / 2 - hr.left;     // Mochi's centre inside the hint
    const cy = sr.top + sr.height / 2 - hr.top;
    const spread = (i - (n - 1) / 2) * 36;
    const sx = from ? from.x - hr.left - cx + spread * 0.4 : spread;           // start where it was dropped, else from above
    const sy = from ? from.y - hr.top - cy : -cy + 6;
    const rot = (i % 2 ? 1 : -1) * (8 + 4 * i);

    const chip = h("div", { class: "cbx-chip" }, svg(ICONS.doc, 16), h("span", { text: extOf(name) }));
    chip.style.left = `${cx}px`;
    chip.style.top = `${cy}px`;
    fly.append(chip);
    if (typeof chip.animate !== "function") { chip.remove(); return; }
    const anim = chip.animate([
      { transform: `translate(${sx}px, ${sy}px) rotate(${rot}deg) scale(1.05)`, opacity: 0, offset: 0 },
      { opacity: 1, offset: 0.12 },
      { transform: `translate(${sx * 0.4}px, ${sy * 0.4 - 16}px) rotate(${-rot * 0.4}deg) scale(.9)`, opacity: 1, offset: 0.6, easing: "ease-in" },
      { transform: "translate(0px, 2px) rotate(0deg) scale(.16)", opacity: 0.15, offset: 1 },
    ], { duration: FLY_MS, delay: i * STAGGER_MS, easing: "cubic-bezier(.3,.1,.6,1)", fill: "both" });
    anim.onfinish = () => chip.remove();
  }

  function feed(files: DropFxFile[], from?: DropFxOrigin): void {
    const c = show();
    if (!c) return;
    clearIdle();
    clearSeq();
    phase = "feeding";
    FileDropFx.busy = true;
    pending = null;
    munchDone = false;
    fedCount = files.length;
    card.classList.add("feeding");
    hint.dataset.phase = "feeding";
    badge.classList.remove("on");
    stage.classList.remove("shake");
    stage.classList.add("eager");
    label.textContent = fedCount === 1 ? `Saving ${shorten(files[0].name)}…` : `Saving ${fedCount} files…`;
    c.look(0, 0.5);
    c.setEmote("surprised");

    const flying = reducedMotion() ? 0 : Math.min(fedCount, MAX_FLYING);
    fly.replaceChildren();
    for (let i = 0; i < flying; i++) spawnChip(files[i].name, i, flying, from);

    // The first file lands: gulp, then chew.
    const firstLand = flying ? FLY_MS : 120;
    later(() => {
      c.look(0, 0);
      c.setEmote("munch");
      bump();
      Sound.play("gulp");
    }, firstLand);
    // Any further flying files each make Mochi gulp again.
    for (let i = 1; i < flying; i++) later(bump, FLY_MS + i * STAGGER_MS);

    const munchEnd = firstLand + Math.max(0, flying - 1) * STAGGER_MS + MIN_MUNCH_MS;
    later(() => {
      munchDone = true;
      if (pending) showResult(pending.ok, pending.message);
    }, munchEnd);
    later(rest, WATCHDOG_MS);   // cleared by showResult once the answer is in
  }

  // ── The answer: saved or not ──
  function showResult(ok: boolean, message?: string): void {
    clearSeq();
    clearIdle();
    const c = show();
    if (!c) { rest(); return; }
    phase = "result";
    FileDropFx.busy = true;
    pending = null;
    card.classList.add("feeding");   // keeps the hint up (also when the answer comes without a drop, e.g. a folder)
    hint.dataset.phase = ok ? "ok" : "fail";
    stage.classList.remove("shake");
    stage.classList.add("eager");
    badge.className = `cbx-drop-badge ${ok ? "ok" : "fail"}`;
    badge.replaceChildren(ok ? svg(ICONS.check, 12, { stroke: 3 }) : svg(ICONS.xmark, 10));
    void badge.offsetWidth;
    badge.classList.add("on");
    if (ok) {
      c.look(0, 0);
      c.setEmote("celebrate");
      label.textContent = fedCount > 1 ? `Saved ${fedCount} files` : "Saved";
      Sound.play("approve");
    } else {
      c.setEmote("error");
      stage.classList.remove("eager");
      stage.classList.add("shake");
      label.textContent = message || "Couldn't save that";
      Sound.play("error");
    }
    later(rest, ok ? HOLD_OK_MS : HOLD_FAIL_MS);
  }

  function settle(ok: boolean, message?: string): void {
    if (!enabled) return;
    if (phase === "feeding" && !munchDone) { pending = { ok, message }; return; }   // let it finish chewing first
    showResult(ok, message);
  }

  const offStart = FileDropFx.onStart((files, from) => feed(files, from));
  const offDone = FileDropFx.onDone((ok, message) => settle(ok, message));

  return {
    hover,
    look,
    destroy() {
      offStart();
      offDone();
      offFeatures();
      clearSeq();
      clearIdle();
      FileDropFx.busy = false;
      ch?.destroy();
      ch = null;
      stage.remove();
      fly.remove();
    },
  };
}
