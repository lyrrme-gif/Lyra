// Settings UI kit: nicer pickers than the browser's own.
//
//  • enhanceSelects(): every <select> in the window becomes a dark, rounded dropdown with a check
//    mark on the chosen row and full keyboard support. The real <select> stays in the DOM (hidden), so
//    all the existing code that reads `.value`, sets `.value`, fills it with <option>s or listens for
//    "change" keeps working unchanged.
//  • segmented(): a small pill-shaped switcher for 2–4 short choices.

import { attachChooser, splitLabel, type Chooser } from "./choiceKit";
import { formatCombo } from "../design/shortcuts";

const CHECK = '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3.5 8.5 6.6 11.5 12.5 4.8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const CHEVRON = '<svg viewBox="0 0 12 16" width="10" height="13" aria-hidden="true"><path d="M3 6.2 6 3.2l3 3M3 9.8l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

const valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!;
const indexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex")!;

let openMenu: { close: () => void } | null = null;

function enhance(select: HTMLSelectElement): void {
  if (select.dataset.dd === "1" || select.multiple || select.size > 1) return;
  select.dataset.dd = "1";

  // <select data-seg="1"> (a few short, fixed choices) is drawn as a segmented control instead.
  if (select.dataset.seg === "1") {
    const seg = document.createElement("div");
    seg.className = "seg";
    seg.setAttribute("role", "radiogroup");
    select.parentNode?.insertBefore(seg, select);
    select.classList.add("dd-native");
    select.tabIndex = -1;
    seg.append(select);
    const paint = () => {
      seg.querySelectorAll(".seg-btn").forEach((b) => b.remove());
      Array.from(select.options).forEach((o, i) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "seg-btn" + (i === select.selectedIndex ? " on" : "");
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", String(i === select.selectedIndex));
        b.textContent = o.textContent;
        b.disabled = select.disabled;
        b.addEventListener("click", () => {
          if (i === select.selectedIndex) return;
          indexDesc.set!.call(select, i);
          paint();
          select.dispatchEvent(new Event("input", { bubbles: true }));
          select.dispatchEvent(new Event("change", { bubbles: true }));
        });
        seg.append(b);
      });
    };
    Object.defineProperty(select, "value", { configurable: true, get() { return valueDesc.get!.call(this); }, set(v: string) { valueDesc.set!.call(this, v); paint(); } });
    new MutationObserver(paint).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
    paint();
    return;
  }

  const wrap = document.createElement("span");
  wrap.className = "dd";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "dd-btn";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  const label = document.createElement("span");
  label.className = "dd-label";
  const chev = document.createElement("span");
  chev.className = "dd-chev";
  chev.innerHTML = CHEVRON;
  btn.append(label, chev);

  select.parentNode?.insertBefore(wrap, select);
  select.classList.add("dd-native");
  select.tabIndex = -1;
  wrap.append(btn, select);

  let chooser: Chooser | null = null;       // pills / cards for a short list (choiceKit.ts)
  const refresh = () => {
    chooser?.sync();
    const opt = select.options[select.selectedIndex];
    label.textContent = opt ? opt.textContent : "";
    label.classList.toggle("empty", !opt);
    btn.disabled = select.disabled;
    wrap.classList.toggle("disabled", select.disabled);
    // The control is as wide as its longest choice, so rows do not jump around when you change it.
    const longest = Array.from(select.options).reduce((m, o) => Math.max(m, (o.textContent ?? "").length), 0);
    wrap.style.setProperty("--dd-ch", String(Math.min(34, Math.max(8, longest))));
  };

  // Programmatic `select.value = …` / `selectedIndex = …` must update the visible label too.
  Object.defineProperty(select, "value", {
    configurable: true,
    get() { return valueDesc.get!.call(this); },
    set(v: string) { valueDesc.set!.call(this, v); refresh(); },
  });
  Object.defineProperty(select, "selectedIndex", {
    configurable: true,
    get() { return indexDesc.get!.call(this); },
    set(v: number) { indexDesc.set!.call(this, v); refresh(); },
  });
  new MutationObserver(refresh).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "label"], characterData: true });
  select.addEventListener("change", refresh);
  chooser = attachChooser(select, wrap);
  refresh();

  // ── The menu: drawn on <body> so no card's overflow can clip it ──
  const open = () => {
    if (select.disabled) return;
    openMenu?.close();
    const menu = document.createElement("div");
    menu.className = "dd-menu";
    menu.setAttribute("role", "listbox");
    const rows: HTMLButtonElement[] = [];
    let group: HTMLElement | null = null;
    const addOption = (o: HTMLOptionElement) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "dd-opt";
      row.setAttribute("role", "option");
      row.dataset.value = o.value;
      row.disabled = o.disabled;
      const text = document.createElement("span");
      text.className = "dd-opt-text";
      const parts = splitLabel(o.textContent ?? "");
      text.textContent = parts.main;
      row.dataset.label = (o.textContent ?? "").toLowerCase();
      const tick = document.createElement("span");
      tick.className = "dd-tick";
      tick.innerHTML = CHECK;
      row.append(text);
      if (parts.sub) {
        const sub = document.createElement("span");
        sub.className = "dd-sub";
        sub.textContent = parts.sub;
        row.append(sub);
      }
      row.append(tick);
      const selected = o.index === select.selectedIndex;
      row.classList.toggle("sel", selected);
      row.setAttribute("aria-selected", String(selected));
      row.addEventListener("click", () => pick(o.index));
      row.addEventListener("pointermove", () => focusRow(rows.indexOf(row)));
      rows.push(row);
      (group ?? menu).append(row);
    };
    for (const child of Array.from(select.children)) {
      if (child instanceof HTMLOptGroupElement) {
        const head = document.createElement("div");
        head.className = "dd-group";
        head.textContent = child.label;
        menu.append(head);
        group = menu;
        for (const o of Array.from(child.children)) if (o instanceof HTMLOptionElement) addOption(o);
      } else if (child instanceof HTMLOptionElement) {
        group = null;
        addOption(child);
      }
    }
    // A long list (models, devices, voices) gets a search box: type to narrow it down.
    let search: HTMLInputElement | null = null;
    let none: HTMLElement | null = null;
    if (rows.length >= 8) {
      const box = document.createElement("div");
      box.className = "dd-search";
      box.innerHTML = '<svg viewBox="0 0 20 20" width="14" height="14" aria-hidden="true"><circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="m13.2 13.2 3.6 3.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
      search = document.createElement("input");
      search.type = "text";
      search.placeholder = `Search ${rows.length} choices…`;
      search.setAttribute("aria-label", "Search the list");
      search.spellcheck = false;
      search.autocomplete = "off";
      box.append(search);
      menu.prepend(box);
      none = document.createElement("div");
      none.className = "dd-none";
      none.textContent = "Nothing matches";
      none.hidden = true;
      menu.append(none);
      search.addEventListener("input", () => {
        const q = search!.value.trim().toLowerCase();
        let shown = 0;
        for (const r of rows) {
          const hit = !q || (r.dataset.label ?? "").includes(q);
          r.hidden = !hit;
          if (hit) shown++;
        }
        menu.querySelectorAll<HTMLElement>(".dd-group").forEach((g) => { g.hidden = !!q; });
        none!.hidden = shown > 0;
        const first = rows.findIndex((r) => !r.hidden && !r.disabled);
        if (first >= 0) focusRow(first);
      });
    }
    document.body.append(menu);
    if (search) search.focus({ preventScroll: true });

    // Place under the button; flip above when there is no room.
    const r = btn.getBoundingClientRect();
    const minW = Math.max(r.width, 150);
    menu.style.minWidth = `${minW}px`;
    menu.style.maxHeight = `${Math.max(160, Math.min(320, window.innerHeight - 24))}px`;
    const mh = menu.offsetHeight, mw = menu.offsetWidth;
    const below = window.innerHeight - r.bottom - 8;
    const top = below >= mh || below >= r.top - 8 ? r.bottom + 6 : r.top - mh - 6;
    menu.style.top = `${Math.max(8, Math.min(window.innerHeight - mh - 8, top))}px`;
    menu.style.left = `${Math.max(8, Math.min(window.innerWidth - mw - 8, r.right - mw))}px`;
    requestAnimationFrame(() => menu.classList.add("in"));

    let cur = Math.max(0, rows.findIndex((x) => x.classList.contains("sel")));
    function focusRow(i: number) {
      if (i < 0 || i >= rows.length) return;
      cur = i;
      rows.forEach((x, n) => x.classList.toggle("hot", n === i));
      rows[i].scrollIntoView({ block: "nearest" });
    }
    function pick(index: number) {
      close();
      if (index === select.selectedIndex) return;
      indexDesc.set!.call(select, index);
      refresh();
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    const step = (d: number) => {
      let i = cur;
      for (let n = 0; n < rows.length; n++) {
        i = (i + d + rows.length) % rows.length;
        if (!rows[i].disabled && !rows[i].hidden) break;
      }
      focusRow(i);
    };
    let typed = "", typedAt = 0;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Tab") { close(); return; }
      // with a search box open, letters / Backspace belong to it
      if (search && (e.key === "Backspace" || (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !(e.key === " " && !search.value)))) return;
      e.stopPropagation();                     // the button underneath must not react (it would reopen the menu)
      if (e.key === "Escape") { e.preventDefault(); close(); btn.focus(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); step(-1); }
      else if (e.key === "Home") { e.preventDefault(); focusRow(0); }
      else if (e.key === "End") { e.preventDefault(); focusRow(rows.length - 1); }
      else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        const o = select.options[Array.from(select.options).findIndex((x) => x.value === rows[cur]?.dataset.value)];
        if (o) pick(o.index);
      } else if (!search && e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
        const t = performance.now();
        typed = t - typedAt > 700 ? e.key.toLowerCase() : typed + e.key.toLowerCase();
        typedAt = t;
        const hit = rows.findIndex((x) => !x.disabled && (x.dataset.label ?? "").startsWith(typed));
        if (hit >= 0) focusRow(hit);
      }
    };
    const onDown = (e: Event) => { if (!menu.contains(e.target as Node) && !btn.contains(e.target as Node)) close(); };
    const onScroll = (e: Event) => { if (!menu.contains(e.target as Node)) close(); };
    function close() {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("blur", close);
      window.removeEventListener("resize", close);
      btn.setAttribute("aria-expanded", "false");
      wrap.classList.remove("open");
      menu.classList.remove("in");
      window.setTimeout(() => menu.remove(), 120);
      if (openMenu === handle) openMenu = null;
    }
    const handle = { close };
    openMenu = handle;
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("blur", close);
    window.addEventListener("resize", close);
    btn.setAttribute("aria-expanded", "true");
    wrap.classList.add("open");
    focusRow(cur);
  };

  btn.addEventListener("click", () => (wrap.classList.contains("open") ? openMenu?.close() : open()));
  btn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(); }
  });
}

/** Upgrades every <select> under `root` now, and every one added later. */
export function enhanceSelects(root: ParentNode = document.body): void {
  root.querySelectorAll("select").forEach((s) => enhance(s as HTMLSelectElement));
  new MutationObserver((records) => {
    for (const rec of records) {
      rec.addedNodes.forEach((n) => {
        if (n instanceof HTMLSelectElement) enhance(n);
        else if (n instanceof HTMLElement) n.querySelectorAll("select").forEach((s) => enhance(s as HTMLSelectElement));
      });
    }
  }).observe(root instanceof Node ? root : document.body, { childList: true, subtree: true });
}

/** A pill-shaped switcher: one highlighted choice, click another to pick it. */
export function segmented<T extends string>(options: Array<[T, string]>, current: T, onPick: (v: T) => void): HTMLElement {
  const el = document.createElement("div");
  el.className = "seg";
  el.setAttribute("role", "radiogroup");
  const buttons = new Map<T, HTMLButtonElement>();
  const mark = (v: T) => {
    buttons.forEach((b, k) => {
      b.classList.toggle("on", k === v);
      b.setAttribute("aria-checked", String(k === v));
    });
  };
  for (const [v, text] of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "seg-btn";
    b.setAttribute("role", "radio");
    b.textContent = text;
    b.addEventListener("click", () => { mark(v); onPick(v); });
    buttons.set(v, b);
    el.append(b);
  }
  mark(current);
  return el;
}

// ═══ Lyra design-language kit (Phase 4) ═════════════════════════════════════════
// Every component below emits the same classes and DOM shape the existing helpers in main.ts use
// (card → section.card > h3 + div.group, row → div.item > div.row + div.hint, switch → button.switch),
// so panels built with them are restyled by design/skin-settings.css like everything else, and
// Settings search (which reads data-s on .item) keeps finding them.

export type Tone = "ok" | "warn" | "err" | "info" | "idle";
type Kid2 = Node | string | null | undefined | false;

function mk<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text?: string, attrs: Record<string, string> = {}): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function put(parent: ParentNode, kids: Kid2[]): void {
  for (const k of kids) if (k) parent.append(typeof k === "string" ? document.createTextNode(k) : k);
}
let uid = 0;
const nid = (p: string) => `${p}${++uid}`;

/** Card: a titled, inset group of rows. `title` null → no heading. */
export function Card(title: string | null, ...rows: Kid2[]): HTMLElement {
  const c = mk("section", "card");
  if (title) c.append(mk("h3", "", title));
  const g = mk("div", "group");
  put(g, rows);
  c.append(g);
  return c;
}

/** Section: a titled block inside a panel that is not a card (e.g. a list under a heading). */
export function Section(title: string, ...children: Kid2[]): HTMLElement {
  const s = mk("section", "p4-section");
  s.append(mk("h3", "p4-section-title", title));
  put(s, children);
  return s;
}

/** Row: label + controls on one line, helper text underneath. Searchable through data-s. */
export function Row(label: string, hint: string | null, ...controls: Kid2[]): HTMLElement {
  const lab = mk("label", "", label);
  const row = mk("div", "row");
  row.append(lab);
  put(row, controls);
  const it = mk("div", "item");
  it.append(row);
  let hintEl: HTMLElement | null = null;
  if (hint) { hintEl = mk("div", "hint", hint); it.append(hintEl); }
  it.dataset.s = `${label} ${hint ?? ""}`.toLowerCase();
  // tie the visible label to the first control for screen readers
  const first = controls.find((c): c is HTMLElement => c instanceof HTMLElement);
  if (first && !first.getAttribute("aria-label") && !first.getAttribute("aria-labelledby")) {
    const id = nid("p4l");
    lab.id = id;
    first.setAttribute("aria-labelledby", id);
    if (hintEl) { hintEl.id = nid("p4h"); first.setAttribute("aria-describedby", hintEl.id); }
  }
  return it;
}

/** Toggle: an on/off switch announced as a switch. */
export function Toggle(on: boolean, onChange: (v: boolean) => void, label?: string): HTMLButtonElement {
  const b = mk("button", on ? "switch on" : "switch", undefined, { type: "button", role: "switch", "aria-checked": String(on), "aria-pressed": String(on) });
  if (label) b.setAttribute("aria-label", label);
  b.addEventListener("click", () => {
    const v = b.getAttribute("aria-checked") !== "true";
    b.classList.toggle("on", v);
    b.setAttribute("aria-checked", String(v));
    b.setAttribute("aria-pressed", String(v));
    onChange(v);
  });
  return b;
}

/** Slider: a range input with a live value readout. */
export function Slider(o: { min: number; max: number; step?: number; value: number; label: string; format?: (v: number) => string; onInput: (v: number) => void }): HTMLElement {
  const wrap = mk("div", "p4-slider");
  const r = mk("input", "p4-range", undefined, { type: "range", min: String(o.min), max: String(o.max), step: String(o.step ?? 1), "aria-label": o.label });
  r.value = String(o.value);
  const out = mk("output", "p4-slider-val");
  const fmt = o.format ?? ((v: number) => String(v));
  const paint = () => {
    const pct = ((Number(r.value) - o.min) / (o.max - o.min)) * 100;
    r.style.setProperty("--v", `${pct}%`);
    out.textContent = fmt(Number(r.value));
    r.setAttribute("aria-valuetext", out.textContent);
  };
  r.addEventListener("input", () => { paint(); o.onInput(Number(r.value)); });
  paint();
  wrap.append(r, out);
  return wrap;
}

/** Segmented: the existing pill switcher plus arrow-key navigation (radiogroup pattern). */
export function Segmented<T extends string>(options: Array<[T, string]>, current: T, onPick: (v: T) => void, label?: string): HTMLElement {
  const el = segmented(options, current, onPick);
  if (label) el.setAttribute("aria-label", label);
  const btns = () => Array.from(el.querySelectorAll<HTMLButtonElement>("button"));
  const sync = () => btns().forEach((b) => b.tabIndex = b.classList.contains("on") ? 0 : -1);
  sync();
  el.addEventListener("click", () => queueMicrotask(sync));
  el.addEventListener("keydown", (e) => {
    const k = (e as KeyboardEvent).key;
    if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(k)) return;
    e.preventDefault();
    const list = btns();
    const i = list.findIndex((b) => b === document.activeElement);
    const next = k === "Home" ? 0 : k === "End" ? list.length - 1 : (i + (k === "ArrowRight" || k === "ArrowDown" ? 1 : -1) + list.length) % list.length;
    list[next].focus();
    list[next].click();
  });
  return el;
}

/** KeyCap: one key combination drawn as keycaps. */
export function KeyCap(keys: string[] | string): HTMLElement {
  const parts = typeof keys === "string" ? formatCombo(keys) : keys;
  const w = mk("span", "p4-keys", undefined, { role: "img", "aria-label": parts.join(" plus ") });
  parts.forEach((k, i) => {
    if (i) w.append(mk("span", "p4-keys-plus", "+", { "aria-hidden": "true" }));
    w.append(mk("kbd", "p4-key", k, { "aria-hidden": "true" }));
  });
  return w;
}

/** StatusPill: a small state label. The dot is decoration; the text always says the state. */
export function StatusPill(tone: Tone, text: string): HTMLElement {
  const p = mk("span", `chip p4-pill ${tone}`, undefined, { role: "status" });
  p.append(mk("i", "p4-dot", undefined, { "aria-hidden": "true" }), document.createTextNode(text));
  return p;
}

/** EmptyState: what is missing, why, and the one thing to do about it. */
export function EmptyState(title: string, body?: string, action?: { label: string; onClick: () => void }): HTMLElement {
  const e = mk("div", "empty p4-empty");
  e.append(mk("strong", "p4-empty-title", title));
  if (body) e.append(mk("span", "p4-empty-body", body));
  if (action) {
    const b = mk("button", "primary", action.label, { type: "button" });
    b.addEventListener("click", action.onClick);
    e.append(b);
  }
  return e;
}

/** Banner: a message that stays visible. `err` interrupts screen readers; the others are polite. */
export function Banner(tone: Exclude<Tone, "idle">, title: string, body?: string | Node, ...actions: HTMLElement[]): HTMLElement {
  const b = mk("div", `notice p4-banner ${tone === "err" ? "err" : tone}`, undefined, { role: tone === "err" ? "alert" : "status" });
  const text = mk("div", "p4-banner-text");
  text.append(mk("b", "", title));
  if (body) text.append(typeof body === "string" ? mk("span", "", body) : body);
  b.append(text);
  if (actions.length) { const a = mk("div", "p4-banner-actions"); a.append(...actions); b.append(a); }
  return b;
}

