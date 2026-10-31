// Phase 7 — Notetaker panel: record a call, read/export notes, settings (retention, speakers, summaries).
// Built from the Settings helpers (card/item/toggle/confirmButton) so the design pass can restyle it.
import { h, clear } from "../../views/dom";
import { visiblePoll } from "../../core/visiblePoll";

type Kid = Node | string | null | undefined | false;

export interface P7Ctx {
  lyra: string;
  card: (title: Kid, ...children: Kid[]) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: Kid[]) => HTMLElement;
  toggle: (on: boolean, onChange: (v: boolean) => void) => HTMLElement;
  confirmButton: (label: string, confirmLabel: string, onConfirm: () => void, cls?: string) => HTMLElement;
  flashSaved: (ok?: boolean) => void;
}

interface Status {
  recorder: { recording: boolean; id?: string; title?: string; elapsed?: number; system_audio?: boolean;
    levels?: Record<string, number>; warnings?: string[] };
  processing: { id: string; stage: string; progress: number; error: string };
  queued: string[]; pending: string[]; reminder: string;
  settings: { p7_retention_days: number; p7_diarize: boolean; p7_summarize: boolean; p7_summary_provider: string };
  diarization: { installed: boolean; downloading: boolean; progress: number; error: string; sherpa: boolean; approx_mb: number };
}
interface Line { start: number; end: number; speaker: string; text: string }
interface Doc { id: string; title: string; started: number; duration: number; speakers: string[]; lines: Line[];
  summary: { text: string; todos: { text: string; owner: string }[]; provider: string } | null; diarized: boolean }
interface Row { id: string; title: string; started: number; duration: number; speakers: string[]; preview: string; has_summary: boolean }

const mmss = (t: number) => { t = Math.max(0, Math.floor(t)); const m = Math.floor(t / 60), s = t % 60;
  return t >= 3600 ? `${Math.floor(t / 3600)}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`; };

export function notetakerCards(c: P7Ctx): HTMLElement[] {
  const base = `${c.lyra}/p7/notetaker/`;
  async function api(path: string, body?: unknown): Promise<any | null> {
    try {
      const r = await fetch(base + path, body === undefined ? undefined :
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return await r.json().catch(() => null);
    } catch { return null; }
  }
  let st: Status | null = null;
  let openId = "";

  // ── Record ─────────────────────────────────────────────────────────────────────────────────────
  const reminder = h("div", { class: "notice warn", text: "" });
  const ack = h("input", { type: "checkbox", id: "p7-ack" }) as HTMLInputElement;
  const ackLabel = h("label", { for: "p7-ack", text: "I will tell everyone on the call that I am recording." });
  const title = h("input", { type: "text", placeholder: "Meeting title (optional)", maxlength: "80" }) as HTMLInputElement;
  const startBtn = h("button", { class: "primary", text: "Start recording" }) as HTMLButtonElement;
  const stopBtn = h("button", { class: "danger", text: "Stop & write notes" }) as HTMLButtonElement;
  const recLine = h("div", { class: "hint" });
  const procLine = h("div", { class: "hint" });
  const warn = h("div", { class: "hint", style: "color:#f5a524" });
  ack.addEventListener("change", paintRecord);
  startBtn.addEventListener("click", async () => {
    const r = await api("start", { title: title.value, acknowledged: ack.checked });
    if (!r?.ok) recLine.textContent = r?.error === "acknowledge_required" ? "Please confirm you will tell the participants first." : (r?.error ?? "Could not reach Lyra.");
    void refresh();
  });
  stopBtn.addEventListener("click", async () => { await api("stop", {}); title.value = ""; void refresh(); });

  function paintRecord() {
    const rec = !!st?.recorder.recording;
    reminder.textContent = st?.reminder ?? "Tell everyone on the call that you are recording it.";
    startBtn.style.display = rec ? "none" : "";
    startBtn.disabled = !ack.checked;
    stopBtn.style.display = rec ? "" : "none";
    ack.disabled = rec; title.disabled = rec;
    const r = st?.recorder;
    if (rec && r) {
      const lv = r.levels ?? {};
      recLine.textContent = `● Recording ${mmss(r.elapsed ?? 0)} — mic ${Math.round((lv.mic ?? 0) * 100)}%` +
        (r.system_audio ? ` · call audio ${Math.round((lv.system ?? 0) * 100)}%` : " · call audio unavailable (only your microphone)");
    } else if (!recLine.textContent?.startsWith("Please")) recLine.textContent = "";
    warn.textContent = (r?.warnings ?? []).join(" ");
    const p = st?.processing;
    const busy = p && ["transcribing", "speakers", "summarizing"].includes(p.stage);
    procLine.textContent = busy ? `${p!.stage === "speakers" ? "Telling speakers apart" : p!.stage === "summarizing" ? "Writing the summary" : "Transcribing"}… ${Math.round(p!.progress * 100)}%`
      : p?.stage === "failed" ? `Could not finish: ${p.error}` : p?.error ? p.error : (st?.pending.length ? "A recording is waiting to be transcribed (the audio is kept until then)." : "");
  }

  // ── Notes list ─────────────────────────────────────────────────────────────────────────────────
  const listHost = h("div");
  const detailHost = h("div");
  async function loadList() {
    const r = await api("sessions");
    clear(listHost);
    const rows: Row[] = r?.sessions ?? [];
    if (!rows.length) listHost.append(h("div", { class: "hint", text: "No notes yet. They appear here after a recording is transcribed." }));
    for (const s of rows) {
      const b = h("button", { class: s.id === openId ? "primary" : "", style: "display:block;width:100%;text-align:left;margin:4px 0",
        text: `${s.title} — ${new Date(s.started * 1000).toLocaleString()} · ${mmss(s.duration)}${s.speakers.length ? " · " + s.speakers.join(", ") : ""}` });
      b.addEventListener("click", () => { openId = openId === s.id ? "" : s.id; void loadList(); void loadDetail(); });
      listHost.append(b);
      if (s.preview) listHost.append(h("div", { class: "hint", text: s.preview }));
    }
  }
  async function loadDetail() {
    clear(detailHost);
    if (!openId) return;
    const r = await api(`session?id=${encodeURIComponent(openId)}`);
    const d: Doc | undefined = r?.session;
    if (!d) return;
    const box = h("div", { style: "border-top:1px solid rgba(128,128,128,.3);margin-top:10px;padding-top:10px" });
    if (d.summary) {
      box.append(h("div", { text: d.summary.text }));
      if (d.summary.todos.length) box.append(h("ul", {}, ...d.summary.todos.map((t) => h("li", { text: t.text + (t.owner ? ` (${t.owner})` : "") }))));
      box.append(h("div", { class: "hint", text: d.summary.provider === "local" ? "Written by a model on this computer." : "Written by your AI provider from the transcript text." }));
    }
    for (const l of d.lines) box.append(h("div", { style: "margin:4px 0" }, h("b", { text: l.speaker }), h("span", { class: "hint", text: ` ${mmss(l.start)} ` }), l.text));
    const actions = h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center" });
    const copy = h("button", { text: "Copy as Markdown" });
    copy.addEventListener("click", async () => {
      const e = await api(`export?id=${encodeURIComponent(d.id)}`);
      try { await navigator.clipboard.writeText(e?.markdown ?? ""); c.flashSaved(true); } catch { c.flashSaved(false); }
    });
    actions.append(copy);
    if (st?.settings.p7_summarize) {
      const sum = h("button", { text: d.summary ? "Summarize again" : "Summarize" });
      sum.addEventListener("click", async () => { sum.setAttribute("disabled", ""); await api("summarize", { id: d.id }); setTimeout(() => { void loadDetail(); void loadList(); }, 4000); });
      actions.append(sum);
    }
    const sel = h("select", {}, ...d.speakers.map((s) => h("option", { value: s, text: s }))) as HTMLSelectElement;
    const nm = h("input", { type: "text", placeholder: "Rename speaker to…", maxlength: "40" }) as HTMLInputElement;
    const rn = h("button", { text: "Rename" });
    rn.addEventListener("click", async () => { if (!nm.value.trim()) return; await api("rename-speaker", { id: d.id, old: sel.value, new: nm.value }); void loadDetail(); void loadList(); });
    actions.append(sel, nm, rn, c.confirmButton("Delete notes", "Click again to delete", async () => { await api("delete", { id: d.id }); openId = ""; void loadList(); void loadDetail(); }));
    box.append(actions);
    detailHost.append(box);
  }

  // ── Settings ───────────────────────────────────────────────────────────────────────────────────
  async function save(patch: Record<string, unknown>) { const r = await api("settings", patch); c.flashSaved(!!r?.ok); void refresh(); }
  const retention = h("select", {},
    ...[["7", "7 days"], ["30", "30 days"], ["90", "90 days"], ["365", "1 year"], ["0", "Keep until I delete"]].map(([v, t]) => h("option", { value: v, text: t }))) as HTMLSelectElement;
  retention.addEventListener("change", () => void save({ p7_retention_days: Number(retention.value) }));
  const diarToggle = c.toggle(true, (v) => void save({ p7_diarize: v }));
  const dlBtn = h("button", { class: "primary", text: "Download speaker model" });
  dlBtn.addEventListener("click", async () => { await api("diarization/download", {}); void refresh(); });
  const dlCancel = h("button", { text: "Cancel" });
  dlCancel.addEventListener("click", async () => { await api("diarization/cancel", {}); void refresh(); });
  const dlDelete = c.confirmButton("Delete model", "Click again to delete", async () => { await api("diarization/delete", {}); void refresh(); });
  const dlText = h("span", { class: "hint" });
  const sumToggle = c.toggle(false, (v) => void save({ p7_summarize: v }));
  const provider = h("select", {}, h("option", { value: "local", text: "A model on this computer (Ollama)" }), h("option", { value: "key", text: "My AI provider key" })) as HTMLSelectElement;
  provider.addEventListener("change", () => void save({ p7_summary_provider: provider.value }));
  const privacy = h("div", { class: "hint" });

  function paintSettings() {
    if (!st) return;
    const s = st.settings, d = st.diarization;
    retention.value = String(s.p7_retention_days);
    diarToggle.classList.toggle("on", s.p7_diarize);
    sumToggle.classList.toggle("on", s.p7_summarize);
    provider.value = s.p7_summary_provider;
    provider.disabled = !s.p7_summarize;
    privacy.textContent = !s.p7_summarize ? "Off. Nothing is sent anywhere."
      : s.p7_summary_provider === "local" ? "On. The transcript text is processed by Ollama on this computer. Audio is never sent."
      : "On. The transcript TEXT is sent to the AI provider you set under AI. Audio is never sent.";
    dlBtn.style.display = !d.installed && !d.downloading ? "" : "none";
    dlCancel.style.display = d.downloading ? "" : "none";
    dlDelete.style.display = d.installed && !d.downloading ? "" : "none";
    dlText.textContent = d.downloading ? `Downloading… ${Math.round(d.progress * 100)}%` : d.installed ? "Speaker model ready."
      : d.error ? d.error : !d.sherpa ? "The speech package (sherpa-onnx) is missing." : `Optional, about ${d.approx_mb} MB. Without it the other side is labelled “Remote”.`;
  }

  async function refresh() {
    const r = await api("status");
    if (r?.ok) { st = r as Status; paintRecord(); paintSettings(); }
    else recLine.textContent = "Could not reach Lyra — is the backend running?";
    const busy = st && (st.recorder.recording || st.diarization.downloading || ["transcribing", "speakers", "summarizing"].includes(st.processing.stage));
    if (!busy && lastBusy) { void loadList(); void loadDetail(); }
    lastBusy = !!busy;
    isBusy = !!busy;
    // A refresh made by hand (after a button) must not leave two timers: the poll re-arms itself.
    poll?.reschedule();
  }
  // Polled only while this panel is on screen and the window is visible (core/visiblePoll.ts): once a second while a
  // call is recording or being processed, every 5 s otherwise. The poll's first run happens when the panel opens.
  let lastBusy = false, isBusy = false;
  let poll: ReturnType<typeof visiblePoll> | null = null;
  void loadList();

  const cards = [
    c.card("Record a call",
      reminder,
      c.item("Title", null, title),
      c.item("Consent", "Recording without telling people is not allowed in many places.", h("span", {}, ack, " ", ackLabel)),
      c.item("Recording", "Records your microphone and what the PC plays (the other people). Audio is saved only temporarily.", startBtn, stopBtn),
      recLine, warn, procLine),
    c.card("Meeting notes", listHost, detailHost),
    c.card("Notetaker settings",
      c.item("Keep notes for", "Older notes are deleted automatically. Audio is deleted as soon as the notes are written.", retention),
      c.item("Tell speakers apart", "Uses a small extra model on this computer.", diarToggle, dlBtn, dlCancel, dlDelete, dlText),
      c.item("Summary and to-dos", "Sends the transcript TEXT (never audio) to the option you choose. Off by default.", sumToggle, provider),
      privacy),
  ];
  poll = visiblePoll(refresh, { ms: () => (isBusy ? 1000 : 5000), target: cards[0] });
  return cards;
}
