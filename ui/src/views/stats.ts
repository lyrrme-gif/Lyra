// Stats: CPU, GPU, RAM rings, disk, network, uptime, top apps and an optional speed test.
// Data comes from the backend (sysstats.py) and is polled only while the tab is on screen.

import { visiblePoll, type VisiblePoll } from "../core/visiblePoll";
import { h } from "./dom";
import type { ViewHost } from "./views";

const BASE = "http://127.0.0.1:8765";
const SVG_NS = "http://www.w3.org/2000/svg";

interface Snap {
  ok?: boolean;
  cpu?: { pct: number; name: string; ghz: number | null; procs: number } | null;
  gpu?: { pct: number; name: string; mem_used_gb: number; mem_total_gb: number } | null;
  ram?: { pct: number; used_gb: number; total_gb: number; free_gb: number } | null;
  disk?: { label: string; used_gb: number; total_gb: number } | null;
  net?: { down_bps: number; up_bps: number } | null;
  uptime_s?: number;
  top?: { ram: { name: string; mb: number }[]; cpu: { name: string; pct: number }[] };
  speedtest?: { running: boolean; down_mbps: number | null; up_mbps: number | null; at: number | null; where: string; error: string };
  study?: { today_min?: number; week_min?: number; sessions_today?: number; cards_today?: number };
}

const R = 26;
const CIRC = 2 * Math.PI * R;

function ring(color: string) {
  const svgEl = document.createElementNS(SVG_NS, "svg");
  svgEl.setAttribute("viewBox", "0 0 64 64");
  svgEl.setAttribute("class", "st-ring");
  const mk = (cls: string) => {
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", "32"); c.setAttribute("cy", "32"); c.setAttribute("r", String(R));
    c.setAttribute("fill", "none"); c.setAttribute("stroke-width", "7"); c.setAttribute("class", cls);
    return c;
  };
  const track = mk("st-track");
  track.setAttribute("stroke", color);          // Fitness-style: the track is the same colour, faint
  track.setAttribute("stroke-opacity", ".2");
  const arc = mk("st-arc");
  arc.setAttribute("stroke", color);
  arc.setAttribute("stroke-linecap", "round");
  arc.setAttribute("stroke-dasharray", `0 ${CIRC}`);
  arc.setAttribute("transform", "rotate(-90 32 32)");
  svgEl.append(track, arc);
  const label = h("div", { class: "st-ring-pct", text: "--" });
  const el = h("div", { class: "st-ring-wrap" }, svgEl, label);
  return {
    el,
    set(pct: number | null) {
      if (pct == null) { label.textContent = "—"; arc.setAttribute("stroke-dasharray", `0 ${CIRC}`); return; }
      const p = Math.max(0, Math.min(100, pct));
      label.textContent = `${Math.round(p)}%`;
      arc.setAttribute("stroke-dasharray", `${(CIRC * p) / 100} ${CIRC}`);
    },
  };
}

const chip = (text = "") => h("span", { class: "st-chip", text });

function rate(bps: number): string {
  if (bps >= 1024 * 1024) return `${(bps / 1024 / 1024).toFixed(1)} MB/s`;
  return `${Math.round(bps / 1024)} KB/s`;
}

function uptime(s: number): string {
  const d = Math.floor(s / 86400), hr = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${hr} h`;
  if (hr) return `${hr} h ${m} m`;
  return `${m} m`;
}

function ago(ms: number): string {
  const m = Math.floor((Date.now() - ms) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const hr = Math.floor(m / 60);
  return hr < 24 ? `${hr} h ago` : `${Math.floor(hr / 24)} d ago`;
}

const ramText = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);

export function buildStats(): ViewHost {
  // Gauges
  const cpuRing = ring("#8f7bff");
  const cpuName = h("div", { class: "st-sub" });
  const cpuGhz = chip(), cpuProcs = chip();
  const cpu = h("div", { class: "st-card st-gauge" }, cpuRing.el, h("div", { class: "st-name", text: "CPU" }), cpuName, h("div", { class: "st-chips" }, cpuGhz, cpuProcs));

  const gpuRing = ring("#5ab4ff");
  const gpuName = h("div", { class: "st-sub" });
  const gpuMem = chip();
  const gpu = h("div", { class: "st-card st-gauge" }, gpuRing.el, h("div", { class: "st-name", text: "GPU" }), gpuName, h("div", { class: "st-chips" }, gpuMem));

  const ramRing = ring("#34c759");
  const ramSub = h("div", { class: "st-sub" });
  const ramFree = chip();
  const ram = h("div", { class: "st-card st-gauge" }, ramRing.el, h("div", { class: "st-name", text: "RAM" }), ramSub, h("div", { class: "st-chips" }, ramFree));

  // Small cards
  const diskVal = h("div", { class: "st-val", text: "—" });
  const diskLbl = h("div", { class: "st-lbl", text: "DISK" });
  const netVal = h("div", { class: "st-val two", text: "—" });
  const upVal = h("div", { class: "st-val", text: "—" });
  const mini = (lbl: Node | string, val: Node) => h("div", { class: "st-card st-mini" }, typeof lbl === "string" ? h("div", { class: "st-lbl", text: lbl }) : lbl, val);

  // Top apps
  let mode: "ram" | "cpu" = "ram";
  const appsTitle = h("span", { class: "st-apps-title", text: "Top apps · RAM" });
  const toggle = h("button", { class: "st-toggle", type: "button", title: "Switch between RAM and CPU", text: "CPU ⇄" }) as HTMLButtonElement;
  const appsList = h("div", { class: "st-apps" });
  const apps = h("div", { class: "st-card st-apps-card" }, h("div", { class: "st-apps-head" }, appsTitle, toggle), appsList);

  // Speed test
  const spMeta = h("span", { class: "st-sp-meta", text: "Not tested yet" });
  const spDown = h("span", { class: "st-sp-num", text: "—" });
  const spUp = h("span", { class: "st-sp-num", text: "—" });
  const spBtn = h("button", { class: "st-run", type: "button", text: "▶ Run speed test" }) as HTMLButtonElement;
  const speed = h("div", { class: "st-card st-speed" },
    h("div", { class: "st-sp-head" }, h("span", { text: "Internet speed" }), spMeta),
    h("div", { class: "st-sp-row" },
      h("span", { class: "st-sp-pair" }, spDown, h("span", { class: "st-unit", text: "Mbps ↓" })),
      h("span", { class: "st-sp-pair" }, spUp, h("span", { class: "st-unit", text: "Mbps ↑" }))),
    spBtn);

  const left = h("div", { class: "st-left" }, cpu, gpu, ram,
    mini(diskLbl, diskVal),
    mini("NETWORK", netVal),
    mini("UP FOR", upVal));
  const right = h("div", { class: "st-right" }, apps, speed);
  const el = h("div", { class: "view stats-view" }, left, right);

  let last: Snap = {};

  const paintApps = () => {
    const rows = (mode === "ram" ? last.top?.ram ?? [] : last.top?.cpu ?? []).slice(0, 3);   // three fit; the fourth was always clipped
    appsTitle.textContent = mode === "ram" ? "Top apps · RAM" : "Top apps · CPU";
    toggle.textContent = mode === "ram" ? "CPU ⇄" : "RAM ⇄";
    if (!rows.length) { appsList.replaceChildren(h("span", { class: "st-none", text: "Reading…" })); return; }
    const vals = rows.map((r) => ("mb" in r ? r.mb : r.pct));
    const max = Math.max(...vals, mode === "cpu" ? 5 : 1);
    appsList.replaceChildren(...rows.map((r, i) => {
      const v = vals[i];
      const bar = h("i", { class: "st-bar" });
      bar.style.width = `${Math.max(3, (v / max) * 100)}%`;
      return h("div", { class: "st-app" },
        h("span", { class: "st-badge", text: (r.name[0] || "?").toUpperCase() }),
        h("div", { class: "st-app-body" },
          h("div", { class: "st-app-row" }, h("span", { class: "st-app-name", text: r.name }), h("span", { class: "st-app-val", text: mode === "ram" ? ramText(v) : `${v.toFixed(v >= 10 ? 0 : 1)}%` })),
          h("div", { class: "st-track-bar" }, bar)));
    }));
  };

  const paintSpeed = () => {
    const sp = last.speedtest;
    if (!sp) return;
    spBtn.disabled = sp.running;
    spBtn.textContent = sp.running ? "Testing…" : "▶ Run speed test";
    spDown.textContent = sp.down_mbps != null ? String(sp.down_mbps) : "—";
    spUp.textContent = sp.up_mbps != null ? String(sp.up_mbps) : "—";
    if (sp.running) spMeta.textContent = "Testing your connection…";
    else if (sp.error) spMeta.textContent = sp.error;
    else if (sp.at) spMeta.textContent = `last: ${ago(sp.at)}${sp.where ? ` · ${sp.where}` : ""}`;
    else spMeta.textContent = "Not tested yet";
  };

  const paint = () => {
    const s = last;
    if (s.cpu) {
      cpuRing.set(s.cpu.pct);
      cpuName.textContent = s.cpu.name;
      cpuGhz.textContent = s.cpu.ghz ? `${s.cpu.ghz.toFixed(1)} GHz` : "";
      cpuGhz.style.display = s.cpu.ghz ? "" : "none";
      cpuProcs.textContent = `${s.cpu.procs} proc`;
    }
    if (s.gpu) {
      gpuRing.set(s.gpu.pct);
      gpuName.textContent = s.gpu.name;
      gpuMem.textContent = `${s.gpu.mem_used_gb.toFixed(1)} / ${s.gpu.mem_total_gb.toFixed(1)} GB`;
      gpuMem.style.display = "";
    } else if ("gpu" in s) {
      gpuRing.set(null);
      gpuName.textContent = "No GPU data";
      gpuMem.style.display = "none";
    }
    if (s.ram) {
      ramRing.set(s.ram.pct);
      ramSub.textContent = `${s.ram.used_gb.toFixed(1)} / ${s.ram.total_gb.toFixed(1)} GB`;
      ramFree.textContent = `${s.ram.free_gb.toFixed(1)} GB free`;
    }
    if (s.disk) {
      diskLbl.textContent = `DISK ${s.disk.label}`;
      diskVal.textContent = `${s.disk.used_gb} / ${s.disk.total_gb} GB`;
    }
    if (s.net) netVal.replaceChildren(h("span", { text: `↓ ${rate(s.net.down_bps)}` }), h("span", { text: `↑ ${rate(s.net.up_bps)}` }));
    if (typeof s.uptime_s === "number") upVal.textContent = uptime(s.uptime_s);
    paintApps();
    paintSpeed();
  };

  toggle.addEventListener("click", (e) => { e.stopPropagation(); mode = mode === "ram" ? "cpu" : "ram"; paintApps(); });
  spBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    spBtn.disabled = true;
    spBtn.textContent = "Testing…";
    spMeta.textContent = "Testing your connection…";
    try { await fetch(`${BASE}/stats/speedtest`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* the poll shows the state */ }
    void poll();
  });

  let timer: VisiblePoll | null = null;
  const poll = async () => {
    try {
      const res = await fetch(`${BASE}/stats`);
      const data = (await res.json()) as Snap;
      if (data && data.ok !== false) { last = data; paint(); }
    } catch { /* backend not reachable: keep the last values */ }
  };

  return {
    el,
    sync() { paint(); },
    setActive(on) {
      timer?.stop(); timer = null;
      if (on) timer = visiblePoll(() => poll(), { ms: 1500 });   // runs at once; cleared while the window is hidden
    },
  };
}
