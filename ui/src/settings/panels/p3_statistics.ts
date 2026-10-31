// Settings → Statistics (Phase 3): how much you dictate, drawn from the local log in
// backend/dictation_stats.py. Counters, a words-per-day chart, where you dictate, a friendly empty state.
//
// Styling: this file only sets inline styles built from the existing CSS variables (with fallbacks), plus
// stable `p3-*` class names, so Phase 4 can restyle everything from CSS without touching this file.

import { h } from "../../views/dom";
import { visiblePoll } from "../../core/visiblePoll";
import {
  axisLabel, barGeometry, barTitle, formatCount, formatDuration, labelEvery, streakLine,
  type SeriesPoint,
} from "./p3_statsFormat";

type Kid = Node | string | null | undefined | false;

export interface StatsDeps {
  card: (title: Kid, ...children: Kid[]) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: Kid[]) => HTMLElement;
  toggle: (on: boolean, onChange: (v: boolean) => void) => HTMLElement;
  confirmButton: (label: string, confirmLabel: string, onConfirm: () => void, cls?: string) => HTMLElement;
  flashSaved: (ok?: boolean) => void;
  lyra: (path: string, body?: unknown) => Promise<{ ok: boolean; data: any; error: string }>;
  /** Backend base URL, used only for app icons. */
  base: string;
}

interface Summary {
  ok: boolean;
  range: "week" | "month" | "all";
  enabled: boolean;
  has_data: boolean;
  ever_dictated: boolean;
  typing_wpm: number;
  totals: { words: number; dictations: number; seconds: number; wpm: number; saved_seconds: number; avg_words: number };
  today: { words: number; dictations: number };
  streak: { current: number; longest: number };
  series: { granularity: "day" | "month"; points: SeriesPoint[] };
  apps: { app: string; words: number; dictations: number; share: number; other?: boolean }[];
}

const SVG_NS = "http://www.w3.org/2000/svg";
const INK = "var(--ink, #f5f6f8)";
const DIM = "var(--dim, #9398a1)";
const DIM2 = "var(--dim-2, #6b7079)";
const SURFACE = "var(--surface, rgba(255,255,255,.045))";
const HAIR = "var(--hairline, rgba(255,255,255,.07))";
const BAR = "var(--p3-bar, #7c86ff)";
const BAR_TODAY = "var(--p3-bar-today, #a9b0ff)";

const RANGES: [string, string][] = [["week", "Week"], ["month", "Month"], ["all", "All time"]];
const PRIVACY_NOTE = "Statistics never leave this computer. Lyra keeps only counts (how many words, how long, which app); never what you said, and never any audio.";

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number> = {}): SVGElementTagNameMap[K] {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}

export function statsCards(d: StatsDeps): HTMLElement[] {
  let range: Summary["range"] = "week";
  let ticket = 0;                       // drops answers that arrive after a newer request
  const icons = new Map<string, string | null>();

  const counters = h("div", { class: "p3-counters", style: "display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-top:14px" });
  const streakEl = h("div", { class: "p3-streak", style: `font-size:12.5px;color:${DIM};margin-top:12px` });
  const statusEl = h("div", { class: "p3-status", hidden: true });
  const chartHost = h("div", { class: "p3-chart" });
  const appsHost = h("div", { class: "p3-apps" });
  const enabledSwitch = d.toggle(true, (on) => void setEnabled(on));

  // ── period picker (the same segmented <select> the rest of Settings uses) ──
  const select = h("select", { "data-seg": "1", "aria-label": "Period" }) as HTMLSelectElement;
  for (const [v, label] of RANGES) select.append(h("option", { value: v, text: label }));
  select.value = range;
  select.addEventListener("change", () => { range = select.value as Summary["range"]; void load(); });

  // ── drawing ──
  function counter(label: string, value: string, sub: string): HTMLElement {
    return h("div", { class: "p3-counter", style: `background:${SURFACE};border-radius:12px;padding:12px 14px;min-width:0` },
      h("div", { class: "p3-counter-label", text: label, style: `font-size:11.5px;color:${DIM};letter-spacing:.02em` }),
      h("div", { class: "p3-counter-value", text: value, style: `font-size:21px;font-weight:650;color:${INK};margin-top:2px;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis` }),
      h("div", { class: "p3-counter-sub", text: sub, style: `font-size:11.5px;color:${DIM2};margin-top:2px` }),
    );
  }

  function renderCounters(s: Summary) {
    const t = s.totals;
    const per = range === "week" ? "this week" : range === "month" ? "last 30 days" : "since you started";
    counters.replaceChildren(
      counter("Words", formatCount(t.words), s.today.words ? `${formatCount(s.today.words)} today` : per),
      counter("Dictations", formatCount(t.dictations), t.dictations ? `${formatCount(t.avg_words)} words on average` : per),
      counter("Speed", t.wpm ? `${formatCount(t.wpm)} wpm` : "–", t.wpm ? `typing: about ${s.typing_wpm} wpm` : "needs a few timed dictations"),
      counter("Time saved", formatDuration(t.saved_seconds), `vs typing at ${s.typing_wpm} wpm`),
    );
    streakEl.textContent = streakLine(s.streak.current, s.streak.longest);
    streakEl.prepend(h("span", { text: s.streak.current > 0 ? "🔥 " : "", "aria-hidden": "true" }));
  }

  function emptyState(title: string, text: string): HTMLElement {
    const art = el("svg", { viewBox: "0 0 64 64", width: 56, height: 56, "aria-hidden": "true" });
    art.append(
      el("rect", { x: 24, y: 8, width: 16, height: 30, rx: 8, fill: "none", stroke: "currentColor", "stroke-width": 3 }),
      el("path", { d: "M14 30a18 18 0 0 0 36 0M32 48v8M24 56h16", fill: "none", stroke: "currentColor", "stroke-width": 3, "stroke-linecap": "round" }),
    );
    return h("div", { class: "p3-empty", style: `text-align:center;padding:26px 12px;color:${DIM}` },
      h("div", { style: `color:${DIM2};display:flex;justify-content:center;margin-bottom:8px` }, art),
      h("div", { text: title, style: `font-size:14px;font-weight:600;color:${INK};margin-bottom:4px` }),
      h("div", { text, style: "font-size:12.5px;line-height:1.5;max-width:380px;margin:0 auto" }),
    );
  }

  function renderChart(s: Summary) {
    const pts = s.series.points;
    if (!s.ever_dictated) {
      chartHost.replaceChildren(emptyState("Nothing here yet", "Hold your dictation shortcut in any app and start talking. Your first words will show up here, and the chart fills in day by day."));
      return;
    }
    if (!s.has_data) {
      chartHost.replaceChildren(emptyState("Quiet stretch", `You haven't dictated ${range === "week" ? "in the last 7 days" : "in the last 30 days"}. Try “All time” to see everything.`));
      return;
    }
    const W = 640, H = 210, padL = 38, padB = 26, padT = 14, plotW = W - padL - 6, plotH = H - padB - padT;
    const { bars, max } = barGeometry(pts, plotW, plotH, padT);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img", preserveAspectRatio: "xMidYMid meet" });
    svg.setAttribute("aria-label", `Words per ${s.series.granularity}, ${formatCount(s.totals.words)} in total`);
    svg.style.cssText = "display:block;max-height:240px;overflow:visible";
    for (const frac of [0, 0.5, 1]) {                       // gridlines: 0, half, full
      const y = padT + plotH - frac * plotH;
      svg.append(el("line", { x1: padL, x2: W - 6, y1: y, y2: y, stroke: frac === 0 ? "rgba(255,255,255,.14)" : "rgba(255,255,255,.06)", "stroke-width": 1 }));
      const t = el("text", { x: padL - 8, y: y + 3.5, "text-anchor": "end", "font-size": 10.5, fill: "currentColor", opacity: 0.55 });
      t.textContent = formatCount(max * frac);
      svg.append(t);
    }
    const g = s.series.granularity;
    const every = labelEvery(pts.length);
    const last = pts.length - 1;
    const plot = el("g", { transform: `translate(${padL},0)` });
    for (const b of bars) {
      const isToday = b.index === last && g === "day";
      const title = el("title");
      title.textContent = barTitle(b.point, g);
      const grp = el("g");
      grp.append(title);
      grp.append(b.h > 0
        ? el("rect", { x: b.x, y: b.y, width: b.w, height: b.h, rx: Math.min(5, b.w / 2.4), fill: isToday ? BAR_TODAY : BAR })
        : el("rect", { x: b.x, y: padT + plotH - 2, width: b.w, height: 2, rx: 1, fill: "rgba(255,255,255,.12)" }));
      // a wide invisible hit area so thin bars are easy to hover
      grp.append(el("rect", { x: b.x - 3, y: padT, width: b.w + 6, height: plotH, fill: "transparent" }));
      plot.append(grp);
      if ((last - b.index) % every === 0) {
        const t = el("text", { x: b.x + b.w / 2, y: H - 8, "text-anchor": "middle", "font-size": 10.5, fill: "currentColor", opacity: isToday ? 0.9 : 0.55 });
        t.textContent = axisLabel(b.point.key, g, pts.length);
        plot.append(t);
      }
    }
    svg.append(plot);
    chartHost.style.color = DIM;
    chartHost.replaceChildren(svg);
  }

  function appRow(a: Summary["apps"][number]): HTMLElement {
    const pct = Math.max(0.01, a.share);
    const badge = h("span", { class: "p3-app-badge", text: (a.app[0] ?? "?").toUpperCase(),
      style: `width:26px;height:26px;border-radius:7px;display:inline-flex;align-items:center;justify-content:center;flex:none;background:${SURFACE};color:${DIM};font-size:12px;font-weight:600;overflow:hidden` });
    if (!a.other && a.app !== "Unknown") void iconFor(a.app).then((src) => {
      if (src) badge.replaceChildren(h("img", { src, alt: "", style: "width:100%;height:100%;object-fit:contain" }));
    });
    return h("div", { class: "p3-app", style: "display:flex;align-items:center;gap:10px;padding:7px 0" },
      badge,
      h("div", { style: "flex:1;min-width:0" },
        h("div", { style: "display:flex;justify-content:space-between;gap:10px;font-size:13px" },
          h("span", { text: a.app, style: `color:${INK};overflow:hidden;text-overflow:ellipsis;white-space:nowrap` }),
          h("span", { text: `${formatCount(a.words)} ${a.words === 1 ? "word" : "words"} · ${Math.round(a.share * 100)}%`, style: `color:${DIM};white-space:nowrap;font-variant-numeric:tabular-nums` }),
        ),
        h("div", { style: `height:5px;border-radius:3px;background:${HAIR};margin-top:5px;overflow:hidden` },
          h("div", { style: `height:100%;width:${(pct * 100).toFixed(1)}%;border-radius:3px;background:${a.other ? DIM2 : BAR}` })),
      ),
    );
  }

  async function iconFor(name: string): Promise<string | null> {
    if (icons.has(name)) return icons.get(name) ?? null;
    icons.set(name, null);
    try {
      const r = await fetch(`${d.base}/app/icon?name=${encodeURIComponent(name)}`);
      const j = r.ok ? await r.json() : {};
      icons.set(name, typeof j.icon === "string" ? j.icon : null);
    } catch { /* no icon: the letter badge stays */ }
    return icons.get(name) ?? null;
  }

  function renderApps(s: Summary) {
    if (!s.has_data) {
      appsHost.replaceChildren(emptyState("No apps yet", "Once you dictate, you'll see here which apps you talk into most."));
      return;
    }
    appsHost.replaceChildren(...s.apps.map(appRow));
  }

  function render(s: Summary) {
    statusEl.replaceChildren(...(s.enabled ? [] : [
      h("div", { class: "notice warn", text: "Statistics are paused: new dictations aren't being counted. Turn counting back on under Privacy below.", style: "margin-bottom:10px" }),
    ]));
    statusEl.hidden = s.enabled;
    enabledSwitch.classList.toggle("on", s.enabled);
    enabledSwitch.setAttribute("aria-pressed", String(s.enabled));
    renderCounters(s);
    renderChart(s);
    renderApps(s);
  }

  function offline(error: string) {
    counters.replaceChildren();
    streakEl.textContent = "";
    chartHost.replaceChildren();
    appsHost.replaceChildren();
    statusEl.hidden = false;
    statusEl.replaceChildren(h("div", { class: "notice warn", text: error || "Could not reach Lyra. Is the backend running?", style: "margin-bottom:10px" }));
  }

  async function load(): Promise<void> {
    const mine = ++ticket;
    const r = await d.lyra(`/dictation/stats?range=${range}`);
    if (mine !== ticket) return;
    if (!r.ok || !r.data || r.data.ok === false) { offline(r.error || String(r.data?.error ?? "")); return; }
    render(r.data as Summary);
  }

  async function setEnabled(on: boolean): Promise<void> {
    const r = await d.lyra("/dictation/stats/enabled", { enabled: on });
    d.flashSaved(r.ok);
    void load();
  }

  async function reset(): Promise<void> {
    const r = await d.lyra("/dictation/stats/clear", {});
    d.flashSaved(r.ok);
    void load();
  }

  // Fresh numbers while the panel is on screen (a dictation may finish while Settings is open).
  void load();
  visiblePoll(() => load(), { ms: 8000, target: chartHost });   // cleared (not skipped) while the panel or window is hidden

  const privacy = h("div", { class: "p3-private", style: `display:flex;gap:8px;align-items:flex-start;font-size:12px;line-height:1.5;color:${DIM};margin-top:4px` },
    h("span", { text: "🔒", "aria-hidden": "true" }), h("span", { text: PRIVACY_NOTE }));

  return [
    d.card("Your dictation",
      statusEl,
      d.item("Period", "Streaks always count every day, whatever period you pick.", select),
      counters,
      streakEl,
    ),
    d.card("Words per day", chartHost),
    d.card("Where you dictate", appsHost),
    d.card("Privacy",
      d.item("Count my dictations", "Turn off to stop adding to these numbers. What's already there stays until you reset it.", enabledSwitch),
      d.item("Reset statistics", "Deletes every counter, the chart and the streak. This can't be undone.",
        d.confirmButton("Reset…", "Click again to delete", () => void reset())),
      privacy,
    ),
  ];
}
