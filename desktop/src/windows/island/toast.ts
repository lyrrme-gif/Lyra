// Notification toast: a Discord / WhatsApp / Telegram message, a call or a reminder
// shown inside the compact notch (340×40) for a few seconds. Same black notch, bold
// white text and 32px rounded icon tile as the Spotify compact view.

import { h, svg } from "../../shared/views/dom";

export type NotifyKind = "message" | "call" | "reminder" | "dictation";

export interface NotifyEvent {
  app: string;
  kind: NotifyKind;
  sender: string;
  text: string;
  /** Backend clock, ms. Old events replayed by the bridge's buffer are dropped. */
  ts: number;
  /** How long to show it (Settings > Island). Missing = the island's default. */
  durationMs?: number;
  /** The conversation it belongs to (WhatsApp through Lyra's link): a click opens that chat on the notch. */
  chat?: string;
}

interface AppLook {
  color: string;
  /** Protocol that opens/focuses the desktop app. Fixed here on purpose: the URL is
   *  never taken from the event, so nothing posted to the bridge can open a link. */
  url: string | null;
}

const APPS: Record<string, AppLook> = {
  discord: { color: "#5865f2", url: "discord://" },
  whatsapp: { color: "#25d366", url: "whatsapp://" },
  telegram: { color: "#26a5e4", url: "tg://" },
  reminder: { color: "#ff455c", url: null },
  dictation: { color: "#0a84ff", url: null },
};
// Official brand marks (simple-icons, 24×24), drawn white on the brand-coloured tile.
const BRAND: Record<string, string> = {
  discord: "M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z",
  whatsapp: "M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z",
  telegram: "M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z",
};

const FALLBACK: AppLook = { color: "#3a3b40", url: null };

// Material-style glyphs, drawn white on the app-coloured tile.
const GLYPH: Record<NotifyKind, string> = {
  message: "M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z",
  call:
    "M6.62 10.79a15.05 15.05 0 0 0 6.59 6.59l2.2-2.2a1 1 0 0 1 1.02-.24c1.12.37 2.33.57 3.57.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1.02l-2.2 2.2z",
  reminder:
    "M12 22a2 2 0 0 0 2-2h-4a2 2 0 0 0 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4a1.5 1.5 0 0 0-3 0v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z",
  dictation:
    "M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z",
};
// "Apply polished version" toast (dictation): a check applies it, a cross dismisses it.
const CHECK = "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z";
const CROSS = "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z";

export function appUrl(app: string): string | null {
  return (APPS[app] ?? FALLBACK).url;
}

export interface Toast {
  el: HTMLElement;
  /** Call buttons: wired by the island. */
  onAnswer?: () => void;
  onDecline?: () => void;
  /** Fill the toast with this notification (does not show it). */
  set(ev: NotifyEvent): void;
}

export function buildToast(): Toast {
  const tile = h("div", { id: "notify-toast-tile" });
  const badge = h("span", { id: "notify-toast-badge" }, svg(GLYPH.call, 8));
  const sender = h("div", { id: "notify-toast-sender" });
  const text = h("div", { id: "notify-toast-text" });
  const col = h("div", { id: "notify-toast-col" }, sender, text);
  const callDeclineIcon = svg("M12 9c-1.6 0-3.1.3-4.5.7-.9.3-1.5 1.1-1.5 2v2.3c0 .5.4.9.9.9.1 0 .2 0 .3-.1l2.4-.8c.4-.1.7-.5.7-.9v-1.4c.5-.2 1.1-.3 1.7-.3s1.2.1 1.7.3v1.4c0 .4.3.8.7.9l2.4.8c.1.1.2.1.3.1.5 0 .9-.4.9-.9v-2.3c0-.9-.6-1.7-1.5-2C15.1 9.3 13.6 9 12 9z", 16);
  const callAnswerIcon = svg(GLYPH.call, 15);
  const decline = h("button", { id: "notify-decline", type: "button", "aria-label": "Decline" }, callDeclineIcon);
  const answer = h("button", { id: "notify-answer", type: "button", "aria-label": "Answer" }, callAnswerIcon);
  const actions = h("div", { id: "notify-call-actions" }, decline, answer);
  const el = h("div", { id: "notify-toast" }, tile, col, actions);
  const api: Toast = {
    el,
    set: () => undefined,
  };
  decline.addEventListener("click", (e) => { e.stopPropagation(); api.onDecline?.(); });
  answer.addEventListener("click", (e) => { e.stopPropagation(); api.onAnswer?.(); });
  Object.assign(api, {
    set(ev: NotifyEvent) {
      const look = APPS[ev.app] ?? FALLBACK;
      tile.style.background = look.color;
      // Brand logo for known apps (a phone badge marks calls); generic glyph otherwise.
      const brand = BRAND[ev.app];
      tile.replaceChildren(svg(brand ?? GLYPH[ev.kind] ?? GLYPH.message, brand ? 20 : 18));
      if (brand && ev.kind === "call") tile.append(badge);
      tile.dataset.kind = ev.kind;
      el.dataset.kind = ev.kind;
      // The two round buttons: answer / decline a call, or apply / dismiss a polished dictation.
      const dictation = ev.kind === "dictation";
      decline.replaceChildren(dictation ? svg(CROSS, 16) : callDeclineIcon);
      answer.replaceChildren(dictation ? svg(CHECK, 16) : callAnswerIcon);
      answer.setAttribute("aria-label", dictation ? "Apply polished version" : "Answer");
      decline.setAttribute("aria-label", dictation ? "Dismiss" : "Decline");
      answer.title = dictation ? "Apply polished version" : "";
      // Updated in place while already showing: replay the pop so it reads as new.
      if (el.classList.contains("on")) for (const a of tile.getAnimations?.() ?? []) { a.cancel(); a.play(); }
      const who = ev.sender || (ev.kind === "reminder" ? "Reminder" : ev.app);
      const what = ev.text || (ev.kind === "call" ? "Incoming call" : "");
      sender.textContent = who;
      text.textContent = what;
      sender.title = who;
      text.title = what;
      text.style.display = what ? "block" : "none";
    },
  });
  return api;
}
