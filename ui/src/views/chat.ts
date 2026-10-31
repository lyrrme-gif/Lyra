// Chat tab: the "Search or ask Lyra" pill on the island. Answers also appear as subtitles at the bottom
// of the screen (caption window). Double-click the pill to open / close this session's conversation under
// it; rich search results appear here alone. Nothing is kept: closing the tab and reopening it starts empty.

import { h, svg, clear } from "./dom";
import { ICONS } from "./icons";
import { Sound } from "../core/sound";
import { State } from "../core/state";
import { sendChat } from "../lyra/client";
import { Bridge } from "../core/bridge";
import "./chatExtra.css";
import "./searchDeck.css";
import { isSending, setSending, pushUser } from "../lyra/chatBus";
import { Chats } from "../lyra/chatStore";
import { buildDeck, buildPeek, deckEnter, deckStep, openNth, PEEK_H } from "./searchDeck";
import type { ViewHost } from "./views";
import type { SearchResult } from "../core/state";

function contextChip(label: string): HTMLElement {
  const chip = h("div", { class: "chip" }, h("i", { class: "chip-dot" }), h("span", { text: label }));
  requestAnimationFrame(() => chip.classList.add("settled"));
  return chip;
}

export function buildPrompt(onHeightChange: () => void): ViewHost {
  const chipRow = h("div", { class: "chip-row" });
  const stateLine = h("div", { class: "chat-state-line" });
  const empty = h("div", { class: "chat-empty cx-empty" });
  const quickRow = h("div", { class: "chat-quick-row" });
  // Holds the rich search results while there are any; empty otherwise.
  const log = h("div", { class: "chat-log" });
  const input = h("input", {
    type: "text",
    class: "chat-input",
    placeholder: "Search or ask Lyra",
    spellcheck: "false",
  }) as HTMLInputElement;
  const send = h("button", { class: "send-btn", title: "Send" }, svg(ICONS.arrowUp, 11));
  const mic = h("button", { class: "mic-btn", type: "button", title: "Talk to Lyra", "aria-label": "Talk to Lyra" }, svg(ICONS.mic, 15, { stroke: 2 }));
  mic.addEventListener("click", (e) => {
    e.stopPropagation();
    void fetch("http://127.0.0.1:8765/voice/toggle", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => undefined);
  });
  const extra = h("div", { class: "cx-extra" });
  const wave = h("span", { class: "cx-wave", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i"), h("i"));
  const bar = h("div", { class: "chat-bar cx-bar" },
    input,
    wave,
    mic,
    send,
  );

  const closeBtn = h("button", { class: "cx-close", type: "button", title: "Close", "aria-label": "Close conversation" }, svg(ICONS.xmark, 13));
  closeBtn.addEventListener("click", (e) => { e.stopPropagation(); window.dispatchEvent(new CustomEvent("lyra-collapse")); });
  const cardEl = h("div", { class: "card chat-card" }, closeBtn, h("div", { class: "chat-body" },
    stateLine, chipRow, empty, quickRow, log, bar, extra,
  ));
  const el = h("div", { class: "view" }, cardEl);

  let renderedKey = "";
  let hadResults = false;

  const quickActions: Array<[string, string]> = [
    ["Search the web", "Search the web for "],
    ["Open an app", "Open "],
    ["Set a timer", "Set a timer for "],
    ["What’s playing?", "What is playing right now?"],
  ];
  for (const [label, prompt] of quickActions) {
    const b = h("button", { class: "chat-quick", type: "button", text: label });
    b.addEventListener("click", () => {
      input.value = prompt;
      input.focus();
      if (!prompt.endsWith(" ")) submit();
    });
    quickRow.append(b);
  }

  // ── This session's conversation, under the pill (the island grows a bit to fit, see State.chatExtra) ──
  type Turn = { q: string; a: string; ts: number; search?: SearchResult };
  let panel: "compose" | "chat" = "compose";
  let convo: Turn[] = [];
  let lastConvo: Turn[] = [];   // the conversation of the previous opening: a double-click on the empty pill brings it back
  let lastSeen = Chats.last()?.id ?? 0;   // answers that already existed when the tab opened are not part of it
  const MAX_TURNS = 6, MAX_PX = 172;   // + 26 = the same 198 px the island has always had room for under the pill
  const SEARCH_JOIN_MS = 12_000;       // a search and the sentence Lyra says about it belong to one turn

  /** A fresh Chat tab: no conversation, nothing typed. What was said is kept for a double-click. */
  function resetSession() {
    if (convo.length) lastConvo = convo;
    convo = [];
    panel = "compose";
    lastSeen = Chats.last()?.id ?? 0;
    input.value = "";
    bar.classList.remove("has-text");
    renderExtra();
  }

  function renderExtra() {
    clear(extra);
    let px = 0;
    if (panel === "chat" && convo.length) {
      const list = h("div", { class: "cx-convo" });
      let total = 0;
      for (const turn of convo.slice(-MAX_TURNS)) {
        if (turn.q) { list.append(h("div", { class: "cx-q", text: turn.q })); total += 36; }
        if (turn.a) {
          const lines = turn.a.split("\n").reduce((n, l) => n + Math.max(1, Math.ceil(l.length / 56)), 0);
          const ah = Math.max(40, Math.min(132, lines * 19 + 18));   // includes the bubble padding
          const a = h("div", { class: "cx-a", text: turn.a });
          a.style.maxHeight = `${ah}px`;
          list.append(a);
          total += ah + 6;
        }
        if (turn.search) {
          const result = turn.search;
          list.append(buildPeek(result, () => window.dispatchEvent(new CustomEvent("lyra:reopen-search", { detail: result }))));
          total += PEEK_H + 6;
        }
      }
      const cap = Math.min(total, MAX_PX + (convo.some((t) => t.search) ? PEEK_H : 0));
      list.style.maxHeight = `${cap}px`;
      extra.append(list);
      px = cap + 26;
      window.requestAnimationFrame(() => { list.scrollTop = list.scrollHeight; });
    }
    cardEl.classList.toggle("cx-extra-on", px > 0);
    if (State.chatExtra !== px) { State.chatExtra = px; onHeightChange(); }
  }
  const open = () => State.view === "prompt" && State.mode === "expanded";
  const trim = () => { if (convo.length > MAX_TURNS) convo = convo.slice(-MAX_TURNS); };

  // Lyra's answer arrives while Chat is open: it joins the conversation and opens under the pill.
  Chats.onChange(() => {
    const ex = Chats.last();
    if (!ex || ex.id === lastSeen) return;
    lastSeen = ex.id;
    if (!open()) return;   // Chat is closed: it starts empty when opened
    const prev = convo[convo.length - 1];
    if (prev?.search && !prev.a && ex.ts - prev.ts < SEARCH_JOIN_MS) { prev.a = ex.a; if (!prev.q) prev.q = ex.q; }   // the sentence that goes with a search card
    else convo.push({ q: ex.q, a: ex.a, ts: ex.ts });
    trim();
    if (!State.searchResult) { panel = "chat"; renderExtra(); }
  });
  // A search came in: the deck is shown by the island; the conversation keeps a small card that reopens it.
  window.addEventListener("lyra:search-turn", (e) => {
    const result = (e as CustomEvent<SearchResult>).detail;
    if (!result || !open()) return;
    const prev = convo[convo.length - 1];
    if (prev && !prev.search && Date.now() - prev.ts < SEARCH_JOIN_MS) prev.search = result;
    else convo.push({ q: Chats.pending(), a: "", ts: Date.now(), search: result });
    trim();
  });

  /** Ask Lyra something from a button (related searches, Web <-> Videos): the same path as typing it. */
  function ask(text: string) {
    if (isSending()) return;
    input.value = text;
    submit();
  }

  function submit() {
    const query = input.value.trim();
    if (!query || isSending()) return;
    Chats.user(query);   // paired with the answer (spoken questions come from the backend feed)
    input.value = "";
    bar.classList.remove("has-text");
    setSending(true);
    Sound.play("send");
    pushUser(query);
    onHeightChange();
    sendChat(query);
    input.focus();
  }

  input.addEventListener("input", () => bar.classList.toggle("has-text", !!input.value.trim()));
  const focusInput = () => {
    void Bridge.focusWindow(true);
    window.setTimeout(() => input.focus(), 0);
  };
  for (const target of [bar, cardEl]) {
    target.addEventListener("click", (e) => {
      const t = e.target as HTMLElement;
      if (t.closest("button, a, .search-results-block")) return;
      focusInput();
    });
  }
  // Double-click the pill (not a button, and not while there is text to select) to open / close the conversation.
  // After the tab was closed and opened again it is empty: the double-click brings back the last conversation.
  // ONE listener, on the card: the bar sits inside the card, so a second listener on the bar would fire the same
  // double-click twice (open, then close again).
  cardEl.addEventListener("dblclick", (e) => {
    const t = e.target as HTMLElement;
    if (t.closest("button, a, .search-results-block, .cx-extra")) return;
    if (input.value.trim() || State.searchResult) return;
    if (!convo.length) {
      if (!lastConvo.length) return;
      convo = lastConvo.slice(-MAX_TURNS);
      lastConvo = [];
      panel = "chat";
    } else {
      panel = panel === "chat" ? "compose" : "chat";
    }
    e.preventDefault();
    renderExtra();
  });
  // 1-4 opens that result while a deck is on screen; Up/Down move the highlight, Enter opens it.
  window.addEventListener("keydown", (e) => {
    const res = State.searchResult;
    if (!res || State.view !== "prompt" || State.mode !== "expanded" || e.ctrlKey || e.metaKey || e.altKey) return;
    if ((e.target as HTMLElement | null)?.closest?.("input, textarea")) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { if (deckStep(e.key === "ArrowDown" ? 1 : -1)) e.preventDefault(); return; }
    if (e.key === "Enter") { if (deckEnter()) e.preventDefault(); return; }
    const n = Number(e.key);
    if (n >= 1 && n <= 4 && openNth(res, n)) e.preventDefault();
  });
  send.addEventListener("click", () => submit());
  input.addEventListener("keydown", (e) => {
    if ((e as KeyboardEvent).key === "Enter") {
      e.preventDefault();
      submit();
    }
    e.stopPropagation();
  });

  return {
    el,
    sync() {
      const file = State.droppedFile;
      const wantChip = file?.name ?? "";
      if (chipRow.dataset.label !== wantChip) {
        chipRow.dataset.label = wantChip;
        clear(chipRow);
        if (wantChip) chipRow.append(contextChip(wantChip));
      }

      // Closing the tab (or leaving Chat) empties it: the next opening is the empty pill again (a double-click on
      // it brings the last conversation back). A deck taking over only folds the conversation up; when the deck is
      // closed the conversation is there again, with the search as a card in it.
      if (State.view !== "prompt" || State.mode !== "expanded") {
        if (panel !== "compose" || convo.length || State.chatExtra || input.value) resetSession();
        hadResults = false;
      } else if (State.searchResult) {
        hadResults = true;
        if (panel !== "compose" || State.chatExtra) { panel = "compose"; renderExtra(); }
      } else if (hadResults) {
        hadResults = false;
        if (convo.length) { panel = "chat"; renderExtra(); }
      }
      const res = State.searchResult;
      const hasResults = !!res && (res.items.length > 0 || !!res.card);
      cardEl.classList.toggle("cx-small", !hasResults);
      const isl = cardEl.closest<HTMLElement>("#island");
      if (isl) {
        isl.classList.toggle("chat-small", !hasResults && State.view === "prompt");
        if (!isl.dataset.chatClick) {
          isl.dataset.chatClick = "1";
          // The whole resting pill is the text field: a click anywhere on it starts typing.
          isl.addEventListener("click", (e) => {
            if (!isl.classList.contains("chat-small") || State.view !== "prompt" || State.mode !== "expanded") return;
            if ((e.target as HTMLElement).closest("button, a")) return;
            focusInput();
          });
        }
      }
      empty.hidden = hasResults;
      quickRow.hidden = hasResults;
      bar.style.display = hasResults ? "none" : ""; // results are shown alone
      stateLine.hidden = true;
      stateLine.textContent = "";

      const renderKey = hasResults ? `r:${res!.title}:${res!.provider ?? ""}:${res!.card?.title ?? ""}:${res!.items.map((i) => i.url || i.label).join("|")}:${(res!.related ?? []).join("|")}` : "none";
      if (renderKey !== renderedKey) {
        renderedKey = renderKey;
        clear(log);
        if (hasResults) {
          log.append(buildDeck(res!, ask));
          log.scrollTop = 0;
        }
      }
      log.classList.toggle("sd-busy", hasResults && isSending());

      input.placeholder = "Search or ask Lyra";
      input.disabled = isSending();
    },
    focus() {
      input.focus();
      input.select();
    },
  };
}
