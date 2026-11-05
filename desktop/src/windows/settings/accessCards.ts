// Settings → Features: the things that can act on, or read from, your PC.
//   • Screen guide: what Lyra may do (point only / ask before each click / whole task).
//   • Google Calendar & Gmail: sign in (read-only), see the state, disconnect.
// The on/off switches are the normal Features rows; this file adds the details.

import { whenVisible } from "./lazy";
import { h } from "../../shared/views/dom";

type PrefVal = boolean | number | string;
export interface AccessDeps {
  prefs: { data: Record<string, PrefVal>; set: (key: string, value: PrefVal) => void; flush: () => Promise<boolean> };
  card: (title: string | null, ...children: Array<Node | string | null | undefined | false>) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: Array<Node | string | null | undefined | false>) => HTMLElement;
  lyra: string; // backend base URL
}

const OK = "#22c55e";
const BAD = "#f4505e";

/** Why the last call failed: "offline" (nothing answered) or the HTTP status the backend sent back. */
let lastFail: "offline" | number = "offline";

async function api<T>(lyra: string, path: string, body?: unknown): Promise<T | null> {
  try {
    const r = await fetch(`${lyra}${path}`, body === undefined ? undefined
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { lastFail = r.status; return null; }
    return (await r.json()) as T;
  } catch {
    lastFail = "offline";
    return null; // nothing answered
  }
}

/** The honest reason a call failed. A running backend that answers 404 is an old version that lacks this feature. */
function down(): string {
  if (lastFail === "offline") return "Lyra's backend isn't running.";
  if (lastFail === 404) return "Lyra's backend is running but is an old version (it doesn't know this feature). Restart Lyra after updating.";
  if (lastFail === 403) return "Lyra's backend refused this request (403).";
  return `Lyra's backend answered with an error (${lastFail}).`;
}

function note(el: HTMLElement, text: string, ok: boolean | null = null): void {
  el.textContent = text;
  el.style.color = ok === null ? "" : ok ? OK : BAD;
}

const LEVELS: Array<[string, string]> = [
  ["look", "Show only — she points, never clicks"],
  ["step", "One step at a time — she asks before each click"],
  ["auto", "Whole task — one go-ahead, then she does it"],
];

function guideCard({ prefs, card, item }: AccessDeps): HTMLElement {
  const sel = h("select", {}) as HTMLSelectElement;
  for (const [v, label] of LEVELS) sel.append(h("option", { value: v, text: label }));
  const cur = String(prefs.data.guide_level ?? "look");
  sel.value = LEVELS.some(([v]) => v === cur) ? cur : "look";
  sel.addEventListener("change", () => prefs.set("guide_level", sel.value));
  return card("Screen guide — what Lyra may do",
    item("Level", "Then say “how do I … in Chrome”, followed by “do it for me”, or “open the Privacy tab for me on screen”. She finds buttons by their name, shows her cursor, and re-checks the screen after every click.", sel),
  );
}

function googleCard({ card, item, lyra }: AccessDeps): HTMLElement {
  const status = h("span", { class: "hint", style: "margin-left:10px" });
  const connect = h("button", { class: "btn", type: "button", text: "Connect Google" }) as HTMLButtonElement;
  const disconnect = h("button", { class: "btn", type: "button", text: "Disconnect", style: "margin-left:8px" }) as HTMLButtonElement;
  const refresh = async () => {
    const st = await api<{ connected: boolean; has_client_secret: boolean; libs: boolean; folder: string }>(lyra, "/google/status");
    if (!st) { note(status, down(), false); connect.disabled = true; disconnect.disabled = true; return; }
    disconnect.disabled = !st.connected;
    connect.disabled = !st.libs || !st.has_client_secret;
    connect.textContent = st.connected ? "Sign in again" : "Connect Google";
    if (st.connected) note(status, "Connected (read-only).", true);
    else if (!st.libs) note(status, "Needs: pip install google-auth google-auth-oauthlib", false);
    // Optional feature, nothing is broken: say so in the normal text colour, not as a red error.
    else if (!st.has_client_secret) note(status, "Optional, not set up. To use it, put client_secret.json in the folder below (see GOOGLE_SETUP.md).", null);
    else note(status, "Not connected.", null);
  };
  connect.addEventListener("click", async () => {
    connect.disabled = true;
    const r = await api<{ message: string }>(lyra, "/google/connect", {});
    note(status, r?.message ?? down(), r ? null : false);
    window.setTimeout(() => void refresh(), 15000); // the browser sign-in finishes on its own
  });
  disconnect.addEventListener("click", async () => {
    if (!window.confirm("Disconnect Google? Lyra will forget the sign-in and ask Google to cancel her access.")) return;
    const r = await api<{ message: string }>(lyra, "/google/disconnect", {});
    await refresh();
    if (r) note(status, r.message, true);
  });
  const googleBox = card("Google Calendar & Gmail",
    item("Sign in", "Lyra asks for two read-only permissions: view your calendar and read your email. She can't send, delete or change anything, and a reply is only ever drafted as text. Emails you ask about are sent to your AI provider to be summarised.",
      h("span", {}, connect, disconnect, status)),
  );
  whenVisible(googleBox, () => void refresh());   // first look at the tab, not window open
  return googleBox;
}

export function accessCards(deps: AccessDeps): HTMLElement[] {
  return [guideCard(deps), googleCard(deps)];
}
