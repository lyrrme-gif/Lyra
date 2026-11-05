// Settings → Files: which folders Lyra may search, what to skip, and indexing progress.
// Helpers (card, item, ...) live in main.ts, so main.ts passes them in.
import { h, clear } from "../../shared/views/dom";
import { visiblePoll } from "../../shared/core/visiblePoll";

type Kid = Node | string | null | undefined | false;
export interface FilesDeps {
  card: (title: Kid, ...children: Kid[]) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: Kid[]) => HTMLElement;
  lyra: (path: string, body?: unknown) => Promise<{ ok: boolean; data: any; error: string }>;
  flashSaved: (ok?: boolean) => void;
}

export function filesHost(d: FilesDeps): HTMLElement {
  const { card, item, lyra, flashSaved } = d;
  const host = h("div", {});
  const statusEl = h("div", { class: "hint" });
  const errEl = h("div", { class: "hint", style: "color:#f4505e" });

  const showError = (msg: string) => {
    errEl.textContent = msg;
  };

  const renderStatus = async () => {
    const r = await lyra("/files/status");
    if (!r.ok || r.data.ok === false) {
      statusEl.textContent = r.error || r.data.error || "Could not read the index.";
      return;
    }
    const s = r.data;
    const base = `${s.files} file${s.files === 1 ? "" : "s"} indexed.`;
    if (s.phase === "indexing" && s.total) statusEl.textContent = `Indexing… ${s.done} of ${s.total} (${s.current})`;
    else if (s.phase === "scanning") statusEl.textContent = `Looking for new and changed files… ${base}`;
    else if (!s.enabled) statusEl.textContent = `File search is off (Features). ${base}`;
    else statusEl.textContent = s.running ? base : `${base} The indexer starts when Lyra restarts.`;
  };

  const act = async (path: string, body: unknown = {}) => {
    showError("");
    const r = await lyra(path, body);
    if (!r.ok) showError(r.error || r.data.error || "That didn't work.");
    flashSaved(r.ok);
    void load();
    return r;
  };

  const load = async () => {
    const r = await lyra("/files/folders");
    clear(host);
    if (!r.ok) {
      host.append(card("Files", h("p", { class: "empty", text: r.error || "Could not load the folder list." })));
      return;
    }
    const { folders, exclude, default: isDefault } = r.data as {
      folders: { path: string; exists: boolean }[]; exclude: string[]; default: boolean;
    };

    const reindex = h("button", { text: "Re-index now" });
    reindex.addEventListener("click", () => void act("/files/reindex"));
    host.append(card("Search your files",
      item("Index", "Lyra reads text from txt, md, pdf, docx and pptx files in the folders below and keeps the index on this PC. " +
        "“Answer from my files” sends the best matching passages to your AI provider.", h("span", { class: "spacer" }), reindex),
      h("div", { class: "item" }, statusEl, errEl)));

    const list = card(isDefault ? "Folders (defaults: Documents and Desktop)" : "Folders");
    const group = list.querySelector(".group") as HTMLElement;
    if (!folders.length) group.append(h("p", { class: "empty", text: "No folders: nothing is indexed." }));
    for (const f of folders) {
      const rm = h("button", { class: "danger", text: "Remove" });
      rm.addEventListener("click", () => void act("/files/folders/remove", { path: f.path }));
      group.append(item(f.path, f.exists ? null : "Not found right now (kept until you remove it).", h("span", { class: "spacer" }), rm));
    }
    const pick = h("button", { text: "Choose folder…" });
    pick.addEventListener("click", () => void act("/files/folders/pick"));
    const input = h("input", { type: "text", placeholder: "or paste a folder path", style: "min-width:220px" }) as HTMLInputElement;
    const add = h("button", { text: "Add" });
    add.addEventListener("click", () => {
      if (input.value.trim()) void act("/files/folders/add", { path: input.value.trim() });
    });
    group.append(item("Add a folder", null, h("span", { class: "spacer" }), input, add, pick));
    host.append(list);

    const ex = card("Never index");
    const exGroup = ex.querySelector(".group") as HTMLElement;
    for (const p of exclude) {
      const rm = h("button", { class: "danger", text: "Remove" });
      rm.addEventListener("click", () => void act("/files/exclude/remove", { pattern: p }));
      exGroup.append(item(p, null, h("span", { class: "spacer" }), rm));
    }
    const exIn = h("input", { type: "text", placeholder: "folder name, path or *.tmp", style: "min-width:220px" }) as HTMLInputElement;
    const exAdd = h("button", { text: "Add" });
    exAdd.addEventListener("click", () => {
      if (exIn.value.trim()) void act("/files/exclude/add", { pattern: exIn.value.trim() });
    });
    exGroup.append(item("Skip", "Hidden folders, node_modules, venv and Lyra's own data are always skipped.",
      h("span", { class: "spacer" }), exIn, exAdd));
    host.append(ex);
    void renderStatus();
  };

  void load();
  // Index progress is polled every 5 s only while the Files panel is on screen and the window is visible;
  // otherwise there is no timer at all (core/visiblePoll.ts). It runs once when the panel opens.
  visiblePoll(renderStatus, { ms: 5000, target: host });
  return host;
}
