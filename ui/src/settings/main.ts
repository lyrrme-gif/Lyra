// Settings — Lyra: LLM providers + editable mod slots + Coucou general prefs.

import "./settings.css";
import "./settings-mac.css";
import "./settings-lyra.css";
import "./settings-coucou.css";
import "./settings-frameless.css";
import "./controls-v2.css";
import { lineIcon } from "./coucou/icons";
import { Bridge, onEvent } from "../core/bridge";
import { DEFAULT_SETTINGS, readExtras, writeExtras, type LiveActivity, type Settings } from "../core/state";
import { h, clear } from "../views/dom";
import { FEATURES, FEATURE_GROUPS, featureEnabled, type FeatureDef } from "../core/features";
import { filesHost } from "./files";
import { visiblePoll, type VisiblePoll } from "../core/visiblePoll";
import { islandScene, navScene as navScreen } from "./screenPreview";
import { themePreview } from "./themePreview";
import { getFeatures } from "../core/featuresCache";
import { featurePreview } from "./featureScenes";
import { activityScene, playActivityPreview } from "./activityPreviews";
import { showSetupWizard } from "./setupWizard";
// >>> phase-8 begin
import { updateCards } from "./panels/p8_updates";
// <<< phase-8 end
import { accessCards } from "./accessCards";
import { ollamaCard } from "./ollama";
import { whenVisible } from "./lazy";
import { enhanceSelects } from "./uiKit";
import { installControlKit } from "./choiceKit";
import { mochiCards } from "./mochiPanel";
import { appsCards } from "./appsPanel";
import { WHATSAPP_BG, WHATSAPP_GLYPH } from "./whatsappIcon";
import { SETTINGS_GOTO_KEY } from "../core/appsApi";
import { AUTOHIDE_HINT, AUTOHIDE_MODES, normalizeAutoHide } from "../core/autohide";
// >>> phase-0 begin
import { p0KeyStatus } from "./panels/p0_keystatus";
// <<< phase-0 end
// >>> phase-1 begin
import { voiceEngineCards } from "./panels/p1_voice_engine";
// <<< phase-1 end
// >>> phase-2 begin
import { registerP2 } from "./panels/p2_dictation";
import { settingKeyButton } from "./panels/p2_common";
import { recordingsCards } from "./recordingsPanel";
// <<< phase-2 end
// >>> phase-3 begin
import { statsCards } from "./panels/p3_statistics";
// <<< phase-3 end
// >>> phase-4 begin
import { register as registerP4 } from "./panels/p4_design";
// <<< phase-4 end
// >>> phase-5 begin
// <<< phase-5 end
// >>> phase-6 begin
// <<< phase-6 end
// >>> phase-7 begin
// <<< phase-7 end

// Live UI trace (clicks, what opened, errors) -> backend terminal. See core/trace.ts.
import { installTracer } from "../core/trace";
import { registerPanel, withOwner } from "../core/loadOwner";
import { slog } from "./settingsLog";
import { setEco } from "../core/eco";
installTracer();
slog.start("general");   // Settings log (settingsLog.ts): from here on every request and tab change is recorded

const LYRA = "http://127.0.0.1:8765";

let settings: Settings = { ...DEFAULT_SETTINGS };
let version = "";

const root = document.getElementById("settings-root")!;

type Kid = Node | string | null | undefined | false;

// ── Auto-save indicator ───────────────────────────────────────────────────────

const savedEl = h("span", { class: "saved" });
let savedTimer = 0;

function flashSaved(ok = true) {
  savedEl.textContent = ok ? "Saved ✓" : "Not saved";
  savedEl.className = ok ? "saved show" : "saved show err";
  window.clearTimeout(savedTimer);
  savedTimer = window.setTimeout(() => savedEl.classList.remove("show"), ok ? 1600 : 3500);
}

async function save() {
  try {
    await Bridge.saveSettings(settings);
    flashSaved();
  } catch {
    flashSaved(false);
  }
}

let saveSoonTimer = 0;
function saveSoon() {
  window.clearTimeout(saveSoonTimer);
  saveSoonTimer = window.setTimeout(() => void save(), 350);
}

function toggle(on: boolean, onChange: (v: boolean) => void): HTMLElement {
  const el = h("button", { class: on ? "switch on" : "switch", "aria-pressed": on });
  el.addEventListener("click", () => {
    const next = !el.classList.contains("on");
    el.classList.toggle("on", next);
    onChange(next);
  });
  return el;
}

function statusDot(ok: boolean | null): HTMLElement {
  const color = ok == null ? "#6b7079" : ok ? "#22c55e" : "#f4505e";
  return h("i", { class: "dot", style: `background:${color}` });
}

/** POST/GET JSON to Lyra's backend. Never throws: returns ok=false with a readable error. */
async function lyra(path: string, body?: unknown): Promise<{ ok: boolean; data: any; error: string }> {
  try {
    const r = await fetch(`${LYRA}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, data, error: r.ok ? "" : String(data.error || r.statusText) };
  } catch {
    return { ok: false, data: {}, error: "Could not reach Lyra — is the backend running?" };
  }
}

/** Two-click button for anything destructive: the first click asks, the second does it. */
function confirmButton(label: string, confirmLabel: string, onConfirm: () => void, cls = "danger"): HTMLElement {
  const b = h("button", { class: cls, text: label });
  let timer = 0;
  const reset = () => {
    b.textContent = label;
    b.classList.remove("armed");
  };
  b.addEventListener("click", () => {
    if (b.classList.contains("armed")) {
      window.clearTimeout(timer);
      reset();
      onConfirm();
      return;
    }
    b.textContent = confirmLabel;
    b.classList.add("armed");
    timer = window.setTimeout(reset, 4000);
  });
  return b;
}

// ── Layout building blocks ────────────────────────────────────────────────────

/** One searchable setting: label + controls on a row, short helper text underneath. */
function item(label: string, hint: string | HTMLElement | null, ...controls: Kid[]): HTMLElement {
  const hintEl =
    hint == null ? null : typeof hint === "string" ? h("div", { class: "hint", text: hint }) : hint;
  const el = h("div", { class: "item" }, h("div", { class: "row" }, h("label", { text: label }), ...controls), hintEl);
  el.dataset.s = `${label} ${hintEl?.textContent ?? ""}`.toLowerCase();
  return el;
}

function card(title: Kid, ...children: Kid[]): HTMLElement {
  // macOS grouped list: heading sits above an inset rounded group of rows.
  return h("section", { class: "card" }, title ? h("h3", {}, title) : null, h("div", { class: "group" }, ...children));
}

/** Filled in by main(): build a tab that is still an empty shell now / all of them (search needs every card). */
let ensureBuilt: (id: string) => void = () => {};
let ensureAll: () => void = () => {};

function panel(title: string, sub: string, ...cards: Kid[]): HTMLElement {
  const el = h("div", { class: "panel" }, h("h2", { text: title }), h("p", { class: "sub", text: sub }), ...cards);
  el.dataset.title = title.toLowerCase();
  return el;
}

// ── Status strip (backend + LLM key) ──────────────────────────────────────────

let llmInfo: LlmInfo | null = null;
let backendUp: boolean | null = null;
let refreshLlmUi: () => void = () => {};
let onProviderChanged: () => void = () => {}; // AI tab: keeps the fallback list in step with the provider
const backendChip = h("span", { class: "chip" });
const llmChip = h("span", { class: "chip" });

function renderStatus() {
  clear(backendChip);
  backendChip.append(
    statusDot(backendUp),
    h("span", { text: backendUp == null ? "Checking backend…" : backendUp ? "Backend running" : "Backend offline" }),
  );
  clear(llmChip);
  let ok: boolean | null = null;
  let text = "LLM key unknown";
  if (backendUp !== false && llmInfo) {
    ok = hasKey(llmInfo, llmInfo.provider || "openrouter");
    text = ok ? "LLM key OK" : "No LLM key";
  }
  llmChip.append(statusDot(ok), h("span", { text }));
}

async function pollBackend() {
  const was = backendUp;
  try {
    const r = await fetch(`${LYRA}/health`, { signal: AbortSignal.timeout(2000) });
    backendUp = r.ok;
  } catch {
    backendUp = false;
  }
  if (backendUp && was !== true && llmInfo) {
    try {
      const r = await fetch(`${LYRA}/settings/llm`);
      if (r.ok) Object.assign(llmInfo, await r.json());
      refreshLlmUi();
    } catch {
      /* */
    }
  }
  renderStatus();
}

// ── LLM providers (OpenRouter / Gemini / Groq) ────────────────────────────────

type LlmInfo = {
  provider: string;
  openrouter_model: string;
  gemini_model: string;
  groq_model: string;
  ollama_model: string;
  has_openrouter: boolean;
  has_gemini: boolean;
  has_groq: boolean;
  has_ollama: boolean;
};

const PROVIDERS: [string, string][] = [
  ["openrouter", "OpenRouter"],
  ["gemini", "Gemini"],
  ["groq", "Groq"],
  ["ollama", "Ollama (local)"],
];

const MODELS: Record<string, [string, string][]> = {
  openrouter: [
    ["google/gemini-3.8-flash", "Gemini 3.8 Flash"],
    ["google/gemini-2.5-pro", "Gemini 2.5 Pro"],
    ["openai/gpt-4o-mini", "GPT-4o Mini"],
    ["anthropic/claude-sonnet-4", "Claude Sonnet 4"],
  ],
  gemini: [
    ["gemini-3.8-flash", "Gemini 3.8 Flash"],
    ["gemini-3.7-flash", "Gemini 3.7 Flash"],
  ],
  groq: [
    ["openai/gpt-oss-120b", "GPT-OSS 120B"],
    ["openai/gpt-oss-20b", "GPT-OSS 20B"],
    ["llama-3.3-70b-versatile", "Llama 3.3 70B"],
  ],
  ollama: [["", "Automatic (first installed model)"]],
};

function keyFieldFor(provider: string): string {
  return provider === "gemini"
    ? "gemini_api_key"
    : provider === "groq"
      ? "groq_api_key"
      : "openrouter_api_key";
}

function modelFieldFor(provider: string): string {
  if (provider === "ollama") return "ollama_model";
  return provider === "gemini"
    ? "gemini_model"
    : provider === "groq"
      ? "groq_model"
      : "openrouter_model";
}

function placeholderFor(provider: string): string {
  if (provider === "gemini") return "AIza…";
  if (provider === "groq") return "gsk_…";
  return "sk-or-…";
}

function hasKey(info: LlmInfo, provider: string): boolean {
  if (provider === "ollama") return info.has_ollama;
  if (provider === "gemini") return info.has_gemini;
  if (provider === "groq") return info.has_groq;
  return info.has_openrouter;
}

function llmSection(info: LlmInfo): HTMLElement {
  llmInfo = info;
  let provider = info.provider || "openrouter";
  const title = h("span", { class: "ttl" });
  const section = card(title);

  const providerSelect = h("select", {}) as HTMLSelectElement;
  for (const [id, label] of PROVIDERS) {
    providerSelect.append(h("option", { value: id, text: label }));
  }
  providerSelect.value = provider;
  providerSelect.dataset.glyph = "mono";   // cards get a coloured initial instead of a radio dot

  const keyInput = h("input", {
    type: "password",
    placeholder: placeholderFor(provider),
    style: "flex:1 1 140px;min-width:0",
    autocomplete: "off",
    spellcheck: "false",
  }) as HTMLInputElement;

  const modelSelect = h("select", {}) as HTMLSelectElement;
  const fillModels = async () => {
    clear(modelSelect);
    const list = MODELS[provider] || MODELS.openrouter;
    for (const [id, label] of list) modelSelect.append(h("option", { value: id, text: label }));
    const current =
      provider === "ollama"
        ? info.ollama_model
        : provider === "gemini"
        ? info.gemini_model
        : provider === "groq"
          ? info.groq_model
          : info.openrouter_model;
    if (current && !list.some(([id]) => id === current)) {
      modelSelect.append(h("option", { value: current, text: current }));
    }
    modelSelect.value = current || list[0][0];
    try {
      const rr = await fetch(`${LYRA}/settings/llm/models?provider=${encodeURIComponent(provider)}`);
      const md = await rr.json();
      if (md.ok && Array.isArray(md.models) && md.models.length) {
        const selected = modelSelect.value;
        clear(modelSelect);
        for (const id of md.models) modelSelect.append(h("option", { value: id, text: id }));
        modelSelect.value = md.models.includes(selected) ? selected : (md.models[0] || selected);
      }
    } catch { /* static list remains */ }
  };
  void fillModels();

  const feedback = h("div", {});
  const state = h("div", { class: "hint" });

  const refreshHead = () => {
    clear(title);
    title.append(statusDot(hasKey(info, provider)), h("span", { text: "LLM provider" }));
    keyRow.style.display = provider === "ollama" ? "none" : "";
    state.textContent = provider === "ollama"
      ? "Runs on this PC. Install and start Ollama; no key is needed."
      : hasKey(info, provider)
        ? "Key saved — used by Lyra chat & voice. Paste a new one to replace it."
        : "No key yet — chat needs one.";
    keyInput.placeholder = hasKey(info, provider)
      ? "••••••••  (saved — paste to replace)"
      : placeholderFor(provider);
    renderStatus();
    onProviderChanged();
  };
  refreshLlmUi = () => {
    provider = info.provider || provider;
    providerSelect.value = provider;
    void fillModels();
    refreshHead();
  };

  /** POST provider + model (and the key, when one was typed). Keys never leave the backend again. */
  const persist = async (withKey: boolean, notify: boolean) => {
    clear(feedback);
    const payload: Record<string, string> = {
      provider,
      [modelFieldFor(provider)]: modelSelect.value,
    };
    const key = keyInput.value.trim();
    if (withKey && key) payload[keyFieldFor(provider)] = key;
    try {
      const res = await fetch(`${LYRA}/settings/llm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      info.has_openrouter = !!data.has_openrouter;
      info.has_gemini = !!data.has_gemini;
      info.has_groq = !!data.has_groq;
      info.has_ollama = !!data.has_ollama;
      info.provider = data.provider || provider;
      if (withKey && key) keyInput.value = "";
      if (notify) feedback.append(h("div", { class: "notice ok", text: "Saved. Lyra will use this provider now." }));
      flashSaved();
      refreshHead();
    } catch (err) {
      flashSaved(false);
      feedback.append(
        h("div", {
          class: "notice err",
          text: `Could not save — is Lyra running? (${String(err)})`,
        }),
      );
    }
  };

  providerSelect.addEventListener("change", () => {
    provider = providerSelect.value;
    void fillModels();
    refreshHead();
    void persist(false, false);
  });
  modelSelect.addEventListener("change", () => void persist(false, false));
  keyInput.addEventListener("change", () => {
    if (keyInput.value.trim()) void persist(true, true);
  });

  const saveBtn = h("button", { class: "primary", text: "Save key" });
  saveBtn.addEventListener("click", () => void persist(true, true));

  const clearBtn = h("button", { class: "danger", text: "Remove key" });
  clearBtn.addEventListener("click", async () => {
    clear(feedback);
    try {
      const res = await fetch(`${LYRA}/settings/llm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, clear_provider_key: true }),
      });
      if (!res.ok) throw new Error(res.statusText);
      if (provider === "gemini") info.has_gemini = false;
      else if (provider === "groq") info.has_groq = false;
      else info.has_openrouter = false;
      feedback.append(h("div", { class: "notice ok", text: "Key removed." }));
      flashSaved();
      refreshHead();
    } catch (err) {
      flashSaved(false);
      feedback.append(h("div", { class: "notice err", text: String(err) }));
    }
  });

  const testResult = h("span", { class: "hint" });
  const testBtn = h("button", { text: "Test connection" });
  testBtn.addEventListener("click", async () => {
    testBtn.setAttribute("disabled", "");
    testResult.textContent = "Testing…";
    try {
      const res = await fetch(`${LYRA}/settings/llm/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider }),
      });
      const data = await res.json();
      testResult.textContent = `${data.ok ? "✓" : "✕"} ${data.message || ""}${data.ms != null ? ` (${data.ms} ms)` : ""}`;
    } catch {
      testResult.textContent = "✕ Could not reach Lyra — is the backend running?";
    } finally {
      testBtn.removeAttribute("disabled");
    }
  });

  const keyRow = item("API key", state, keyInput, saveBtn, clearBtn);
  section.append(
    item("Provider", "Choose a cloud provider or local Ollama.", providerSelect),
    keyRow,
    item("Connection", "Sends a real short chat request with the selected model.", testBtn, testResult),
    item("Model", "The model used for Lyra's replies.", modelSelect),
    feedback,
    h("div", {
      class: "hint",
      text: "Keys are stored only in Lyra’s backend (llm_keys.json) and are never shown in full.",
    }),
  );
  // >>> phase-0 begin
  p0KeyStatus(section);
  // <<< phase-0 end
  refreshHead();
  return section;
}

// ── Mods (editable names, up to 4) ────────────────────────────────────────────


// ── General / Island prefs (Coucou island prefs — unchanged behaviour) ────────

function soundCard(): HTMLElement {
  const volume = h("input", {
    type: "range",
    min: "0",
    max: "0.2",
    step: "0.01",
    value: String(settings.soundVolume),
  }) as HTMLInputElement;
  volume.addEventListener("input", () => {
    settings.soundVolume = Number(volume.value);
    saveSoon();
  });
  return card(
    "Sound & startup",
    item(
      "Sound",
      "Sound effects played by the island. Use the slider for volume.",
      toggle(settings.soundEnabled, (v) => {
        settings.soundEnabled = v;
        void save();
      }),
      volume,
    ),
    item(
      "Launch at startup",
      "Start Lyra's island when you sign in to Windows.",
      toggle(settings.autostart, (v) => {
        settings.autostart = v;
        void save();
      }),
    ),
  );
}

/** Eco mode lives on General (not on the Features page). The look changes at once in every window (core/eco.ts);
 *  the slower hotkey / screen polling in the backend applies after the backend restarts. */
function ecoCard(featPrefs: Prefs): HTMLElement {
  return card(
    "Performance",
    item(
      "Eco mode",
      "As little CPU and GPU as possible while animations stay at 60 fps: no shadows, blurs or filters, the eyes stop following the cursor, and background checks slow down (restart Lyra's backend for those).",
      toggle(featPrefs.data.feature_eco_mode === true, (v) => {
        setEco(v);                                    // instant in every open window
        featPrefs.set("feature_eco_mode", v);         // saved in the backend
      }),
    ),
  );
}

/** Theme + position live in localStorage (same keys the island reads). */
function readIslandTheme(): string {
  try {
    const v = window.localStorage.getItem("lyra-island-theme");
    const migrated: Record<string, string> = { graphite:"midnight", "space-black":"pure-black", classic:"midnight", oled:"pure-black", glass:"frost", liquid:"frost", titanium:"midnight", "macos-dark":"midnight", "macos-light":"midnight" };
    if (v) {
      const theme = migrated[v] || v;
      if (["pure-black","midnight","aurora","dynamic","frost"].includes(theme)) {
        if (theme !== v) window.localStorage.setItem("lyra-island-theme", theme);
        return theme;
      }
    }
  } catch { /* ignore */ }
  return "midnight";
}

function readIslandPosition(): "floating" | "attached" {
  try {
    return window.localStorage.getItem("lyra-island-position") === "attached" ? "attached" : "floating";
  } catch {
    return "floating";
  }
}

function writeIslandTheme(theme: string) {
  try { window.localStorage.setItem("lyra-island-theme", theme); } catch { /* ignore */ }
}

function writeIslandPosition(position: "floating" | "attached") {
  try { window.localStorage.setItem("lyra-island-position", position); } catch { /* ignore */ }
}

/** "Island behaviour": everything about how the island opens, hides and pops up. `extra` = more rows to append (clipboard, music). */
function islandCard(notify: Prefs, extra: HTMLElement[] = []): HTMLElement {
  const autoClose = h("input", {
    type: "number",
    min: "3",
    max: "60",
    value: String(settings.autoCloseInterval),
    style: "width:4rem",
  }) as HTMLInputElement;
  autoClose.addEventListener("change", () => {
    settings.autoCloseInterval = Math.max(3, Number(autoClose.value) || 5);
    void save();
  });

  const screen = h("select", {}) as HTMLSelectElement;
  screen.append(
    h("option", { value: "primary", text: "Main display" }),
    h("option", { value: "cursor", text: "Display under the cursor" }),
  );
  screen.value = settings.screen;
  // With two or more screens, each one can be picked by name (the list is Windows' own order).
  void (async () => {
    try {
      const { availableMonitors } = await import("@tauri-apps/api/window");
      const mons = await availableMonitors();
      if (mons.length < 2) return;
      mons.forEach((m, i) => screen.append(h("option", {
        value: `display:${i + 1}`,
        text: `Screen ${i + 1}${m.position.x === 0 && m.position.y === 0 ? " (main)" : ""} · ${m.size.width}×${m.size.height}`,
      })));
      screen.value = settings.screen;   // the saved choice may be one of the screens just added
    } catch { /* not running inside Lyra: the two basic choices stay */ }
  })();
  screen.addEventListener("change", () => {
    settings.screen = screen.value as Settings["screen"];
    void save();
  });

  const wakeMode = h("select", {}) as HTMLSelectElement;
  wakeMode.append(
    h("option", { value: "dwell", text: "Hold the pointer on the top edge" }),
    h("option", { value: "instant", text: "Instant (any hover)" }),
  );
  wakeMode.value = settings.wakeMode;
  wakeMode.addEventListener("change", () => {
    settings.wakeMode = wakeMode.value as Settings["wakeMode"];
    void save();
  });

  const autoHide = h("select", {}) as HTMLSelectElement;
  for (const m of AUTOHIDE_MODES) autoHide.append(h("option", { value: m.id, text: m.label }));
  autoHide.value = normalizeAutoHide(settings.islandAutoHide);
  autoHide.addEventListener("change", () => {
    settings.islandAutoHide = normalizeAutoHide(autoHide.value);
    void save();
  });

  // ── Theme Studio ───────────────────────────────────────────────────────────
  // Visual cards make the theme choice feel like an Apple-style appearance picker.
  // The actual theme IDs/storage remain unchanged so this is backwards-compatible.
  type ThemeMeta = { id: string; label: string; sub: string; material: string; accent: string; glow: string };

  const themes: ThemeMeta[] = [
    { id:"pure-black", label:"Black", sub:"Absolute black · no glass", material:"#000", accent:"#fff", glow:"rgba(255,255,255,.08)" },
    { id:"midnight", label:"Midnight", sub:"Deep Apple glass", material:"linear-gradient(135deg,#17191e,#08090c)", accent:"#9aaeff", glow:"rgba(120,145,255,.28)" },
    { id:"aurora", label:"Aurora", sub:"Cool luminous colour", material:"linear-gradient(135deg,#101827,#151125)", accent:"#9ff2da", glow:"rgba(91,231,191,.27)" },
    { id:"dynamic", label:"Dynamic", sub:"Adaptive Lyra look", material:"linear-gradient(135deg,#151722,#252a40)", accent:"#c1ccff", glow:"rgba(139,157,255,.30)" },
    { id:"frost", label:"Frost", sub:"Black fading into glass", material:"linear-gradient(180deg,#000 0%,#000 46%,rgba(70,58,48,.78) 100%)", accent:"#3b82f6", glow:"rgba(59,130,246,.28)" },
  ];

  let currentTheme = readIslandTheme();
  const themeMochis: Array<{ setShape?: (s: string) => void }> = [];
  void getFeatures().then((p) => {                                  // the Mochi in the pictures is the one you picked
    const sh = (p as { mascot_shape?: string } | null)?.mascot_shape;
    if (sh) for (const c of themeMochis) c.setShape?.(sh);
  }).catch(() => undefined);
  const themeGrid = h("div", { class: "theme-gallery" });
  const themePreviewLabel = h("div", { class: "theme-preview-label" });

  const updateThemeSelection = (id: string, preview = true) => {
    currentTheme = id;
    writeIslandTheme(id);
    if (preview) {   // only a real click previews on the island; the initial paint must not pop it open at boot
      try {
        window.localStorage.setItem("lyra-theme-preview", `${id}|${Date.now()}`);
      } catch { /* ignore */ }
    }
    themeGrid.querySelectorAll<HTMLElement>(".theme-option").forEach((el) => {
      el.classList.toggle("on", el.dataset.theme === id);
      el.setAttribute("aria-checked", el.dataset.theme === id ? "true" : "false");
    });
    const meta = themes.find((x) => x.id === id) || themes[0];
    themePreviewLabel.textContent = `${meta.label} · ${meta.sub}`;
    flashSaved();
  };

  for (const meta of themes) {
    const pv = themePreview({ bg: meta.material, accent: meta.accent }, "cloud");   // the same picture as in the setup
    themeMochis.push(pv.mochi);
    const preview = pv.el;

    const option = h("button", {
      class: `theme-option${meta.id === currentTheme ? " on" : ""}`,
      type: "button",
      role: "radio",
      "aria-checked": meta.id === currentTheme ? "true" : "false",
    }, preview,
      h("span", { class: "theme-option-copy" },
        h("strong", { text: meta.label }),
        h("small", { text: meta.sub }),
      ),
      h("span", { class: "theme-check", text: "✓" }),
    ) as HTMLElement;
    option.dataset.theme = meta.id;
    option.addEventListener("click", () => updateThemeSelection(meta.id));
    themeGrid.append(option);
  }

  const themeStudio = h("div", { class: "theme-studio" },
    h("div", { class: "theme-studio-head" },
      h("div", {},
        h("strong", { text: "Island theme" }),
        h("p", { text: "Choose how Lyra feels. Changes preview on the island instantly." }),
      ),
      h("span", { class: "theme-preview-label", text: "" }),
    ),
    themeGrid,
  );
  updateThemeSelection(currentTheme, false);

  // ── Position picker (moved from the in-island settings tab) ───────────────
  let currentPosition = readIslandPosition();
  const posFloating = h("button", {
    class: currentPosition === "floating" ? "theme-chip on" : "theme-chip",
    type: "button",
    text: "Floating",
  });
  const posAttached = h("button", {
    class: currentPosition === "attached" ? "theme-chip on" : "theme-chip",
    type: "button",
    text: "Top attached",
  });
  const setPos = (p: "floating" | "attached") => {
    currentPosition = p;
    writeIslandPosition(p);
    posFloating.classList.toggle("on", p === "floating");
    posAttached.classList.toggle("on", p === "attached");
    flashSaved();
  };
  posFloating.addEventListener("click", () => setPos("floating"));
  posAttached.addEventListener("click", () => setPos("attached"));
  return card(
    "Island behaviour",
    item("Auto-close", "How long the expanded island stays open after you leave it.", autoClose, h("span", { class: "hint", text: "seconds" })),
    item("Auto-hide", `${AUTOHIDE_HINT} When the island is hidden, rest the pointer on the top centre of the screen for a moment and it comes back.`, autoHide),
    item("Wake gesture", "How to call the hidden island from the top edge. Hold keeps the top of the screen clickable.", wakeMode),
    item("Island lives on", "Which display the island appears on.", screen),
    item("Island theme", "Pick a visual style. Lyra previews the selected theme on the island for 5 seconds.", themeStudio),
    item("Pop-up duration", "How long a message, call or reminder stays in the notch before it hides.", prefNumber(notify, "notify_toast_ms", 1.5, 15, 0.5, 1000), h("span", { class: "hint", text: "seconds" })),
    ...extra,
  );
}

function aboutCard(): HTMLElement {
  const val = (text: string) => h("span", { class: "val wrap", text });
  const backendVer = val("…");
  const runtime = val("…");
  const uptime = val("…");
  const logFile = val("…");
  void lyra("/about").then((r) => {
    if (!r.ok) {
      for (const el of [backendVer, runtime, uptime, logFile]) el.textContent = "Backend offline";
      return;
    }
    const d = r.data;
    backendVer.textContent = `Python ${d.python}`;
    runtime.textContent = String(d.os || "");
    const m = Math.floor(Number(d.uptime_s) / 60);
    uptime.textContent = m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
    logFile.textContent = `${d.log_path} (${d.log_kb} KB)`;
  });
  return card(
    "Lyra",
    item("App version", "The Lyra UI you are running.", val(version || "1.0.0")),
    item("Backend", "The Python side that listens, thinks and speaks.", backendVer),
    item("System", null, runtime),
    item("Backend running for", "Since it last started.", uptime),
    item("Log file", "Where Lyra writes what she is doing.", logFile),
    item(
      "Privacy",
      "No telemetry. LLM keys go to Lyra’s local backend. Island sound/auto-close stay on this device.",
      h("span", { class: "val", text: "Local only" }),
    ),
  );
}

function advancedCards(a: Prefs): HTMLElement[] {
  const note = h("div", {});
  const say = (text: string, kind: "ok" | "err" | "warn") => {
    clear(note);
    note.append(h("div", { class: `notice ${kind}`, text }));
  };

  const openLogs = h("button", { text: "Open log folder" });
  openLogs.addEventListener("click", async () => {
    const r = await lyra("/logs/open", {});
    if (!r.ok) say(r.error || "Could not open the log folder.", "err");
  });

  const checkBtn = h("button", { text: "Check for problems" });
  checkBtn.addEventListener("click", async () => {
    say("Checking…", "warn");
    const r = await lyra("/diagnostics");
    if (!r.ok) {
      say(r.error || "Could not reach the backend.", "err");
      return;
    }
    const problems = (r.data.problems as string[]) || [];
    if (problems.length === 0) {
      say("All good — models, microphone and API key look fine.", "ok");
    } else {
      clear(note);
      note.append(
        h("div", {
          class: "notice warn",
          text: problems.map((p, i) => `${i + 1}. ${p}`).join(" "),
        }),
      );
    }
  });

  const restart = confirmButton(
    "Restart backend",
    "Click again to restart",
    async () => {
      const r = await lyra("/backend/restart", {});
      if (!r.ok) return say(r.error || "Could not restart the backend.", "err");
      say("Restarting… Lyra is offline for a few seconds.", "warn");
      backendUp = false;
      renderStatus();
    },
    "",
  );

  const reset = confirmButton("Reset to defaults", "Click again to reset", async () => {
    const r = await lyra("/settings/reset", {});
    if (!r.ok) return say(r.error || "Could not reset.", "err");
    settings = {
      ...settings,
      soundEnabled: DEFAULT_SETTINGS.soundEnabled,
      soundVolume: DEFAULT_SETTINGS.soundVolume,
      autoCloseInterval: DEFAULT_SETTINGS.autoCloseInterval,
      compactAutoHideInterval: DEFAULT_SETTINGS.compactAutoHideInterval,
      alwaysShowNotch: DEFAULT_SETTINGS.alwaysShowNotch,
      wakeMode: DEFAULT_SETTINGS.wakeMode,
      islandAutoHide: DEFAULT_SETTINGS.islandAutoHide,
      screen: DEFAULT_SETTINGS.screen,
    };
    await save();
    say("Reset done. Reloading…", "ok");
    window.setTimeout(() => location.reload(), 600);
  });

  return [
    card(
      "Logs",
      item(
        "Log level",
        "How much detail goes into the log file. Debug is chatty; use it only while chasing a problem. Applies right away.",
        prefSelect(a, "log_level", [
          ["WARNING", "Problems only"],
          ["INFO", "Normal"],
          ["DEBUG", "Debug"],
        ]),
      ),
      item("Log folder", "Opens the folder with ui.log, handy when sending a bug report.", openLogs),
    ),
    card(
      "Backend",
      item(
        "Restart backend",
        "Restarts Lyra’s Python process. Needed after changing the microphone, speaker or wake sensitivity if they do not apply on their own.",
        restart,
      ),
      item(
        "Diagnostics",
        "Lists missing voice models, a busy bridge port, a missing LLM key, or no microphone.",
        checkBtn,
      ),
      note,
    ),
    card(
      "Reset",
      item(
        "Reset all settings",
        "Puts notifications, AI style, voice, privacy and island options back to their defaults. API keys, shortcuts and memory are kept.",
        reset,
      ),
    ),
  ];
}

// ── Backend prefs groups (notifications, etc.) ──────────────────────────────
// Stored in the backend's settings.json; read/written over /settings/<group>.

type PrefVal = boolean | number | string;
type Prefs = { data: Record<string, PrefVal>; set: (key: string, value: PrefVal) => void; flush: () => Promise<boolean> };

const PREF_DEFAULTS: Record<string, Record<string, PrefVal>> = {
  notifications: {
    notify_toast_ms: 4000,
    notify_discord: true,
    notify_whatsapp: true,
    notify_telegram: true,
    notify_reminders: true,
    notify_calls_only: false,
    notify_quiet_enabled: false,
    notify_quiet_start: "22:00",
    notify_quiet_end: "07:00",
  },
  privacy: {
    confirm_before_click: true,
    screen_watch_mode: "ask",
  },
  ai: {
    llm_fallback_provider: "",
    reply_style: "balanced",
  },
  voice: {
    audio_input: "Default Microphone",
    audio_output: "Default Speaker",
    wake_sensitivity: 3,
    voice_activation_mode: "ptt",
    tts_voice: "",
    tts_engine: "edge",
    dictation_enabled: true,
    dictation_hotkey: "ctrl+alt",
    dictation_hotkey_mode: "hold",
    dictation_polish: true,
    mochi_sound_mode: "mochi",
  },
  advanced: {
    log_level: "INFO",
  },
  features: {
    feature_notifications: true,
    feature_clipboard: true,
    feature_downloads: true,
    feature_volume: true,
    feature_status: true,
    feature_light_mode: true,
    feature_circle_ask: true,
    feature_eco_mode: false,
    proactive_enabled: true,
  },
};

async function loadPrefs(group: string): Promise<Prefs> {
  const data: Record<string, PrefVal> = { ...PREF_DEFAULTS[group] };
  try {
    const r = await fetch(`${LYRA}/settings/${group}`);
    if (r.ok) Object.assign(data, await r.json());
    if (group === "features") setEco(data.feature_eco_mode === true);
  } catch {
    /* backend offline: defaults until it is back */
  }
  let pending: Record<string, PrefVal> = {};
  let timer = 0;
  let saving: Promise<boolean> | null = null;
  const flush = async (): Promise<boolean> => {
    window.clearTimeout(timer);
    const body = pending;
    pending = {};
    if (!Object.keys(body).length) return true;
    const request = (async () => {
      try {
        const r = await fetch(`${LYRA}/settings/${group}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(r.statusText);
        Object.assign(data, await r.json()); // the backend clamps; keep its value
        if (group === "features") setEco(data.feature_eco_mode === true);   // every window follows through localStorage
        flashSaved();
        return true;
      } catch {
        // Put the values back in the queue so a later change/retry cannot silently lose them.
        pending = { ...body, ...pending };
        flashSaved(false);
        return false;
      }
    })();
    saving = request;
    try { return await request; } finally { if (saving === request) saving = null; }
  };
  return {
    data,
    set(key, value) {
      data[key] = value;
      pending[key] = value;
      window.clearTimeout(timer);
      // Normal controls are batched briefly; this is still persisted automatically.
      timer = window.setTimeout(() => void flush(), 150);
    },
    flush: async () => {
      if (saving) await saving;
      return flush();
    },
  };
}

function prefToggle(p: Prefs, key: string): HTMLElement {
  return toggle(p.data[key] === true, (v) => p.set(key, v));
}

/** Number box; `scale` converts between the shown unit and the stored one (e.g. s ↔ ms). */
function prefNumber(p: Prefs, key: string, min: number, max: number, step = 1, scale = 1): HTMLInputElement {
  const el = h("input", {
    type: "number",
    min: String(min),
    max: String(max),
    step: String(step),
    value: String(Number(p.data[key]) / scale),
    style: "width:5rem",
  }) as HTMLInputElement;
  el.addEventListener("change", () => {
    const v = Math.min(max, Math.max(min, Number(el.value) || min));
    el.value = String(v);
    p.set(key, Math.round(v * scale));
  });
  return el;
}

function prefTime(p: Prefs, key: string): HTMLInputElement {
  const el = h("input", { type: "time", value: String(p.data[key]) }) as HTMLInputElement;
  el.addEventListener("change", () => {
    if (el.value) p.set(key, el.value);
  });
  return el;
}

/** Dropdown bound to a pref. `options` = [value, label]; an unknown saved value is kept as its own entry. */
function prefSelect(p: Prefs, key: string, options: Array<[string, string] | [string, string, string]>, unknownLabel = (v: string) => v): HTMLSelectElement {
  const el = h("select", {}) as HTMLSelectElement;
  const cur = String(p.data[key] ?? "");
  const list: Array<[string, string] | [string, string, string]> = options.some(([v]) => v === cur) ? options : [...options, [cur, unknownLabel(cur)] as [string, string]];
  for (const [v, label, desc] of list) {
    const o = h("option", { value: v, text: label }) as HTMLOptionElement;
    if (desc) o.dataset.desc = desc;      // shown under the title when the choice is drawn as a card (choiceKit.ts)
    el.append(o);
  }
  el.value = cur;
  el.addEventListener("change", () => p.set(key, el.value));
  return el;
}

/** Slider bound to a numeric pref; saves on release, shows the live value through `fmt`. */
function prefRange(p: Prefs, key: string, min: number, max: number, step: number, fmt: (v: number) => string): HTMLElement[] {
  const val = h("span", { class: "val", text: fmt(Number(p.data[key])) });
  const el = h("input", {
    type: "range",
    min: String(min),
    max: String(max),
    step: String(step),
    value: String(p.data[key]),
  }) as HTMLInputElement;
  el.addEventListener("input", () => (val.textContent = fmt(Number(el.value))));
  el.addEventListener("change", () => p.set(key, Number(el.value)));
  return [el, val];
}

// ── AI tab: fallback provider + reply style ───────────────────────────────────

function aiCards(a: Prefs): HTMLElement[] {
  const fallback = h("select", {}) as HTMLSelectElement;
  const note = h("div", { class: "hint" });
  const fillFallback = () => {
    const primary = llmInfo?.provider || "openrouter";
    clear(fallback);
    fallback.append(h("option", { value: "", text: "None" }));
    for (const [id, label] of PROVIDERS) {
      if (id !== primary) fallback.append(h("option", { value: id, text: label }));
    }
    const cur = String(a.data.llm_fallback_provider || "");
    fallback.value = cur && cur !== primary ? cur : "";
    const missing = fallback.value && llmInfo && !hasKey(llmInfo, fallback.value);
    note.textContent = missing
      ? "No key saved for this provider yet — to add one, pick it as the provider above, save its key, then switch back."
      : "";
  };
  fallback.addEventListener("change", () => {
    a.set("llm_fallback_provider", fallback.value);
    fillFallback();
  });
  fillFallback();
  onProviderChanged = fillFallback;

  const style = prefSelect(a, "reply_style", [
    ["short", "Short"],
    ["balanced", "Balanced"],
    ["detailed", "Detailed"],
  ]);

  return [
    card(
      "Fallback",
      item(
        "Fallback provider",
        "If the main provider errors, Lyra retries her chat answer with this one. Needs its own saved key. Command routing doesn't use it.",
        fallback,
      ),
      note,
    ),
    card(
      "Replies",
      item(
        "Reply length & style",
        "Short = a sentence or two. Detailed = fuller explanations. Applies to Lyra's chat answers from the next message.",
        style,
      ),
    ),
  ];
}

// ── Voice tab: devices, push-to-talk, speech ─────────────────────────────────────

type VoiceDevices = { inputs: string[]; outputs: string[]; voices: string[] };

function voiceCards(v: Prefs, dev: VoiceDevices): HTMLElement[] {
  const mic = prefSelect(
    v,
    "audio_input",
    [["Default Microphone", "System default"], ...dev.inputs.map((n): [string, string] => [n, n])],
    (n) => `${n} (not connected)`,
  );
  const speaker = prefSelect(
    v,
    "audio_output",
    [["Default Speaker", "System default"], ...dev.outputs.map((n): [string, string] => [n, n])],
    (n) => `${n} (not connected)`,
  );
  const names: Record<string, string> = {};
  const voices: [string, string][] = [["", "Default (Jenny)"]];
  for (const id of dev.voices) {
    const short = id.replace(/Neural$/, "").split("-").slice(1).join(" ");
    names[id] = short;
    voices.push([id, `${short} (${id.slice(0, 5)})`]);
  }
  const voice = prefSelect(v, "tts_voice", voices);

  const mochiMode = prefSelect(v, "mochi_sound_mode", [
    ["mochi", "Mochi sounds"],
    ["both", "Both"],
    ["speak", "Speak"],
  ]);
  mochiMode.dataset.choice = "pills";

  // Voice engine: Microsoft's Edge voice is the default. The offline voice (Kokoro) is an option that is only
  // downloaded when you press the button, never automatically.
  const engine = prefSelect(v, "tts_engine", [
    ["edge", "Microsoft Edge voice (default)", "Natural online voices. Needs an internet connection."],
    ["offline", "Offline voice (Kokoro)", "Runs on this PC and works without internet. One-time download."],
  ]);
  type Off = { installed: boolean; package_ok: boolean; downloading: boolean; done: number; total: number; error: string; active: string };
  let off: Off = { installed: false, package_ok: true, downloading: false, done: 0, total: 1, error: "", active: "edge" };
  const offStatus = h("span", { class: "hint" });
  const offBtn = h("button", { class: "primary", text: "Download offline voice (about 350 MB)" }) as HTMLButtonElement;
  const offDel = h("button", { text: "Remove download" }) as HTMLButtonElement;
  const paintOff = () => {
    const mb = (n: number) => `${Math.round(n / 1048576)} MB`;
    offBtn.hidden = off.installed || off.downloading;
    offDel.hidden = !off.installed || off.downloading;
    offBtn.disabled = off.downloading;
    if (off.downloading) offStatus.textContent = `Downloading… ${mb(off.done)} of ${mb(off.total)}`;
    else if (off.error) offStatus.textContent = `Download failed: ${off.error}`;
    else if (!off.installed) offStatus.textContent = engine.value === "offline"
      ? "Not downloaded yet: Microsoft's voice keeps speaking until you download it."
      : "Not downloaded. Nothing is downloaded unless you press the button.";
    else if (!off.package_ok) offStatus.textContent = "Downloaded, but the kokoro-onnx package is missing from this install.";
    else offStatus.textContent = off.active === "offline" ? "Downloaded. Lyra speaks with it." : "Downloaded. Pick it above to use it.";
  };
  const pollOff = async () => {
    try {
      const r = await fetch(`${LYRA}/voice/offline`);
      if (r.ok) off = (await r.json()) as Off;
    } catch { /* backend not up */ }
    paintOff();
    // Download progress every second only while downloading; cleared while this window is hidden.
    if (off.downloading && !offPoll) offPoll = visiblePoll(() => pollOff(), { ms: 1000, runOnShow: false });
    else if (!off.downloading && offPoll) { offPoll.stop(); offPoll = undefined; }
  };
  let offPoll: VisiblePoll | undefined;
  offBtn.addEventListener("click", async () => {
    try { await fetch(`${LYRA}/voice/offline/download`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* shown by the poll */ }
    void pollOff();
  });
  offDel.addEventListener("click", async () => {
    try { await fetch(`${LYRA}/voice/offline/delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* ignore */ }
    void pollOff();
  });
  engine.addEventListener("change", () => { window.setTimeout(() => void pollOff(), 400); });
  void pollOff();

  const testResult = h("span", { class: "hint" });
  const testBtn = h("button", { class: "primary", text: "Test voice" });
  testBtn.addEventListener("click", async () => {
    testResult.textContent = "";
    try {
      const r = await fetch(`${LYRA}/voice/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!r.ok) throw new Error(r.statusText);
      testResult.textContent = "Playing a sample…";
    } catch {
      testResult.textContent = "Could not play — is Lyra running?";
    }
  });

  return [
    card(
      "Devices",
      item(
        "Microphone",
        "Used when you hold Right Ctrl to talk and when you dictate.",
        mic,
      ),
      item("Speaker", "Where Lyra's voice plays. Applies from her next reply.", speaker),
    ),
    card(
      "Push-to-talk",
      item("Talk key", "Hold it to speak, release it to stop. Click the key, then press the one you want (a modifier such as Right Ctrl or Left Alt). A quick tap does nothing, so it keeps working in your other shortcuts. You can change it here or in Shortcuts.", settingKeyButton("p2_talk_key", "rctrl", () => flashSaved())),
    ),
    card(
      "Speech",
      item("Voice engine", "Microsoft's Edge voice is the default and needs an internet connection. The offline voice works without internet but has to be downloaded first; it is never downloaded by itself.", engine),
      item("Offline voice", "Optional. Download it once, then pick it above.", offBtn, offDel, offStatus),
      item("Microsoft voice", "Which Microsoft voice to use (when the Microsoft engine speaks).", voice),
      item("Try it", "Speaks a sample line with the settings above.", testBtn, testResult),
    ),
    card(
      "Short replies",
      item(
        "Done, opened, errors",
        "How Lyra gives a short reply such as “Done” or “Opening Spotify”. Mochi sounds: the Mochi plays a sound and an animation, with a subtitle, instead of speaking. Both: sound and voice. Speak: voice only. Lyra still speaks for answers and asks out loud before anything risky.",
        mochiMode,
      ),
    ),
  ];
}

/** The dictation switches that used to sit on the Voice tab; the Dictation tab now shows them first. */
function dictationBasicsCard(v: Prefs): HTMLElement {
  return card(
      "Basics",
      item("Dictation", "Hold Left Alt in any window and Lyra pastes what you say when you let go. Say “note one … next point …” for a numbered list. English only for now.", prefToggle(v, "dictation_enabled")),
    );
}

// ── Privacy & Memory tab ──────────────────────────────────────────────────────

type MemItem = { key: string; value: string };
type MemSnapshot = { facts: MemItem[]; paths: MemItem[]; sites: MemItem[] };

function memoryHost(): HTMLElement {
  const host = h("div", {});
  const GROUPS: [keyof MemSnapshot, string, string, string][] = [
    ["facts", "fact", "Things you told her to remember", "Facts"],
    ["paths", "path", "Folder nicknames", "Folders"],
    ["sites", "learned_site", "Websites she learned", "Sites"],
  ];

  const load = async () => {
    const r = await lyra("/memory");
    clear(host);
    if (!r.ok) {
      host.append(card("Memory", h("p", { class: "empty", text: r.error || "Could not load memory." })));
      return;
    }
    const snap = r.data as MemSnapshot;
    const total = GROUPS.reduce((n, [k]) => n + (snap[k]?.length ?? 0), 0);

    const refresh = h("button", { text: "Refresh" });
    refresh.addEventListener("click", () => void load());
    const clearAll = confirmButton("Clear all memory", "Click again to erase", async () => {
      const d = await lyra("/memory/clear", {});
      flashSaved(d.ok);
      void load();
    });
    if (total === 0) clearAll.setAttribute("disabled", "");

    const head = card(
      "Memory",
      item(
        "What Lyra remembers",
        `${total} item${total === 1 ? "" : "s"} stored on this PC (lyra_memory.db). Used in her answers; never shown to anyone else.`,
        h("span", { class: "spacer" }),
        refresh,
        clearAll,
      ),
    );
    host.append(head);

    for (const [key, kind, title] of GROUPS) {
      const rows = snap[key] ?? [];
      if (!rows.length) continue;
      const c = card(title);
      for (const m of rows) {
        const forget = h("button", { class: "danger", text: "Forget" });
        forget.addEventListener("click", async () => {
          forget.setAttribute("disabled", "");
          const d = await lyra("/memory/forget", { kind, key: m.key });
          flashSaved(d.ok);
          void load();
        });
        const row = item(m.key, null, h("span", { class: "val wrap", text: m.value }), h("span", { class: "spacer" }), forget);
        row.dataset.s += ` ${m.value} memory`.toLowerCase();
        c.append(row);
      }
      host.append(c);
    }
  };

  whenVisible(host, () => void load());
  return host;
}

function privacyCards(p: Prefs): HTMLElement[] {
  return [
    card(
      "Confirmations",
      item(
        "Confirm before clicking",
        "Lyra asks for a yes before she clicks something on screen for you. Messages are different: she always reads one back and waits for your yes before sending, and that can't be switched off.",
        prefToggle(p, "confirm_before_click"),
      ),
    ),
    card(
      "Your data",
      item(
        "Where it lives",
        "No telemetry. API keys, memory and settings stay on this PC; only your LLM provider and the voice service see what you ask.",
        h("span", { class: "val", text: "Local" }),
      ),
    ),
  ];
}

/** Export / import of everything Settings controls. Keys and memory are never included. */
function backupCard(): HTMLElement {
  const result = h("div", {});
  const note = (text: string, kind: "ok" | "err" | "warn") => {
    clear(result);
    result.append(h("div", { class: `notice ${kind}`, text }));
  };

  const exportBtn = h("button", { text: "Export settings" });
  exportBtn.addEventListener("click", async () => {
    const r = await lyra("/settings/export");
    if (!r.ok) return note(r.error || "Could not export.", "err");
    const bundle = { ...r.data, ui: exportableUi() };
    const text = JSON.stringify(bundle, null, 2);
    const a = h("a", { href: URL.createObjectURL(new Blob([text], { type: "application/json" })), download: "lyra-settings.json" });
    document.body.append(a);
    a.click();
    a.remove();
    let copied = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch {
      /* clipboard may be blocked */
    }
    note(
      `Export created (lyra-settings.json)${copied ? " and copied to the clipboard" : ""}. API keys and memory are not included.`,
      "ok",
    );
  });

  const file = h("input", { type: "file", accept: ".json,application/json", style: "display:none" }) as HTMLInputElement;
  const importBtn = h("button", { text: "Import settings…" });
  importBtn.addEventListener("click", () => file.click());
  file.addEventListener("change", async () => {
    const f = file.files?.[0];
    file.value = "";
    if (!f) return;
    let data: any;
    try {
      data = JSON.parse(await f.text());
    } catch {
      return note("That file isn't valid JSON.", "err");
    }
    if (!data || data.app !== "lyra-settings") return note("That isn't a Lyra settings file.", "err");
    clear(result);
    result.append(
      h("div", { class: "notice warn", text: `${f.name}: this will overwrite your current settings and matching shortcuts.` }),
      h(
        "div",
        { class: "row pad" },
        confirmButton("Apply import", "Click again to apply", async () => {
          const r = await lyra("/settings/import", data);
          if (!r.ok) return note(r.error || "Import failed.", "err");
          await applyUiImport(data.ui);
          const skipped = (r.data.skipped || []).length;
          note(`Imported ${r.data.applied} settings${skipped ? ` (${skipped} skipped: ${r.data.skipped[0]})` : ""}. Reloading…`, "ok");
          window.setTimeout(() => location.reload(), 1400);
        }, "primary"),
      ),
    );
  });

  return card(
    "Backup",
    item(
      "Export & import",
      "Saves your preferences, modes, shortcuts and provider choice to a file you can restore later or copy to another PC. API keys and memory are never included.",
      exportBtn,
      importBtn,
      file,
    ),
    result,
  );
}

const UI_KEYS = ["soundEnabled", "soundVolume", "autoCloseInterval", "compactAutoHideInterval", "alwaysShowNotch", "screen", "wakeMode", "islandAutoHide"] as const;

function exportableUi(): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of UI_KEYS) out[k] = settings[k];
  return out;
}

/** Island prefs from an imported file: only known keys, only the right type, clamped. */
async function applyUiImport(ui: unknown) {
  if (!ui || typeof ui !== "object") return;
  const src = ui as Record<string, unknown>;
  const next = { ...settings };
  if (typeof src.soundEnabled === "boolean") next.soundEnabled = src.soundEnabled;
  if (typeof src.alwaysShowNotch === "boolean") next.alwaysShowNotch = src.alwaysShowNotch;
  if (typeof src.soundVolume === "number") next.soundVolume = Math.min(0.2, Math.max(0, src.soundVolume));
  if (typeof src.autoCloseInterval === "number") next.autoCloseInterval = Math.min(60, Math.max(1, Math.round(src.autoCloseInterval)));
  if (typeof src.compactAutoHideInterval === "number") next.compactAutoHideInterval = Math.min(60, Math.max(1, Math.round(src.compactAutoHideInterval)));
  if (src.screen === "primary" || src.screen === "cursor") next.screen = src.screen;
  else if (typeof src.screen === "string" && /^display:[1-9]$/.test(src.screen)) next.screen = src.screen as Settings["screen"];
  if (src.wakeMode === "dwell" || src.wakeMode === "instant") next.wakeMode = src.wakeMode;   // an old "double" is ignored
  if (src.islandAutoHide === "always" || src.islandAutoHide === "desktop") next.islandAutoHide = src.islandAutoHide;   // an old "apps" is ignored
  settings = next;
  await save();
}

// ── Shell: sidebar tabs + search ──────────────────────────────────────────────

// ── Display shape / outline / Live Activities (System Settings style previews) ──

/** A tiny "desktop" scene with the island drawn on it; the tile is the radio button. */
const SPEAKER_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9.5v5h3.6L12 18.5v-13L7.6 9.5H4z" fill="currentColor" stroke="none"/><path d="M15.4 9.2a4 4 0 0 1 0 5.6"/><path d="M18 6.6a7.6 7.6 0 0 1 0 10.8"/></svg>`;

/** `onPlay`: adds a small speaker button to the tile (Live Activities: hear the real sound and see the light again). */
function previewTile(label: string, on: boolean, scene: HTMLElement, onPick: () => void, sub = "", onPlay?: () => void): HTMLButtonElement {
  let snd: HTMLElement | null = null;
  if (onPlay) {
    snd = h("span", { class: "ptile-snd", role: "button", tabindex: "0", title: "Play its sound", "aria-label": `Play the sound of ${label}` });
    snd.innerHTML = SPEAKER_ICON;
    const go = (e: Event) => {
      e.stopPropagation();
      e.preventDefault();
      onPlay();
      snd!.classList.add("playing");
      window.setTimeout(() => snd!.classList.remove("playing"), 1800);
    };
    snd.addEventListener("click", go);
    snd.addEventListener("keydown", (e) => { if ((e as KeyboardEvent).key === "Enter" || (e as KeyboardEvent).key === " ") go(e); });
  }
  const b = h("button", { class: on ? "ptile on" : "ptile", type: "button", "aria-pressed": on },
    h("span", { class: "ptile-scene" }, scene, snd),
    h("span", { class: "ptile-label", text: label }),
    sub ? h("span", { class: "ptile-sub", text: sub }) : null,
  ) as HTMLButtonElement;
  b.addEventListener("click", onPick);
  return b;
}

function radioTiles(options: Array<[string, string]>, current: string, onPick: (v: string) => void, kind: (v: string) => HTMLElement): HTMLElement {
  const wrap = h("div", { class: "ptiles" });
  const render = (cur: string) => {
    clear(wrap);
    for (const [v, label] of options) {
      wrap.append(previewTile(label, v === cur, kind(v), () => { onPick(v); render(v); }));
    }
  };
  render(current);
  return wrap;
}

/** The island preview card, plus the two rows that used to be its own "Behaviour" card (merged into "Island behaviour"). */
function displayCards(): { look: HTMLElement; behaviour: HTMLElement[] } {
  const ex = readExtras();
  // The island as it will look: one big preview that follows every choice below.
  let shape = ex.shapeNotchless as "notch" | "island";
  let material = ex.compactStyle as string;
  type Mat = "black" | "frost" | "glass";
  const live = h("div", { class: "island-live" });
  const paintLive = () => {
    clear(live);
    live.append(h("div", { class: "il-col solo" }, islandScene({ shape, material: material as Mat, state: "rest" })));
  };

  const notchless = radioTiles([["notch", "Notch"], ["island", "Dynamic Island"]], shape, (v) => {
    shape = v as "notch" | "island";
    writeExtras({ shapeNotchless: shape });
    // Windows screens have no hardware notch, so this is the shape that is actually drawn.
    writeIslandPosition(v === "notch" ? "attached" : "floating");
    paintLive();
  }, (v) => islandScene({ shape: v as "notch" | "island", material: material as Mat, state: "rest" }));

  const compactScene = (v: string): HTMLElement => islandScene({ shape, material: v as Mat, state: "rest" });
  const compactStyle = radioTiles([["black", "Black"], ["frost", "Frost"], ["glass", "Glass"]], material, (v) => {
    material = v;
    writeExtras({ compactStyle: v as "black" | "frost" | "glass" });
    paintLive();
  }, compactScene);
  // Tab navigation: the wheel (with the Mochi · Music · Chat dock) or a scroller that shows three tabs at a time.
  const navStyle = radioTiles([["wheel", "Tab wheel"], ["scroller", "Scroller"], ["both", "Both"]], ex.navStyle, (v) => {
    writeExtras({ navStyle: v as "wheel" | "scroller" | "both" });
  }, (v) => navScreen(v as "wheel" | "scroller" | "both"));
  const hideCompact = toggle(ex.hideCompact, (v) => writeExtras({ hideCompact: v }));
  const keepClipboard = toggle(ex.keepClipboard, (v) => { writeExtras({ keepClipboard: v }); void fetch(`${LYRA}/clipboard/keep`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep: v }) }).catch(() => undefined); });
  paintLive();
  const liveItem = h("div", { class: "item" }, live);
  liveItem.dataset.s = "preview your island open resting";
  return {
    look: card("Your island",
      liveItem,
      item("Shape", "Windows screens have no hardware notch, so this is the shape Lyra draws.", notchless),
      item("Material", "Black, Frost or Glass for the small island, whatever the theme.", compactStyle),
      item("Tab navigation", "Tab wheel: everything on the wheel. Scroller: three tab icons at a time that you scroll through. Both: the scroller, and the wheel still opens with Ctrl+CapsLock, right-click or your voice.", navStyle),
    ),
    behaviour: [
      item("Hide while music plays", "Keep the island out of the way even when music is playing.", hideCompact),
      item("Keep clipboard", "Keep copied items and dropped files after Lyra restarts.", keepClipboard),
    ],
  };
}

const ACTIVITY_DEFS: Array<{ id: LiveActivity; label: string; sub: string; group: string }> = [
  { id: "music", group: "Media", label: "Now Playing", sub: "Artwork and controls in the notch" },
  { id: "volume", group: "System", label: "Volume", sub: "Replace the Windows volume pop-up" },
  { id: "brightness", group: "System", label: "Brightness", sub: "Replace the Windows brightness pop-up" },
  { id: "devices", group: "System", label: "Connected devices", sub: "Headphones and Bluetooth audio" },
  { id: "downloads", group: "System", label: "Downloads", sub: "Progress for files you download" },
  { id: "timer", group: "System", label: "Timers", sub: "Countdown, stopwatch and Pomodoro" },
  { id: "listening", group: "Assistant", label: "Listening", sub: "Shows while Lyra hears you" },
  { id: "recognition", group: "Assistant", label: "Song recognition", sub: "Identify what is playing" },
  { id: "clipboard", group: "Assistant", label: "Clipboard", sub: "Copied items at a glance" },
  { id: "notifications", group: "Assistant", label: "Messages", sub: "Discord, WhatsApp and Telegram" },
  { id: "calls", group: "Assistant", label: "Calls", sub: "Caller name with answer and decline" },
  { id: "reminders", group: "Assistant", label: "Reminders", sub: "Your calendar reminders" },
];

function liveCards(): HTMLElement[] {
  const ex = readExtras();
  const groups: Record<string, HTMLElement[]> = {};
  for (const a of ACTIVITY_DEFS) {
    const on = ex.activities[a.id] !== false;
    const scene = activityScene(a.id);
    const tile = previewTile(a.label, on, scene, () => {
      const cur = readExtras();
      const next = !(cur.activities[a.id] !== false);
      writeExtras({ activities: { ...cur.activities, [a.id]: next } });
      tile.classList.toggle("on", next);
      tile.setAttribute("aria-pressed", String(next));
      if (next) playActivityPreview(a.id, scene);        // switching an activity on lets you hear and see it
    }, a.sub, () => playActivityPreview(a.id, scene));
    (groups[a.group] ??= []).push(tile);
  }
  const out: HTMLElement[] = [];
  for (const [name, tiles] of Object.entries(groups)) {
    const el = card(name, h("div", { class: "pgrid" }, ...tiles));
    // make the whole grid searchable by its tile labels
    const wrap = el.querySelector(".pgrid") as HTMLElement;
    const holder = h("div", { class: "item" }, wrap);
    holder.dataset.s = `${name} ${tiles.map((t) => t.textContent).join(" ")}`.toLowerCase();
    el.querySelector(".group")!.replaceChildren(holder);
    out.push(el);
  }
  return out;
}

const TABS: [string, string][] = [
  ["general", "General"],
  ["ai", "AI"],
  ["voice", "Voice"],
  ["notetaker", "Recording"],
  ["features", "Features"],
  ["apps", "Apps"],
  ["live", "Live Activities"],
  ["shortcuts", "Shortcuts"],
  ["files", "Files"],
  ["privacy", "Privacy & Memory"],
  ["advanced", "Advanced"],
  ["about", "About"],
];

// SF-style glyphs (24×24) drawn inside coloured rounded squares, like System Settings.
type Glyph = { c: string; bg: string };
export const GLYPHS: Record<string, Glyph> = {
  general: { bg: "linear-gradient(#9a9aa0,#6e6e73)", c: '<path d="M10.9 2h2.2l.35 2.1c.6.17 1.16.4 1.67.71l1.9-1 1.55 1.55-1 1.9c.3.5.54 1.07.7 1.67l2.13.35v2.2l-2.12.35c-.17.6-.4 1.16-.71 1.67l1 1.9-1.55 1.55-1.9-1c-.5.3-1.07.54-1.67.7L13.1 22h-2.2l-.35-2.12c-.6-.17-1.16-.4-1.67-.71l-1.9 1L5.43 18.6l1-1.9c-.3-.5-.54-1.07-.7-1.67L3.6 14.7v-2.2l2.12-.35c.17-.6.4-1.16.71-1.67l-1-1.9 1.55-1.55 1.9 1c.5-.3 1.07-.54 1.67-.7L10.9 2zM12 8.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6z" fill-rule="evenodd"/>' },
  ai: { bg: "linear-gradient(#b07cff,#6d4ee6)", c: '<path d="M10 4l1.8 5.2L17 11l-5.2 1.8L10 18l-1.8-5.2L3 11l5.2-1.8z"/><path d="M18 2.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/><path d="M18.5 14l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>' },
  voice: { bg: "linear-gradient(#ff6b6b,#e5322d)", c: '<path d="M12 2.5a3.5 3.5 0 0 0-3.5 3.5v6a3.5 3.5 0 0 0 7 0V6A3.5 3.5 0 0 0 12 2.5z"/><path d="M5.5 11.2a.9.9 0 0 1 1.8 0 4.7 4.7 0 0 0 9.4 0 .9.9 0 0 1 1.8 0 6.5 6.5 0 0 1-5.6 6.43V20.3h2.2a.9.9 0 0 1 0 1.8H9a.9.9 0 0 1 0-1.8h2.2v-2.67A6.5 6.5 0 0 1 5.5 11.2z"/>' },
  notetaker: { bg: "linear-gradient(#ff8a5c,#e8442e)", c: '<circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="9" fill="none" stroke="#fff" stroke-width="1.8"/>' }, // phase-7
  island: { bg: "linear-gradient(#4aa8ff,#0a6cff)", c: '<path fill-rule="evenodd" d="M5 5h14a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H5a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3zm4 2.2a1.4 1.4 0 0 0 0 2.8h6a1.4 1.4 0 0 0 0-2.8H9z"/>' },
  mochi: { bg: "linear-gradient(#b39cff,#6b52e8)", c: '<ellipse cx="12" cy="14" rx="6.6" ry="5.1"/><circle cx="9.6" cy="14" r="1.15" fill="#6b52e8"/><circle cx="14.4" cy="14" r="1.15" fill="#6b52e8"/><path fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" d="M4.9 13.2a7.1 7.1 0 0 1 14.2 0"/><rect x="3.3" y="11.6" width="2.3" height="4.8" rx="1.1"/><rect x="18.4" y="11.6" width="2.3" height="4.8" rx="1.1"/>' },
  apps: { bg: WHATSAPP_BG, c: WHATSAPP_GLYPH },   // the Apps tab is WhatsApp only, so it wears the WhatsApp mark
  features: { bg: "linear-gradient(#5ee08a,#1fa74a)", c: '<rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/>' },
  live: { bg: "linear-gradient(#b983ff,#7b4dea)", c: '<circle cx="12" cy="12" r="3.2"/><path fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" d="M6.6 6.6a7.6 7.6 0 0 0 0 10.8M17.4 6.6a7.6 7.6 0 0 1 0 10.8"/>' },
  notifications: { bg: "linear-gradient(#ff6b5f,#ea3b30)", c: '<path d="M12 2.6a1.2 1.2 0 0 1 1.2 1.2v.7c2.7.5 4.6 2.8 4.6 5.6v3.2c0 .9.4 1.7 1 2.4l.7.8c.5.6.1 1.5-.7 1.5H5.2c-.8 0-1.2-.9-.7-1.5l.7-.8c.6-.7 1-1.5 1-2.4V10.1c0-2.8 1.9-5.1 4.6-5.6v-.7A1.2 1.2 0 0 1 12 2.6zM9.6 19.5h4.8a2.4 2.4 0 0 1-4.8 0z"/>' },
  shortcuts: { bg: "linear-gradient(#ffb340,#ff8a00)", c: '<path fill="none" stroke="#fff" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M9 9V6.5A2.5 2.5 0 1 0 6.5 9H9zm0 0h6m-6 0v6m6-6V6.5A2.5 2.5 0 1 1 17.5 9H15zm0 0v6m0 0h2.5a2.5 2.5 0 1 1-2.5 2.5V15zm0 0H9m0 0H6.5A2.5 2.5 0 1 0 9 17.5V15"/>' },
  privacy: { bg: "linear-gradient(#5ab0ff,#2a7de8)", c: '<path d="M6 10.5h12a1.5 1.5 0 0 1 1.5 1.5v7.5a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5V12A1.5 1.5 0 0 1 6 10.5z"/><path fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>' },
  files: { bg: "linear-gradient(#63b5ff,#2878d0)", c: '<path d="M3 5.5A2.5 2.5 0 0 1 5.5 3h4l2 2H18.5A2.5 2.5 0 0 1 21 7.5v10a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><path fill="none" stroke="#fff" stroke-width="1.3" d="M3.5 8h17"/>' },
  recordings: { bg: "linear-gradient(#ff7a7a,#d92b3a)", c: '<circle cx="12" cy="12" r="4.6"/><circle cx="12" cy="12" r="8.6" fill="none" stroke="#fff" stroke-width="1.8"/>' },
  screen: { bg: "linear-gradient(#52d6c4,#16a99a)", c: '<path d="M3 4.5h18a1 1 0 0 1 1 1V16a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1z"/><path d="M8.5 20h7a.9.9 0 0 1 0 1.8h-7a.9.9 0 0 1 0-1.8zM11 17h2v3.2h-2z"/>' },
  advanced: { bg: "linear-gradient(#9a9aa0,#6e6e73)", c: '<path fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" d="M4 7h16M4 12h16M4 17h16"/><circle cx="9" cy="7" r="2.3"/><circle cx="15.5" cy="12" r="2.3"/><circle cx="8" cy="17" r="2.3"/>' },
  about: { bg: "linear-gradient(#9a9aa0,#6e6e73)", c: '<path fill-rule="evenodd" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 4.3a1.3 1.3 0 1 1 0 2.6 1.3 1.3 0 0 1 0-2.6zM10.7 10.8h2.6v6.9h-2.6z"/>' },
  // >>> phase-2 begin
  p2_dictation: { bg: "linear-gradient(#ff7a7a,#e0373b)", c: '<path d="M12 2.5a3.5 3.5 0 0 0-3.5 3.5v6a3.5 3.5 0 0 0 7 0V6A3.5 3.5 0 0 0 12 2.5z"/><path fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3.5"/>' },
  p2_styles: { bg: "linear-gradient(#6fb6ff,#2f7ae8)", c: '<path d="M5 18.5V16l9.2-9.2 2.5 2.5L7.5 18.5z"/><path d="M15.4 5.6l1.3-1.3a1.2 1.2 0 0 1 1.7 0l.8.8a1.2 1.2 0 0 1 0 1.7l-1.3 1.3z"/><rect x="4" y="19.5" width="16" height="1.6" rx=".8"/>' },
  // <<< phase-2 end
  // >>> phase-3 begin
  statistics: { bg: "linear-gradient(#7c86ff,#4f5be8)", c: '<rect x="3.5" y="12" width="3.6" height="8" rx="1.2"/><rect x="10.2" y="5" width="3.6" height="15" rx="1.2"/><rect x="16.9" y="9" width="3.6" height="11" rx="1.2"/>' },
  // <<< phase-3 end
};

function sideIcon(id: string): HTMLElement {
  const el = h("span", { class: "tab-icon" });
  el.innerHTML = lineIcon(id);   // line icons (coucou/icons.ts); the coloured GLYPHS above stay for the onboarding / tour
  return el;
}

const NAV_GROUPS: Array<[string | null, string[]]> = [
  ["Assistant", ["general", "ai", "voice", "p2_dictation", "notetaker", "shortcuts"]],
  ["Island", ["features", "apps", "live"]],
  ["System", ["privacy", "files", "advanced", "about"]],
];
// >>> phase-2 begin
const buildP2Panels = registerP2(TABS, NAV_GROUPS);   // adds the Dictation, Dictionary, Snippets and Writing style tabs
// <<< phase-2 end

/** Shorter names for the sidebar where the page title is too long to fit. */
const NAV_SHORT: Record<string, string> = { notetaker: "Recording" };

function buildShell(panels: Record<string, HTMLElement>) {
  let active = "general";
  const history: string[] = ["general"];
  let hpos = 0;
  const tabBtns = new Map<string, HTMLElement>();
  const labelOf = (id: string) => TABS.find((t) => t[0] === id)?.[1] ?? id;

  const nav = h("nav", { class: "tabs", role: "tablist" });
  for (const [heading, ids] of NAV_GROUPS) {
    const grp = h("div", { class: "tab-group" }, heading ? h("div", { class: "tab-heading", text: heading }) : null);
    for (const id of ids) {
      const b = h("button", { class: "tab", role: "tab", type: "button" },
        sideIcon(id),
        h("span", { class: "tab-label", text: NAV_SHORT[id] ?? labelOf(id) }),
      );
      b.addEventListener("click", () => go(id));
      tabBtns.set(id, b);
      grp.append(b);
    }
    nav.append(grp);
  }

  const search = h("input", {
    type: "text",
    class: "search",
    placeholder: "Search settings",
    "aria-label": "Search settings",
    spellcheck: "false",
  }) as HTMLInputElement;
  const searchWrap = h("div", { class: "search-wrap" }, search);
  const noMatch = h("p", { class: "nomatch", text: "No settings match your search." });
  noMatch.hidden = true;

  const titleEl = h("h1", { class: "win-title", text: "General" });
  const backBtn = h("button", { class: "nav-arrow", type: "button", "aria-label": "Back", title: "Back", html: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M15 5.5 8.5 12 15 18.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>' }) as HTMLButtonElement;
  const fwdBtn = h("button", { class: "nav-arrow", type: "button", "aria-label": "Forward", title: "Forward", html: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M9 5.5 15.5 12 9 18.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>' }) as HTMLButtonElement;

  function go(id: string, record = true) {
    ensureBuilt(id);   // the tab may still be an empty shell
    search.value = "";
    if (record && id !== active) {
      history.splice(hpos + 1);
      history.push(id);
      hpos = history.length - 1;
    }
    active = id;
    slog.tab(id, record ? "tab click" : "back/forward");
    applyView();
  }
  backBtn.addEventListener("click", () => { if (hpos > 0) { hpos--; go(history[hpos], false); } });
  fwdBtn.addEventListener("click", () => { if (hpos < history.length - 1) { hpos++; go(history[hpos], false); } });

  /** Show the active tab, or — while searching — every setting that matches. */
  const applyView = () => {
    const q = search.value.trim().toLowerCase();
    slog.search(q);
    if (q) ensureAll();   // search looks through every card, so every tab has to exist
    let any = false;
    for (const [id] of TABS) {
      const p = panels[id];
      let panelHit = false;
      for (const c of p.querySelectorAll<HTMLElement>(".card")) {
        const items = c.querySelectorAll<HTMLElement>(".item");
        let hit = false;
        for (const it of items) {
          const match = !q || (it.dataset.s ?? "").includes(q) || (p.dataset.title ?? "").includes(q);
          it.hidden = !match;
          if (match) hit = true;
        }
        c.hidden = q ? !hit : false;
        if (hit) panelHit = true;
      }
      p.hidden = q ? !panelHit : id !== active;
      if (panelHit) any = true;
      tabBtns.get(id)!.classList.toggle("on", !q && id === active);
      tabBtns.get(id)!.classList.toggle("dim", !!q && !panelHit);
    }
    noMatch.hidden = !q || any;
    titleEl.textContent = q ? "Search results" : labelOf(active);
    backBtn.disabled = hpos <= 0;
    fwdBtn.disabled = hpos >= history.length - 1;
    mainEl.scrollTop = 0;
  };
  search.addEventListener("input", applyView);
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      search.value = "";
      applyView();
    }
  });

  // The window has no title bar: this is its close button (and .win-drag below is where it can be dragged by).
  const closeBtn = h("button", { class: "win-close", type: "button", title: "Close", "aria-label": "Close settings" });
  closeBtn.innerHTML = '<svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  closeBtn.addEventListener("click", () => {
    void import("@tauri-apps/api/window").then(({ getCurrentWindow }) => getCurrentWindow().close()).catch(() => window.close());
  });

  const top = h(
    "div",
    { class: "topbar" },
    h("div", { class: "nav-arrows" }, backBtn, fwdBtn),
    titleEl,
    h("div", { class: "strip" }, backendChip, llmChip),
    savedEl,
    closeBtn,
  );
  const mainEl = h("main", { class: "main" }, top, h("div", { class: "content" }, ...TABS.map(([id]) => panels[id]), noMatch));

  // Brand at the top of the sidebar: just the wordmark.
  // The version moved to General and About; p4_design still reads it from this (hidden) line.
  const acct = h("div", { class: "brand" },
    h("span", { class: "brand-name", text: "LYRA" }),
    h("span", { class: "acct-text", hidden: "" }, h("small", { text: `Version ${version || "1.0.0"}` })),
  );

  const side = h("aside", { class: "sidebar" }, acct, searchWrap, nav);
  applyView();

  // The island's "Set up in Settings" asks for a section through localStorage (the windows share it).
  const gotoRequested = () => {
    try {
      const id = localStorage.getItem(SETTINGS_GOTO_KEY);
      if (id) { localStorage.removeItem(SETTINGS_GOTO_KEY); if (panels[id]) go(id); }
    } catch { /* storage unavailable: nothing to do */ }
  };
  gotoRequested();
  window.addEventListener("storage", gotoRequested);
  window.addEventListener("focus", gotoRequested);
  return h("div", { class: "shell" }, h("div", { class: "win-drag", "data-tauri-drag-region": "" }), side, mainEl);
}

// ── Features: one switch per thing Lyra can do ───────────────────────────────

/** Current state of a feature: the island switch AND, for helpers, the backend switch. */
function featureIsOn(f: FeatureDef, featPrefs: Prefs): boolean {
  // The two Mochi switches are OFF until the user turns them on (a missing value is not "on"), also on a fresh install.
  if (MOCHI_FEATURE_IDS.has(f.id)) return !!f.backend && featPrefs.data[f.backend] === true;
  if (!featureEnabled(readExtras().features, f.id)) return false;
  return f.backend ? featPrefs.data[f.backend] !== false : true;
}

/** Applies one switch. Island tabs apply live; backend helpers apply when Lyra restarts. */
function applyFeature(f: FeatureDef, on: boolean, featPrefs: Prefs) {
  writeExtras({ features: { ...readExtras().features, [f.id]: on } });
  if (f.backend) featPrefs.set(f.backend, on);
}

/** One row on Settings → Features: a looping preview, what it does, a "Try" hint and the switch. */
function featureItem(f: FeatureDef, on: boolean, onChange: (v: boolean) => void): HTMLElement {
  const sw = toggle(on && !f.soon, (v) => { row.classList.toggle("off", !v); onChange(v); });
  if (f.soon) { sw.setAttribute("disabled", ""); sw.classList.remove("on"); sw.setAttribute("aria-pressed", "false"); }
  const row = h("div", { class: f.soon ? "item fx-item off soon" : on ? "item fx-item" : "item fx-item off" },
    featurePreview(f),
    h("div", { class: "fx-copy" },
      h("strong", {}, f.name, f.soon ? h("em", { class: "fx-soon", text: "Coming soon" }) : null),
      h("span", { class: "fx-blurb", text: f.blurb }),
      f.soon ? null : h("span", { class: "fx-try" }, h("b", { text: "Try" }), h("span", { text: f.tip })),
    ),
    sw);
  row.dataset.s = `${f.name} ${f.blurb} ${f.tip}`.toLowerCase();
  return row;
}

/** The Mochi switches live in General (Mochi) only. */
const MOCHI_FEATURE_IDS = new Set(["mascot", "mascot_stay"]);

/** WhatsApp is an app plugin, not an island tab: it has its own switch (the backend plugin list). */
const WHATSAPP_FEATURE: FeatureDef = {
  id: "whatsapp", name: "WhatsApp", group: "island", from: "#3fd16f", to: "#13a04a",
  blurb: "Read and answer your WhatsApp chats in the notch, with a pop-up for every new message.",
  tip: "Turn it on, then open Settings → Apps and press Connect to scan the code with your phone.",
};

function whatsappFeatureRow(featPrefs: Prefs, restart: HTMLElement): HTMLElement {
  const row = featureItem(WHATSAPP_FEATURE, true, (v) => {
    void (async () => {
      const r = await lyra("/plugins/toggle", { name: "WhatsApp", enabled: v });
      flashSaved(r.ok);
      // Plugins as a whole must be allowed for WhatsApp to load.
      if (v && featPrefs.data["feature_plugins"] === false) { featPrefs.set("feature_plugins", true); restart.style.display = ""; }
    })();
  });
  // The switch shows the plugin's real state as soon as the backend answers.
  void lyra("/plugins").then((r) => {
    const wa = (r.data?.plugins as Array<{ name: string; enabled: boolean }> | undefined)?.find((p) => p.name === "WhatsApp");
    if (!r.ok || !wa) return;
    const sw = row.querySelector<HTMLElement>(".switch");
    sw?.classList.toggle("on", wa.enabled);
    sw?.setAttribute("aria-pressed", String(wa.enabled));
    row.classList.toggle("off", !wa.enabled);
  });
  return row;
}

/** One tab for both kinds of recording: meeting notes (audio) and screen recordings (video). */
function featuresCards(featPrefs: Prefs): HTMLElement[] {
  const restart = h("div", { class: "notice restart", style: "display:none" },
    h("span", { text: "Background helpers change when Lyra restarts. " }),
  );
  const restartBtn = h("button", { class: "btn", type: "button", text: "Restart Lyra now" }) as HTMLButtonElement;
  restartBtn.addEventListener("click", async () => {
    restartBtn.disabled = true;
    await featPrefs.flush();
    try { await fetch(`${LYRA}/backend/restart`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* the status chip shows it */ }
    window.setTimeout(() => { restart.style.display = "none"; restartBtn.disabled = false; }, 1500);
  });
  restart.append(restartBtn);

  const replay = item("Take the tour again", "Walks through everything Lyra can do, with a switch for each part.",
    h("button", { class: "btn", type: "button", text: "Replay tour", onclick: () => void showOnboarding({ replay: true }) }));
  const intro = card(null, replay);

  const groups = FEATURE_GROUPS.map((g) => {
    const rows = FEATURES.filter((f) => f.group === g.id && !MOCHI_FEATURE_IDS.has(f.id)).map((f) => {
      return featureItem(f, featureIsOn(f, featPrefs), (v) => {
        applyFeature(f, v, featPrefs);
        if (f.backend) restart.style.display = "";
      });
    });
    if (g.id === "island") rows.push(whatsappFeatureRow(featPrefs, restart));
    return card(`${g.title} — ${g.sub}`, ...rows);
  });
  const access = accessCards({ prefs: featPrefs, card, item, lyra: LYRA });
  const proactive = card("Smart heads-ups",
    ...(["proactive_deadlines", "proactive_break", "proactive_hydration", "proactive_bedtime", "proactive_resources"] as const).map((key) =>
      item(key.replace("proactive_", "").replaceAll("_", " "), "", toggle(featPrefs.data[key] !== false, (v) => featPrefs.set(key, v)))),
    item("Break interval", "Minutes of activity before a reminder.", ...prefRange(featPrefs, "proactive_break_minutes", 10, 240, 5, (v) => `${v} min`)),
    item("Hydration interval", "Minutes between optional reminders.", ...prefRange(featPrefs, "proactive_hydration_minutes", 20, 240, 10, (v) => `${v} min`)),
    item("CPU alert threshold", "Percent used before Lyra alerts.", ...prefRange(featPrefs, "proactive_cpu_percent", 50, 100, 1, (v) => `${v}%`)),
    item("Memory alert threshold", "Percent used before Lyra alerts.", ...prefRange(featPrefs, "proactive_ram_percent", 50, 100, 1, (v) => `${v}%`)),
    item("Disk alert threshold", "Percent used before Lyra alerts.", ...prefRange(featPrefs, "proactive_disk_percent", 50, 100, 1, (v) => `${v}%`)),
    item("Bedtime", "Local time for the optional wind-down reminder.", prefTime(featPrefs, "proactive_bedtime_time")));
  const distractors = h("input", { class: "input", value: String(featPrefs.data.study_distractors ?? ""), placeholder: "Discord, YouTube" }) as HTMLInputElement;
  distractors.addEventListener("input", () => featPrefs.set("study_distractors", distractors.value));
  const studyPrefs = card("Study preferences",
    item("Mute message pop-ups during focus", "Calls and reminders still come through.", toggle(featPrefs.data.study_focus_mutes !== false, (v) => featPrefs.set("study_focus_mutes", v))),
    item("Focus length", "Pomodoro focus time in minutes.", ...prefRange(featPrefs, "pomo_focus_min", 1, 180, 1, (v) => `${v} min`)),
    item("Short break", "Pomodoro break in minutes.", ...prefRange(featPrefs, "pomo_break_min", 1, 60, 1, (v) => `${v} min`)),
    item("Long break", "Long break after a focus cycle.", ...prefRange(featPrefs, "pomo_long_break_min", 1, 120, 1, (v) => `${v} min`)),
    item("Distracting apps", "Comma-separated app names Lyra can remind you about.", distractors));
  const gameApps = h("input", { class: "input", value: String(featPrefs.data.game_mode_apps ?? ""), placeholder: "valorant.exe, cs2.exe" }) as HTMLInputElement;
  gameApps.addEventListener("input", () => featPrefs.set("game_mode_apps", gameApps.value));
  const gameMode = card("Game mode",
    item("Switch Lyra's key shortcuts off while you play", "Right Ctrl, Ctrl+Win, tap-and-hold Ctrl and Ctrl+CapsLock are fully removed during a game, so nothing of Lyra sits in the keyboard path. Press Ctrl+Alt+L to talk to her meanwhile, or say “Hey Lyra”.",
      toggle(featPrefs.data.feature_game_mode !== false, (v) => featPrefs.set("feature_game_mode", v))),
    item("Game list", "Programs that switch game mode on while they run. Comma-separated, e.g. cs2.exe.", gameApps));
  return [restart, intro, ...groups, ...access, proactive, studyPrefs, gameMode];
}

// ── First-launch setup / tour ────────────────────────────────────────────────
// The wizard itself lives in setupWizard.ts; this only hands it what it needs to read and write.
const ONBOARDING_KEY = "lyra-onboarding-complete";

function onboardingDone(): boolean {
  try { return window.localStorage.getItem(ONBOARDING_KEY) === "1"; } catch { return false; }
}

function markOnboardingDone() {
  try { window.localStorage.setItem(ONBOARDING_KEY, "1"); } catch { /* ignore */ }
}

async function showOnboarding(opts: { replay?: boolean } = {}): Promise<void> {
  await showSetupWizard({
    root,
    replay: !!opts.replay,
    lyra: LYRA,
    loadPrefs,
    featureIsOn,
    applyFeature,
    theme: { read: readIslandTheme, write: writeIslandTheme },
    position: { read: readIslandPosition, write: writeIslandPosition },
    settings: {
      get islandAutoHide() { return normalizeAutoHide(settings.islandAutoHide); }, set islandAutoHide(v) { settings.islandAutoHide = v; },
      get wakeMode() { return settings.wakeMode; }, set wakeMode(v) { settings.wakeMode = v; },
      get screen() { return settings.screen; }, set screen(v: string) { settings.screen = v as Settings["screen"]; },
      get autostart() { return settings.autostart; }, set autostart(v: boolean) { settings.autostart = v; },
    },
    saveSettings: save,
    markDone: markOnboardingDone,
  });
}


// ── Boot ──────────────────────────────────────────────────────────────────────

async function main() {
  const boot = await Bridge.boot();
  if (boot) {
    settings = { ...settings, ...boot.settings };
    version = boot.version;
  }

  if (!onboardingDone()) {
    await showOnboarding();
    return;
  }

  let llm: LlmInfo = {
    provider: "openrouter",
    openrouter_model: "google/gemini-3.8-flash",
    gemini_model: "gemini-3.8-flash",
    groq_model: "openai/gpt-oss-120b",
    ollama_model: "",
    has_openrouter: false,
    has_gemini: false,
    has_groq: false,
    has_ollama: false,
  };
  // Everything the first paint needs is requested at once (it used to be llm, then six prefs, then devices, one
  // after the other: ~9 round trips in a row while the window froze).
  const fetchJson = (path: string): Promise<Record<string, unknown> | null> =>
    fetch(`${LYRA}${path}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const devicesP = fetchJson("/voice/devices");
  const llmP = fetchJson("/settings/llm");
  const [notifyPrefs, aiPrefs, voicePrefs, privacyPrefs, advancedPrefs, featurePrefs] = await Promise.all([
    loadPrefs("notifications"),
    loadPrefs("ai"),
    loadPrefs("voice"),
    loadPrefs("privacy"),
    loadPrefs("advanced"),
    loadPrefs("features"),
  ]);
  { const j = await llmP; if (j) llm = { ...llm, ...(j as Partial<typeof llm>) }; }
  // Notifications have no settings tab any more: they are always on. A switch turned off in an older
  // version is put back to its default the first time this window opens.
  for (const [key, want] of Object.entries({ notify_whatsapp: true, notify_discord: true, notify_telegram: true, notify_reminders: true, notify_calls_only: false, notify_quiet_enabled: false })) {
    if (notifyPrefs.data[key] !== want) notifyPrefs.set(key, want);
  }
  let devices: VoiceDevices = { inputs: [], outputs: [], voices: [] };
  { const j = await devicesP; if (j) devices = { ...devices, ...(j as Partial<VoiceDevices>) }; }   // offline: the pickers show only the saved choice

  // First-run: friendly tip if no LLM key or no mic listed yet.
  const firstRunBits: HTMLElement[] = [];
  const hasAnyKey = !!(llm.has_openrouter || llm.has_gemini || llm.has_groq || llm.has_ollama);
  if (!hasAnyKey) {
    firstRunBits.push(
      h("div", {
        class: "notice warn",
        text: "Welcome — add an API key under AI (OpenRouter, Gemini or Groq) so Lyra can think.",
      }),
    );
  }
  if (!devices.inputs || devices.inputs.length === 0) {
    firstRunBits.push(
      h("div", {
        class: "notice warn",
        text: "No microphone listed yet. Plug one in and pick it under Voice, or open Advanced → Check for problems.",
      }),
    );
  }

  const mascotDef = FEATURES.find((f) => f.id === "mascot")!;
  const stayDef = FEATURES.find((f) => f.id === "mascot_stay")!;
  // Only General is built before the window is first drawn. Every other tab starts as an empty shell (title and
  // subtitle, so the sidebar, the page title and search keep working) and is filled in one task at a time afterwards,
  // or at once when its tab is opened or a search starts. Building all of them in one go froze the page for
  // ~330 ms every time Settings opened (the slow script in the trace was this very block).
  const pendingBuild = new Map<string, () => Kid[]>();
  const later = (id: string, title: string, sub: string, build: () => Kid[]): HTMLElement => {
    pendingBuild.set(id, build);
    return panel(title, sub);
  };
  // General also holds the island and Mochi cards (they used to be tabs of their own). Sound is
  // built up front; the island and Mochi cards (live previews: the heavy part) follow in the first idle slice and
  // are appended after the cards already there (and stay ahead of the other tabs in the queue: Map keeps insertion order).
  pendingBuild.set("general", () => {
    const display = displayCards();
    return [
      display.look,
      ecoCard(featurePrefs),
      islandCard(notifyPrefs, display.behaviour),
      ...mochiCards({
        card, item, toggle, prefs: featurePrefs,
        isOn: () => featureIsOn(mascotDef, featurePrefs),
        setOn: (v) => {
          applyFeature(mascotDef, v, featurePrefs);
          if (!v && featureIsOn(stayDef, featurePrefs)) applyFeature(stayDef, false, featurePrefs);   // no Mochi, nothing on screen
        },
        isStay: () => featureIsOn(stayDef, featurePrefs),
        setStay: (v) => applyFeature(stayDef, v, featurePrefs),
      }),
    ];
  });
  const panels: Record<string, HTMLElement> = {
    general: panel(
      "General",
      "How Lyra looks and sounds, plus your island and your Mochi.",
      ...firstRunBits,
      soundCard(),
    ),
    ai: later("ai", "AI", "Which model Lyra thinks with.", () => [llmSection(llm), ...aiCards(aiPrefs), ollamaCard({ lyra: LYRA, card, item, flashSaved })]),
    voice: later("voice", "Voice", "Microphone, speaker and voice.", () => {
      const kids: Kid[] = [...voiceCards(voicePrefs, devices), /* >>> phase-1 */ ...voiceEngineCards({ lyra: LYRA, card, item, toggle, confirmButton, flashSaved }), /* <<< phase-1 */];
      // >>> phase-5 begin
      // <<< phase-5 end
      return kids;
    }),
    notetaker: later("notetaker", "Recording", "Record your screen to a video.", () => recordingsCards({ card, item, flashSaved, lyra: LYRA })),
    features: later("features", "Features", "Turn any part of Lyra on or off. Island tabs change instantly; background helpers apply after a restart.", () => [...featuresCards(featurePrefs)]),
    apps: later("apps", "Apps", "Connect WhatsApp here. Once connected, your chats show up in the island's Apps tab.",
      () => [...appsCards({ card, item, toggle, confirmButton, flashSaved })]),
    live: later("live", "Live Activities", "Choose which activities can take over the notch. Click a preview to turn it on or off.", () => [...liveCards()]),
    shortcuts: later("shortcuts", "Shortcuts", "Keys that run Lyra.", () => []),
    files: later("files", "Files", "Choose folders Lyra can index and search.", () => [filesHost({ card, item, lyra, flashSaved })]),
    privacy: later("privacy", "Privacy & Memory", "What Lyra keeps and what she sends.", () => [...privacyCards(privacyPrefs), memoryHost(), backupCard()]),
    advanced: later("advanced", "Advanced", "Logs, restarts and resets.", () => [...advancedCards(advancedPrefs)]),
    // >>> phase-8 begin
    about: later("about", "About", "Version and privacy.", () => [aboutCard(), ...updateCards(version)]),
    // <<< phase-8 end
  };
  // >>> phase-6 begin
  // <<< phase-6 end

  // >>> phase-2 begin
  panels.p2_dictation = later("p2_dictation", "Dictation", "Hold a key, talk, and your words appear in any app. Everything here runs on this PC.",
    () => Array.from(buildP2Panels({ panel, card, item, toggle, confirmButton, lyra, flashSaved, extra: [dictationBasicsCard(voicePrefs)],
      // >>> phase-3 begin
      statistics: () => statsCards({ card, item, toggle, confirmButton, flashSaved, lyra, base: LYRA }),   // Statistics now lives inside the Dictation tab
      // <<< phase-3 end
    }).p2_dictation.children).slice(2));
  // <<< phase-2 end
  for (const [pid, pel] of Object.entries(panels)) registerPanel(pid, pel);   // Settings log: element -> tab
  slog.pending([...pendingBuild.keys()]);
  clear(root);
  root.append(buildShell(panels));
  enhanceSelects(document.body);   // dark, keyboard-friendly dropdowns instead of the browser's own
  installControlKit(document.body); // choice cards / pills, steppers, eye, sliders, undo toasts (choiceKit.ts)
  renderStatus();
  const fill = (id: string, why = "opened"): void => {
    const build = pendingBuild.get(id);
    const el = panels[id];
    if (!build || !el) return;
    pendingBuild.delete(id);
    slog.pending([...pendingBuild.keys()]);
    slog.buildStart(id, why);
    const t0 = performance.now();
    try {
      // the owner tells the Settings log who made any request fired while the cards are constructed
      const kids = withOwner(`build:${id}:${why.split(" ")[0]}`, build).filter((k): k is Node | string => !!k);
      const privacy = el.querySelector(":scope > .p4-privacy");   // added to the empty shell by p4_design: keep it last
      el.append(...kids);
      if (privacy) el.append(privacy);
    } catch (e) {
      console.error(`settings: could not build "${id}"`, e);
    }
    slog.buildEnd(id, performance.now() - t0, why);
  };
  ensureBuilt = (id) => fill(id, "opened");
  ensureAll = () => { for (const id of [...pendingBuild.keys()]) fill(id, "search"); };
  window.addEventListener("lyra-settings-build-all", ensureAll);
  const whenIdle = (cb: () => void): void => {
    const ric = (window as unknown as { requestIdleCallback?: (f: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
    if (ric) ric(cb, { timeout: 1500 }); else window.setTimeout(cb, 60);
  };
  whenIdle(() => fill("general", "idle fill"));   // the island and Mochi cards of General: first in the queue
  const drain = (): void => {
    const next = pendingBuild.keys().next();
    if (next.done) return;
    fill(next.value, "idle fill");
    whenIdle(drain);          // one panel per idle slice: no single task blocks a frame for long
  };
  window.setTimeout(() => whenIdle(drain), 900);   // after the window's opening animation
  // Backend / LLM-key chips: at start, then at most every 30 s, and not at all while this window is hidden.
  visiblePoll(pollBackend, { ms: 30_000, floorMs: 30_000, name: "backend-chips" });
}

void main();
// >>> phase-4 begin
registerP4();
// <<< phase-4 end

onEvent("settings-changed", () => {
  /* island picks up general prefs via Bridge */
});
