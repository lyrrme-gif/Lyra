// Phase 1 — Voice engine card: the Parakeet voice model (required, no engine choice, no delete), one-click download, status pill.
// Built only from the Settings helpers (card/item/toggle/confirmButton) so the design pass can restyle it.
import { h } from "../../views/dom";
import { visiblePoll, type VisiblePoll } from "../../core/visiblePoll";

type Kid = Node | string | null | undefined | false;

export interface P1Ctx {
  lyra: string;
  card: (title: Kid, ...children: Kid[]) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: Kid[]) => HTMLElement;
  toggle: (on: boolean, onChange: (v: boolean) => void) => HTMLElement;
  confirmButton: (label: string, confirmLabel: string, onConfirm: () => void, cls?: string) => HTMLElement;
  flashSaved: (ok?: boolean) => void;
}

interface P1Status {
  ok?: boolean; ready: boolean; engine: string; downloading: boolean; progress: number; mode: string;
  pill: string; error?: string;
  parakeet: { state: string; installed: boolean; available: boolean; error: string; resumable: boolean;
    bytes_done: number; bytes_total: number; size_bytes: number };
}

const mb = (n: number) => `${Math.round(n / 1e6)} MB`;

export function voiceEngineCards(c: P1Ctx): HTMLElement[] {
  let dlPoll: VisiblePoll | undefined;

  async function call(path: string, body?: unknown): Promise<P1Status | null> {
    try {
      const r = await fetch(`${c.lyra}/p1/voice/${path}`, body === undefined ? undefined : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const d = await r.json().catch(() => null);
      if (d && typeof d === "object") return d as P1Status;
    } catch { /* backend not running */ }
    return null;
  }

  const dot = h("i", { class: "dot", style: "background:#6b7079" });
  const pillText = h("span", { text: "Checking voice model…" });
  const pill = h("span", { class: "chip" }, dot, pillText);

  const bar = h("div", { class: "p1-bar", style: "height:6px;border-radius:3px;background:rgba(128,128,128,.25);overflow:hidden;flex:1;min-width:120px" },
    h("div", { class: "p1-bar-fill", style: "height:100%;width:0%;background:#22c55e;transition:width .3s" }));
  const barFill = bar.firstElementChild as HTMLElement;
  const progressText = h("span", { class: "hint" });
  const err = h("div", { class: "hint", style: "color:#f4505e" });

  const dlBtn = h("button", { class: "primary", text: "Download voice model (~700 MB)" });
  dlBtn.addEventListener("click", async () => { const r = await call("download", {}); if (r) apply(r); });
  const cancelBtn = h("button", { text: "Cancel" });
  cancelBtn.addEventListener("click", async () => { const r = await call("cancel", {}); if (r) apply(r); });

  function apply(s: P1Status) {
    const p = s.parakeet;
    dot.style.background = s.ready ? "#22c55e" : s.downloading ? "#f5a524" : "#f4505e";
    pillText.textContent = s.pill;
    const showProgress = s.downloading;
    bar.style.display = showProgress ? "" : "none";
    progressText.style.display = showProgress ? "" : "none";
    barFill.style.width = `${Math.round(s.progress * 100)}%`;
    progressText.textContent = p.bytes_total ? `${mb(p.bytes_done)} of ${mb(p.bytes_total)}${p.state === "verifying" ? " — verifying" : ""}` : "";
    dlBtn.style.display = !p.installed && !s.downloading ? "" : "none";
    dlBtn.textContent = p.resumable ? "Resume download" : "Download voice model (~700 MB)";
    cancelBtn.style.display = s.downloading ? "" : "none";
    const msg = p.error || (!p.available ? "The speech package (onnx-asr) is not installed, so Whisper is used for now." : "");
    err.textContent = s.error || msg;
    // Download progress every 0.7 s only while downloading; cleared while this window is hidden.
    if (s.downloading && !dlPoll) dlPoll = visiblePoll(() => refresh(), { ms: 700, runOnShow: false });
    else if (!s.downloading && dlPoll) { dlPoll.stop(); dlPoll = undefined; }
  }

  async function refresh() {
    const r = await call("status");
    if (r) apply(r); else { pillText.textContent = "Could not reach Lyra — is the backend running?"; }
  }
  void refresh();

  return [
    c.card("Voice engine",
      c.item("Status", "Everything runs on this computer after the one-time download. No audio is saved or sent anywhere.", pill),
      c.item("Voice model", "Lyra understands your voice with this model (Parakeet v3, about 700 MB), stored in Lyra's data folder. It is required, so it can't be removed. Safe to stop and resume; the files are checked before use.",
        h("div", { style: "display:flex;gap:10px;align-items:center;flex-wrap:wrap" }, dlBtn, bar, progressText, cancelBtn)),
      err,
    ),
  ];
}
