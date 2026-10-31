// Settings "Control Kit": how settings are chosen and changed, in the content area only.
//
//  • attachChooser(): every <select> picks the best way to be shown for what it holds —
//      2–3 short choices  → a pill switcher with a sliding highlight,
//      up to 6 choices    → big choice cards under the label (with a description when the option has one),
//      anything longer    → the dropdown (which now has search; see uiKit.ts).
//    The real <select> stays in the DOM, so every existing `.value`, `change` listener and `<option>` fill keeps working.
//  • installControlKit(): − / + steppers on number boxes, a show/hide eye on password boxes, filled sliders with a
//    value bubble, a sliding highlight on every segmented control, rows with a single switch that toggle when you click
//    anywhere on the row, a soft spotlight that follows the pointer, key-storage chips, and a small
//    "changed — Undo" toast after a select / switch / slider changes.
//
// Nothing here touches the sidebar, the top bar or the island. Styles: design/skin-settings-v2.css.

const idxDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex")!;
const inputValueDesc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!;

type Mode = "dropdown" | "pills" | "tiles";

export interface Chooser {
  sync(): void;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const CHECK_SVG =
  '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M3.5 8.5 6.6 11.5 12.5 4.8" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';

/** "Microsoft Edge voice (default)" → main + "default";  "openai/gpt-oss-120b" → "gpt-oss-120b" + "openai". */
export function splitLabel(label: string): { main: string; sub: string } {
  const text = (label ?? "").trim();
  const paren = /^(.*?)\s*\(([^()]{1,26})\)\s*$/.exec(text);
  if (paren && paren[1]) return { main: paren[1], sub: paren[2] };
  const slash = /^([A-Za-z0-9._-]{2,22})\/([^\s/]{2,})$/.exec(text);
  if (slash) return { main: slash[2], sub: slash[1] };
  return { main: text, sub: "" };
}

function hueOf(text: string): number {
  let n = 0;
  for (let i = 0; i < text.length; i++) n = (n * 31 + text.charCodeAt(i)) % 360;
  return n;
}

// ── Sliding highlight (pills, and the old segmented control) ─────────────────────────────────────────

/** A highlight that glides under the "on" button of `box`. `box` must be position:relative. */
export function slideThumb(box: HTMLElement, buttonSel: string): () => void {
  let thumb = box.querySelector<HTMLElement>(":scope > .cz-thumb");
  if (!thumb) {
    thumb = el("span", "cz-thumb");
    thumb.setAttribute("aria-hidden", "true");
    box.prepend(thumb);
  }
  const t = thumb;
  let first = true;
  // `buttonSel` may be a selector LIST (".seg-btn, button"): ".on" has to be added to every part of it, otherwise
  // the list reads ".seg-btn, button.on" and the highlight sticks to the first .seg-btn whatever is selected.
  const onSel = buttonSel.split(",").map((s) => `${s.trim()}.on`).join(", ");
  const place = () => {
    const on = box.querySelector<HTMLElement>(onSel);
    if (!on || !box.offsetWidth || !on.offsetWidth) {
      box.removeAttribute("data-thumb");
      return;
    }
    if (first) t.style.transition = "none";
    t.style.width = `${on.offsetWidth}px`;
    t.style.height = `${on.offsetHeight}px`;
    t.style.transform = `translate(${on.offsetLeft}px, ${on.offsetTop}px)`;
    box.setAttribute("data-thumb", "1");
    if (first) {
      first = false;
      void t.offsetWidth;
      requestAnimationFrame(() => (t.style.transition = ""));
    }
  };
  new ResizeObserver(place).observe(box);
  new MutationObserver(place).observe(box, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
  // Panels built while hidden (Recording) have no size yet: re-place when the box scrolls/shows into view and after layout settles.
  if (typeof IntersectionObserver !== "undefined") new IntersectionObserver(() => place()).observe(box);
  requestAnimationFrame(() => { place(); requestAnimationFrame(place); });
  window.setTimeout(place, 300);
  place();
  return place;
}

// ── Chooser: pills / cards / dropdown for one <select> ──────────────────────────────────────────────

export function attachChooser(select: HTMLSelectElement, wrap: HTMLElement): Chooser {
  const row = wrap.parentElement;
  const item = select.closest<HTMLElement>(".item");
  const homeRow: HTMLElement | null = row && row.matches(".item > .row") ? row : null;
  const inItem = !!item;

  const pills = el("div", "cz-pills");
  pills.setAttribute("role", "radiogroup");
  const tiles = el("div", "cz-tiles");
  tiles.setAttribute("role", "radiogroup");
  wrap.append(pills, tiles);

  let mode: Mode = "dropdown";
  let placePills: (() => void) | null = null;

  const options = () => Array.from(select.options);
  const selectedIndex = () => idxDesc.get!.call(select) as number;

  function decide(): Mode {
    if (!inItem || select.dataset.choice === "dropdown" || select.multiple || select.size > 1) return "dropdown";
    if (select.querySelector("optgroup")) return "dropdown";
    const labels = options().map((o) => (o.textContent ?? "").trim());
    const n = labels.length;
    if (n < 2) return "dropdown";
    const total = labels.reduce((s, l) => s + l.length, 0);
    const longest = labels.reduce((m, l) => Math.max(m, l.length), 0);
    if (select.dataset.choice === "pills" || (n <= 3 && total <= 30 && longest <= 16)) return "pills";
    // cards: when the options explain themselves (a description each), or are a few short names (providers)
    const described = options().some((o) => !!(o.dataset.desc || o.title));
    if (select.dataset.choice === "tiles" && n <= 8 && homeRow) return "tiles";
    if (n <= 6 && homeRow && ((described && longest <= 46) || (n <= 4 && longest <= 24))) return "tiles";
    return "dropdown";
  }

  function setMode(next: Mode) {
    if (next === mode) return;
    mode = next;
    wrap.dataset.mode = next;
    if (next === "tiles" && item && homeRow) {
      item.append(wrap);                       // cards sit under the label and the hint, full width
      item.classList.add("cz-stack");
    } else {
      if (homeRow && wrap.parentElement !== homeRow) homeRow.append(wrap);
      item?.classList.remove("cz-stack");
    }
  }

  function pick(i: number) {
    if (i === selectedIndex() || select.disabled) return;
    idxDesc.set!.call(select, i);
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
    sync();
  }

  function arrows(group: HTMLElement, sel: string) {
    group.addEventListener("keydown", (e) => {
      const k = e.key;
      if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(k)) return;
      const list = Array.from(group.querySelectorAll<HTMLButtonElement>(sel)).filter((b) => !b.disabled);
      if (!list.length) return;
      e.preventDefault();
      const cur = list.findIndex((b) => b === document.activeElement);
      const next = k === "Home" ? 0 : k === "End" ? list.length - 1 : (cur + (k === "ArrowRight" || k === "ArrowDown" ? 1 : -1) + list.length) % list.length;
      list[next].focus();
      list[next].click();
    });
  }
  arrows(pills, ".cz-pill");
  arrows(tiles, ".cz-tile");

  function paintPills() {
    pills.querySelectorAll(".cz-pill").forEach((b) => b.remove());   // keep the sliding highlight itself
    const sel = selectedIndex();
    options().forEach((o, i) => {
      const b = el("button", "cz-pill" + (i === sel ? " on" : ""), (o.textContent ?? "").trim());
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(i === sel));
      b.tabIndex = i === sel ? 0 : -1;
      b.disabled = select.disabled || o.disabled;
      b.addEventListener("click", () => pick(i));
      pills.append(b);
    });
    if (!placePills) placePills = slideThumb(pills, ".cz-pill");
    else placePills();
  }

  function paintTiles() {
    tiles.replaceChildren();
    const sel = selectedIndex();
    const mono = select.dataset.glyph === "mono";
    options().forEach((o, i) => {
      const { main, sub } = splitLabel((o.textContent ?? "").trim());
      const b = el("button", "cz-tile" + (i === sel ? " on" : ""));
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(i === sel));
      b.tabIndex = i === sel ? 0 : -1;
      b.disabled = select.disabled || o.disabled;
      if (mono) {
        const g = el("span", "cz-mono", (main.trim()[0] ?? "?").toUpperCase());
        g.style.setProperty("--mh", String(hueOf(main)));
        b.append(g);
      } else {
        b.append(el("span", "cz-radio"));
      }
      const body = el("span", "cz-tile-body");
      const head = el("span", "cz-tile-head");
      head.append(el("span", "cz-tile-title", main));
      if (sub) head.append(el("span", "cz-badge", sub));
      body.append(head);
      const desc = o.dataset.desc || o.title;
      if (desc) body.append(el("span", "cz-tile-desc", desc));
      b.append(body);
      const tick = el("span", "cz-tile-tick");
      tick.innerHTML = CHECK_SVG;
      b.append(tick);
      b.addEventListener("click", () => pick(i));
      tiles.append(b);
    });
  }

  function sync() {
    setMode(decide());
    wrap.classList.toggle("disabled", select.disabled);
    if (mode === "pills") paintPills();
    else if (mode === "tiles") paintTiles();
  }

  sync();
  new MutationObserver(sync).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "label"], characterData: true });
  return { sync };
}

// ── Everything else ──────────────────────────────────────────────────────────────────────────────

const inContent = (n: Element) => !!n.closest(".content");

function stepper(input: HTMLInputElement) {
  const wrap = el("span", "cz-num");
  const minus = el("button", "cz-step", "−");
  const plus = el("button", "cz-step", "+");
  for (const [b, label] of [[minus, "Decrease"], [plus, "Increase"]] as const) {
    b.type = "button";
    b.tabIndex = -1;
    b.setAttribute("aria-label", label);
  }
  input.parentNode?.insertBefore(wrap, input);
  wrap.append(minus, input, plus);

  const bump = (dir: 1 | -1) => {
    const before = input.value;
    try {
      if (dir > 0) input.stepUp();
      else input.stepDown();
    } catch { /* not a number yet */ }
    if (input.value !== before) input.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const hold = (b: HTMLButtonElement, dir: 1 | -1) => {
    let t = 0, iv = 0, start = "";
    const stop = () => {
      window.clearTimeout(t);
      window.clearInterval(iv);
      if (start !== "" && input.value !== start) input.dispatchEvent(new Event("change", { bubbles: true }));
      start = "";
    };
    b.addEventListener("pointerdown", (e) => {
      if (input.disabled || e.button !== 0) return;
      e.preventDefault();
      start = input.value;
      bump(dir);
      t = window.setTimeout(() => { iv = window.setInterval(() => bump(dir), 70); }, 380);
      window.addEventListener("pointerup", stop, { once: true });
    });
    b.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); const s = input.value; bump(dir); if (input.value !== s) input.dispatchEvent(new Event("change", { bubbles: true })); }
    });
  };
  hold(minus, -1);
  hold(plus, 1);
  const sync = () => { minus.disabled = plus.disabled = input.disabled; wrap.classList.toggle("disabled", input.disabled); };
  new MutationObserver(sync).observe(input, { attributes: true, attributeFilter: ["disabled"] });
  sync();
}

const EYE_ON = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M3 3l18 18M10.6 5.7A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-3.3 4M6.6 7.6A16 16 0 0 0 2.5 12S6 18.5 12 18.5c1.5 0 2.8-.4 4-.9M9.9 9.9a3 3 0 0 0 4.2 4.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function eye(input: HTMLInputElement) {
  const wrap = el("span", "cz-pass");
  const s = input.style;
  wrap.style.flex = s.flex;
  wrap.style.width = s.width;
  wrap.style.minWidth = s.minWidth || "0";
  wrap.style.maxWidth = s.maxWidth;
  s.flex = "1 1 auto";
  s.width = "100%";
  s.minWidth = "0";
  const b = el("button", "cz-eye");
  b.type = "button";
  b.tabIndex = -1;
  b.title = "Show what you typed";
  b.setAttribute("aria-label", "Show or hide the text");
  b.setAttribute("aria-pressed", "false");
  b.innerHTML = EYE_ON;
  input.parentNode?.insertBefore(wrap, input);
  wrap.append(input, b);
  b.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    b.innerHTML = show ? EYE_OFF : EYE_ON;
    b.setAttribute("aria-pressed", String(show));
    b.title = show ? "Hide it again" : "Show what you typed";
    input.focus();
  });
}

function range(input: HTMLInputElement) {
  const wrap = el("span", "cz-rng");
  const tip = el("span", "cz-tip");
  tip.setAttribute("aria-hidden", "true");
  input.parentNode?.insertBefore(wrap, input);
  wrap.append(input, tip);
  const paint = () => {
    const min = Number(input.min) || 0, max = input.max === "" ? 100 : Number(input.max);
    const p = max > min ? (Number(input.value) - min) / (max - min) : 0;
    wrap.style.setProperty("--cz-p", String(Math.max(0, Math.min(1, p))));
    // the page's own readout (a .val / <output> next to the slider) already holds the nicely formatted value
    const ro = wrap.parentElement?.querySelector<HTMLElement>(":scope > .val, :scope > output");
    tip.textContent = ro?.textContent?.trim() || input.value;
  };
  Object.defineProperty(input, "value", {
    configurable: true,
    get() { return inputValueDesc.get!.call(this); },
    set(v: string) { inputValueDesc.set!.call(this, v); paint(); },
  });
  input.addEventListener("input", paint);
  input.addEventListener("change", paint);
  paint();
  const lift = (on: boolean) => wrap.classList.toggle("drag", on);
  input.addEventListener("pointerdown", () => { lift(true); window.addEventListener("pointerup", () => lift(false), { once: true }); });
}

function keyChips(list: HTMLElement) {
  for (const chip of Array.from(list.querySelectorAll<HTMLElement>(":scope > .chip"))) {
    if (chip.dataset.cz) continue;
    const text = Array.from(chip.querySelectorAll("span")).pop();
    const m = /^([^:]+):\s*(.+)$/.exec(text?.textContent ?? "");
    if (!text || !m) continue;
    chip.dataset.cz = "1";
    const name = el("span", "cz-chip-name", m[1]);
    const state = el("span", "cz-chip-state", m[2]);
    text.replaceChildren(name, state);
  }
}

function upgrade(scope: ParentNode) {
  scope.querySelectorAll<HTMLInputElement>('input[type="number"]:not([data-cz])').forEach((i) => {
    if (!inContent(i)) return;
    i.dataset.cz = "1";
    stepper(i);
  });
  scope.querySelectorAll<HTMLInputElement>('input[type="password"]:not([data-cz])').forEach((i) => {
    if (!inContent(i)) return;
    i.dataset.cz = "1";
    eye(i);
  });
  scope.querySelectorAll<HTMLInputElement>('input[type="range"]:not([data-cz])').forEach((i) => {
    if (!inContent(i)) return;
    i.dataset.cz = "1";
    range(i);
  });
  scope.querySelectorAll<HTMLElement>(".content .seg:not([data-cz])").forEach((s) => {
    s.dataset.cz = "1";
    slideThumb(s, ".seg-btn, button");
  });
  scope.querySelectorAll<HTMLElement>(".p0-keys").forEach((l) => {
    keyChips(l);
    if (!l.dataset.czo) {
      l.dataset.czo = "1";
      new MutationObserver(() => keyChips(l)).observe(l, { childList: true });
    }
  });
}

// ── Row behaviour: spotlight and "click the row to flip its switch" ────────────────────────────────

function lonelySwitch(row: Element): HTMLElement | null {
  const ctl = row.querySelectorAll("button, input, select, textarea, a");
  return ctl.length === 1 && ctl[0].classList.contains("switch") ? (ctl[0] as HTMLElement) : null;
}

function rowBehaviour(root: HTMLElement) {
  let raf = 0;
  root.addEventListener("pointermove", (e) => {
    const t = e.target as Element | null;
    const it = t?.closest?.<HTMLElement>(".content .group > .item");
    if (!it) return;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      const r = it.getBoundingClientRect();
      it.style.setProperty("--mx", `${e.clientX - r.left}px`);
      it.style.setProperty("--my", `${e.clientY - r.top}px`);
    });
  }, { passive: true });

  root.addEventListener("pointerover", (e) => {
    const row = (e.target as Element | null)?.closest?.<HTMLElement>(".content .item > .row");
    if (row) row.classList.toggle("cz-click", !!lonelySwitch(row));
  });

  root.addEventListener("click", (e) => {
    const t = e.target as Element | null;
    const row = t?.closest?.(".content .item > .row");
    if (!t || !row) return;
    if (t.closest("button, input, select, textarea, a, .dd, .cz-num, .cz-pass, kbd")) return;
    if (window.getSelection()?.toString()) return;
    lonelySwitch(row)?.click();
  });
}

// ── "Changed — Undo" toast ───────────────────────────────────────────────────────────────────────────

type Undo = () => void;

function labelOf(c: Element): string {
  return c.closest(".item")?.querySelector(":scope > .row > label")?.textContent?.trim() ?? "";
}

function stateOf(c: Element): string | null {
  if (c instanceof HTMLSelectElement) return c.value;
  if (c instanceof HTMLInputElement) return c.type === "checkbox" ? String(c.checked) : c.type === "range" || c.type === "number" ? c.value : null;
  if (c.classList.contains("switch")) return c.classList.contains("on") ? "on" : "off";
  return null;
}

function undoToasts(root: HTMLElement) {
  const snap = new WeakMap<Element, string>();
  let toast: HTMLElement | null = null;
  let timer = 0;
  let undoing = false;
  let live: { c: Element; prev: string } | null = null;

  const hide = () => {
    window.clearTimeout(timer);
    const t = toast;
    toast = null;
    live = null;
    if (t) { t.classList.remove("in"); window.setTimeout(() => t.remove(), 220); }
  };

  const show = (c: Element, prev: string, valueText: string, undo: Undo) => {
    const label = labelOf(c);
    if (!label) return;
    // while the same control keeps changing (a slider drag), undo goes back to where it started
    const origin = live && live.c === c ? live.prev : prev;
    live = { c, prev: origin };
    if (!toast) {
      toast = el("div", "cz-toast");
      toast.setAttribute("role", "status");
      document.body.append(toast);
      requestAnimationFrame(() => toast?.classList.add("in"));
    }
    toast.replaceChildren();
    const msg = el("span", "cz-toast-msg");
    msg.append(el("b", "", label), document.createTextNode(` · ${valueText}`));
    const btn = el("button", "cz-toast-undo", "Undo");
    btn.type = "button";
    btn.addEventListener("click", () => { undoing = true; try { undo(); } finally { undoing = false; } hide(); });
    const bar = el("i", "cz-toast-bar");
    toast.append(el("span", "cz-toast-dot"), msg, btn, bar);
    bar.style.animation = "none";
    void bar.offsetWidth;
    bar.style.animation = "";
    window.clearTimeout(timer);
    timer = window.setTimeout(hide, 6000);
  };

  const remember = (e: Event) => {
    const it = (e.target as Element | null)?.closest?.(".content .item");
    if (!it) return;
    it.querySelectorAll("select, input[type=range], input[type=number], input[type=checkbox], .switch").forEach((c) => {
      const s = stateOf(c);
      if (s != null) snap.set(c, s);
    });
  };
  root.addEventListener("pointerdown", remember, true);
  root.addEventListener("keydown", remember, true);
  root.addEventListener("focusin", remember, true);

  root.addEventListener("change", (e) => {
    const c = e.target as Element | null;
    if (!c || undoing || !inContent(c)) return;
    const now = stateOf(c);
    const prev = snap.get(c);
    if (now == null) return;
    snap.set(c, now);
    if (prev == null || prev === now) return;
    if (c instanceof HTMLSelectElement) {
      const text = splitLabel(c.options[c.selectedIndex]?.textContent ?? now).main || now;
      show(c, prev, text, () => { c.value = prev; c.dispatchEvent(new Event("change", { bubbles: true })); snap.set(c, prev); });
    } else if (c instanceof HTMLInputElement && (c.type === "range" || c.type === "number")) {
      const ro = c.closest(".item")?.querySelector<HTMLElement>(".val, output")?.textContent?.trim();
      show(c, prev, ro || now, () => { c.value = prev; c.dispatchEvent(new Event("input", { bubbles: true })); c.dispatchEvent(new Event("change", { bubbles: true })); snap.set(c, prev); });
    }
  }, true);

  root.addEventListener("click", (e) => {
    const sw = (e.target as Element | null)?.closest?.<HTMLElement>(".content .switch");
    if (!sw || undoing) return;
    const before = sw.classList.contains("on");
    window.setTimeout(() => {
      const after = sw.classList.contains("on");
      if (after === before) return;
      show(sw, before ? "on" : "off", after ? "On" : "Off", () => sw.click());
    }, 0);
  }, true);

  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && toast) hide(); });
}

/** Call once, after the settings shell is in the page. */
export function installControlKit(root: HTMLElement = document.body): void {
  upgrade(root);
  new MutationObserver((records) => {
    for (const rec of records) {
      rec.addedNodes.forEach((n) => { if (n instanceof HTMLElement) upgrade(n); });
    }
  }).observe(root, { childList: true, subtree: true });
  rowBehaviour(root);
  undoToasts(root);
}
