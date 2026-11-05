// Settings → About → Updates (phase 8).
// Talks to the backend routes in backend/api_p8.py. Nothing here touches the network except Lyra's own local
// backend (127.0.0.1:8765); the backend is the one that asks GitHub. Installing is a deliberate two-click action,
// and the Tauri command `install_update` re-checks the installer's SHA-256 once more before running it.

import { invoke } from "@tauri-apps/api/core";
import { h, clear } from "../../../shared/views/dom";
import { visiblePoll, type VisiblePoll } from "../../../shared/core/visiblePoll";

const LYRA = "http://127.0.0.1:8765";

interface UpdateInfo {
  version: string;
  name: string;
  published_at: string;
  prerelease: boolean;
  notes: string;
  html_url: string;
  size: number;
  installer_name: string;
  checksum_sources: string[];
  verifiable: boolean;
  problem: string;
}
interface Problem {
  code: string;
  message: string;
  retry_at: number;
}
interface Snapshot {
  ok: boolean;
  enabled: boolean;
  repo: string;
  prereleases: boolean;
  current: string;
  phase: "idle" | "checking" | "available" | "up_to_date" | "downloading" | "ready" | "error";
  update: UpdateInfo | null;
  error: Problem | null;
  progress: { done: number; total: number };
  last_check: number;
  ready_version: string;
  throttled: boolean;
  updates_dir: string;
}
interface InstallHandoff {
  ok: boolean;
  path?: string;
  sha256?: string;
  version?: string;
  error?: Problem;
}

async function api<T = Snapshot>(path: string, body?: unknown): Promise<T | null> {
  try {
    const r = await fetch(`${LYRA}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    // 4xx still carries a readable snapshot/error body; only a dead backend returns nothing.
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

function switchEl(on: boolean, onChange: (v: boolean) => void): HTMLElement {
  const el = h("button", { class: on ? "switch on" : "switch", "aria-pressed": on, type: "button" });
  el.addEventListener("click", () => {
    const next = !el.classList.contains("on");
    el.classList.toggle("on", next);
    el.setAttribute("aria-pressed", String(next));
    onChange(next);
  });
  return el;
}

function item(label: string, hint: string | null, control?: HTMLElement): HTMLElement {
  const el = h("div", { class: "item" }, h("div", { class: "row" }, h("label", { text: label }), control ?? null));
  if (hint) el.append(h("div", { class: "hint", text: hint }));
  return el;
}

function btn(text: string, onClick: () => void, extra = ""): HTMLButtonElement {
  const b = h("button", { class: `btn ${extra}`.trim(), type: "button", text }) as HTMLButtonElement;
  b.addEventListener("click", onClick);
  return b;
}

const mb = (bytes: number) => `${(bytes / 1048576).toFixed(bytes >= 104857600 ? 0 : 1)} MB`;

function whenText(ts: number): string {
  if (!ts) return "never";
  const mins = Math.round((Date.now() / 1000 - ts) / 60);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

/** The Updates card for the About panel. `appVersion` is the version of the app the user is running. */
export function updateCards(appVersion: string): HTMLElement[] {
  const cur = appVersion || undefined;
  const host = h("div", { class: "group" });
  const card = h("section", { class: "card" }, h("h3", { text: "Updates" }), host);
  let snap: Snapshot | null = null;
  let poll: VisiblePoll | undefined;
  let armedUntil = 0;           // second click window for "Install and restart"
  let installNote = "";

  const stopPolling = () => {
    poll?.stop();
    poll = undefined;
  };
  const take = (s: Snapshot | null) => {
    snap = s;
    render();
    if (s?.phase === "downloading" && poll === undefined) {
      // Download progress: 500 ms only while downloading, cleared while this window is hidden.
      poll = visiblePoll(async () => {
        if (!host.isConnected) return stopPolling();
        const next = await api("/p8/status");
        if (!next) return;
        snap = next;
        render();
        if (next.phase !== "downloading") stopPolling();
      }, { ms: 500, runOnShow: false });
    }
  };

  const saveRepo = async (repo: string) => take(await api("/p8/config", { repo }));

  function updateBlock(s: Snapshot): HTMLElement | null {
    const u = s.update;
    if (!u) return null;
    const box = h("div", { class: "item" });
    box.append(h("div", { class: "row" }, h("label", { text: `Lyra ${u.version} is available${u.prerelease ? " (pre-release)" : ""}` }),
      h("span", { class: "val", text: u.size ? mb(u.size) : "" })));
    if (u.notes) box.append(h("div", { class: "hint", style: "white-space:pre-wrap", text: u.notes }));  // plain text only

    if (!u.verifiable) {
      box.append(h("div", { class: "notice warn", text: `Lyra will not install this one. ${u.problem}` }));
    } else {
      box.append(h("div", { class: "hint", text: `Will be checked against: ${u.checksum_sources.join(", ")}.` }));
    }

    const actions = h("div", { class: "row", style: "gap:8px;justify-content:flex-start;flex-wrap:wrap" });
    if (s.phase === "downloading") {
      const pct = s.progress.total ? Math.min(100, Math.round((s.progress.done / s.progress.total) * 100)) : 0;
      const bar = h("div", { style: "flex:1;min-width:160px;height:6px;border-radius:3px;background:rgba(127,127,127,.25);overflow:hidden" },
        h("div", { style: `height:100%;width:${pct}%;background:var(--accent,#6aa2ff);transition:width .3s` }));
      actions.append(bar, h("span", { class: "hint", text: `${pct}% · ${mb(s.progress.done)} of ${mb(s.progress.total)}` }),
        btn("Cancel", async () => take(await api("/p8/cancel", {}))));
    } else if (s.phase === "ready" || s.ready_version === u.version) {
      const armed = Date.now() < armedUntil;
      const go = btn(armed ? "Click again to install and restart" : "Install and restart", () => void install(u.version), armed ? "primary" : "");
      actions.append(go, h("span", { class: "hint", text: "Verified. Lyra will close, the installer runs, then Lyra opens again." }));
    } else if (u.verifiable) {
      actions.append(btn("Download and verify", async () => take(await api("/p8/download", { current: cur }))));
    }
    if (s.phase !== "downloading") {
      actions.append(btn("Skip this version", async () => take(await api("/p8/skip", { version: u.version }))));
      if (u.html_url) {
        actions.append(btn("Release notes on GitHub", () => void invoke("open_url", { url: u.html_url }).catch(() => undefined)));
      }
    }
    box.append(actions);
    return box;
  }

  async function install(version: string) {
    if (Date.now() >= armedUntil) {                       // first click only arms the button
      armedUntil = Date.now() + 5000;
      render();
      window.setTimeout(render, 5100);
      return;
    }
    armedUntil = 0;
    installNote = "";
    const hand = await api<InstallHandoff>("/p8/install", { version, confirm: true, current: cur });
    if (!hand || !hand.ok || !hand.path || !hand.sha256) {
      installNote = hand?.error?.message ?? "Lyra's backend did not answer. Is it running?";
      return take(await api("/p8/status"));
    }
    try {
      await invoke("install_update", { path: hand.path, sha256: hand.sha256 });   // quits Lyra on success
    } catch (e) {
      installNote = `${String(e)} The verified installer is here if you want to run it yourself: ${hand.path}`;
      take(await api("/p8/status"));
    }
  }

  function render() {
    clear(host);
    const s = snap;
    if (!s) {
      host.append(h("div", { class: "notice warn", text: "Could not reach Lyra's backend, so update settings are not available. Is it running?" }));
      return;
    }
    const auto = switchEl(s.enabled, async (v) => take(await api("/p8/config", { enabled: v })));
    host.append(item("Check for updates automatically",
      "Off by default. When on, Lyra asks GitHub twice a day whether a newer version exists. It never downloads or installs anything by itself.", auto));

    const repoInput = h("input", { type: "text", placeholder: "owner/repo", value: s.repo, spellcheck: "false", style: "min-width:220px" }) as HTMLInputElement;
    const repoRow = h("div", { class: "row", style: "gap:8px" }, repoInput, btn("Save", () => void saveRepo(repoInput.value)));
    host.append(item("Where updates come from", "The public GitHub repository that publishes Lyra releases, for example acme/lyra.", repoRow));
    host.append(item("Include pre-releases", "Beta and release-candidate versions. Off keeps you on stable releases.",
      switchEl(s.prereleases, async (v) => take(await api("/p8/config", { prereleases: v })))));

    const status = s.phase === "checking" ? "Checking…"
      : s.phase === "up_to_date" ? "Lyra is up to date."
      : s.phase === "available" || s.phase === "downloading" || s.phase === "ready" ? ""
      : s.phase === "error" && s.error ? s.error.message : "";
    const checkBtn = btn("Check now", async () => {
      take({ ...s, phase: "checking", error: null });
      take(await api("/p8/check", { current: cur }));
    }) as HTMLButtonElement;
    checkBtn.disabled = s.phase === "checking" || s.phase === "downloading";
    const right = h("div", { class: "row", style: "gap:10px" }, h("span", { class: "hint", text: status }), checkBtn);
    host.append(item(`You have Lyra ${s.current || appVersion || "…"}`,
      `Last checked: ${whenText(s.last_check)}${s.throttled ? " (you just checked)" : ""}.`, right));

    if (s.phase === "error" && s.error && s.error.code !== "rate_limited" && s.update === null) {
      // the error text is already beside the button; keep a stable notice for the serious ones
      if (["checksum_mismatch", "no_checksum", "insecure_url", "disk_full"].includes(s.error.code)) {
        host.append(h("div", { class: "notice err", text: s.error.message }));
      }
    }
    const block = updateBlock(s);
    if (block) host.append(block);
    if (s.phase === "error" && s.update && s.error) host.append(h("div", { class: "notice err", text: s.error.message }));
    if (installNote) host.append(h("div", { class: "notice err", style: "user-select:text", text: installNote }));

    host.append(h("div", { class: "hint", style: "padding:10px 14px", text:
      "No telemetry. Lyra only talks to api.github.com and the release's own files on github.com; GitHub sees your IP address " +
      "and a “Lyra-Updater/version” user agent. An update is installed only after you click, and only if its SHA-256 " +
      "matches the checksum published with that release." }));
  }

  host.append(h("div", { class: "hint", style: "padding:10px 14px", text: "Loading…" }));
  void api("/p8/status").then(async (s) => {
    take(s);
    // A verified download can already be waiting from an earlier session. Only if the user turned updates on,
    // look once so the card can offer it (the backend answers from its ETag cache when nothing changed).
    if (s && s.enabled && s.repo && s.phase === "idle" && s.ready_version) take(await api("/p8/check", { current: cur }));
  });
  return [card];
}
