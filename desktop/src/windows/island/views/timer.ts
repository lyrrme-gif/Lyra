// Timer tab — Apple Clock style. Two faces:
//   • running : pause / cancel circles on the left, label + big orange time on the right
//   • setup   : Timer · Pomodoro · Stopwatch, a scrubbable tape ruler, big readout, Start
// All control goes through the backend's /timers/control endpoint.

import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import type { ViewHost } from "./views";
import { clockText, colorFor, labelText, ringFraction, type TimerItem, type TimersPayload } from "../timerChip";

const BASE = "http://127.0.0.1:8765";

// ── shared store: island.onTimers() feeds it, the view listens ────────────────
let current: TimersPayload = { items: [], now: Date.now() };
const listeners = new Set<() => void>();
export function setTimerItems(p: TimersPayload) {
  current = p;
  for (const fn of listeners) fn();
}

async function control(body: Record<string, unknown>): Promise<void> {
  try {
    const r = await fetch(`${BASE}/timers/control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => null);
    if (data && Array.isArray(data.items)) setTimerItems({ items: data.items, now: Number(data.now) || Date.now() });
  } catch { /* backend offline: nothing to do */ }
}

type Mode = "timer" | "pomodoro" | "stopwatch";
const GAP = 9;              // px per minute on the tape
const MAX_MIN = 180;

function fmtMinutes(m: number): string {
  const h2 = Math.floor(m / 60), mm = m % 60;
  return h2 > 0 ? `${h2}:${String(mm).padStart(2, "0")}:00` : `${m}:00`;
}

export function buildTimer(): ViewHost {
  let mode: Mode = "timer";
  let minutes = 25;

  // ── running face ────────────────────────────────────────────────────────────
  const pauseBtn = h("button", { class: "tm-pill tm-pause", type: "button", "aria-label": "Pause" });
  const cancelBtn = h("button", { class: "tm-circle tm-cancel", type: "button", "aria-label": "Cancel timer" }, svg("M6.5 6.5l11 11M17.5 6.5l-11 11", 14, { fill: "none", stroke: 2.4 }));
  const runLabel = h("div", { class: "tm-run-label" });
  const runTime = h("div", { class: "tm-run-time" });
  const ringSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  ringSvg.setAttribute("viewBox", "0 0 24 24");
  ringSvg.classList.add("tm-ring");
  const rTrack = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  const rArc = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  for (const c of [rTrack, rArc]) { c.setAttribute("cx", "12"); c.setAttribute("cy", "12"); c.setAttribute("r", "10"); c.setAttribute("fill", "none"); c.setAttribute("stroke", "currentColor"); c.setAttribute("stroke-width", "1.6"); }
  rTrack.setAttribute("opacity", ".2");
  rArc.setAttribute("stroke-linecap", "round");
  const C = 2 * Math.PI * 10;
  rArc.setAttribute("stroke-dasharray", String(C));
  rArc.setAttribute("transform", "rotate(-90 12 12)");
  ringSvg.append(rTrack, rArc);
  // Running face: label, huge digits, one clear primary action, quiet cancel.
  const running = h("div", { class: "tm-face tm-running" },
    h("div", { class: "tm-run-center" }, runLabel, runTime),
    h("div", { class: "tm-run-actions" }, pauseBtn, cancelBtn),
    h("div", { class: "tm-ring-wrap" }, ringSvg),
  );

  // ── setup face ──────────────────────────────────────────────────────────────
  const modeBtns = new Map<Mode, HTMLButtonElement>();
  const modes: Array<[Mode, string]> = [["timer", "Timer"], ["pomodoro", "Pomodoro"], ["stopwatch", "Stopwatch"]];
  const segs = h("div", { class: "tm-seg", role: "tablist" });
  for (const [m, label] of modes) {
    const b = h("button", { class: "tm-seg-btn", type: "button", role: "tab", text: label }) as HTMLButtonElement;
    for (const ev of ["pointerdown", "mousedown"]) b.addEventListener(ev, (e) => e.stopPropagation());
    b.addEventListener("click", (e) => { e.stopPropagation(); mode = m; syncSetup(); });
    modeBtns.set(m, b);
    segs.append(b);
  }

  // tape ruler: ticks every minute, labelled every 5, centre marker
  const tape = h("div", { class: "tm-tape" });
  const strip = h("div", { class: "tm-strip" });
  for (let i = 0; i <= MAX_MIN; i++) {
    const major = i % 5 === 0;
    const t = h("i", { class: major ? "tm-tick major" : "tm-tick", style: `left:${i * GAP}px` });
    if (major && i > 0) t.append(h("b", { text: String(i) }));
    strip.append(t);
  }
  const marker = h("i", { class: "tm-marker" });
  tape.append(strip, marker);

  const readout = h("div", { class: "tm-readout" });
  const startBtn = h("button", { class: "tm-start", type: "button" }) as HTMLButtonElement;
  const hint = h("div", { class: "tm-hint" });
  const setup = h("div", { class: "tm-face tm-setup" }, segs, tape, hint, startBtn, readout);

  const place = (animate: boolean) => {
    strip.style.transition = animate ? "transform .45s cubic-bezier(.34,1.56,.64,1)" : "none";
    strip.style.transform = `translateX(${-minutes * GAP}px)`;
    readout.textContent = mode === "stopwatch" ? "0:00" : fmtMinutes(minutes);
  };
  function syncSetup() {
    for (const [m, b] of modeBtns) b.classList.toggle("on", m === mode);
    setup.dataset.mode = mode;
    startBtn.textContent = mode === "timer" ? "Start Timer" : mode === "pomodoro" ? "Start Focus" : "Start";
    hint.textContent = mode === "pomodoro" ? "25 min focus · 5 min break" : mode === "stopwatch" ? "Counts up until you stop it" : "Drag the ruler to set minutes";
    if (mode === "pomodoro") readout.textContent = "25:00"; else place(true);
  }

  // scrub with pointer + wheel
  let dragging = false, startX = 0, startMin = 0;
  tape.addEventListener("pointerdown", (e) => {
    if (mode !== "timer") return;
    dragging = true; startX = e.clientX; startMin = minutes; tape.setPointerCapture(e.pointerId); e.stopPropagation();
  });
  tape.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const next = Math.max(1, Math.min(MAX_MIN, Math.round(startMin - (e.clientX - startX) / GAP)));
    if (next !== minutes) { minutes = next; place(false); }
  });
  const end = () => { if (dragging) { dragging = false; place(true); } };
  tape.addEventListener("pointerup", end);
  tape.addEventListener("pointercancel", end);
  tape.addEventListener("wheel", (e) => {
    if (mode !== "timer") return;
    e.preventDefault();
    minutes = Math.max(1, Math.min(MAX_MIN, minutes + (e.deltaY + e.deltaX > 0 ? 1 : -1)));
    place(true);
  }, { passive: false });

  startBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (mode === "timer") void control({ action: "start_timer", seconds: minutes * 60 });
    else if (mode === "pomodoro") void control({ action: "start_pomodoro" });
    else void control({ action: "start_stopwatch" });
  });

  // ── running controls ────────────────────────────────────────────────────────
  const primary = (): TimerItem | null => current.items[0] ?? null;
  pauseBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const it = primary();
    if (it) void control({ action: it.state === "paused" ? "resume" : "pause", id: it.id });
  });
  cancelBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const it = primary();
    if (it) void control({ action: "cancel", id: it.id });
  });

  const el = h("div", { class: "view timer-view" }, running, setup);

  let lastKey = "";
  const sync = () => {
    const it = primary();
    el.dataset.face = it ? "running" : "setup";
    if (!it) { syncSetup(); lastKey = ""; return; }
    const accent = colorFor(it);
    el.style.setProperty("--tm-accent", accent);
    el.dataset.paused = it.state === "paused" ? "1" : "0";
    const key = `${it.id}|${it.state}|${it.phase}`;
    if (key !== lastKey) {
      lastKey = key;
      runLabel.textContent = labelText(it);
      pauseBtn.replaceChildren(svg(it.state === "paused" ? ICONS.play : ICONS.pause, 14), h("span", { text: it.state === "paused" ? "Resume" : "Pause" }));
      pauseBtn.setAttribute("aria-label", it.state === "paused" ? "Resume" : "Pause");
    }
    runTime.textContent = clockText(it, Date.now());
    rArc.setAttribute("stroke-dashoffset", String((1 - Math.max(0.001, ringFraction(it, Date.now()))) * C));
  };
  listeners.add(sync);

  return {
    el,
    sync,
    tick() {
      const it = primary();
      if (!it) return;
      const t = clockText(it, Date.now());
      if (runTime.textContent !== t) {
        runTime.textContent = t;
        rArc.setAttribute("stroke-dashoffset", String((1 - Math.max(0.001, ringFraction(it, Date.now()))) * C));
      }
    },
    setActive(on) { if (on) sync(); },
  };
}
