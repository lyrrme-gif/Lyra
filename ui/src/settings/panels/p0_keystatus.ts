// Phase 0: "key stored" status for the LLM card in Settings.
// Reads GET /p0/secrets/status from Lyra's backend: names and where each secret is kept, never a value.
// Built with the same markup as the item() helper in main.ts (div.item > div.row > label + controls),
// so Phase 4's restyle covers it without changes.
import { h } from "../../views/dom";

const LYRA = "http://127.0.0.1:8765";

type Where = "keychain" | "file" | "none";
interface P0Item {
  name: string;
  label: string;
  stored: boolean;
  where: Where;
  secure: boolean;
  dot: "green" | "red";
}
interface P0Status {
  ok: boolean;
  backend: string;
  keychain: boolean;
  items: P0Item[];
  warnings: string[];
}

// The three provider keys shown in this card (the rest, e.g. Home Assistant, are listed by the backend too).
const SHOWN = ["openrouter_api_key", "gemini_api_key", "groq_api_key"];

function dot(color: "green" | "red" | "grey"): HTMLElement {
  const c = color === "green" ? "#22c55e" : color === "red" ? "#f4505e" : "#6b7079";
  return h("i", { class: "dot", style: `background:${c}` });
}

function where(it: P0Item): string {
  if (!it.stored) return "Not stored";
  return it.where === "keychain" ? "Stored in the system keychain" : "Stored in a plain file (no keychain available)";
}

async function fetchStatus(): Promise<P0Status | null> {
  try {
    const r = await fetch(`${LYRA}/p0/secrets/status`);
    if (!r.ok) return null;
    const data = (await r.json()) as P0Status;
    return data && data.ok ? data : null;
  } catch {
    return null; // backend not running, or an older backend without phase 0: show nothing
  }
}

/** Adds a "Key storage" row (green/red dot per provider key) to the LLM card and keeps it fresh. */
export function p0KeyStatus(section: HTMLElement): void {
  const list = h("div", { class: "p0-keys", style: "display:flex;flex-direction:column;gap:4px;align-items:flex-end" });
  const hint = h("div", { class: "hint" });
  const row = h(
    "div",
    { class: "item", "data-s": "key storage keychain stored" },
    h("div", { class: "row" }, h("label", { text: "Key storage" }), list),
    hint,
  );
  row.style.display = "none"; // appears only once the backend answered

  const render = (st: P0Status) => {
    list.replaceChildren();
    for (const name of SHOWN) {
      const it = st.items.find((i) => i.name === name);
      if (!it) continue;
      list.append(
        h("span", { class: "chip" }, dot(it.dot), h("span", { text: `${it.label.replace(" API key", "")}: ${where(it)}` })),
      );
    }
    hint.textContent = st.warnings.length
      ? st.warnings.join(" ")
      : st.keychain
        ? "API keys are kept in your system keychain and are never shown in full."
        : "";
    // The old hint under the card says keys live in llm_keys.json; that is no longer true with a keychain.
    if (st.keychain) {
      for (const el of Array.from(section.querySelectorAll<HTMLElement>(".hint"))) {
        if (el.textContent?.includes("llm_keys.json")) {
          el.textContent = "Keys are stored in your system keychain (Windows Credential Manager) and are never shown in full.";
        }
      }
    }
    row.style.display = list.childElementCount ? "" : "none";
  };

  let timer = 0;
  const refresh = () => {
    window.clearTimeout(timer);
    void fetchStatus().then((st) => st && render(st));
  };

  section.append(row);
  refresh();
  // Save / Clear buttons live in main.ts: re-check shortly after any click inside the card.
  section.addEventListener("click", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(refresh, 700);
  });
}
