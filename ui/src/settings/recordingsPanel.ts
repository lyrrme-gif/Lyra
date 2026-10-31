// Settings → Recordings: how Lyra reaches OBS Studio (it records by remote-controlling OBS: backend/screen_record.py +
// obs_control.py), where recordings are saved (Videos\Lyra Recordings unless the user picks another folder), and the
// recordings Lyra has made. The recording shortcuts live in Settings → Shortcuts.

import { h, clear } from "../views/dom";

import { visiblePoll } from "../core/visiblePoll";

type Kid = Node | string | null | undefined | false;

export interface RecordingsDeps {
  card(title: Kid, ...children: Kid[]): HTMLElement;
  item(label: string, hint: string | HTMLElement | null, ...controls: Kid[]): HTMLElement;
  flashSaved(ok?: boolean): void;
  lyra: string;
}

interface Rec { name: string; size: number; mtime: number; current: boolean; duration?: number; width?: number; height?: number; fps?: number; thumb?: boolean }
const fmtSize = (n: number) => (n >= 1073741824 ? `${(n / 1073741824).toFixed(1)} GB` : n >= 1048576 ? `${Math.round(n / 1048576)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const fmtDate = (s: number) => new Date(s * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const fmtLen = (t: number) => { const x = Math.max(0, Math.round(t)); const m = Math.floor(x / 60), sec = x % 60; return x >= 3600 ? `${Math.floor(x / 3600)}:${String(m % 60).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`; };

export function recordingsCards(d: RecordingsDeps): HTMLElement[] {
  const post = async (path: string, body: Record<string, unknown> = {}) => {
    try {
      const r = await fetch(`${d.lyra}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return (await r.json()) as { ok?: boolean; error?: string };
    } catch {
      return { ok: false, error: "Lyra isn't running." };
    }
  };
  const btn = (label: string, onClick: () => void, cls = "") => {
    const b = h("button", { type: "button", class: cls, text: label }) as HTMLButtonElement;
    b.addEventListener("click", onClick);
    return b;
  };
  const save = (u: Record<string, unknown>) => void post("/settings/recordings", u).then((r) => d.flashSaved(!!r.ok));

  // ── Save location: where the recordings go; pick another folder any time ──
  interface FolderInfo { ok?: boolean; error?: string; folder?: string; custom?: boolean; wanted?: string; fallback?: boolean; picked?: boolean }
  const folderPath = h("div", { style: "word-break:break-all;user-select:text;font-weight:600;color:var(--ink,#f5f6f8)", text: "…" });
  const folderNote = h("div", { text: "" });
  const folderErr = h("div", { style: "color:#f4505e", text: "" });
  const folderHint = h("div", { class: "hint" }, folderPath, folderNote, folderErr);   // .hint: same padding as every other row's hint
  const changeBtn = btn("Change folder…", () => void changeFolder({ action: "pick" })) as HTMLButtonElement;
  const defaultBtn = btn("Use default", () => void changeFolder({ action: "reset" })) as HTMLButtonElement;
  defaultBtn.hidden = true;
  const paintFolder = (f: FolderInfo) => {
    if (!f.folder) return;
    folderPath.textContent = f.folder;
    defaultBtn.hidden = !f.custom;
    folderNote.textContent = f.fallback
      ? `Your chosen folder (${f.wanted}) can't be used right now, so recordings go here until it is available again.`
      : f.custom
        ? "Your own folder. Recordings made earlier stay in the folder they were saved to."
        : "The default folder. Choose another one to save recordings somewhere else, such as another drive.";
  };
  async function changeFolder(body: Record<string, unknown>): Promise<void> {
    folderErr.textContent = "";
    const all = [changeBtn, defaultBtn];
    all.forEach((b) => { b.disabled = true; });
    try {
      const r = await fetch(`${d.lyra}/record/folder`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = (await r.json()) as FolderInfo;
      if (j.ok === false) folderErr.textContent = j.error || "Couldn't change the folder.";
      else {
        paintFolder(j);
        if (j.picked !== false) { d.flashSaved(true); void refresh(); }
      }
    } catch {
      folderErr.textContent = "Lyra isn't running.";
    } finally {
      all.forEach((b) => { b.disabled = false; });
    }
  }
  void fetch(`${d.lyra}/record/folder`).then((r) => r.json()).then((j: FolderInfo) => paintFolder(j)).catch(() => { folderPath.textContent = "Can't reach Lyra — is she running?"; });
  const folderCard = d.card("Save location", d.item("Recordings are saved in", folderHint, changeBtn, defaultBtn));

  // ── The list ──
  const summary = h("span", { class: "hint", text: "" });
  const note = h("div", { class: "hint", text: "" });
  const list = h("div", { style: "display:flex;flex-direction:column;gap:.5rem;margin-top:.4rem" });
  const say = (msg: string) => { note.textContent = msg; };

  const row = (r: Rec): HTMLElement => {
    let armed = 0;
    const del = btn("Delete", async () => {
      if (!armed) {
        armed = window.setTimeout(() => { armed = 0; del.textContent = "Delete"; }, 3000);
        del.textContent = "Click again to delete";
        return;
      }
      window.clearTimeout(armed);
      const res = await post("/record/delete", { name: r.name });
      say(res.ok ? "" : res.error || "Couldn't delete it.");
      void refresh();
    });
    const play = async () => { const res = await post("/record/open", { name: r.name }); say(res.ok ? "" : res.error || ""); };
    const facts = [fmtDate(r.mtime), fmtSize(r.size), r.width && r.height ? `${r.width}×${r.height}` : "", r.fps ? `${r.fps} fps` : ""].filter(Boolean).join(" · ");
    const thumb = h("button", { type: "button", class: "rec-thumb", "aria-label": `Play ${r.name}`,
      style: "position:relative;flex:none;width:176px;aspect-ratio:16/9;border:0;border-radius:10px;overflow:hidden;background:#1c1c1e;padding:0;cursor:pointer" },
      r.thumb ? h("img", { src: `${d.lyra}/record/thumb?name=${encodeURIComponent(r.name)}&v=${r.mtime}`, alt: "", loading: "lazy", style: "width:100%;height:100%;object-fit:cover;display:block" }) : null,
      h("span", { style: "position:absolute;inset:0;display:grid;place-items:center;color:#fff;font-size:22px;text-shadow:0 1px 6px rgba(0,0,0,.6)", text: r.current ? "●" : "▶" }),
      r.duration ? h("span", { style: "position:absolute;right:6px;bottom:6px;padding:1px 6px;border-radius:6px;background:rgba(0,0,0,.65);color:#fff;font-size:11px", text: fmtLen(r.duration) }) : null);
    thumb.addEventListener("click", () => { if (!r.current) void play(); });
    return h("div", { style: "display:flex;gap:.8rem;align-items:center" }, thumb,
      h("div", { style: "flex:1;min-width:0" },
        h("div", { style: "word-break:break-all", text: r.name }),
        h("div", { class: "hint", text: `${facts}${r.current ? " · recording now" : ""}` })),
      h("div", { style: "display:flex;flex-direction:column;gap:.3rem" },
        btn("Play", () => void play()),
        btn("Show in folder", async () => { const res = await post("/record/reveal", { name: r.name }); say(res.ok ? "" : res.error || ""); }),
        r.current ? null : del));
  };

  async function refresh(): Promise<void> {
    try {
      const res = await fetch(`${d.lyra}/record/list`);
      const j = (await res.json()) as { items: Rec[]; total: number } & FolderInfo;
      paintFolder(j);
      clear(list);
      summary.textContent = j.items.length ? `${j.items.length} recording${j.items.length === 1 ? "" : "s"} · ${fmtSize(j.total)}` : "";
      if (!j.items.length) list.append(h("div", { class: "hint", text: "No recordings yet. Say “Hey Lyra, start recording”, or press Record on the island." }));
      for (const r of j.items) list.append(row(r));
    } catch {
      clear(list);
      list.append(h("div", { class: "hint", text: "Can't reach Lyra — is she running?" }));
    }
  }
  void refresh();
  // Keep it current while the tab is open (a recording may have just finished).
  visiblePoll(refresh, { ms: 4000, target: list });   // cleared (not skipped) while the tab or window is hidden
  window.addEventListener("focus", () => void refresh());

  const listCard = d.card(
    "Your recordings",
    h("div", { class: "item" },
      h("div", { class: "row" }, summary, btn("Open folder", () => void post("/record/open-folder")), btn("Refresh", () => void refresh())),
      note, list),
  );
  // ── OBS: Lyra records by remote-controlling OBS Studio over its WebSocket server ──
  const field = (type: string, ph: string, width: string, onSave: (v: string) => void) => {
    const el = h("input", { type, placeholder: ph, autocomplete: "off", spellcheck: "false", style: `width:${width};padding:7px 10px;border-radius:9px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.06);color:inherit;font:inherit` }) as HTMLInputElement;
    el.addEventListener("change", () => onSave(el.value.trim()));
    return el;
  };
  const hostEl = field("text", "127.0.0.1", "170px", (v) => save({ obs_host: v || "127.0.0.1" }));
  const portEl = field("number", "4455", "90px", (v) => save({ obs_port: Math.max(1, Math.min(65535, Number(v) || 4455)) }));
  const passEl = field("password", "only if OBS asks", "170px", (v) => save({ obs_password: v }));
  const obsNote = h("div", { style: "margin-top:6px;min-height:1.2em" });
  const testBtn = btn("Test connection", () => {
    testBtn.disabled = true; obsNote.style.color = ""; obsNote.textContent = "Connecting to OBS…";
    void post("/record/obs-test").then((r) => {
      const j = r as { ok?: boolean; message?: string };
      obsNote.style.color = j.ok ? "#30d158" : "#ffb340";
      obsNote.textContent = j.message ?? (j.ok ? "Connected." : "Couldn't connect.");
    }).finally(() => { testBtn.disabled = false; });
  }) as HTMLButtonElement;
  void fetch(`${d.lyra}/settings/recordings`).then((r) => r.json()).then((j: Record<string, unknown>) => {
    hostEl.value = String(j.obs_host ?? "127.0.0.1"); portEl.value = String(j.obs_port ?? 4455); passEl.value = String(j.obs_password ?? "");
  }).catch(() => { /* backend not up: the defaults stay shown */ });
  const obsCard = d.card("Recording with OBS",
    d.item("How it works", "Lyra records through OBS Studio, so a recording looks exactly like one made in OBS (its capture, encoder and sound settings). In OBS open Tools → WebSocket Server Settings, tick “Enable WebSocket server” and press OK. Then press Test connection here.", null),
    d.item("OBS address", "Leave it as it is when OBS runs on this PC.", hostEl),
    d.item("Port", "OBS's default is 4455.", portEl),
    d.item("Password", "Only if OBS asks for one (OBS: Tools → WebSocket Server Settings → Enable Authentication).", passEl),
    d.item("Check the connection", obsNote, testBtn));
  return [obsCard, folderCard, listCard];
}
