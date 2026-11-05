import { h, clear, svg } from "../../../shared/views/dom";
import { visiblePoll, type VisiblePoll } from "../../../shared/core/visiblePoll";
import { ICONS } from "../../../shared/views/icons";
import { State } from "../../../shared/core/state";
import type { ViewHost } from "./views";
import { FileDropFx } from "../../../shared/core/fileDropFx";
import { Sound } from "../../../shared/core/sound";
import { mountDropMochi } from "./clipboardMochi";

const API = "http://127.0.0.1:8765";

// Stroke icons (drawn on the same 24×24 grid as ICONS, rendered with svg(..., {stroke})).
const COPY_ICON = "M9 9h10.5v10.5H9z M5.5 15H4.5V4.5H15v1";
const FOLDER_ICON = "M3 6.5h6.2l2.3 2.3H21V19H3z";

interface ClipItem {
  id: number;
  kind: "text" | "files";
  text: string;
  files: string[];
  names?: string[];
  sizes?: number[];
  label: string;
  source?: string;
  ts: number;
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

/**
 * Clipboard tab.
 *
 * Files dropped here are uploaded to the backend, which keeps them in a session
 * folder until the user removes them or Lyra restarts. The drop uses the page's own
 * HTML5 drag-and-drop (the island window runs with Tauri's native file-drop hook off),
 * so Windows never shows the "not allowed" cursor and no file path is needed.
 */
export function buildClipboard(): ViewHost {
  const emptyStatus = h("div", { class: "cbx-empty-sub", text: "Drop a file, or copy something" });
  const emptyBody = h("div", { class: "cbx-empty" },
    h("div", { class: "cbx-empty-ic" }, svg(ICONS.doc, 30)),
    h("div", { class: "cbx-empty-title", text: "Clipboard" }),
    emptyStatus,
  );

  const count = h("span", { class: "cbx-count", text: "0" });
  const clearBtn = h("button", { class: "cbx-clear", type: "button", text: "Clear", title: "Remove everything from the clipboard" });
  const head = h("div", { class: "cbx-head" },
    h("div", { class: "cbx-title-row" }, h("span", { class: "cbx-title", text: "Clipboard" }), count),
    clearBtn,
  );
  const list = h("div", { class: "cbx-list" });
  const status = h("div", { class: "cbx-status", text: "" });
  const hintIcon = svg(ICONS.doc, 26);
  hintIcon.classList.add("cbx-drop-ic");
  const hintLabel = h("span", { class: "cbx-drop-label", text: "Drop to add" });
  const hint = h("div", { class: "cbx-drop-hint" }, hintIcon, hintLabel);
  const listBody = h("div", { class: "cbx-body" }, head, list, status);

  const card = h("div", { class: "cbx", title: "Drop files here" }, emptyBody, listBody, hint);
  const el = h("div", { class: "view clipboard-view" }, card);
  // Mochi reacts to files hovering, being dropped and being saved (hint + core/fileDropFx.ts).
  const mochi = mountDropMochi(card, hint, hintLabel);

  let items: ClipItem[] = [];
  let renderKey = "";
  let over = false;
  let dragCount = 0;
  let overTimer: number | undefined;
  let statusTimer: number | undefined;
  let pollTimer: VisiblePoll | undefined;
  let clearArmed: number | undefined;
  let active = false;

  const live = () => active || State.view === "clipboard";

  // ── Status line ──────────────────────────────────────────────────────────
  function say(msg: string, ms = 2600) {
    status.textContent = msg;
    emptyStatus.textContent = msg;
    if (statusTimer) window.clearTimeout(statusTimer);
    statusTimer = window.setTimeout(() => {
      status.textContent = "";
      emptyStatus.textContent = "Drop a file, or copy something";
    }, ms);
  }

  // ── Rendering ────────────────────────────────────────────────────────────
  function iconBtn(path: string, label: string, onClick: () => void, fill = false): HTMLElement {
    const b = h("button", { class: "cb-btn", type: "button", title: label, "aria-label": label });
    b.append(fill ? svg(path, 12) : svg(path, 14, { stroke: 1.8 }));
    b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
    // Don't let the island's own drag / pager handlers see a press on a button.
    b.addEventListener("pointerdown", (e) => e.stopPropagation());
    return b;
  }

  const IMG_EXT = /\.(png|jpe?g|gif|webp|bmp|avif)$/i;
  const extOf = (n: string) => (n.split(".").pop() || "").slice(0, 4).toUpperCase();

  function preview(it: ClipItem): HTMLElement {
    const isFiles = it.kind === "files";
    if (isFiles) {
      const name = it.names?.[0] ?? it.label;
      if (IMG_EXT.test(name)) {
        const img = h("img", { alt: "", src: `${API}/clipboard/file?id=${it.id}&i=0`, draggable: "false" }) as HTMLImageElement;
        return h("div", { class: "cbx-pv image" }, img);
      }
      return h("div", { class: "cbx-pv file" }, svg(ICONS.doc, 26), h("span", { class: "cbx-ext", text: extOf(name) || "FILE" }));
    }
    const t = it.text.trim();
    if (/^https?:\/\/\S+$/i.test(t)) {
      let host = t;
      try { host = new URL(t).hostname.replace(/^www\./, ""); } catch { /* keep raw */ }
      return h("div", { class: "cbx-pv link" }, svg(ICONS.doc, 22), h("span", { class: "cbx-host", text: host }));
    }
    return h("div", { class: "cbx-pv text" }, h("span", { text: t.slice(0, 140) }));
  }

  function row(it: ClipItem): HTMLElement {
    const isFiles = it.kind === "files";
    const name = isFiles ? (it.names?.[0] ?? it.label) + (it.files.length > 1 ? `  +${it.files.length - 1}` : "") : it.label;
    const total = (it.sizes ?? []).reduce((a, b) => a + b, 0);
    const isUrl = !isFiles && /^https?:\/\/\S+$/i.test(it.text.trim());
    const meta = isFiles ? fmtSize(total) : isUrl ? "Link" : `${it.text.length} characters`;

    const actions = h("div", { class: "cbx-actions" });
    actions.append(iconBtn(COPY_ICON, isFiles ? "Copy (then Ctrl+V)" : "Copy text", () => void act(it.id, "copy")));
    if (isFiles) actions.append(iconBtn(FOLDER_ICON, "Show in Explorer", () => void act(it.id, "reveal")));
    actions.append(iconBtn(ICONS.xmark, "Remove", () => void act(it.id, "remove"), true));

    const r = h("div", { class: "cbx-card", role: "button", tabindex: "0", title: isFiles ? "Click to open, drag to take it out" : "Click to copy" },
      preview(it),
      h("div", { class: "cbx-cap" }, h("div", { class: "cbx-name", text: name }), h("div", { class: "cbx-meta", text: meta })),
      actions,
    );
    r.addEventListener("click", () => void act(it.id, isFiles ? "open" : "copy"));
    r.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); void act(it.id, isFiles ? "open" : "copy"); }
    });

    if (isFiles && it.files.length === 1) {
      r.draggable = true;
      r.addEventListener("dragstart", (e) => {
        const fname = it.names?.[0] ?? it.label;
        e.dataTransfer?.setData("DownloadURL", `application/octet-stream:${fname}:${API}/clipboard/file?id=${it.id}&i=0`);
        if (e.dataTransfer) e.dataTransfer.effectAllowed = "copy";
        r.classList.add("dragging");
      });
      r.addEventListener("dragend", () => r.classList.remove("dragging"));
    }
    return r;
  }

  function render() {
    const key = items.map((i) => `${i.id}:${i.label}:${(i.sizes ?? []).join(",")}`).join("|");
    card.classList.toggle("cbx-has-items", items.length > 0);
    count.textContent = String(items.length);
    if (key === renderKey) return;
    renderKey = key;
    clear(list);
    for (const it of items) list.append(row(it));
    emptyStatus.textContent = "Drop a file, or copy something";
  }

  function setItems(next: unknown) {
    if (!Array.isArray(next)) return;
    items = next as ClipItem[];
    render();
  }

  // ── Backend calls ────────────────────────────────────────────────────────
  let loadedOnce = false;
  async function refresh() {
    try {
      const r = await fetch(`${API}/clipboard/session`);
      const d = await r.json();
      if (d.ok) {
        // something new was copied (not the first load, not a removal): a crisp little paper snap
        const grew = loadedOnce && Array.isArray(d.items) && d.items.length > items.length;
        loadedOnce = true;
        setItems(d.items);
        if (grew) Sound.activity("copy");
      }
    } catch { /* backend not up yet; the next poll tries again */ }
  }

  async function act(id: number, action: "copy" | "open" | "reveal" | "remove") {
    try {
      const r = await fetch(`${API}/clipboard/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, action }),
      });
      const d = await r.json();
      if (Array.isArray(d.items)) setItems(d.items);
      if (!d.ok && d.message) say(String(d.message));
      else if (action === "copy" && d.message) say(String(d.message));
    } catch {
      say("Lyra is unavailable");
    }
  }

  async function clearAll() {
    try {
      const r = await fetch(`${API}/clipboard/clear`, { method: "POST" });
      const d = await r.json();
      if (Array.isArray(d.items)) setItems(d.items);
    } catch {
      say("Lyra is unavailable");
    }
  }

  clearBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (clearArmed === undefined) {
      clearBtn.textContent = "Sure?";
      clearBtn.classList.add("armed");
      clearArmed = window.setTimeout(() => {
        clearArmed = undefined;
        clearBtn.textContent = "Clear";
        clearBtn.classList.remove("armed");
      }, 2500);
      return;
    }
    window.clearTimeout(clearArmed);
    clearArmed = undefined;
    clearBtn.textContent = "Clear";
    clearBtn.classList.remove("armed");
    void clearAll();
  });

  async function captureCurrent() {
    try {
      const r = await fetch(`${API}/clipboard/capture`, { method: "POST" });
      const d = await r.json();
      if (Array.isArray(d.items)) setItems(d.items);
      say(d.ok ? "Clipboard saved" : "Clipboard is empty");
    } catch {
      say("Lyra is unavailable");
    }
  }

  // Empty card: tap to grab whatever is on the Windows clipboard right now.
  emptyBody.addEventListener("click", () => void captureCurrent());

  async function upload(files: File[], skippedFolders: number) {
    let saved = 0;
    let failed = "";
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      say(`Saving ${f.name}${files.length > 1 ? ` (${i + 1}/${files.length})` : ""}…`, 60000);
      try {
        const r = await fetch(`${API}/clipboard/upload?name=${encodeURIComponent(f.name)}`, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: f,
        });
        const d = await r.json().catch(() => ({} as Record<string, unknown>));
        if (r.ok && d.ok) { saved++; setItems(d.items); }
        else failed = String(d.error ?? `could not save ${f.name}`);
      } catch {
        failed = "Lyra is unavailable";
      }
    }
    if (failed) say(failed, 4200);
    else if (skippedFolders) say(`Saved ${saved} · folders aren't supported, drop the files inside`, 4200);
    else say(saved === 1 ? "Saved 1 file" : `Saved ${saved} files`);
    FileDropFx.done(saved > 0, saved > 0 ? undefined : failed || "Couldn't save that");
    void refresh();
  }

  // ── Drag & drop (page-level so the whole island is a forgiving drop zone) ─
  const hasFiles = (e: DragEvent) => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");

  function setOver(on: boolean) {
    over = on;
    card.classList.toggle("over", on || State.fileDragOver);
    mochi.hover(on || State.fileDragOver, dragCount);
    if (overTimer) window.clearTimeout(overTimer);
    // dragleave is unreliable when the cursor leaves the window; dragover keeps re-arming this.
    if (on) overTimer = window.setTimeout(() => setOver(false), 450);
  }

  window.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragCount = e.dataTransfer?.items?.length ?? 0;
    if (live()) setOver(true);
  });
  window.addEventListener("dragover", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); // without this the cursor is the red "not allowed" sign
    if (e.dataTransfer) e.dataTransfer.dropEffect = live() ? "copy" : "none";
    if (live()) { setOver(true); mochi.look(e.clientX, e.clientY); }
  });
  window.addEventListener("dragleave", (e) => {
    if (!hasFiles(e)) return;
    // Leaving the window entirely reports no related target.
    if (!e.relatedTarget) { dragCount = 0; setOver(false); }
  });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); // never let the webview navigate to the dropped file
    if (!live() || !e.dataTransfer) { setOver(false); return; }
    const dt = e.dataTransfer;
    const dirs = new Set<string>();
    for (const it of Array.from(dt.items || [])) {
      const entry = typeof it.webkitGetAsEntry === "function" ? it.webkitGetAsEntry() : null;
      if (entry?.isDirectory) dirs.add(entry.name);
    }
    const files = Array.from(dt.files).filter((f) => !dirs.has(f.name));
    // Mochi first (it takes over the hint), then the hover state is released.
    if (files.length) FileDropFx.start(files.map((f) => ({ name: f.name })), { x: e.clientX, y: e.clientY });
    dragCount = 0;
    setOver(false);
    if (files.length) void upload(files, dirs.size);
    else if (dirs.size) {
      say("Folders aren't supported, drop the files inside", 4200);
      FileDropFx.done(false, "Folders aren't supported");
    }
  });

  // ── Polling only while the tab is on screen ──────────────────────────────
  function setPolling(on: boolean) {
    if (on && pollTimer === undefined) {
      pollTimer = visiblePoll(() => refresh(), { ms: 1500 });   // runs at once; cleared while hidden
    } else if (!on && pollTimer !== undefined) {
      pollTimer.stop();
      pollTimer = undefined;
    }
  }

  return {
    el,
    setActive(on: boolean) {
      active = on;
      setPolling(on);
    },
    sync() {
      card.classList.toggle("over", over || State.fileDragOver);
      mochi.hover(over || State.fileDragOver, dragCount);   // the native drag stream only reaches us through State
      setPolling(active || (State.view === "clipboard" && State.mode === "expanded"));
    },
  };
}
