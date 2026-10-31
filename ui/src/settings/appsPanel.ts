// Settings → Apps: where WhatsApp is set up. Sign-in, the QR code to scan and
// the app's options live here; the island's Apps tab only shows the chats once it is connected.

import "./appsPanel.css";
import { h, svg } from "../views/dom";
import { ICONS } from "../views/icons";
import { Bridge } from "../core/bridge";
import { appsPost, fetchApps, type AppField, type AppInfo, type AppView } from "../core/appsApi";
import { WHATSAPP_BG, whatsappGlyph } from "./whatsappIcon";
import { visiblePoll } from "../core/visiblePoll";

type Kid = Node | string | null | undefined | false;

interface Deps {
  card: (title: Kid, ...children: Kid[]) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: Kid[]) => HTMLElement;
  toggle: (on: boolean, onChange: (v: boolean) => void) => HTMLElement;
  confirmButton: (label: string, confirmLabel: string, onConfirm: () => void, cls?: string) => HTMLElement;
  flashSaved: (ok?: boolean) => void;
}

/** Steps shown next to a QR code (WhatsApp wording; the plugin's own text is used when it sends one). */
const QR_STEPS = ["Open WhatsApp on your phone", "Settings → Linked devices → Link a device", "Point the phone at this code"];

export function appsCards(d: Deps): HTMLElement[] {
  const host = h("div", { class: "apps-host" });
  let last = "";
  let views = new Map<string, AppView>();
  let busy = false;

  async function load(force = false): Promise<void> {
    if (busy) return;
    busy = true;
    try {
      const data = await fetchApps();
      const next = new Map<string, AppView>();
      if (data) {
        for (const a of data.apps.filter((x) => x.id === "whatsapp")) {
          const needsInput = a.settings.some((f) => f.type === "text" && !f.set);
          if (!a.connected && a.canConnect && !needsInput) {
            const res = await appsPost<{ ok: boolean; view?: AppView }>("/apps/view", { id: a.id, route: "" });
            if (res?.ok && res.view) next.set(a.id, res.view);
          }
        }
      }
      views = next;
      const sig = JSON.stringify([data, [...next]]);
      const typing = host.contains(document.activeElement) && document.activeElement?.tagName === "INPUT";
      if (sig !== last && (force || !typing)) { last = sig; paint(data); }
    } finally {
      busy = false;
    }
  }

  function paint(data: { enabled: boolean; apps: AppInfo[] } | null): void {
    if (!data) { host.replaceChildren(note("Lyra's backend is not running, so apps can't be shown yet.")); return; }
    if (!data.enabled) { host.replaceChildren(note("WhatsApp is switched off. Turn it on under Features.")); return; }
    if (!data.apps.length) { host.replaceChildren(note("WhatsApp is not installed.")); return; }
    const shown = data.apps.filter((a) => a.id === "whatsapp");
    if (!shown.length) { host.replaceChildren(note("WhatsApp is not available right now. Turn it on under Features.")); return; }
    host.replaceChildren(...shown.map(appCard));
  }

  const note = (text: string) => h("div", { class: "apps-empty", text });

  function appCard(a: AppInfo): HTMLElement {
    const accent = a.accent || "#4aa8ff";
    const title = h("div", { class: "apps-cardtitle" },
      a.id === "whatsapp"
        ? h("span", { class: "apps-tile", style: `background:${WHATSAPP_BG}` }, whatsappGlyph(17))
        : h("span", { class: "apps-tile", style: `--c:${accent}` }, svg((ICONS as Record<string, string>)[a.icon] ?? ICONS.apps, 15)),
      h("span", { text: a.title }));
    const state = a.connected ? `Connected${a.account ? ` as ${a.account}` : ""}` : a.message || "Not connected";
    const rows: Kid[] = [
      d.item("Status", a.description || null, h("span", { class: `apps-state${a.connected ? " on" : ""}` }, h("i"), h("span", { text: state }))),
    ];

    const inputs = new Map<string, HTMLInputElement>();
    for (const f of a.settings.filter((s) => s.type === "text")) rows.push(textField(a, f, inputs));

    if (!a.connected) {
      const view = views.get(a.id);
      if (view?.type === "qr") rows.push(qrBlock(view));
      else if (view?.type === "message" && view.text && view.text !== a.message) rows.push(h("div", { class: "apps-line", text: view.text }));
      if (a.canConnect) rows.push(connectRow(a, inputs));
    }

    for (const f of a.settings.filter((s) => s.type === "toggle")) {
      rows.push(d.item(f.label, f.hint || null, d.toggle(f.value === true, (v) => {
        void appsPost("/apps/settings", { id: a.id, values: { [f.key]: v } }).then((r) => d.flashSaved(!!r));
      })));
    }
    if (a.connected && a.canConnect) {
      rows.push(d.item("Disconnect", "Unlinks this app and deletes what Lyra stored for it on this PC.",
        d.confirmButton("Disconnect", "Click again to confirm", () => {
          void appsPost("/apps/disconnect", { id: a.id }).then(() => load(true));
        })));
    }
    return d.card(title, ...rows);
  }

  function textField(a: AppInfo, f: AppField, inputs: Map<string, HTMLInputElement>): HTMLElement {
    const input = h("input", { class: "apps-input", type: f.secret ? "password" : "text", placeholder: f.label,
      value: typeof f.value === "string" ? f.value : "", autocomplete: "off", spellcheck: false }) as HTMLInputElement;
    inputs.set(f.key, input);
    input.addEventListener("change", () => {
      void appsPost("/apps/settings", { id: a.id, values: { [f.key]: input.value } }).then((r) => { d.flashSaved(!!r); void load(true); });
    });
    return d.item(f.label, f.hint || null, input);
  }

  function connectRow(a: AppInfo, inputs: Map<string, HTMLInputElement>): HTMLElement {
    const status = h("span", { class: "apps-hint" });
    const b = h("button", { class: "primary", type: "button", text: "Connect" });
    b.addEventListener("click", async () => {
      b.setAttribute("disabled", "true");
      status.textContent = "";
      if (inputs.size) await appsPost("/apps/settings", { id: a.id, values: Object.fromEntries([...inputs].map(([k, i]) => [k, i.value])) });
      const res = await appsPost<{ ok: boolean; url?: string; message?: string }>("/apps/connect", { id: a.id });
      if (res?.url) void Bridge.openUrl(res.url);
      status.textContent = res?.message ?? "Lyra's backend did not answer.";
      b.removeAttribute("disabled");
      window.setTimeout(() => void load(true), 1200);
    });
    return d.item("Connect", null, status, b);
  }

  function qrBlock(v: AppView): HTMLElement {
    const steps = h("ol", { class: "apps-steps" }, ...QR_STEPS.map((s) => h("li", { text: s })));
    return h("div", { class: "apps-qrbox" },
      v.image ? h("img", { class: "apps-qrimg", src: v.image, alt: "QR code to scan", width: 176, height: 176 }) : null,
      h("div", { class: "apps-qrside" },
        h("div", { class: "apps-qrtitle", text: "Scan to link" }),
        v.image ? steps : h("div", { class: "apps-line", text: v.text ?? "" }),
        h("div", { class: "apps-hint", text: "The code refreshes by itself. Keep this window open until it says Connected." })));
  }

  // Refresh while the section is on screen: a QR code expires, a sign-in finishes in another window.
  void load(true);
  visiblePoll(() => load(), { ms: 2500, target: host });   // cleared (not skipped) while the section or window is hidden
  return [host];
}
