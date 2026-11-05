// Per-application volume mixer — Island Volume tab.
// Lists Windows audio sessions (pycaw via /volume/apps) with icon, name, slider, mute.

import { visiblePoll, type VisiblePoll } from "../../../shared/core/visiblePoll";
import { h, clear, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import type { ViewHost } from "./views";

const BASE = "http://127.0.0.1:8765";

export interface AppSession {
  exe: string;
  name: string;
  level: number;
  muted: boolean;
  active?: boolean;
  icon?: string | null; // data:image/png;base64,... from backend
}

function appGlyph(name: string, exe: string): string {
  const k = `${name} ${exe}`.toLowerCase();
  if (k.includes("spotify")) return ICONS.music;
  if (k.includes("discord")) return ICONS.bubble;
  if (k.includes("chrome") || k.includes("edge") || k.includes("firefox") || k.includes("brave"))
    return ICONS.stack;
  if (k.includes("code") || k.includes("cursor")) return ICONS.doc;
  return ICONS.speakerOn;
}

async function fetchApps(): Promise<AppSession[]> {
  try {
    const res = await fetch(`${BASE}/volume/apps`);
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.apps) ? data.apps : [];
  } catch {
    return [];
  }
}

async function setLevel(exe: string, level: number): Promise<void> {
  try {
    await fetch(`${BASE}/volume/app`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exe, level }),
    });
  } catch { /* offline */ }
}

async function setMuted(exe: string, muted: boolean): Promise<void> {
  try {
    await fetch(`${BASE}/volume/mute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exe, muted }),
    });
  } catch { /* offline */ }
}

export function buildVolume(): ViewHost {
  // Sounds: one large rounded row per app. The row itself is the slider (drag it
  // sideways), the icon mutes, the number is the level.
  const list = h("div", { class: "snd-list" });
  const empty = h("div", { class: "snd-empty" }, svg(ICONS.speakerOn, 22), h("span", { text: "No apps are playing audio" }));
  const el = h("div", { class: "view volume-view" }, h("div", { class: "snd-body" }, list));

  let active = false;
  let pollTimer: VisiblePoll | undefined;
  let dragging = false;
  let lastKey = "";

  function render(apps: AppSession[]) {
    if (dragging) return;
    const key = apps.map((a) => `${a.exe}:${Math.round(a.level * 100)}:${a.muted ? 1 : 0}:${a.active ? 1 : 0}`).join("|");
    if (key === lastKey && list.childElementCount > 0) return;
    lastKey = key;

    clear(list);
    if (!apps.length) { list.append(empty); return; }
    for (const app of apps) {
      const pct = Math.round(Math.max(0, Math.min(1, app.level)) * 100);
      const icon = h("button", { class: "snd-ic", type: "button", title: app.muted ? "Unmute" : "Mute", "aria-label": app.muted ? "Unmute" : "Mute" },
        app.icon
          ? (h("img", { src: app.icon, alt: "", width: "26", height: "26", draggable: "false" }) as HTMLElement)
          : svg(appGlyph(app.name, app.exe), 16));
      const name = h("span", { class: "snd-name", text: app.name });
      const eq = h("span", { class: "snd-eq" }, h("i"), h("i"), h("i"));
      const pctLabel = h("span", { class: "snd-pct", text: app.muted ? "Muted" : `${pct}%` });
      const fill = h("div", { class: "snd-fill" });
      fill.style.width = `${app.muted ? 0 : pct}%`;
      const row = h("div", { class: `snd-row${app.muted ? " muted" : ""}${app.active ? " playing" : ""}`, title: app.name },
        fill, icon, name, app.active && !app.muted ? eq : null, pctLabel);

      icon.addEventListener("pointerdown", (e) => e.stopPropagation());
      icon.addEventListener("click", (e) => { e.stopPropagation(); void setMuted(app.exe, !app.muted).then(() => void refresh()); });

      let shown = pct;
      const setFromX = (clientX: number) => {
        const r = row.getBoundingClientRect();
        const v = Math.round(Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * 100);
        shown = v;
        fill.style.width = `${v}%`;
        pctLabel.textContent = `${v}%`;
        row.classList.remove("muted");
        void setLevel(app.exe, v / 100);
      };
      row.addEventListener("pointerdown", (e: PointerEvent) => {
        if (e.button !== 0) return;
        dragging = true;
        row.classList.add("dragging");
        row.setPointerCapture(e.pointerId);
        setFromX(e.clientX);
      });
      row.addEventListener("pointermove", (e: PointerEvent) => { if (dragging && row.hasPointerCapture(e.pointerId)) setFromX(e.clientX); });
      const endDrag = (e: PointerEvent) => {
        if (!dragging) return;
        dragging = false;
        row.classList.remove("dragging");
        if (row.hasPointerCapture(e.pointerId)) row.releasePointerCapture(e.pointerId);
      };
      row.addEventListener("pointerup", endDrag);
      row.addEventListener("pointercancel", endDrag);
      row.addEventListener("wheel", (e: WheelEvent) => {
        e.preventDefault();
        shown = Math.max(0, Math.min(100, shown + (e.deltaY < 0 ? 2 : -2)));
        fill.style.width = `${shown}%`;
        pctLabel.textContent = `${shown}%`;
        void setLevel(app.exe, shown / 100);
      }, { passive: false });

      list.append(row);
    }
  }

  async function refresh() {
    if (!active) return;
    render(await fetchApps());
  }
  function startPoll() { stopPoll(); pollTimer = visiblePoll(() => refresh(), { ms: 1200 }); }   // runs at once; cleared while hidden
  function stopPoll() { if (pollTimer) { pollTimer.stop(); pollTimer = undefined; } }

  return {
    el,
    setActive(on: boolean) { active = on; if (on) startPoll(); else stopPoll(); },
    sync() { if (active) void refresh(); },
  };
}
