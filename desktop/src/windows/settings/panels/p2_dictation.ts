// Phase 2 — "Dictation" panel: shortcuts, how the text arrives, pill position, local history.
// It also holds the Writing style and Statistics sections (one switcher at the top), and is the
// single entry point main.ts calls (registerP2), which adds the one Dictation tab.

import { segmented } from "../uiKit";
import { whenVisible } from "../lazy";
import { h, clear } from "../../../shared/views/dom";
import {
  PRIVACY_HISTORY, api, button, fmtTime, keyRecorder, loadSettings, saveSettings, select, statusLine, textInput,
  type P2Kit,
} from "./p2_common";
import { stylesPanel } from "./p2_styles";

type Tabs = [string, string][];
type Nav = Array<[string | null, string[]]>;

/**
 * Adds the Dictation tab to the Settings window's tab list and "Assistant" group (the arrays are the ones
 * main.ts owns), and returns the function that builds its panel. Writing style and Statistics
 * are sections inside that one tab.
 */
export function registerP2(tabs: Tabs, nav: Nav): (kit: P2Kit) => Record<string, HTMLElement> {
  const mine: Tabs = [["p2_dictation", "Dictation"]];
  for (const t of mine) if (!tabs.some((x) => x[0] === t[0])) tabs.push(t);
  const group = nav.find(([heading]) => heading === "Assistant") ?? nav[0];
  for (const [id] of mine) if (!group[1].includes(id)) group[1].push(id);
  return (kit) => ({ p2_dictation: dictationPanel(kit) });
}

export function dictationPanel(kit: P2Kit): HTMLElement {
  const { card } = kit;
  const general = h("div", {});                 // keys, how the text arrives, history
  // The others used to be tabs of their own; each is built as before and shown one at a time.
  const sections: Array<[string, string, HTMLElement]> = [
    ["general", "Dictation", general],
    ["styles", "Writing style", stylesPanel(kit)],
  ];
  if (kit.statistics) sections.push(["statistics", "Statistics", h("div", {}, ...kit.statistics())]);   // moved here from its own tab
  for (const [, , el] of sections.slice(1)) el.querySelector("h2")?.remove();   // the tab already has its title
  const show = (id: string) => { for (const [k, , el] of sections) el.hidden = k !== id; };
  const switcher = segmented(sections.map(([k, label]) => [k, label] as [string, string]), "general", show);
  show("general");
  const body = h("div", {}, switcher, ...sections.map(([, , el]) => el));
  const root = kit.panel(
    "Dictation",
    "Hold a key, talk, let go, and your words are pasted into any app. Everything here runs on this PC.",
    body,
  );

  void (async () => {
    const s = await loadSettings();
    const note = statusLine();
    const save = (u: Record<string, unknown>) => saveSettings(kit, u, note);

    // Where the dictation engine stands (Phase 1 engines are optional).
    const engineLine = h("div", { class: "hint", text: "" });
    void api("/p2/status").then((r) => {
      const e = r.data?.engines;
      engineLine.textContent = !r.ok ? "" : e
        ? (e.ready ? `Speech engine ready: ${e.engine}.` : e.downloading ? `Speech engine downloading… ${Math.round((e.progress ?? 0) * 100)}%` : "Speech engine not ready yet — Lyra uses her built-in live recognizer.")
        : "Using Lyra's built-in live recognizer.";
    });

    const holdKey = keyRecorder(String(s.p2_dictation_key ?? "lalt"), "Left Alt", async (combo) => !!(await save({ p2_dictation_key: combo })));

    const shortcuts = card(
      "Keys",
      kit.item("Dictation key", "Click, then press the key (or keys) to hold while you talk, for example Left Alt, F9 or Ctrl + Alt + D. Left Alt is the default.",
        holdKey),
      note.el,
    );

    clear(general);
    general.append(...(kit.extra ?? []), shortcuts, historyCard(kit, s, save));
  })();
  return root;
}

function historyCard(kit: P2Kit, s: Record<string, any>, save: (u: Record<string, unknown>) => Promise<unknown>): HTMLElement {
  const { card, item, toggle, confirmButton } = kit;
  const list = h("div", { class: "p2-history", style: "display:flex;flex-direction:column;gap:.4rem;margin-top:.4rem" });
  const count = h("span", { class: "hint", text: "" });
  const search = textInput("", "Search your dictations", "14rem");
  let timer = 0;

  const row = (e: any): HTMLElement => {
    const text = h("div", { style: "white-space:pre-wrap;word-break:break-word", text: String(e.text) });
    const meta = h("div", { class: "hint", text: `${fmtTime(e.ts)}${e.app ? " · " + e.app : ""} · ${e.words} words` });
    const copy = button("Copy", () => {
      void navigator.clipboard?.writeText(String(e.text)).then(() => { copy.textContent = "Copied"; window.setTimeout(() => (copy.textContent = "Copy"), 1200); }).catch(() => kit.flashSaved(false));
    });
    const del = button("Delete", async () => { await api("/p2/history", { op: "delete", id: e.id }); void refresh(); });
    return h("div", { class: "p2-entry", style: "display:flex;gap:.6rem;justify-content:space-between;align-items:flex-start" },
      h("div", { style: "flex:1;min-width:0" }, text, meta), h("div", { style: "display:flex;gap:.3rem" }, copy, del));
  };

  async function refresh(): Promise<void> {
    const r = await api(`/p2/history?limit=50&q=${encodeURIComponent(search.value.trim())}`);
    clear(list);
    if (!r.ok) { list.append(h("div", { class: "hint", text: r.error || "History unavailable." })); return; }
    count.textContent = `${r.data.count} saved`;
    if (!r.data.items.length) list.append(h("div", { class: "hint", text: search.value ? "Nothing matches." : "No dictations yet." }));
    for (const e of r.data.items) list.append(row(e));
  }
  search.addEventListener("input", () => { window.clearTimeout(timer); timer = window.setTimeout(() => void refresh(), 200); });

  const retention = select(
    [["7", "7 days"], ["30", "30 days"], ["90", "90 days"], ["0", "Forever"]],
    String(s.p2_history_retention_days ?? 30),
    (v) => void save({ p2_history_retention_days: Number(v) }),
  );
  const erased = h("span", { class: "hint", text: "" });

  const c = card(
    "History",
    item("Keep a history", PRIVACY_HISTORY, toggle(s.p2_history_enabled !== false, (v) => void save({ p2_history_enabled: v }))),
    item("Keep entries for", "Older entries are deleted automatically.", retention),
    item("Erase", "Erase history removes every saved dictation. Erase everything also removes your dictionary, replacements, snippets, writing styles, dictation statistics and these settings. Both ask you to confirm.",
      confirmButton("Erase history", "Click again to erase history", async () => {
        const r = await api("/p2/history", { op: "clear", confirm: true });
        erased.textContent = r.ok ? "History erased." : r.error;
        void refresh();
      }),
      confirmButton("Erase everything", "Click again to erase everything", async () => {
        const r = await api("/p2/erase-everything", { confirm: true });
        erased.textContent = r.ok ? "Everything erased. Reopen Settings to see the defaults." : r.error;
        void refresh();
      }),
      erased),
    h("div", { class: "item" }, h("div", { class: "row" }, search, count), list),
  );
  whenVisible(c, () => void refresh());
  return c;
}
