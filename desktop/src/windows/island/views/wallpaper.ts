// Wallpaper: your recent wallpapers or the pictures in a folder; tap one to set it.
// Data and actions come from the backend (wallpaper.py).

import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import type { ViewHost } from "./views";

const BASE = "http://127.0.0.1:8765";

type Source = "recent" | "folder";
interface Item { id: string; name: string; thumb: string; current: boolean }
interface Snap { ok?: boolean; source?: Source; folder?: string; folder_name?: string; items?: Item[] }

async function call<T>(path: string, body?: object): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`, body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined);
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export function buildWallpaper(): ViewHost {
  let source: Source = "recent";
  let snap: Snap = {};
  let loading = true;
  let seq = 0;

  const pillRecent = h("button", { class: "wp-pill on", type: "button", text: "Recent", "data-src": "recent" }) as HTMLButtonElement;
  const pillFolder = h("button", { class: "wp-pill", type: "button", text: "My folder", "data-src": "folder" }) as HTMLButtonElement;
  const chooseBtn = h("button", { class: "wp-choose", type: "button", title: "Choose a folder", "aria-label": "Choose a folder" },
    svg(ICONS.folder, 16, { stroke: 1.9 })) as HTMLButtonElement;
  const status = h("span", { class: "wp-status" });
  const row = h("div", { class: "wp-row" });
  const empty = h("div", { class: "wp-empty" });
  const el = h("div", { class: "view wallpaper-view" },
    h("div", { class: "wp-head" }, h("div", { class: "wp-seg", role: "tablist" }, pillRecent, pillFolder), status, chooseBtn),
    row, empty);

  const paint = () => {
    pillRecent.classList.toggle("on", source === "recent");
    pillFolder.classList.toggle("on", source === "folder");
    chooseBtn.style.display = source === "folder" ? "" : "none";
    const items = snap.items ?? [];
    status.textContent = source === "folder" && snap.folder_name ? `${snap.folder_name} · ${items.length}` : "";
    row.style.display = items.length ? "" : "none";
    empty.style.display = items.length ? "none" : "";
    if (!items.length) {
      empty.textContent = loading ? "Loading…"
        : source === "folder" ? `No pictures in ${snap.folder_name || "this folder"}. Choose another folder.`
        : "No recent wallpapers yet. Pick some from My folder.";
    }
    row.replaceChildren(...items.map((it) => {
      const img = h("img", { src: it.thumb, alt: "", draggable: "false" }) as HTMLImageElement;
      const card = h("button", {
        class: `wp-card${it.current ? " current" : ""}`, type: "button", title: it.name, "aria-label": `Set ${it.name} as wallpaper`, "data-id": it.id,
      }, img, h("span", { class: "wp-check" }, svg(ICONS.check, 12, { stroke: 3 })));
      card.addEventListener("click", (e) => { e.stopPropagation(); void apply(it); });
      return card;
    }));
  };

  const load = async () => {
    const mine = ++seq;
    loading = true; paint();
    const s = await call<Snap>(`/wallpaper?source=${source}`);
    if (mine !== seq) return;                        // a newer request replaced this one
    loading = false;
    snap = s && s.ok ? s : { items: [] };
    paint();
  };

  async function apply(it: Item) {
    const items = (snap.items ?? []).map((i) => ({ ...i, current: i.id === it.id }));
    snap = { ...snap, items };                       // highlight it straight away
    paint();
    const res = await call<{ ok?: boolean }>("/wallpaper/set", { id: it.id });
    if (!res || res.ok === false) {
      const card = row.querySelector(`[data-id="${CSS.escape(it.id)}"]`) as HTMLElement | null;
      if (card) { card.classList.add("err"); window.setTimeout(() => card.classList.remove("err"), 1800); }
      void load();                                   // show what is really set
    }
  }

  const pick = (s: Source) => (e: Event) => { e.stopPropagation(); if (s !== source) { source = s; void load(); } };
  pillRecent.addEventListener("click", pick("recent"));
  pillFolder.addEventListener("click", pick("folder"));
  chooseBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    chooseBtn.classList.add("busy");
    const res = await call<{ ok?: boolean; changed?: boolean }>("/wallpaper/folder", {});
    chooseBtn.classList.remove("busy");
    if (res?.changed) void load();
  });

  // The mouse wheel scrolls the row sideways.
  row.addEventListener("wheel", (e) => {
    if (row.scrollWidth <= row.clientWidth + 1 || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
    e.preventDefault();
    row.scrollLeft += e.deltaY;
  }, { passive: false });

  return {
    el,
    sync() { paint(); },
    setActive(on) { if (on) void load(); },
  };
}
