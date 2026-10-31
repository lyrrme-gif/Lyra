// Study tab: practise maths (and any other topic) with cards Lyra writes, in a calm, distraction-free layout.
// Same cards Lyra uses by voice ("quiz me on photosynthesis"). All state lives in the backend
// (backend/study.py); this view polls GET /study while it is on screen and posts actions to /study/action.
//
// What is new here:
//   • equations are drawn properly (KaTeX, loaded on first use — see ../study/math.ts)
//   • answers can be typed ("x^2/3", "sqrt(x)" or LaTeX), picked from a symbol palette, or handwritten
//     (the pad in ../study/ink.ts; "Read it" turns it into LaTeX with the vision model, you check it, then Check)
//   • while a quiz runs the tab shows only the problem and the answer area (no title row, no deck list)
//   • a Scratchpad for working things out with live-rendered maths, no quiz needed
//
// The poll used to rebuild the whole tab every 1.5 s. That would wipe a half-drawn answer, so a repaint now
// happens only when the backend state really changed, and the answer area lives on across repaints.

import { visiblePoll, type VisiblePoll } from "../core/visiblePoll";
import { h } from "./dom";
import type { ViewHost } from "./views";
import { answerText, loadKatex, renderRich, renderTex, toLatex } from "../study/math";
import { InkPad } from "../study/ink";
import { PALETTE, insertTemplate, nextSlot } from "../study/palette";

const BASE = "http://127.0.0.1:8765";
let apiProblem = "";

interface Result { verdict: "correct" | "partial" | "wrong"; feedback: string; answer: string; given: string; solution?: string }
interface StudyState {
  ok: boolean; busy: string; error: string;
  focus: { active: boolean; phase: string; state: string; remaining_ms: number | null };
  session: null | {
    deck: string; index: number; total: number; done: boolean; question: string; revealed: boolean;
    answer: string; result: Result | null; right: number; wrong: number; math?: boolean; solution?: string;
  };
  decks: Array<{ id: string; name: string; cards: number; due: number }>;
  stats: { today_min: number; streak_days: number };
  ink?: { latex: string; error: string };
}
type Session = NonNullable<StudyState["session"]>;

async function api(path: string, body?: object): Promise<StudyState | null> {
  try {
    const res = await fetch(`${BASE}${path}`, body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined);
    if (!res.ok) {
      apiProblem = res.status === 404
        ? "Restart Lyra to load Study mode. The running backend does not have the Study endpoint yet."
        : `Study mode could not load (backend returned ${res.status}).`;
      return null;
    }
    const data = await res.json();
    if (data && Array.isArray(data.decks) && data.focus && data.stats) {
      apiProblem = "";
      return data as StudyState;
    }
    apiProblem = "Study mode received an invalid response from Lyra's backend.";
    return null;
  } catch {
    apiProblem = "Study mode cannot reach Lyra's backend. Start Lyra, then try again.";
    return null;
  }
}

const mins = (n: number) => (n >= 60 ? `${Math.floor(n / 60)} h ${n % 60} min` : `${n} min`);
const stop = (e: Event) => e.stopPropagation();
const MATH_TOPICS = ["Algebra", "Quadratic equations", "Derivatives", "Integrals", "Trigonometry", "Limits"];

// ── the answer area ──────────────────────────────────────────────────────────────────────────

interface Composer {
  el: HTMLElement;
  key: string;
  /** The answer as it should be sent to the backend. */
  text(): string;
  focus(): void;
  destroy(): void;
}

interface ComposerOpts {
  key: string;
  math: boolean;
  /** Called with the answer text when the user submits (Enter or Check). Omit for the scratchpad. */
  submit?: (text: string) => void;
  placeholder?: string;
  /** Extra buttons placed after Check. */
  extra?: HTMLElement[];
  /** Ask the backend to read the handwriting. */
  readInk(png: string): Promise<{ latex: string; error: string }>;
  /** Mirrors the typed text into another element (the scratchpad's big preview). */
  onText?: (typed: string) => void;
}

function button(label: string, cls: string, fn: () => void, title = ""): HTMLButtonElement {
  return h("button", {
    class: `study-btn ${cls}`, type: "button", text: label, title: title || undefined,
    onmousedown: (e: Event) => e.preventDefault(),               // keep the caret in the answer field
    onclick: (e: Event) => { stop(e); fn(); },
  }) as HTMLButtonElement;
}

function buildComposer(o: ComposerOpts): Composer {
  // ── plain text answer (non-maths cards): the original one-line box ──
  if (!o.math) {
    const field = h("input", { class: "study-in", type: "text", placeholder: o.placeholder ?? "Type your answer…", maxlength: "300" }) as HTMLInputElement;
    field.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter" && field.value.trim()) o.submit?.(field.value.trim()); });
    field.addEventListener("click", stop);
    const row = h("div", { class: "study-row" }, field,
      o.submit ? button("Check", "primary", () => field.value.trim() && o.submit?.(field.value.trim())) : null,
      ...(o.extra ?? []));
    return { el: h("div", { class: "sm-composer plain" }, row), key: o.key, text: () => field.value.trim(), focus: () => field.focus(), destroy() {} };
  }

  // ── maths answer ──
  const field = h("textarea", {
    class: "study-in sm-field", rows: "1", spellcheck: "false", autocomplete: "off", maxlength: "400",
    placeholder: o.placeholder ?? "Type it: x^2/3, sqrt(x), pi …  or LaTeX",
  }) as HTMLTextAreaElement;

  const preview = h("div", { class: "sm-preview empty" });
  const showPreview = () => {
    const typed = field.value.trim();
    preview.classList.toggle("empty", !typed);
    if (!typed) { preview.textContent = "Your answer is drawn here as you type"; return; }
    const tex = toLatex(typed);
    preview.textContent = tex;
    void loadKatex().then(() => renderTex(preview, tex, true)).catch(() => { /* the plain text stays */ });
  };

  let previewTimer = 0;
  field.addEventListener("input", () => {
    field.style.height = "auto";
    field.style.height = `${Math.min(64, field.scrollHeight)}px`;
    window.clearTimeout(previewTimer);
    previewTimer = window.setTimeout(showPreview, 70);
    o.onText?.(field.value);
  });
  field.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (field.value.trim()) o.submit?.(answerText(field.value)); }
    else if (e.key === "Tab" && !e.shiftKey && nextSlot(field)) e.preventDefault();
  });
  field.addEventListener("click", stop);

  // tabs: Type | Write
  let tab: "type" | "write" = "type";
  let composerEl: HTMLElement | null = null;
  const typePane = h("div", { class: "sm-pane sm-type" }, field);
  const palette = h("div", { class: "sm-palette", hidden: true });
  let paletteBuilt = false;
  let group = PALETTE[0].id;
  const groupRow = h("div", { class: "sm-groups" });
  const keyRow = h("div", { class: "sm-keys" });
  const paintKeys = () => {
    groupRow.replaceChildren(...PALETTE.map((g) => h("button", {
      class: `sm-group${g.id === group ? " on" : ""}`, type: "button", text: g.label,
      onmousedown: (e: Event) => e.preventDefault(), onclick: (e: Event) => { stop(e); group = g.id; paintKeys(); },
    })));
    const g = PALETTE.find((x) => x.id === group) ?? PALETTE[0];
    keyRow.replaceChildren(...g.keys.map((key) => {
      const face = h("span", { class: "sm-keyface", text: key.tex });
      const b = h("button", {
        class: "sm-key", type: "button", title: key.title, "aria-label": key.title,
        onmousedown: (e: Event) => e.preventDefault(),
        onclick: (e: Event) => { stop(e); insertTemplate(field, key.insert); },
      }, face);
      void loadKatex().then(() => renderTex(face, key.tex)).catch(() => { /* the raw LaTeX text stays */ });
      return b;
    }));
  };
  palette.append(groupRow, keyRow);

  // handwriting
  let pad: InkPad | null = null;
  const status = h("span", { class: "sm-status", text: "" });
  let reading = false;
  const readBtn = button("Read it", "primary", () => void read(), "Turn the handwriting into maths you can check");
  const undoBtn = button("Undo", "ghost", () => pad?.undo());
  const clearBtn = button("Clear", "ghost", () => pad?.clear());
  const syncInkButtons = () => {
    const empty = !pad || pad.isEmpty();
    readBtn.disabled = empty || reading; undoBtn.disabled = empty; clearBtn.disabled = empty;
  };
  const writePane = h("div", { class: "sm-pane sm-write", hidden: true });
  const ensurePad = (): InkPad => {
    if (!pad) {
      pad = new InkPad();
      pad.onChange = () => { status.textContent = ""; status.className = "sm-status"; syncInkButtons(); };
      writePane.append(pad.el, h("div", { class: "sm-ink-bar" }, undoBtn, clearBtn, status, readBtn));
      syncInkButtons();
    }
    return pad;
  };

  const note = h("div", { class: "sm-note", hidden: true });
  let noteTimer = 0;
  const flash = (msg: string) => {
    note.textContent = msg; note.hidden = false;
    window.clearTimeout(noteTimer);
    noteTimer = window.setTimeout(() => { note.hidden = true; }, 5000);
  };

  const tabType = h("button", { class: "sm-tab on", type: "button", text: "Type", onclick: (e: Event) => { stop(e); setTab("type"); } });
  const tabWrite = h("button", { class: "sm-tab", type: "button", text: "Write", onclick: (e: Event) => { stop(e); setTab("write"); } });
  const symBtn = h("button", {
    class: "sm-tab sm-sym", type: "button", text: "∑ Symbols", "aria-expanded": "false",
    onmousedown: (e: Event) => e.preventDefault(),
    onclick: (e: Event) => {
      stop(e);
      if (tab !== "type") setTab("type");
      const open = palette.hidden;
      if (open && !paletteBuilt) { paletteBuilt = true; paintKeys(); }
      palette.hidden = !open;
      symBtn.classList.toggle("on", open);
      symBtn.setAttribute("aria-expanded", String(open));
    },
  });
  function setTab(t: "type" | "write") {
    tab = t;
    typePane.hidden = t !== "type"; writePane.hidden = t !== "write";
    tabType.classList.toggle("on", t === "type"); tabWrite.classList.toggle("on", t === "write");
    composerEl?.classList.toggle("writing", t === "write");      // no preview while writing: the room goes to the pad
    if (t === "write") {
      palette.hidden = true; symBtn.classList.remove("on");
      const p = ensurePad();
      requestAnimationFrame(() => p.fit());
    } else field.focus();
  }

  async function read() {
    const png = pad?.toPng();
    if (!png || reading) return;
    reading = true; status.textContent = "Reading your writing…"; status.className = "sm-status"; syncInkButtons();
    const r = await o.readInk(png);
    reading = false;
    if (r.latex) {
      field.value = r.latex;
      field.dispatchEvent(new Event("input"));
      setTab("type");
      status.textContent = "";
      flash("Check the reading, fix anything, then press Check.");
    } else {
      status.textContent = r.error || "I couldn't read that."; status.className = "sm-status bad";
    }
    syncInkButtons();
  }

  const actions = h("div", { class: "study-row sm-actions" },
    o.submit ? button("Check", "primary", () => field.value.trim() && o.submit?.(answerText(field.value))) : null,
    ...(o.extra ?? []));
  showPreview();

  const el = h("div", { class: o.key === "scratch" ? "sm-composer scratch" : "sm-composer" },
    preview,
    h("div", { class: "sm-tabs" }, tabType, tabWrite, h("span", { class: "grow" }), symBtn),
    typePane, palette, writePane, note, actions);
  composerEl = el;
  return {
    el, key: o.key,
    text: () => answerText(field.value),
    focus: () => { if (tab === "type") field.focus(); },
    destroy() { pad?.destroy(); window.clearTimeout(previewTimer); window.clearTimeout(noteTimer); },
  };
}

// ── the view ─────────────────────────────────────────────────────────────────────────────────

let stageShown = false;
/** True while a quiz or the scratchpad is on screen: the island then uses its tall size (the home screen is shorter). */
export function studyStageActive(): boolean { return stageShown; }
/** Island height of the Study home screen (the quiz / scratchpad use the view's full height from core/layout.ts). */
export const STUDY_HOME_H = 300;

export function buildStudy(onResize?: () => void): ViewHost {
  let st: StudyState | null = null;
  let active = false;
  let timer: VisiblePoll | null = null;
  let sending = false;
  let lastKey = "";
  let composer: Composer | null = null;
  let scratch: Composer | null = null;
  let scratchOpen = false;
  let source: "math" | "topic" = "math";
  let topicDraft = "";

  const body = h("div", { class: "study-body" });
  const chip = h("span", { class: "study-chip" });
  const head = h("div", { class: "study-head" }, h("span", { class: "study-title", text: "Study" }), chip);
  const el = h("div", { class: "view study-view" }, head, body);

  const apply = (next: StudyState) => { st = next; paint(); };
  const act = async (action: string, extra: object = {}) => {
    if (sending) return;
    sending = true;
    el.classList.add("is-busy");
    const next = await api("/study/action", { action, ...extra });
    sending = false;
    el.classList.remove("is-busy");
    if (next) apply(next);
  };
  const poll = async () => { const s = await api("/study"); if (s) apply(s); };
  const readInk = async (png: string) => {
    const next = await api("/study/action", { action: "recognize_ink", image: png });
    if (!next) return { latex: "", error: apiProblem || "I could not reach the handwriting reader." };
    return next.ink ?? { latex: "", error: "I could not read that." };
  };

  const disposeComposer = () => { composer?.destroy(); composer = null; };
  const disposeScratch = () => { scratch?.destroy(); scratch = null; };

  // ── pieces ──
  /** A block of text that may hold $…$ formulas. `bare` = the text is a formula without $ signs (what you typed). */
  const rich = (cls: string, text: string, bare = false) => {
    const d = h("div", { class: cls });
    if (bare && text && !text.includes("$")) {
      d.textContent = text;
      void loadKatex().then(() => renderTex(d, text)).catch(() => { /* plain */ });
    } else renderRich(d, text);
    return d;
  };
  const input = (placeholder: string, value: string, onEnter: (v: string) => void, onType: (v: string) => void) => {
    const i = h("input", { class: "study-in", type: "text", placeholder, maxlength: "200" }) as HTMLInputElement;
    i.value = value;
    i.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter" && i.value.trim()) onEnter(i.value.trim()); });
    i.addEventListener("input", () => onType(i.value));
    i.addEventListener("click", stop);
    return i;
  };

  const verdictLine = (r: Result) => {
    const label = r.verdict === "correct" ? "Correct" : r.verdict === "partial" ? "Partly right" : "Not quite";
    return h("div", { class: `study-res ${r.verdict}`, text: `${label}${r.feedback ? " · " + r.feedback : ""}` });
  };

  const solutionBlock = (text: string) => {
    if (!text) return null;
    const inner = h("div", { class: "sm-steps-body" });
    renderRich(inner, text);
    return h("details", { class: "sm-steps" }, h("summary", { text: "Show the steps" }), inner);
  };

  /** Worked solution as a numbered list ("1. …", "Step 2: …"), open and in large type: the maths stage's main content. */
  const stepsBlock = (text: string) => {
    const lines = (text || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    if (!lines.length) return null;
    const wrap = h("div", { class: "sm-solution" }, h("div", { class: "sm-solution-h", text: "Steps:" }));
    let ol: HTMLOListElement | null = null;
    for (const line of lines) {
      const m = line.match(/^(?:step\s*)?(\d+)\s*[.):]\s*(.*)$/i);
      if (m) {
        if (!ol) { ol = h("ol", { class: "sm-ol" }) as HTMLOListElement; wrap.append(ol); }
        const li = h("li", { class: "sm-li" }) as HTMLLIElement;
        li.value = Number(m[1]);
        renderRich(li, m[2]);
        ol.append(li);
      } else {
        ol = null;
        const p = h("div", { class: "sm-sol-p" });
        renderRich(p, line);
        wrap.append(p);
      }
    }
    return wrap;
  };

  /** The line under the answer box, like the assistant's own footer: voice is push-to-talk (Right Ctrl). */
  const voiceHint = () => h("div", { class: "sm-hint" }, "Type or hold ", h("kbd", { text: "Right Ctrl" }), " to speak");

  // ── screens ──
  const stage = (s: Session): Node[] => {
    const math = !!s.math;
    const q = h("div", { class: "study-q" });
    renderRich(q, s.question);
    const stageHead = h("div", { class: "sm-stagehead" },
      h("span", { class: "sm-orb", "aria-hidden": "true" }),
      h("span", { class: "sm-deck", text: s.deck }),
      h("span", { class: "grow" }),
      h("span", { class: "sm-count", text: `${s.index + 1} / ${s.total}` }),
      h("button", { class: "sm-exit", type: "button", title: "Leave this set", "aria-label": "Leave this set", text: "×", onclick: (e: Event) => { stop(e); void act("stop"); } }));
    const done = Math.round(((s.index + (s.result ? 1 : 0)) / Math.max(1, s.total)) * 100);
    const bar = h("div", { class: "sm-progress" }, h("i", { style: `width:${done}%` }));
    // `lead` scrolls (the problem, then the verdict and the steps); `foot` stays pinned (answer box or the Next buttons).
    const lead: Node[] = [q];
    let foot: Node[];

    if (s.result) {
      disposeComposer();
      const r = s.result;
      lead.push(verdictLine(r));
      if (r.verdict !== "correct") {
        if (r.given) lead.push(h("div", { class: "sm-line" }, h("span", { class: "sm-tag", text: "You" }), rich("sm-val", r.given, math)));
        lead.push(h("div", { class: "sm-line" }, h("span", { class: "sm-tag", text: "Answer" }), rich("sm-val", r.answer)));
      }
      const steps = math ? stepsBlock(r.solution ?? "") : solutionBlock(r.solution ?? "");
      if (steps) lead.push(steps);
      foot = [h("div", { class: "study-row sm-actions" }, button(s.index + 1 >= s.total ? "Finish" : "Next", "primary", () => act("next")))];
    } else if (s.revealed) {
      disposeComposer();
      lead.push(h("div", { class: "sm-line" }, h("span", { class: "sm-tag", text: "Answer" }), rich("sm-val", s.answer)));
      const steps = math ? stepsBlock(s.solution ?? "") : solutionBlock(s.solution ?? "");
      if (steps) lead.push(steps);
      foot = [h("div", { class: "study-row sm-actions" },
        button("I knew it", "primary", () => act("mark", { correct: true })),
        button("Missed it", "ghost", () => act("mark", { correct: false })))];
    } else {
      const key = `${s.deck}|${s.index}|${s.question}`;
      if (!composer || composer.key !== key) {
        disposeComposer();
        composer = buildComposer({
          key, math, readInk,
          submit: (text) => act("answer", { text }),
          extra: [button("Show", "ghost", () => act("reveal"), "Show the answer"), button("🔊", "ghost", () => act("speak"), "Read it aloud")],
        });
        window.setTimeout(() => composer?.focus(), 30);
      }
      foot = [composer.el, voiceHint()];
    }
    el.classList.toggle("is-math", math);
    return [stageHead, bar, h("div", { class: `sm-stage${math ? " math" : ""}` }, h("div", { class: "sm-left" }, ...lead), h("div", { class: "sm-right" }, ...foot))];
  };

  const finished = (s: Session): Node[] => {
    disposeComposer();
    const total = s.right + s.wrong;
    return [
      h("div", { class: "sm-done" },
        h("div", { class: "sm-score", text: total ? `${s.right} of ${total} right` : "Set finished" }),
        h("div", { class: "study-note", text: "Missed cards come back sooner." }),
        h("div", { class: "study-row" }, button("Done", "primary", () => act("stop")))),
    ];
  };

  const scratchpad = (): Node[] => {
    disposeComposer();
    const EMPTY = "Write or type maths on the right. It is drawn here.";
    const big = h("div", { class: "sm-bigpreview empty", text: EMPTY });
    let typed = "";
    const refresh = () => {
      const tex = toLatex(typed.trim());
      big.classList.toggle("empty", !typed.trim());
      if (!typed.trim()) { big.textContent = EMPTY; return; }
      big.textContent = tex;
      void loadKatex().then(() => renderTex(big, tex, true)).catch(() => { /* plain */ });
    };
    if (!scratch) {
      scratch = buildComposer({
        key: "scratch", math: true, readInk, placeholder: "Try: (x+1)/(x-1)   or   \\int_0^1 x^2\\,dx",
        onText: (t) => { typed = t; refresh(); },
        extra: [button("Copy LaTeX", "ghost", () => {
          const tex = toLatex(typed.trim());
          if (tex) void navigator.clipboard?.writeText(tex).catch(() => { /* clipboard not available */ });
        }, "Copy the LaTeX for your notes")],
      });
    }
    return [
      h("div", { class: "sm-stagehead" },
        h("button", { class: "sm-exit", type: "button", title: "Back", "aria-label": "Back", text: "×", onclick: (e: Event) => { stop(e); scratchOpen = false; disposeScratch(); paint(true); } }),
        h("span", { class: "sm-deck", text: "Scratchpad" })),
      h("div", { class: "sm-stage math" }, h("div", { class: "sm-left" }, big), h("div", { class: "sm-right" }, scratch.el)),
    ];
  };

  const home = (): Node[] => {
    disposeComposer();
    const f = st!.focus;
    const topic = input(
      source === "math" ? "Practise… (e.g. integration by parts)" : "Quiz me on… (e.g. the French Revolution)",
      topicDraft,
      (v) => act("start", { source, topic: v }),
      (v) => { topicDraft = v; });
    const seg = h("div", { class: "sm-seg", role: "tablist" },
      ...(["math", "topic"] as const).map((k) => h("button", {
        class: `sm-segbtn${source === k ? " on" : ""}`, type: "button", role: "tab", text: k === "math" ? "Maths" : "Flashcards",
        onclick: (e: Event) => { stop(e); source = k; paint(true); },
      })));
    const kids: Node[] = [
      h("div", { class: "study-row" }, seg, topic, button("Start", "primary", () => topic.value.trim() && act("start", { source, topic: topic.value.trim() }))),
    ];
    if (source === "math") {
      kids.push(h("div", { class: "sm-chips" }, ...MATH_TOPICS.map((t) => h("button", {
        class: "sm-chipbtn", type: "button", text: t, onclick: (e: Event) => { stop(e); topicDraft = ""; void act("start", { source: "math", topic: t }); },
      }))));
    }
    kids.push(h("div", { class: "study-row" },
      button("Scratchpad", "ghost", () => { scratchOpen = true; paint(true); }, "Work things out with live maths"),
      button("From clipboard", "ghost", () => act("start", { source: "clipboard" })),
      button(f.active ? "Focus running" : "Start focus", "ghost", () => !f.active && act("start_focus"))));
    if (st!.error) kids.push(h("div", { class: "study-res wrong", text: st!.error }));
    const decks = st!.decks.slice(-4).reverse();
    if (decks.length) {
      kids.push(h("div", { class: "study-decks" }, ...decks.map((d) =>
        h("button", { class: "study-deck", type: "button", onclick: (e: Event) => { stop(e); void act("start", { source: "deck", deck_id: d.id, only_due: d.due > 0 }); } },
          h("span", { class: "study-deckname", text: d.name }), h("span", { class: "study-due", text: d.due ? `${d.due} due` : `${d.cards} cards` })))));
    }
    return kids;
  };

  /** Repaint only when something the screen shows has really changed (or `force`). */
  const paint = (force = false) => {
    if (!st) { body.replaceChildren(h("span", { class: "study-note", text: apiProblem || "Connecting to Study mode…" })); lastKey = ""; return; }
    const f = st.focus;
    chip.textContent = f.active ? `Focus on · ${mins(st.stats.today_min)} today` : `${mins(st.stats.today_min)} today`;
    chip.classList.toggle("on", f.active);

    const key = JSON.stringify([st.busy, st.error, st.session, st.decks, scratchOpen, source, f.active]);
    if (!force && key === lastKey) return;
    lastKey = key;

    const s = st.session;
    const inQuiz = !!s && !s.done && !st.busy;
    el.classList.toggle("is-stage", inQuiz || scratchOpen);
    if (stageShown !== (inQuiz || scratchOpen)) { stageShown = inQuiz || scratchOpen; onResize?.(); }
    if (!inQuiz && !scratchOpen) el.classList.remove("is-math");

    if (st.busy) { disposeComposer(); body.replaceChildren(h("span", { class: "study-note sm-working", text: st.busy })); return; }
    if (s && !s.done) { scratchOpen = false; disposeScratch(); body.replaceChildren(...stage(s)); return; }
    if (s && s.done) { body.replaceChildren(...finished(s)); return; }
    if (scratchOpen) { body.replaceChildren(...scratchpad()); window.setTimeout(() => scratch?.focus(), 30); return; }
    disposeScratch();
    body.replaceChildren(...home());
  };

  const onChanged = () => { if (active) void poll(); };
  window.addEventListener("lyra:study-changed", onChanged);

  return {
    el,
    sync() { paint(); },
    focus() {
      if (composer) composer.focus();
      else if (scratch) scratch.focus();
      else el.querySelector<HTMLInputElement>(".study-in")?.focus();
    },
    setActive(on) {
      active = on;
      timer?.stop(); timer = null;
      if (on) timer = visiblePoll(() => poll(), { ms: 1500 });   // runs at once; cleared while the window is hidden
      else { disposeScratch(); scratchOpen = false; lastKey = ""; stageShown = false; }
    },
  };
}
