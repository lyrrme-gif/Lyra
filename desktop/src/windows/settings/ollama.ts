// Settings → AI: the "Local AI (Ollama)" card. New file; settings/main.ts only calls ollamaCard().
// It shows whether Ollama is running, lets you set its address, an optional vision model and the
// "too slow" timeout, and shows the exact `ollama pull` command when a model is missing.

import { h, clear } from "../../shared/views/dom";
import { whenVisible } from "./lazy";

type Kid = Node | string | null | undefined | false;

export type OllamaDeps = {
  lyra: string; // backend base URL, e.g. http://127.0.0.1:8765
  card: (title: Kid, ...children: Kid[]) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: Kid[]) => HTMLElement;
  flashSaved: (ok?: boolean) => void;
};

type OllamaStatus = {
  enabled: boolean;
  in_use?: boolean;       // false = never set up: the backend did not even try to reach Ollama
  running: boolean | null; // null = not checked
  base_url: string;
  models: string[];
  vision_models: string[];
  model: string;
  vision_model: string;
  timeout: number;
  keep_alive?: string;
  message?: string;
  pull_command: string;
  vision_pull_command: string;
};

async function saveLlm(lyra: string, body: Record<string, string>): Promise<boolean> {
  try {
    const r = await fetch(`${lyra}/settings/llm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.ok;
  } catch {
    return false;
  }
}

export function ollamaCard(d: OllamaDeps): HTMLElement {
  const title = h("span", { class: "ttl" });
  const msg = h("div", { class: "hint", text: "Checking…" });
  const feedback = h("div", {});
  const refreshBtn = h("button", { text: "Refresh" });

  const baseInput = h("input", {
    type: "text",
    placeholder: "http://localhost:11434",
    style: "flex:1 1 200px;min-width:0",
    autocomplete: "off",
    spellcheck: "false",
  }) as HTMLInputElement;
  const visionSelect = h("select", {}) as HTMLSelectElement;
  const timeoutInput = h("input", { type: "number", min: "5", max: "300", style: "width:90px" }) as HTMLInputElement;
  const keepSelect = h("select", {},
    h("option", { value: "30s", text: "30 seconds" }),
    h("option", { value: "1m", text: "1 minute (recommended)" }),
    h("option", { value: "5m", text: "5 minutes (Ollama's own default)" }),
    h("option", { value: "-1", text: "Always keep it loaded" }),
  ) as HTMLSelectElement;

  const pullCode = h("code", { text: "ollama pull llama3.2:3b" });
  const visionPullCode = h("code", { text: "ollama pull llava" });
  const copyPull = h("button", { text: "Copy" });
  const copyVision = h("button", { text: "Copy" });

  const dot = (ok: boolean | null) =>
    h("i", { class: "dot", style: `background:${ok == null ? "#6b7079" : ok ? "#22c55e" : "#f4505e"}` });

  const note = (text: string, ok: boolean) => {
    clear(feedback);
    feedback.append(h("div", { class: ok ? "notice ok" : "notice err", text }));
  };

  const apply = (s: OllamaStatus) => {
    clear(title);
    title.append(dot(s.running), h("span", { text: "Local AI (Ollama)" }));
    msg.textContent = s.message || "";
    baseInput.value = s.base_url;
    timeoutInput.value = String(s.timeout);
    keepSelect.value = s.keep_alive || "1m";
    pullCode.textContent = s.pull_command;
    visionPullCode.textContent = s.vision_pull_command;

    clear(visionSelect);
    visionSelect.append(h("option", { value: "", text: "None (use a cloud model for screen questions)" }));
    const names = [...s.models];
    if (s.vision_model && !names.includes(s.vision_model)) names.push(s.vision_model);
    for (const m of names) {
      const isVision = s.vision_models.includes(m);
      visionSelect.append(h("option", { value: m, text: isVision ? `${m}  (vision)` : m }));
    }
    visionSelect.value = s.vision_model || "";
  };

  // First open: ask without probing (instant; a PC without Ollama is never contacted). Refresh: probe for real.
  const load = async (probe = false) => {
    msg.textContent = probe ? "Checking…" : msg.textContent || "";
    try {
      const r = await fetch(`${d.lyra}/settings/ollama${probe ? "?probe=1" : ""}`);
      if (!r.ok) throw new Error(r.statusText);
      apply((await r.json()) as OllamaStatus);
    } catch {
      clear(title);
      title.append(dot(null), h("span", { text: "Local AI (Ollama)" }));
      msg.textContent = "Could not reach Lyra's backend — is it running?";
    }
  };

  const persist = async (body: Record<string, string>) => {
    const ok = await saveLlm(d.lyra, body);
    d.flashSaved(ok);
    if (ok) {
      clear(feedback);
      await load(true); // shows what the backend actually accepted (a bad address is ignored)
    } else {
      note("Could not save — is Lyra running?", false);
    }
  };

  refreshBtn.addEventListener("click", () => void load(true));
  baseInput.addEventListener("change", () => void persist({ ollama_base_url: baseInput.value.trim() }));
  visionSelect.addEventListener("change", () => void persist({ ollama_vision_model: visionSelect.value }));
  timeoutInput.addEventListener("change", () => void persist({ ollama_timeout: timeoutInput.value.trim() }));
  keepSelect.addEventListener("change", () => void persist({ ollama_keep_alive: keepSelect.value }));

  const copy = async (text: string, btn: HTMLElement) => {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied ✓";
    } catch {
      btn.textContent = "Select it and press Ctrl+C";
    }
    window.setTimeout(() => (btn.textContent = "Copy"), 1800);
  };
  copyPull.addEventListener("click", () => void copy(pullCode.textContent || "", copyPull));
  copyVision.addEventListener("click", () => void copy(visionPullCode.textContent || "", copyVision));

  const section = d.card(
    title,
    d.item("Status", msg, refreshBtn),
    d.item(
      "Ollama address",
      "Where Ollama is listening. The default works when Ollama runs on this PC.",
      baseInput,
    ),
    d.item(
      "Download a model",
      h("div", { class: "hint" }, "Run this in a terminal, then press Refresh and pick the model above under “Model”."),
      pullCode,
      copyPull,
    ),
    d.item(
      "Vision model",
      "Optional. Used when Lyra looks at your screen. Pick a model that can read images.",
      visionSelect,
    ),
    d.item(
      "Download a vision model",
      h("div", { class: "hint" }, "Optional. llava works well; moondream is smaller."),
      visionPullCode,
      copyVision,
    ),
    d.item(
      "Local timeout (seconds)",
      "Only used when a fallback provider is set under Fallback: if Ollama takes longer than this, the fallback answers instead.",
      timeoutInput,
    ),
    d.item(
      "Keep the model in memory",
      "How long the local model stays loaded after an answer. Shorter frees your graphics card and memory sooner (games, recording); the first answer after that waits a few seconds while it loads again.",
      keepSelect,
    ),
    feedback,
  );
  whenVisible(section, () => void load());
  return section;
}
