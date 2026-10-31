// Settings — Lyra: LLM providers + editable mod slots + Coucou general prefs.

import "./settings.css";
import "./settings-mac.css";
import "./settings-lyra.css";
import "./settings-coucou.css";
import "./settings-frameless.css";
import "./controls-v2.css";
import { createHero } from "./coucou/hero";
import { lineIcon } from "./coucou/icons";
import { Bridge, onEvent } from "../core/bridge";
import { DEFAULT_SETTINGS, readExtras, writeExtras, type LiveActivity, type Settings } from "../core/state";
import { h, clear } from "../views/dom";
import { FEATURES, FEATURE_GROUPS, featureEnabled, type FeatureDef } from "../core/features";
import { filesHost } from "./files";
import { visiblePoll, type VisiblePoll } from "../core/visiblePoll";
import { featurePreview } from "./featurePreviews";
import { activityScene } from "./activityPreviews";
import { onTourSignal } from "../core/tour";
// >>> phase-8 begin
import { updateCards } from "./panels/p8_updates";
// <<< phase-8 end
import { accessCards } from "./accessCards";
import { ollamaCard } from "./ollama";
import { whenVisible } from "./lazy";
import { enhanceSelects } from "./uiKit";
import { installControlKit } from "./choiceKit";
import { mochiCards, mochiTourPickers, onMochiShape } from "./mochiPanel";
import { appsCards } from "./appsPanel";
import { WHATSAPP_BG, WHATSAPP_GLYPH } from "./whatsappIcon";
import { SETTINGS_GOTO_KEY } from "../core/appsApi";
import { AUTOHIDE_HINT, AUTOHIDE_MODES, normalizeAutoHide } from "../core/autohide";
import { createCharacter, gazeToward, type EmoteName } from "../mascot/character";
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
    const preview = h("div", {
      class: "theme-mini-island",
      style: `--theme-material:${meta.material};--theme-accent:${meta.accent};--theme-glow:${meta.glow}`,
      "aria-hidden": "true",
    },
      h("span", { class: "mini-dot" }),
      h("span", { class: "mini-line one" }),
      h("span", { class: "mini-line two" }),
      h("span", { class: "mini-pill" }),
    );

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
    proactive_enabled: true,
  },
};

async function loadPrefs(group: string): Promise<Prefs> {
  const data: Record<string, PrefVal> = { ...PREF_DEFAULTS[group] };
  try {
    const r = await fetch(`${LYRA}/settings/${group}`);
    if (r.ok) Object.assign(data, await r.json());
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
  const voices: [string, string][] = [["", "Default (Aria)"]];
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

// ── Shortcuts tab (shortcuts.json) ────────────────────────────────────────────

type Action = Record<string, unknown> & { type: string };
type Shortcut = { id: string; name: string; triggers: string[]; actions: Action[] };

const ACTION_KINDS: [string, string, string][] = [
  ["open_app", "Open app", "e.g. chrome"],
  ["open_site", "Open website", "e.g. youtube"],
  ["open_url", "Open URL", "https://…"],
  ["open_spotify", "Spotify search", "optional: what to play"],
  ["open_folder", "Open folder", "C:\\Users\\you\\Documents"],
  ["set_volume", "Set volume", "0 – 100"],
  ["volume_up", "Volume up", "steps (default 5)"],
  ["volume_down", "Volume down", "steps (default 5)"],
  ["mute", "Mute", ""],
  ["lock_screen", "Lock screen", ""],
  ["take_screenshot", "Take screenshot", ""],
  ["wait", "Wait", "seconds"],
];

function shortcutsHost(): HTMLElement {
  const host = h("div", {});
  let fields: Record<string, string | null> = {};

  const load = async () => {
    const r = await lyra("/shortcuts");
    clear(host);
    if (!r.ok) {
      host.append(card(null, h("p", { class: "empty", text: r.error || "Could not load shortcuts." })));
      return;
    }
    fields = r.data.types || {};
    showList((r.data.shortcuts || []) as Shortcut[]);
  };

  const showList = (list: Shortcut[]) => {
    clear(host);
    const c = card("Your shortcuts");
    if (!list.length) c.append(h("p", { class: "empty", text: "No shortcuts yet." }));
    for (const sc of list) {
      const edit = h("button", { text: "Edit" });
      edit.addEventListener("click", () => showEditor(sc));
      const del = confirmButton("Delete", "Click again to delete", async () => {
        const d = await lyra("/shortcuts/delete", { id: sc.id });
        flashSaved(d.ok);
        void load();
      });
      const row = item(
        sc.name,
        `Say: ${sc.triggers.map((t) => `“${t}”`).join(", ")} · ${sc.actions.length} action${sc.actions.length === 1 ? "" : "s"}`,
        h("span", { class: "spacer" }),
        edit,
        del,
      );
      row.dataset.s += ` ${sc.triggers.join(" ")} shortcut`.toLowerCase();
      c.append(row);
    }
    const add = h("button", { class: "primary", text: "New shortcut" });
    add.addEventListener("click", () => showEditor(null));
    c.append(
      h("div", { class: "row pad" }, add),
      h("div", { class: "hint", text: "Say a trigger phrase and Lyra runs the actions in order. Changes work straight away." }),
    );
    host.append(c);
  };

  const showEditor = (sc: Shortcut | null) => {
    clear(host);
    const draft: Shortcut = sc
      ? { ...sc, triggers: [...sc.triggers], actions: sc.actions.map((a) => ({ ...a })) }
      : { id: "", name: "", triggers: [], actions: [{ type: "open_app", target: "" }] };
    const feedback = h("div", {});
    const name = h("input", { type: "text", value: draft.name, maxlength: "60", placeholder: "e.g. Work mode", style: "flex:1 1 200px" }) as HTMLInputElement;
    const triggers = h("textarea", { rows: "3", class: "area", placeholder: "One phrase per line, e.g.\nget me ready for work", spellcheck: "false" }) as HTMLTextAreaElement;
    triggers.value = draft.triggers.join("\n");
    const actionsBox = h("div", { class: "stack" });

    const renderActions = () => {
      clear(actionsBox);
      draft.actions.forEach((act, i) => {
        const kind = h("select", {}) as HTMLSelectElement;
        for (const [id, label] of ACTION_KINDS) kind.append(h("option", { value: id, text: label }));
        kind.value = act.type;
        const field = () => fields[act.type] ?? null;
        const value = h("input", { type: "text", style: "flex:1 1 140px;min-width:0", spellcheck: "false" }) as HTMLInputElement;
        const sync = () => {
          const f = field();
          value.disabled = !f;
          value.value = f ? String(act[f] ?? "") : "";
          value.placeholder = ACTION_KINDS.find((k) => k[0] === act.type)?.[2] ?? "";
        };
        sync();
        kind.addEventListener("change", () => {
          for (const k of Object.keys(act)) delete act[k];
          act.type = kind.value;
          sync();
        });
        value.addEventListener("input", () => {
          const f = field();
          if (f) act[f] = value.value;
        });
        const mk = (text: string, title: string, fn: () => void, disabled = false) => {
          const b = h("button", { class: "icon", title, "aria-label": title, text });
          if (disabled) b.setAttribute("disabled", "");
          b.addEventListener("click", fn);
          return b;
        };
        const move = (d: number) => {
          const [a] = draft.actions.splice(i, 1);
          draft.actions.splice(i + d, 0, a);
          renderActions();
        };
        actionsBox.append(
          h(
            "div",
            { class: "row tight" },
            kind,
            value,
            mk("▲", "Move up", () => move(-1), i === 0),
            mk("▼", "Move down", () => move(1), i === draft.actions.length - 1),
            mk("✕", "Remove", () => {
              draft.actions.splice(i, 1);
              renderActions();
            }),
          ),
        );
      });
      if (draft.actions.length < 20) {
        const add = h("button", { text: "+ Add action" });
        add.addEventListener("click", () => {
          draft.actions.push({ type: "open_app", target: "" });
          renderActions();
        });
        actionsBox.append(add);
      }
    };
    renderActions();

    const save = h("button", { class: "primary", text: "Save shortcut" });
    save.addEventListener("click", async () => {
      clear(feedback);
      const payload = {
        id: draft.id,
        name: name.value,
        triggers: triggers.value.split("\n"),
        actions: draft.actions,
      };
      const r = await lyra("/shortcuts/save", payload);
      flashSaved(r.ok);
      if (!r.ok) {
        feedback.append(h("div", { class: "notice err", text: r.error }));
        return;
      }
      void load();
    });
    const cancel = h("button", { text: "Cancel" });
    cancel.addEventListener("click", () => void load());

    host.append(
      card(
        sc ? `Edit “${sc.name}”` : "New shortcut",
        item("Name", null, name),
        item("Trigger phrases", "What you say to run it. Each phrase can only belong to one shortcut.", triggers),
        item("Actions", "Run top to bottom. Actions that need your confirmation (like locking the screen) still ask first.", actionsBox),
        feedback,
        h("div", { class: "row pad" }, save, cancel),
      ),
    );
  };

  whenVisible(host, () => void load());
  return host;
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
function previewTile(label: string, on: boolean, scene: HTMLElement, onPick: () => void, sub = ""): HTMLButtonElement {
  const b = h("button", { class: on ? "ptile on" : "ptile", type: "button", "aria-pressed": on },
    h("span", { class: "ptile-scene" }, scene),
    h("span", { class: "ptile-label", text: label }),
    sub ? h("span", { class: "ptile-sub", text: sub }) : null,
  ) as HTMLButtonElement;
  b.addEventListener("click", onPick);
  return b;
}

/** shape: "notch" = flat top fused to the screen edge; "island" = floating pill. */
function shapeScene(shape: "notch" | "island", inner = "", mochiEmote: EmoteName | null = null): HTMLElement {
  const pill = h("span", { class: `mini-island ${shape}` });
  pill.innerHTML = inner || '<i class="mi-dot"></i><i class="mi-bar"></i>';
  // Only the island previews in General carry your Mochi (same character and shape as the real one).
  if (mochiEmote) {
    const host = h("span", { class: "mi-mochi" });
    const mochi = createCharacter(host, { tight: true, sharp: 2 });
    mochi.setEmote(mochiEmote);
    onMochiShape((sh) => mochi.setShape?.(sh));
    pill.prepend(host);
  }
  return h("span", { class: "mini-desk" }, pill);
}

/** Material looks of the compact island (also drawn in the live preview). */
const ISLAND_MATERIALS: Record<string, string> = {
  black: "background:#000;",
  frost: "background:linear-gradient(180deg,#000 0%,rgba(8,7,6,.92) 52%,rgba(86,72,60,.62) 100%);backdrop-filter:blur(8px);",
  glass: "background:rgba(255,255,255,.16);backdrop-filter:blur(10px) saturate(170%);box-shadow:inset 0 0 0 1px rgba(255,255,255,.28);",
};

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
  const live = h("div", { class: "island-live" });
  const paintLive = () => {
    clear(live);
    const scene = shapeScene(shape, '<em class="mi-search">Search or ask Lyra</em><i class="mi-mic"></i>', "happy");
    const pill = scene.querySelector<HTMLElement>(".mini-island")!;
    pill.style.cssText = ISLAND_MATERIALS[material] || "";
    const rest = shapeScene("island", "", "idle");
    rest.querySelector<HTMLElement>(".mini-island")!.style.cssText = "background:#000;";
    live.append(
      h("div", { class: "il-col" }, scene, h("span", { class: "il-cap", text: "Open" })),
      h("div", { class: "il-col" }, rest, h("span", { class: "il-cap", text: "Resting" })),
    );
  };

  const notchless = radioTiles([["notch", "Notch"], ["island", "Dynamic Island"]], shape, (v) => {
    shape = v as "notch" | "island";
    writeExtras({ shapeNotchless: shape });
    // Windows screens have no hardware notch, so this is the shape that is actually drawn.
    writeIslandPosition(v === "notch" ? "attached" : "floating");
    paintLive();
  }, (v) => shapeScene(v as "notch" | "island", "", "idle"));

  const compactScene = (v: string): HTMLElement => {
    const scene = shapeScene("island", "", "idle");
    const pill = scene.querySelector<HTMLElement>(".mini-island");
    if (pill) pill.style.cssText = ISLAND_MATERIALS[v] || "";
    return scene;
  };
  const compactStyle = radioTiles([["black", "Black"], ["frost", "Frost"], ["glass", "Glass"]], material, (v) => {
    material = v;
    writeExtras({ compactStyle: v as "black" | "frost" | "glass" });
    paintLive();
  }, compactScene);
  // Tab navigation: the wheel (with the Mochi · Music · Chat dock) or a scroller that shows three tabs at a time.
  const navScene = (v: string): HTMLElement => {
    const wrap = h("div", { class: "nav-scene" });
    wrap.style.cssText = "display:flex;align-items:center;justify-content:center;gap:4px;height:46px;";
    const dot = (on = false) => { const d = h("i", {}); d.style.cssText = `display:block;width:10px;height:10px;border-radius:50%;background:${on ? "#fff" : "rgba(255,255,255,.35)"};`; return d; };
    if (v === "wheel" || v === "both") {
      const ring = h("div", {}); ring.style.cssText = "position:relative;width:40px;height:40px;border-radius:50%;border:1.5px solid rgba(255,255,255,.3);";
      [0, 72, 144, 216, 288].forEach((a, i) => { const d = dot(i === 0); d.style.cssText += `position:absolute;left:15px;top:15px;transform:rotate(${a}deg) translateY(-15px);`; ring.append(d); });
      wrap.append(ring);
    }
    if (v === "scroller" || v === "both") {
      const cap = h("div", {}); cap.style.cssText = "display:flex;align-items:center;gap:6px;padding:5px 8px;border-radius:14px;border:1.5px solid rgba(255,255,255,.3);";
      cap.append(dot(true), dot(), dot());
      const more = h("span", { text: "›" }); more.style.cssText = "color:rgba(255,255,255,.6);font-weight:700;margin-left:2px;";
      cap.append(more);
      wrap.append(cap);
    }
    return wrap;
  };
  const navStyle = radioTiles([["wheel", "Tab wheel"], ["scroller", "Scroller"], ["both", "Both"]], ex.navStyle, (v) => {
    writeExtras({ navStyle: v as "wheel" | "scroller" | "both" });
  }, navScene);
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
    }, a.sub);
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
  const hero = createHero();
  const groupOf = (id: string) => NAV_GROUPS.find(([, ids]) => ids.includes(id))?.[0] ?? "";
  const subOf = (id: string) => panels[id]?.querySelector(":scope > .sub")?.textContent?.trim() ?? "";
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
    if (q) hero.set("search", "Search", "Search results", any ? "Everything that matches, from every page." : "");
    else hero.set(active, groupOf(active), labelOf(active), subOf(active));
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
  const mainEl = h("main", { class: "main" }, top, h("div", { class: "content" }, hero.el, ...TABS.map(([id]) => panels[id]), noMatch));

  // Brand at the top of the sidebar: your Mochi (same character as the notch, changes as soon as another one is
  // picked in Settings → General) and a bold "Lyra". Poke it and it hops.
  const avatar = h("span", { class: "brand-mochi acct-avatar" });
  const avatarMochi = createCharacter(avatar, { tight: true, sharp: 2 });
  onMochiShape((shape) => avatarMochi.setShape?.(shape));
  avatarMochi.setEmote("idle");
  // The eyes follow the pointer while it is over this window (and keep looking where it left).
  const aimAvatar = (x: number, y: number) => {
    const [gx, gy] = gazeToward(avatar, x, y);
    avatarMochi.look(gx, gy);
  };
  window.addEventListener("mousemove", (e) => aimAvatar(e.clientX, e.clientY), { passive: true });
  // The version moved to General and About; p4_design still reads it from this (hidden) line.
  const acct = h("button", { class: "brand", type: "button", "aria-label": "Lyra" }, avatar,
    h("span", { class: "brand-name", text: "LYRA" }),
    h("span", { class: "acct-text", hidden: "" }, h("small", { text: `Version ${version || "1.0.0"}` })),
  );
  let brandTimer = 0, brandBusy = false;
  acct.addEventListener("click", () => {
    avatarMochi.setEmote("celebrate");
    window.clearTimeout(brandTimer);
    brandTimer = window.setTimeout(() => avatarMochi.setEmote("idle"), 1800);
    if (brandBusy || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    brandBusy = true;
    // three hops, each lower than the last
    const bounce = (i: number) => {
      const k = 16 * Math.pow(0.62, i);
      avatar.animate(
        [{ transform: "translateY(0) scale(1.1,.88)" }, { transform: `translateY(${-k}px) scale(.94,1.08)`, offset: 0.5 }, { transform: "translateY(0) scale(1.12,.86)" }],
        { duration: 360 - i * 40, easing: "cubic-bezier(.3,.7,.4,1)" },
      ).onfinish = () => { if (i < 2) bounce(i + 1); else brandBusy = false; };
    };
    bounce(0);
  });

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

function featureRow(f: FeatureDef, on: boolean, onChange: (v: boolean) => void, compact = false): HTMLElement {
  const ico = h("span", { class: "tour-ico", style: `background:linear-gradient(${f.from},${f.to})`, text: f.name.slice(0, 1) });
  const copy = h("div", { class: "tour-copy" },
    h("strong", { text: f.name }),
    h("span", { text: f.blurb }),
    h("em", { text: `Try: ${f.tip}` }),
  );
  const row = h("div", { class: compact ? "tour-row compact" : "tour-row" }, ico, copy, toggle(on, onChange));
  return row;
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
    item("Switch Lyra's key shortcuts off while you play", "Right Ctrl, Ctrl+Win, Ctrl+Shift and Ctrl+CapsLock are fully removed during a game, so nothing of Lyra sits in the keyboard path. Press Ctrl+Alt+L to talk to her meanwhile, or say “Hey Lyra”.",
      toggle(featPrefs.data.feature_game_mode !== false, (v) => featPrefs.set("feature_game_mode", v))),
    item("Treat full-screen programs as games", "Browsers, video players and slide shows are ignored.",
      toggle(featPrefs.data.game_mode_fullscreen !== false, (v) => featPrefs.set("game_mode_fullscreen", v))),
    item("Game list", "Programs that switch game mode on while they run. Comma-separated, e.g. cs2.exe.", gameApps));
  return [restart, intro, ...groups, ...access, proactive, studyPrefs, gameMode];
}

// ── First-launch setup / tour ────────────────────────────────────────────────
const ONBOARDING_KEY = "lyra-onboarding-complete";
const SETUP_THEMES: Array<{ id: string; name: string; note: string; bg: string; accent: string }> = [
  { id:"pure-black", name:"Black", note:"Absolute black", bg:"#000", accent:"#fff" },
  { id:"midnight", name:"Midnight", note:"Deep Apple glass", bg:"linear-gradient(135deg,#17191e,#08090c)", accent:"#9aaeff" },
  { id:"aurora", name:"Aurora", note:"Cool luminous colour", bg:"linear-gradient(135deg,#101827,#151125)", accent:"#9ff2da" },
  { id:"dynamic", name:"Dynamic", note:"Adaptive Lyra look", bg:"linear-gradient(135deg,#151722,#252a40)", accent:"#c1ccff" },
  { id:"frost", name:"Frost", note:"Black fading into glass", bg:"linear-gradient(180deg,#000 0%,#000 46%,rgba(70,58,48,.78) 100%)", accent:"#3b82f6" },
];

const SETUP_PROVIDERS = [
  {
    id: "openrouter",
    name: "OpenRouter",
    key: "openrouter_api_key",
    placeholder: "sk-or-…",
    url: "https://openrouter.ai/docs/api/api-reference/api-keys/create-a-new-api-key",
    steps: ["Create or sign in to your account", "Open API Keys and choose Create Key", "Copy the key and paste it here"],
  },
  {
    id: "gemini",
    name: "Google Gemini",
    key: "gemini_api_key",
    placeholder: "AIza…",
    url: "https://aistudio.google.com/api-keys",
    steps: ["Sign in with your Google account", "Open API Keys and create a key", "Copy the key and paste it here"],
  },
  {
    id: "groq",
    name: "Groq",
    key: "groq_api_key",
    placeholder: "gsk_…",
    url: "https://console.groq.com/keys",
    steps: ["Create or sign in to your Groq account", "Open API Keys and create a key", "Copy the key and paste it here"],
  },
  {
    id: "ollama",
    name: "Ollama (local)",
    key: "ollama_api_key",
    placeholder: "No key needed",
    url: "https://ollama.com/download",
    steps: ["Install and start Ollama", "Run: ollama pull llama3.2:3b", "Continue; no key or internet is needed"],
  },
] as const;

type SetupProvider = typeof SETUP_PROVIDERS[number]["id"];

function onboardingDone(): boolean {
  try { return window.localStorage.getItem(ONBOARDING_KEY) === "1"; } catch { return false; }
}

function markOnboardingDone() {
  try { window.localStorage.setItem(ONBOARDING_KEY, "1"); } catch { /* ignore */ }
}

type StepId = "island" | "visible" | "look" | "connect" | "voice" | "tour-island" | "tabs-wheel" | "tabs-ways" | "mochi-look" | "mochi-show"
  | "tour-daily" | "tour-pc" | "tour-helpers" | "mods" | "ready";
const STEPS: StepId[] = ["island", "visible", "look", "connect", "voice", "tour-island", "tabs-wheel", "tabs-ways", "mochi-look", "mochi-show",
  "tour-daily", "tour-pc", "tour-helpers", "mods", "ready"];

/** Tour steps that wait for the user to really do something. Next unlocks when the task(s) are done (or the step is skipped). */
interface TourTask { id: string; label: string; hint: string }
const TOUR_TASKS: Partial<Record<StepId, { need: "all" | "any"; tasks: TourTask[] }>> = {
  "tabs-wheel": { need: "all", tasks: [
    { id: "wheel-open", label: "Hold Ctrl + CapsLock", hint: "The wheel appears right where your cursor is." },
    { id: "wheel-pick", label: "Point at a tab and let go", hint: "The island opens that tab." },
  ] },
  "tabs-ways": { need: "any", tasks: [
    { id: "way-right", label: "Right-click the island", hint: "Move your mouse to the top of the screen to open it first." },
    { id: "way-dock", label: "Click a tab in the dock", hint: "The little row of icons under the island." },
    { id: "way-voice", label: "Ask for a tab by voice", hint: "Say “Hey Lyra, show the tab wheel” or “open my stats”." },
  ] },
  "mochi-look": { need: "any", tasks: [
    { id: "shape", label: "Pick a character", hint: "Cloud, fox or cat." },
  ] },
  "mochi-show": { need: "any", tasks: [
    { id: "mascot", label: "Flip the Mochi switch", hint: "Show it on your screen, or tuck it away." },
  ] },
  "mods": { need: "any", tasks: [
    { id: "mode", label: "Say or type a mode", hint: "For example “Hey Lyra, gaming mode”, or type it in Chat." },
  ] },
};

async function showOnboarding(opts: { replay?: boolean } = {}): Promise<void> {
  const replay = !!opts.replay;
  let step = 0;
  const choices = {
    position: (readIslandPosition() === "attached" ? "attached" : "floating") as "floating" | "attached",
    theme: readIslandTheme(),
    voice: "ptt" as "ptt" | "wake" | "both",
    hidden: false,
    provider: "openrouter" as SetupProvider,
    keys: { openrouter_api_key: "", gemini_api_key: "", groq_api_key: "", ollama_api_key: "" },
    features: {} as Record<string, boolean>,
  };
  let hasSavedKey = false;
  let finishError = "";
  const [featPrefs, privacyPrefs] = await Promise.all([loadPrefs("features"), loadPrefs("privacy")]);
  for (const f of FEATURES) choices.features[f.id] = featureIsOn(f, featPrefs);
  choices.hidden = !settings.alwaysShowNotch;
  try {
    const [llmR] = await Promise.all([
      fetch(`${LYRA}/settings/llm`).catch(() => null),
    ]);
    if (llmR?.ok) {
      const l = await llmR.json();
      if (["openrouter","gemini","groq","ollama"].includes(l.provider)) choices.provider = l.provider;
      hasSavedKey = !!(l.has_openrouter || l.has_gemini || l.has_groq || l.has_ollama);
    }
  } catch { /* defaults are fine */ }

  const rootWizard = h("div", { class: "onboarding" });
  const progress = h("div", { class: "onboarding-progress" });
  progress.style.gridTemplateColumns = `repeat(${STEPS.length},1fr)`;
  const eyebrow = h("div", { class: "onboarding-eyebrow", text: replay ? "LYRA TOUR" : "WELCOME TO LYRA" });
  const title = h("h1", {});
  const sub = h("p", { class: "onboarding-sub" });
  const body = h("div", { class: "onboarding-body" });
  const back = h("button", { class: "onboarding-back", text: "Back" });
  const next = h("button", { class: "onboarding-next", text: "Continue" });
  const skip = h("button", { class: "onboarding-skip", type: "button", text: "Skip the tour" });
  const actions = h("div", { class: "onboarding-actions" }, back, skip, next);

  const renderProgress = () => {
    clear(progress);
    for (let i = 0; i < STEPS.length; i++) progress.append(h("span", { class: i === step ? "on" : i < step ? "done" : "" }));
  };

  const option = (kind: string, id: string, label: string, desc: string, selected: boolean, preview?: HTMLElement) => {
    const b = h("button", { class: `setup-option${selected ? " on" : ""}`, type:"button" }, preview || null, h("span", { class:"setup-option-copy" }, h("strong", { text:label }), h("small", { text:desc })), h("span", { class:"setup-check", text:"✓" }));
    b.addEventListener("click", () => {
      if (kind === "position") choices.position = id as "floating" | "attached";
      if (kind === "theme") choices.theme = id;
      if (kind === "hidden") choices.hidden = id === "hidden";
      if (kind === "provider") choices.provider = id as SetupProvider;
      finishError = "";
      render();
    });
    return b;
  };

  // ── Interactive steps: Next waits for the real action, reported by the island / dock / settings ──
  const done = new Set<string>();          // task ids the user has really done
  const skipped = new Set<StepId>();       // steps the user skipped
  let refreshTasks: () => void = () => undefined;
  const stepDone = (name: StepId): boolean => {
    const def = TOUR_TASKS[name];
    if (!def || skipped.has(name)) return true;
    const hit = def.tasks.filter((t) => done.has(t.id)).length;
    return def.need === "all" ? hit === def.tasks.length : hit > 0;
  };
  const mark = (id: string) => {
    if (done.has(id)) return;
    done.add(id);
    refreshTasks();
    (next as HTMLButtonElement).disabled = !canContinue();
  };
  // What the island reports (see core/tour.ts) counts for the step that is on screen.
  void onTourSignal((sig) => {
    const name = STEPS[step];
    if (name === "tabs-wheel") {
      if (sig.kind === "wheel-open") mark("wheel-open");
      if (sig.kind === "wheel-pick") { mark("wheel-open"); mark("wheel-pick"); }
    } else if (name === "tabs-ways") {
      if (sig.kind === "wheel-open" && sig.detail === "rightclick") mark("way-right");
      if (sig.kind === "dock-pick") mark("way-dock");
      if ((sig.kind === "wheel-open" && sig.detail === "voice") || sig.kind === "open-view") mark("way-voice");
    } else if (name === "mods") {
      if (sig.kind === "said" && /\bmode\b/i.test(sig.detail || "")) mark("mode");
    }
  });

  const taskCard = (name: StepId): HTMLElement => {
    const def = TOUR_TASKS[name]!;
    const status = h("span", { class: "tour-task-status" });
    const skipStep = h("button", { class: "tour-task-skip", type: "button", text: "Skip" });
    skipStep.addEventListener("click", () => { skipped.add(name); if (step < STEPS.length - 1) { step++; render(); } });
    const rows = def.tasks.map((t) => {
      const dot = h("span", { class: "tour-task-dot" });
      const row = h("div", { class: "tour-task" }, dot, h("span", { class: "tour-task-copy" }, h("strong", { text: t.label }), h("small", { text: t.hint })));
      return { t, dot, row };
    });
    const how = def.tasks.length === 1 ? "Your turn" : def.need === "all" ? "Your turn — do both" : "Your turn — do any one";
    const card = h("div", { class: "tour-tasks" },
      h("div", { class: "tour-tasks-head" }, h("span", { class: "tour-tasks-eyebrow", text: how }), status),
      ...rows.map((r) => r.row),
      h("div", { class: "tour-tasks-foot" }, h("span", { text: "Can't try this right now?" }), skipStep));
    refreshTasks = () => {
      for (const { t, dot, row } of rows) { const d = done.has(t.id); row.classList.toggle("done", d); dot.textContent = d ? "✓" : ""; }
      const ok = stepDone(name);
      card.classList.toggle("ok", ok);
      status.textContent = ok ? "Nice, that worked." : "Waiting for you…";
    };
    refreshTasks();
    return card;
  };

  const demo = (id: string) => h("div", { class: "tour-demo" }, featurePreview(FEATURES.find((f) => f.id === id)!));
  const way = (k: string, t: string) => h("div", { class: "ready-row" }, h("span", { class: "setup-key", text: k }), h("span", { text: t }));
  const label = (t: string) => h("div", { class: "tour-label", text: t });

  const interactiveStep = (name: StepId) => {
    if (name === "tabs-wheel") {
      title.textContent = "Open tabs with the wheel.";
      sub.textContent = "Hold Ctrl + CapsLock anywhere. A wheel with all your tabs appears at your cursor. Point at one and let go to open it.";
      body.append(demo("tab_wheel"));
    } else if (name === "tabs-ways") {
      title.textContent = "More ways to open a tab.";
      sub.textContent = "The wheel is one way. Try any one of these.";
      body.append(h("div", { class: "ready-list" },
        way("Right-click", "Right-click the island and the wheel opens in the middle of the screen."),
        way("Dock", "Click an icon in the tab dock under the island."),
        way("Voice", "Say “Hey Lyra, show the tab wheel”, or just ask for a tab: “open stats”.")));
    } else if (name === "mochi-look") {
      title.textContent = "Make your Mochi yours.";
      sub.textContent = "Pick a character: each has its own moods and its own sounds. Your real Mochi changes right away. More under Settings → General.";
      const featureData: Prefs = featPrefs;
      const startShape = String(featureData.data.mascot_shape || "cloud");
      const { shapes } = mochiTourPickers({
        data: featureData.data,
        set: (k, v) => {
          featureData.set(k, v as Parameters<Prefs["set"]>[1]);
          if (k === "mascot_shape" && v !== startShape) mark("shape");
        },
      });
      body.append(label("Character"), shapes);
    } else if (name === "mochi-show") {
      title.textContent = "Show your Mochi.";
      sub.textContent = "Your Mochi can hang out on your screen next to the subtitles, or stay tucked in the notch. It switches on the moment you flip it.";
      const live = (id: string) => {
        const f = FEATURES.find((x) => x.id === id)!;
        return featureRow(f, choices.features[id], (v) => { choices.features[id] = v; applyFeature(f, v, featPrefs); mark("mascot"); });
      };
      body.append(h("div", { class: "tour-list" }, live("mascot"), live("mascot_stay")));
    } else if (name === "mods") {
      title.textContent = "Modes.";
      sub.textContent = "A mode changes how Lyra talks and what she does. Gaming, Chill, Study and Angry come built in.";
      body.append(h("div", { class: "tour-modes" }, ...["Gaming", "Chill", "Study", "Angry"].map((m) => h("span", { class: "tour-mode", text: m }))),
        h("p", { class: "setup-api-note", text: "Say “Hey Lyra, gaming mode”, or type it in Chat. Lyra confirms when it switches." }));
    }
    body.append(taskCard(name));
  };

  const tourPage = (group: FeatureDef["group"]) => {
    const g = FEATURE_GROUPS.find((x) => x.id === group)!;
    title.textContent = g.title + ".";
    sub.textContent = `${g.sub} Switch off anything you don't want. You can change it later in Settings → Features.`;
    const list = h("div", { class: "tour-list" });
    for (const f of FEATURES.filter((x) => x.group === group && !x.soon)) {
      list.append(featureRow(f, choices.features[f.id], (v) => { choices.features[f.id] = v; }));
    }
    body.append(list);
    if (group === "helpers") body.append(h("p", { class: "setup-api-note", text: "Background helpers start when Lyra restarts, which happens when you finish this tour." }));
  };

  const render = () => {
    const name = STEPS[step];
    clear(body);
    renderProgress();
    refreshTasks = () => undefined;
    const isTour = name.startsWith("tour-") || !!TOUR_TASKS[name];
    skip.style.display = isTour ? "" : "none";
    if (name === "island") {
      title.textContent = replay ? "Your island." : "Make Lyra yours.";
      sub.textContent = "A few quick choices, then a short tour of everything Lyra can do. You can change all of it later in Settings.";
      const mkIsland = (kind: "floating" | "attached") => h("div", { class:`setup-screen ${kind}` },
        h("span", { class:"setup-screen-win" }),
        h("span", { class:"setup-notch" }, h("span", { class:"setup-preview-dot" }), h("span", { class:"setup-preview-pill", text:"Lyra" })));
      const floating = mkIsland("floating");
      const attached = mkIsland("attached");
      body.append(h("div", { class:"setup-question", text:"Choose your island" }), h("div", { class:"setup-grid two" },
        option("position","floating","Floating","A small breathing gap above the island.",choices.position === "floating", floating),
        option("position","attached","Top attached","Flush with the top edge, like a classic notch.",choices.position === "attached", attached),
      ));
    } else if (name === "visible") {
      title.textContent = "Keep it visible?";
      sub.textContent = "When hidden, Lyra retracts the small island when idle. You can still wake it from the top edge.";
      body.append(h("div", { class:"setup-grid two" },
        option("hidden","visible","Keep visible","The compact island stays available when idle.",!choices.hidden, h("span", { class:"setup-symbol", text:"●" })),
        option("hidden","hidden","Hide when idle","The island retracts when nothing is happening.",choices.hidden, h("span", { class:"setup-symbol", text:"—" })),
      ));
    } else if (name === "look") {
      title.textContent = "Pick your look.";
      sub.textContent = "Every theme styles the compact island, expanded island and tab dock — not just the settings screen.";
      const grid = h("div", { class:"setup-theme-grid" });
      for (const t of SETUP_THEMES) {
        const preview = h("div", { class:"setup-theme-preview", style:`background:${t.bg};--setup-accent:${t.accent}` }, h("i", {}), h("b", {}));
        grid.append(option("theme", t.id, t.name, t.note, choices.theme === t.id, preview));
      }
      body.append(grid);
    } else if (name === "connect") {
      title.textContent = "Connect Lyra.";
      sub.textContent = hasSavedKey
        ? "You already have a key saved. Add another provider here, or just continue."
        : "Add at least one AI provider key. Lyra keeps these keys locally and never shows the full key back to you.";
      const providerRow = h("div", { class:"setup-provider-row" });
      for (const provider of SETUP_PROVIDERS) {
        providerRow.append(option("provider", provider.id, provider.name, choices.keys[provider.key] ? "Key added" : "Use this provider", choices.provider === provider.id, h("span", { class:"setup-provider-mark", text:provider.name === "Google Gemini" ? "G" : provider.name.slice(0,1) })));
      }
      const current = SETUP_PROVIDERS.find((p) => p.id === choices.provider) || SETUP_PROVIDERS[0];
      const local = current.id === "ollama";
      const input = h("input", { class:"setup-api-input", type:"password", placeholder:current.placeholder, autocomplete:"off", spellcheck:"false", value:choices.keys[current.key], "aria-label":`${current.name} API key` }) as HTMLInputElement;
      input.addEventListener("input", () => { choices.keys[current.key] = input.value; finishError = ""; (next as HTMLButtonElement).disabled = !canContinue(); });
      const link = h("a", { class:"setup-api-link", href:current.url, target:"_blank", rel:"noopener noreferrer", text:local ? "Download Ollama ↗" : `Get a ${current.name} API key ↗` });
      const tutorial = h("div", { class:"setup-tutorial" },
        h("strong", { text:"Quick setup" }),
        h("ol", {}, ...current.steps.map((s) => h("li", { text:s }))),
      );
      const field = h("div", { class:"setup-api-field" }, local ? null : input, link);
      const other = h("p", { class:"setup-api-note", text:"Only one key is required. You can add the others later in Settings → AI." });
      const err = h("div", { class:"setup-error", text:finishError });
      body.append(providerRow, field, tutorial, other, err);
    } else if (name === "voice") {
      title.textContent = "How do you talk to Lyra?";
      sub.textContent = "Hold one key, speak, let go. The microphone is only used while you hold it.";
      body.append(h("div", { class:"setup-grid three" },
        option("voice","ptt","Right Ctrl","Hold it when you want to talk.",true, h("span", { class:"setup-key", text:"Right Ctrl" })),
        option("voice","dictate","Left Alt","Hold it to dictate into any app.",false, h("span", { class:"setup-key", text:"Left Alt" })),
      ), h("p", { class:"setup-api-note", text:"You can change the dictation key later in Settings → Dictation." }));
    } else if (name === "tour-island") tourPage("island");
    else if (name === "tour-daily") tourPage("daily");
    else if (name === "tour-pc") tourPage("pc");
    else if (name === "tour-helpers") tourPage("helpers");
    else if (TOUR_TASKS[name]) interactiveStep(name);
    else {
      const live = FEATURES.filter((f) => !f.soon);
      const on = live.filter((f) => choices.features[f.id]).length;
      title.textContent = "You're all set.";
      sub.textContent = `${on} of ${live.length} features are on. Here's how to reach Lyra.`;
      const way = (k: string, t: string) => h("div", { class:"ready-row" }, h("span", { class:"setup-key", text:k }), h("span", { text:t }));
      const rows: HTMLElement[] = [];
      rows.push(way("Right Ctrl", "Hold it while you talk, release when you're done."));
      rows.push(way("Left Alt", "Hold it and dictate: your words are pasted into the app in front."));
      rows.push(way("Ctrl + Win", "Open the chat tab and type. It is also in the island tabs."));
      if (choices.features.circle_ask) rows.push(way("Ctrl + Shift", "Hold it and circle something on screen to ask about it."));
      rows.push(way("Tray icon", "Open Settings, pause Lyra, or quit. Replay this tour from Settings → Features."));
      body.append(h("div", { class:"ready-list" }, ...rows));
    }
    back.disabled = step === 0;
    next.textContent = name === "ready" ? (replay ? "Save changes" : "Finish setup") : "Continue";
    (next as HTMLButtonElement).disabled = !canContinue();
  };

  const canContinue = () => {
    if (!stepDone(STEPS[step])) return false;
    if (STEPS[step] !== "connect") return true;
    return replay || hasSavedKey || choices.provider === "ollama" || SETUP_PROVIDERS.some((p) => p.id !== "ollama" && choices.keys[p.key].trim());
  };

  next.addEventListener("click", async () => {
    if (step < STEPS.length - 1) { step++; render(); return; }
    const typedKey = SETUP_PROVIDERS.some((p) => p.id !== "ollama" && choices.keys[p.key].trim());
    const useLocal = choices.provider === "ollama";
    if (!replay && !hasSavedKey && !typedKey && !useLocal) { step = STEPS.indexOf("connect"); finishError = "Choose Ollama or add an API key to continue."; render(); return; }

    (next as HTMLButtonElement).disabled = true;
    writeIslandPosition(choices.position);
    writeIslandTheme(choices.theme);
    try { window.localStorage.setItem("lyra-theme-preview", `${choices.theme}|${Date.now()}`); } catch {}
    settings.alwaysShowNotch = !choices.hidden;
    await save();

    if (typedKey || useLocal) {
      let apiSaved = false;
      try {
        const api = await fetch(`${LYRA}/settings/llm`, {
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({ provider:choices.provider, ...choices.keys }),
        });
        apiSaved = api.ok;
      } catch { apiSaved = false; }
      if (!apiSaved) {
        step = STEPS.indexOf("connect");
        finishError = "Lyra could not save the API key. Make sure Lyra is running and try again.";
        render();
        return;
      }
    }

    // Feature switches: island tabs apply now, helpers when the backend restarts below.
    for (const f of FEATURES) if (!f.soon) applyFeature(f, choices.features[f.id], featPrefs);
    await Promise.all([featPrefs.flush(), privacyPrefs.flush()]);

    try {
      const voiceSave = await fetch(`${LYRA}/settings/voice`, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ voice_activation_mode: choices.voice }) });
      if (voiceSave.ok) { try { await fetch(`${LYRA}/backend/restart`, { method:"POST", headers:{"Content-Type":"application/json"}, body:"{}" }); } catch {} }
    } catch {}
    markOnboardingDone();
    window.setTimeout(() => location.reload(), 120);
  });
  back.addEventListener("click", () => { if (step > 0) { step--; render(); } });
  skip.addEventListener("click", () => { step = STEPS.length - 1; render(); });

  clear(root);
  rootWizard.append(h("div", { class:"onboarding-brand" }, h("span", { class:"onboarding-orb" }), h("strong", { text:"Lyra" }), h("span", { class:"onboarding-version", text:"v1.0" })), progress, eyebrow, title, sub, body, actions);
  root.append(rootWizard);
  render();
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
  // subtitle, so the sidebar, the hero and search keep working) and is filled in one task at a time afterwards,
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
    shortcuts: later("shortcuts", "Shortcuts", "Phrases that open apps and sites.", () => [shortcutsHost()]),
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
