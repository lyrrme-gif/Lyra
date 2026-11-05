// Ctrl+K command palette: one search box over settings and actions.
// Pure DOM, no dependencies. Keyboard: ↑ ↓ move, Enter runs, Esc closes. Focus returns to where it was.

export interface PaletteEntry { title: string; sub: string; keywords?: string; run: () => void }

let open: { close: () => void } | null = null;
let seq = 0;

export function isPaletteOpen(): boolean { return open !== null; }

export function openPalette(getEntries: () => PaletteEntry[]): void {
  if (open) return;
  const previous = document.activeElement as HTMLElement | null;
  const entries = getEntries();
  const id = `p4pal${++seq}`;

  const scrim = document.createElement("div");
  scrim.className = "p4-pal-scrim";
  const box = document.createElement("div");
  box.className = "p4-pal";
  box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-label", "Search settings and actions");
  const input = document.createElement("input");
  input.type = "text"; input.className = "p4-pal-input"; input.placeholder = "Search settings and actions…";
  input.setAttribute("role", "combobox"); input.setAttribute("aria-expanded", "true"); input.setAttribute("aria-controls", `${id}-list`);
  input.setAttribute("aria-label", "Search settings and actions"); input.autocomplete = "off"; input.spellcheck = false;
  const list = document.createElement("ul");
  list.className = "p4-pal-list"; list.id = `${id}-list`; list.setAttribute("role", "listbox");
  const foot = document.createElement("div");
  foot.className = "p4-pal-foot";
  foot.textContent = "↑ ↓ to move · Enter to open · Esc to close · Searching happens on this device";
  const live = document.createElement("div");
  live.className = "d-sr-only"; live.setAttribute("aria-live", "polite");
  box.append(input, list, foot, live);
  scrim.append(box);
  document.body.append(scrim);

  let shown: PaletteEntry[] = [];
  let sel = 0;

  const score = (e: PaletteEntry, words: string[]): number => {
    const t = e.title.toLowerCase();
    const hay = `${t} ${e.sub.toLowerCase()} ${(e.keywords ?? "").toLowerCase()}`;
    let s = 0;
    for (const w of words) {
      if (!hay.includes(w)) return -1;
      s += t.startsWith(w) ? 3 : t.includes(w) ? 2 : 1;
    }
    return s;
  };
  const render = () => {
    const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    shown = (words.length
      ? entries.map((e) => [e, score(e, words)] as const).filter(([, s]) => s >= 0).sort((a, b) => b[1] - a[1]).map(([e]) => e)
      : entries).slice(0, 40);
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    list.textContent = "";
    shown.forEach((e, i) => {
      const li = document.createElement("li");
      li.id = `${id}-o${i}`; li.setAttribute("role", "option"); li.setAttribute("aria-selected", String(i === sel));
      const t = document.createElement("span"); t.className = "p4-pal-t"; t.textContent = e.title;
      const s = document.createElement("span"); s.className = "p4-pal-s"; s.textContent = e.sub;
      li.append(t, s);
      li.addEventListener("mousemove", () => { if (sel !== i) { sel = i; mark(); } });
      li.addEventListener("click", () => run(i));
      list.append(li);
    });
    if (!shown.length) {
      const li = document.createElement("li"); li.className = "p4-pal-none"; li.textContent = "Nothing matches. Try a shorter word."; list.append(li);
    }
    live.textContent = shown.length ? `${shown.length} result${shown.length === 1 ? "" : "s"}` : "No results";
    mark();
  };
  const mark = () => {
    list.querySelectorAll("li[role=option]").forEach((li, i) => li.setAttribute("aria-selected", String(i === sel)));
    const cur = list.querySelector<HTMLElement>(`#${id}-o${sel}`);
    if (cur) { input.setAttribute("aria-activedescendant", cur.id); cur.scrollIntoView({ block: "nearest" }); } else input.removeAttribute("aria-activedescendant");
  };
  const close = () => { scrim.remove(); open = null; document.removeEventListener("keydown", onKey, true); previous?.focus?.(); };
  const run = (i: number) => { const e = shown[i]; close(); if (e) window.setTimeout(e.run, 0); };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); close(); }
    else if (ev.key === "ArrowDown") { ev.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); mark(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
    else if (ev.key === "Enter") { ev.preventDefault(); run(sel); }
    else if (ev.key === "Tab") { ev.preventDefault(); input.focus(); }   // focus stays in the box while it is open
  };
  document.addEventListener("keydown", onKey, true);
  scrim.addEventListener("mousedown", (e) => { if (e.target === scrim) close(); });
  input.addEventListener("input", () => { sel = 0; render(); });
  open = { close };
  render();
  input.focus();
}

export function closePalette(): void { open?.close(); }
