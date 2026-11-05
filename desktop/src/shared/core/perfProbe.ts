// Per-window performance probe: answers "WHAT inside this WebView2 page is using the CPU / GPU?".
//
// The backend log (dbg.py) can only say "msedgewebview2.exe[12328] 130%". This file runs inside every
// window (island, caption, settings, wheel, mascot) and reports what that page is doing, as
// `ui:<window>:perf*` lines in the same [trace] stream, next to the process numbers:
//
//   perf        page rAF callbacks/s (how many animation loops the page runs), main-thread busy % split into
//               script / style+layout / paint, the slowest script, and whether the window is visible/focused
//   perf-anim   running CSS/Web animations: which element, endless or not, which ones animate costly properties
//   perf-timer  setInterval / requestAnimationFrame callbacks per second, by the line that created them
//   perf-heavy  elements the GPU has to blur / blend / keep on their own layer (with the actual CSS value),
//               canvases (with the size they are DRAWN at vs SHOWN at), videos, SVG animations
//
// Cost control (v2): the probe adds NO animation loop of its own (v1 counted frames with its own rAF loop,
// which inflated every window to "60 frames/s" and kept the compositor ticking). It only counts the page's own
// callbacks. The expensive DOM scan (perf-heavy) runs once ~8 s after start, then once a minute, or on demand
// from DevTools with  window.__lyraProbeScan() . Everything is off with LYRA_TRACE=0 / localStorage lyra-trace=0.

import { isDebugPaused, isParked, isReallyVisible, onVisibilityChange } from "./visibility";
import { onTraceOff } from "./trace";

type TraceFn = (tag: string, msg?: string) => void;
type DescribeFn = (el: Element | null) => string;

const SAMPLE_MS = 5000;
const BUSY_PCT = 8;
const QUIET_REPORT_MS = 30_000;       // a quiet window says something at least this often
const ANIM_EVERY_MS = 30_000;         // animation / timer detail when nothing is busy
const SCAN_FIRST_MS = 8_000;
const SCAN_EVERY_MS = 60_000;
const MAX_LINE = 380;                 // trace.ts cuts lines at 400 characters

/** CSS properties that make the browser re-layout / re-paint / re-blur on every frame. */
const EXPENSIVE = /^(filter|backdrop-filter|-webkit-backdrop-filter|box-shadow|text-shadow|width|height|top|left|right|bottom|margin.*|padding.*|border.*|font-size|clip-path|mask.*)$/i;

interface Loaf { duration: number; renderStart: number; styleAndLayoutStart: number; startTime: number; scripts?: LoafScript[] }
interface LoafScript { duration: number; sourceURL?: string; sourceFunctionName?: string; invoker?: string; invokerType?: string; sourceCharPosition?: number }

const base = (u: string): string => (u.split("?")[0].split("/").pop() || u).slice(0, 40);
const clip = (s: string): string => (s.length > MAX_LINE ? s.slice(0, MAX_LINE - 1) + "…" : s);

/** `div#island.lyra-char`: a short selector with no text and no ancestors (describe() walks up to the nearest
 *  clickable ancestor, which made every filtered element look like "div#island[view=overview] ..."). */
function sel(el: Element): string {
  let s = el.tagName.toLowerCase();
  if (el.id) s += "#" + el.id.slice(0, 24);
  const cls = Array.from(el.classList).filter((c) => !/^(on|off|in|out|active|hover|focus|show|hide|open|visible|is-.*)$/i.test(c)).slice(0, 2);
  if (cls.length) s += "." + cls.join(".");
  return s.slice(0, 40);
}

/** "frame character.ts:58" out of a stack; works for dev (src/x.ts:12:3) and bundled builds. */
function siteFromStack(stack: string | undefined): string {
  if (!stack) return "?";
  const hits: string[] = [];
  for (const line of stack.split("\n").slice(1)) {
    if (/perfProbe|trace\.(ts|js)/.test(line)) continue;
    const m = /([^\s/()]+\.(?:ts|js|tsx|mjs)):(\d+):(\d+)/.exec(line);
    if (m) {
      const fn = /at\s+([^\s(]+)\s+\(/.exec(line)?.[1];
      hits.push(`${fn && fn !== "Object.<anonymous>" ? fn + " " : ""}${m[1].slice(0, 36)}:${m[2]}`);
      if (hits.length === 2) break;           // the site AND who called it ("ensureRunning < onCursor")
    }
  }
  return hits.length ? hits.join(" < ") : "?";
}

function top(map: Map<string, number>, n: number, unit = "x"): string {
  return [...map.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => `${k} ${unit === "x" ? "x" + v : Math.round(v) + unit}`).join(" | ");
}

/** One finished sampling window of the probe (see onPerfSample). */
export interface PerfSample {
  ms: number; rafRate: number; busy: number; scriptPct: number; renderPct: number; layoutPct: number;
  loafCount: number; worstMs: number; worstWho: string;
  anims: number; endless: number; heavy: number; rafSites: string;
}
const perfListeners = new Set<(s: PerfSample) => void>();
/** The Settings log asks for every sample (the probe measures anyway; this only passes the numbers on). */
export function onPerfSample(cb: (s: PerfSample) => void): () => void {
  perfListeners.add(cb);
  return () => { perfListeners.delete(cb); };
}

export function installPerfProbe(trace: TraceFn, describe: DescribeFn): void {
  try {
    const win = window as unknown as Record<string, unknown>;
    if (win.__lyraPerfProbe) return;
    win.__lyraPerfProbe = true;
    void describe; // kept in the signature for callers; the probe uses its own short selectors

    // ----- the page's own timers / animation loops, counted by the line that created them -----
    const intervals = new Map<number, { site: string; ms: number; runs: number }>();
    const rafSites = new Map<string, number>();
    let rafCalls = 0;

    const realSetInterval = window.setInterval.bind(window) as (h: TimerHandler, ms?: number, ...a: unknown[]) => number;
    (window as unknown as { setInterval: unknown }).setInterval = ((handler: TimerHandler, ms?: number, ...args: unknown[]): number => {
      if (typeof handler !== "function") return realSetInterval(handler, ms, ...args);
      const rec = { site: siteFromStack(new Error().stack), ms: ms ?? 0, runs: 0 };
      const id = realSetInterval((...a: unknown[]) => { rec.runs++; return (handler as (...x: unknown[]) => unknown)(...a); }, ms, ...args);
      intervals.set(id, rec);
      return id;
    }) as typeof window.setInterval;
    const realClearInterval = window.clearInterval.bind(window);
    window.clearInterval = ((id?: number): void => { if (id !== undefined) intervals.delete(id); realClearInterval(id); }) as typeof window.clearInterval;

    let stopped = false;
    const realRaf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = ((cb: FrameRequestCallback): number => {
      rafCalls++;
      // a stack costs real time at 60 fps: look at one call in 30 only
      if (rafCalls % 30 === 0) { const s = siteFromStack(new Error().stack); rafSites.set(s, (rafSites.get(s) ?? 0) + 30); }
      return realRaf(cb);
    }) as typeof window.requestAnimationFrame;

    // ----- Long Animation Frame API: where the main thread's time went -----
    let scriptMs = 0, renderMs = 0, layoutMs = 0, loafCount = 0, worstMs = 0, worstWho = "";
    const scriptBy = new Map<string, number>();
    try {
      const loaf = new PerformanceObserver((list) => {
        for (const e of list.getEntries() as unknown as Loaf[]) {
          loafCount++;
          const end = e.startTime + e.duration;
          let s = 0;
          for (const sc of e.scripts ?? []) {
            s += sc.duration;
            const pos = sc.sourceCharPosition && sc.sourceCharPosition > 0 ? "#" + sc.sourceCharPosition : "";
            const who = `${sc.invoker || sc.sourceFunctionName || sc.invokerType || "?"} @${sc.sourceURL ? base(sc.sourceURL) : "?"}${pos}`;
            scriptBy.set(who, (scriptBy.get(who) ?? 0) + sc.duration);
            if (sc.duration > worstMs) { worstMs = sc.duration; worstWho = who; }
          }
          scriptMs += s;
          if (e.renderStart > 0) renderMs += Math.max(0, end - e.renderStart);
          if (e.styleAndLayoutStart > 0) layoutMs += Math.max(0, end - e.styleAndLayoutStart);
        }
      });
      loaf.observe({ type: "long-animation-frame", buffered: false } as PerformanceObserverInit);
      onTraceOff(() => loaf.disconnect());
    } catch { /* older WebView2: no long-animation-frame; the other lines still work */ }

    // ----- animations (cheap: asks the browser for its list, no DOM scan) -----
    const animLine = (): { line: string; endless: number; running: number; heavy: number } => {
      const all = document.getAnimations?.() ?? [];
      const groups = new Map<string, { n: number; endless: boolean; heavy: string; target: string }>();
      let endless = 0, running = 0, heavy = 0;
      for (const a of all) {
        if (a.playState !== "running") continue;
        running++;
        const eff = a.effect as KeyframeEffect | null;
        const isEndless = eff?.getComputedTiming?.().iterations === Infinity;
        if (isEndless) endless++;
        let props: string[] = [];
        try {
          props = [...new Set((eff?.getKeyframes?.() ?? []).flatMap((k) => Object.keys(k)).filter((p) => !["offset", "easing", "composite", "computedOffset"].includes(p)))];
        } catch { /* ignore */ }
        const css = a as CSSAnimation & CSSTransition;
        const name = css.animationName ? `anim:${css.animationName}` : css.transitionProperty ? `trans:${css.transitionProperty}` : "web-animation";
        const bad = props.filter((p) => EXPENSIVE.test(p.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())));
        const isHeavy = bad.length > 0 || EXPENSIVE.test(css.transitionProperty ?? "");
        if (isHeavy) heavy++;
        const tgt = eff?.target ? sel(eff.target as Element) : "?";
        const key = `${name} on ${tgt}`;
        const g = groups.get(key) ?? { n: 0, endless: isEndless, heavy: isHeavy ? (bad.join("+") || css.transitionProperty || "") : "", target: tgt };
        g.n++;
        groups.set(key, g);
      }
      const rows = [...groups.entries()].sort((a, b) => b[1].n - a[1].n).slice(0, 6)
        .map(([k, g]) => `${k} x${g.n}${g.endless ? " ENDLESS" : ""}${g.heavy ? " HEAVY(" + g.heavy + ")" : ""}`);
      return { line: clip(`${running} running (${endless} endless, ${heavy} costly): ${rows.join(" || ")}`), endless, running, heavy };
    };

    // ----- elements the GPU has to blur / blend / keep on their own layer (DOM scan: rare) -----
    // The scan reads getComputedStyle for thousands of elements. Done in one go it froze the window for
    // 120-150 ms (the LAG lines), so it now works in ~6 ms slices between timers, and is skipped entirely
    // while the window is parked / hidden (nobody is looking, and the numbers would be meaningless).
    let scanning = false;
    const SLICE_MS = 6;

    const scan = (): void => {
      if (scanning || !isReallyVisible()) return;
      scanning = true;
      const t0 = performance.now();
      const groups = new Map<string, number>();
      const els = document.body ? Array.from(document.body.querySelectorAll("*")).slice(0, 5000) : [];
      let i = 0, slices = 0, busyMs = 0;
      let nFilter = 0, nBackdrop = 0, nWill = 0, nBlend = 0, nShadow = 0, nHiddenHeavy = 0;

      const work = (): void => {
        try {
          if (!isReallyVisible()) { scanning = false; return; }   // parked mid-scan: drop it
          const s0 = performance.now();
          slices++;
          while (i < els.length && performance.now() - s0 < SLICE_MS) {
            const el = els[i++];
            if (!el.isConnected) continue;
            const cs = getComputedStyle(el);
            const f = cs.filter;
            const bf = cs.backdropFilter || (cs as unknown as Record<string, string>).webkitBackdropFilter;
            const hasF = !!f && f !== "none";
            const hasB = !!bf && bf !== "none";
            const wc = cs.willChange && cs.willChange !== "auto" ? cs.willChange : "";
            const mb = cs.mixBlendMode && cs.mixBlendMode !== "normal";
            // big soft shadows are re-rasterised whenever the element moves
            const sh = cs.boxShadow && cs.boxShadow !== "none" ? Math.max(0, ...(cs.boxShadow.match(/(\d+(?:\.\d+)?)px/g) ?? []).map((m) => parseFloat(m))) >= 24 : false;
            if (!hasF && !hasB && !wc && !mb && !sh) continue;
            const r = el.getBoundingClientRect();
            const shown = r.width >= 2 && r.height >= 2 && cs.visibility !== "hidden" && cs.display !== "none" && parseFloat(cs.opacity || "1") > 0.01;
            if (!shown) { nHiddenHeavy++; continue; }
            const kinds: string[] = [];
            if (hasF) { nFilter++; kinds.push("filter:" + f.replace(/\s+/g, "").slice(0, 22)); }
            if (hasB) { nBackdrop++; kinds.push("backdrop:" + bf.replace(/\s+/g, "").slice(0, 22)); }
            if (wc) { nWill++; kinds.push("will-change:" + wc.slice(0, 16)); }
            if (mb) { nBlend++; kinds.push("blend:" + cs.mixBlendMode); }
            if (sh) { nShadow++; kinds.push("big-shadow"); }
            const k = `${sel(el)} ${kinds.join("+")} ${Math.round(r.width)}x${Math.round(r.height)}`;
            groups.set(k, (groups.get(k) ?? 0) + 1);
          }
          busyMs += performance.now() - s0;
          if (i < els.length) { window.setTimeout(work, 24); return; }   // let the page breathe, then continue

          const canv = [...document.querySelectorAll("canvas")].map((c) => {
            const r = c.getBoundingClientRect();
            return `${sel(c)} drawn ${c.width}x${c.height} shown ${Math.round(r.width)}x${Math.round(r.height)}`;
          }).slice(0, 3).join("; ");
          const vids = [...document.querySelectorAll("video")].filter((v) => !v.paused).length;
          const svgAnim = document.querySelectorAll("animate, animateTransform, animateMotion").length;
          // Line 1: the counts. Line 2: who is responsible. Kept apart so neither is cut off at 400 characters.
          trace("perf-heavy", clip(`${window.innerWidth}x${window.innerHeight}@${window.devicePixelRatio}x, ${els.length} elements; visible costly: ${nFilter} filter, ${nBackdrop} backdrop, ${nWill} will-change, ${nBlend} blend, ${nShadow} big shadow (+${nHiddenHeavy} costly but not shown); videos ${vids}, svg-anim ${svgAnim}; scan ${Math.round(busyMs)}ms busy in ${slices} slices over ${Math.round(performance.now() - t0)}ms`));
          const who = groups.size ? top(groups, 5) : "none";
          trace("perf-heavy-who", `${who.length > 220 ? who.slice(0, 219) + "…" : who} || canvases: ${canv ? (canv.length > 140 ? canv.slice(0, 139) + "…" : canv) : "none"}`);
          scanning = false;
        } catch { scanning = false; /* ignore */ }
      };
      window.setTimeout(work, 0);
    };
    win.__lyraProbeScan = scan;
    window.setTimeout(scan, SCAN_FIRST_MS);
    realSetInterval(scan, SCAN_EVERY_MS);

    // ----- where is this window, really? (one line at launch, one more when it appears / is parked) -----
    const where = (why: string): void => {
      const x = window.screenX, y = window.screenY;
      trace("perf-window", `${why}: really-visible=${isReallyVisible()} parked=${isParked()}${isDebugPaused() ? " DEBUG-PAUSED" : ""} document=${document.visibilityState}${document.hasFocus() ? "+focus" : ""} at (${x},${y}) size ${window.outerWidth}x${window.outerHeight} inner ${window.innerWidth}x${window.innerHeight}`);
    };
    window.setTimeout(() => where("launch"), 1500);
    window.setTimeout(() => where("launch+10s"), 10_000);
    onVisibilityChange((v) => where(v ? "became visible" : "became hidden/parked"));

    // ----- the 5-second report -----
    let lastQuiet = 0, lastDetail = 0;
    let lastTick = performance.now();
    // Settings is looked at more closely (per-tab numbers), so its sample window is 3 s instead of 5 s.
    const sampleMs = (location.pathname || "").toLowerCase().includes("settings") ? 3000 : SAMPLE_MS;
    const sampler = realSetInterval(() => {
      if (stopped) return;
      try {
        const now = performance.now();
        const dt = Math.max(1, now - lastTick);
        lastTick = now;
        const secs = dt / 1000;
        const rafRate = rafCalls / secs;
        const scriptPct = scriptMs / dt * 100;
        const renderPct = renderMs / dt * 100;
        const layoutPct = layoutMs / dt * 100;
        const busy = Math.min(100, scriptPct + renderPct);
        const interesting = busy >= 2 || rafRate >= 5 || loafCount > 2;

        if (interesting || now - lastQuiet >= QUIET_REPORT_MS) {
          lastQuiet = now;
          const vis = `${document.visibilityState}${document.hasFocus() ? "+focus" : ""} at (${window.screenX},${window.screenY})`;
          const loops = rafRate >= 5 ? ` (~${Math.max(1, Math.round(rafRate / 60))} loop(s) at 60Hz)` : "";
          const worst = worstMs >= 16 ? `; slowest script ${Math.round(worstMs)}ms: ${worstWho}` : "";
          const byScript = scriptMs > 8 ? `; script ms by: ${top(scriptBy, 3, "ms")}` : "";
          trace("perf", clip(`${vis}; nodes ${document.getElementsByTagName("*").length}; page rAF ${rafRate.toFixed(0)}/s${loops}; main thread ~${busy.toFixed(0)}% = script ${scriptPct.toFixed(0)}% + render ${renderPct.toFixed(0)}% (style+layout ${layoutPct.toFixed(0)}%); ${loafCount} slow frames${worst}${byScript}`));
        }

        if (isReallyVisible() && (busy >= BUSY_PCT || now - lastDetail >= ANIM_EVERY_MS)) {
          lastDetail = now;
          trace("perf-anim", animLine().line);
          const t: [string, number][] = [];
          for (const r of intervals.values()) if (r.runs > 0) t.push([`setInterval ${r.ms}ms ${r.site}`, r.runs / secs]);
          t.sort((x, y) => y[1] - x[1]);
          trace("perf-timer", clip(`rAF ${rafRate.toFixed(0)}/s${rafSites.size ? " from " + top(rafSites, 3) : ""}; ${t.slice(0, 5).map(([k, v]) => `${k} = ${v.toFixed(1)}/s`).join(" | ") || "no active setInterval"}`));
        }

        if (perfListeners.size) {
          const a = animLine();
          const sample: PerfSample = {
            ms: dt, rafRate, busy, scriptPct, renderPct, layoutPct, loafCount, worstMs, worstWho,
            anims: a.running, endless: a.endless, heavy: a.heavy, rafSites: top(rafSites, 3),
          };
          for (const cb of [...perfListeners]) { try { cb(sample); } catch { /* a listener must not break the probe */ } }
        }

        rafCalls = 0; rafSites.clear();
        scriptMs = renderMs = layoutMs = 0; loafCount = 0; worstMs = 0; worstWho = ""; scriptBy.clear();
        for (const r of intervals.values()) r.runs = 0;
      } catch { /* a probe must never break the page */ }
    }, sampleMs);
    // Backend tracing is off: give the page its own timers and animation loop back and stop probing.
    onTraceOff(() => {
      stopped = true;
      realClearInterval(sampler);
      window.requestAnimationFrame = realRaf as typeof window.requestAnimationFrame;
      (window as unknown as { setInterval: unknown }).setInterval = realSetInterval;
      window.clearInterval = realClearInterval;
      intervals.clear(); rafSites.clear(); perfListeners.clear();
    });

    // ----- window <-> process mapping: the backend (dbg.py) asks one window at a time to spin ~450 ms so it can see
    // which msedgewebview2 renderer is this page. Idle cost: one tiny request every 30 s (1.5 s only during a mapping run).
    const pn = (location.pathname || "").toLowerCase();
    const myWin = pn.includes("settings") ? "settings" : pn.includes("wheel") ? "wheel" : pn.includes("caption") ? "caption"
      : pn.includes("mascot") ? "mascot" : "island";
    const realTimeout = window.setTimeout.bind(window);
    const askBackend = (): void => {
      if (stopped) return;
      let next = 30_000;
      fetch(`http://127.0.0.1:8765/pidprobe?win=${myWin}`)
        .then((r) => r.json())
        .then((d: { spin?: number; next?: number }) => {
          next = Math.max(1000, Math.min(60_000, Number(d.next) || 30_000));
          const spin = Math.min(600, Number(d.spin) || 0);
          if (spin > 0) { const end = performance.now() + spin; while (performance.now() < end) { /* busy on purpose */ } }
        })
        .catch(() => { next = 30_000; })
        .finally(() => { realTimeout(askBackend, next); });
    };
    realTimeout(askBackend, 12_000);
  } catch { /* never break the page */ }
}
