// Settings → Features → Plugins: one switch per plugin, a Reload button, and any files that failed to load.
// Talks to the backend routes GET /plugins, POST /plugins/toggle, POST /plugins/reload.

import { h, clear } from "../views/dom";

const LYRA = "http://127.0.0.1:8765";

interface PluginRow {
  name: string;
  description: string;
  permission: "SAFE" | "CONFIRM";
  source: "bundled" | "user";
  file: string;
  enabled: boolean;
}
interface PluginInfo {
  enabled: boolean;
  plugins: PluginRow[];
  errors: Array<{ file: string; error: string }>;
  folder: string;
}

async function call(path: string, body?: unknown): Promise<PluginInfo | null> {
  try {
    const r = await fetch(`${LYRA}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!r.ok) return null;
    return (await r.json()) as PluginInfo;
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

function row(p: PluginRow, onToggle: (v: boolean) => void): HTMLElement {
  const meta = [
    p.source === "user" ? "Yours" : "Built in",
    p.permission === "CONFIRM" ? "asks before it runs" : null,
    p.file,
  ].filter(Boolean).join(" · ");
  const el = h("div", { class: "item" },
    h("div", { class: "row" }, h("label", { text: p.name }), switchEl(p.enabled, onToggle)),
    h("div", { class: "hint" }, p.description || "No description.", h("br", {}), h("span", { class: "tour-try", text: meta })),
  );
  el.dataset.s = `${p.name} ${p.description}`.toLowerCase();
  return el;
}

/** The Plugins card for the Features panel. Fills itself in from the backend. */
/** `onAction` fires after a toggle or reload the backend really accepted (the tour waits for it). */
export function pluginCards(onAction?: () => void): HTMLElement[] {
  const host = h("div", { class: "group" });
  const card = h("section", { class: "card" }, h("h3", { text: "Plugins — add abilities by dropping in a file" }), host);

  const render = (info: PluginInfo | null) => {
    clear(host);
    if (!info) {
      host.append(h("div", { class: "notice warn", text: "Could not reach Lyra, so the plugin list is not available. Is the backend running?" }));
      return;
    }
    if (!info.enabled) {
      host.append(h("div", { class: "hint", style: "padding:10px 14px", text: "Plugins are switched off (see the Plugins switch above). Your files are kept." }));
    }
    for (const p of info.plugins) {
      host.append(row(p, async (v) => {
        const next = await call("/plugins/toggle", { name: p.name, enabled: v });
        if (next) onAction?.();
        render(next ?? info);
      }));
    }
    if (info.plugins.length === 0) {
      host.append(h("div", { class: "hint", style: "padding:10px 14px", text: "No plugins found yet." }));
    }
    for (const e of info.errors) {
      host.append(h("div", { class: "notice warn", text: `${e.file} was skipped: ${e.error}` }));
    }
    const reload = h("button", { class: "btn", type: "button", text: "Reload plugins" }) as HTMLButtonElement;
    reload.addEventListener("click", async () => {
      reload.disabled = true;
      const reloaded = await call("/plugins/reload", {});
      if (reloaded) onAction?.();
      render(reloaded);
    });
    host.append(h("div", { class: "item" },
      h("div", { class: "row" }, h("label", { text: "Your plugins folder" }), reload),
      h("div", { class: "hint" }, info.folder, h("br", {}), "Drop a .py file there, then say \"reload plugins\" or press the button."),
    ));
  };

  host.append(h("div", { class: "hint", style: "padding:10px 14px", text: "Loading plugins…" }));
  void call("/plugins").then(render);
  return [card];
}
