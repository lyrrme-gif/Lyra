// Settings → Features: one preview per feature, the same little scenes as before (black island, same content and
// animations) but drawn on a whole-screen desktop (screenPreview.ts) instead of a cropped card. The scenes are laid
// out for a 136 x 80 stage; featurePreview() scales that stage to the preview's width. The two Mochi features draw
// the real character. Nothing here talks to the backend, and every preview is decorative (aria-hidden).

import "./featureScenes.css";
import { h } from "../views/dom";
import { createCharacter } from "../mascot/character";
import { isReallyVisible } from "../core/visibility";
import type { FeatureDef } from "../core/features";
import { screenPreview } from "./screenPreview";

// Building blocks used by the markup below:
//   .isl (.w wide · .t tall)  the black mock island      .win  a faint app window behind it
//   .l (.b bright · .a accent) a text line               .sq   an accent tile (album art, icon …)
//   .bar > i                  a progress bar that fills   .seq  fades in (staggered with --d)
const SCENES: Record<string, string> = {
  music: `<i class="win"></i>
    <div class="isl t"><div class="rw"><i class="sq"></i><div class="col grow"><b class="l b" style="width:38px"></b><b class="l" style="width:24px"></b></div><div class="eq"><i></i><i></i><i></i><i></i></div></div>
    <div class="bar"><i></i></div>
    <div class="ctl"><i></i><i class="play"></i><i></i></div></div>`,

  chat: `<i class="win"></i>
    <div class="isl t"><div class="bub me seq" style="width:44px"></div>
    <div class="bub ly seq" style="width:66px;--d:1.1s"><b class="l b" style="width:50px"></b><b class="l" style="width:34px"></b></div></div>`,

  timer: `<i class="win hi"></i>
    <div class="isl w"><svg class="ring" viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5" class="bg"/><circle cx="10" cy="10" r="7.5" class="fg"/></svg>
    <span class="tx">9:58</span><span class="tx m grow">focus</span><i class="dt pulse"></i></div>
    <div class="lines"><b class="l" style="width:54px"></b><b class="l" style="width:36px"></b></div>`,

  tab_wheel: `<div class="wheel">
    <i class="wd" style="--r:0deg;--d:0s"></i><i class="wd" style="--r:60deg;--d:.5s"></i><i class="wd" style="--r:120deg;--d:1s"></i>
    <i class="wd" style="--r:180deg;--d:1.5s"></i><i class="wd" style="--r:240deg;--d:2s"></i><i class="wd" style="--r:300deg;--d:2.5s"></i>
    <i class="wc"></i></div><span class="keys">Ctrl + CapsLock</span>`,

  weather: `<i class="win hi"></i>
    <div class="isl w"><i class="sun"></i><div class="col grow"><span class="tx">24°</span><b class="l" style="width:26px"></b></div><i class="cloud"></i></div>
    <div class="lines"><b class="l" style="width:70px"></b></div>`,

  calendar: `<i class="win"></i>
    <div class="isl t"><div class="days"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><u></u></div>
    <div class="rw seq" style="--d:.2s"><i class="stripe"></i><div class="col grow"><b class="l b" style="width:46px"></b><b class="l" style="width:30px"></b></div></div>
    <div class="rw seq" style="--d:.9s"><i class="stripe alt"></i><div class="col grow"><b class="l b" style="width:36px"></b></div></div></div>`,

  todo: `<i class="win"></i>
    <div class="isl t">
    <div class="rw todo" style="--d:.2s"><i class="cb"></i><b class="l b" style="width:50px"></b></div>
    <div class="rw todo" style="--d:1.2s"><i class="cb"></i><b class="l b" style="width:38px"></b></div>
    <div class="rw todo" style="--d:2.2s"><i class="cb"></i><b class="l b" style="width:58px"></b></div></div>`,

  clipboard: `<i class="win"></i>
    <div class="isl t">
    <div class="clip" style="--d:0s"><b class="l b" style="width:44px"></b></div>
    <div class="clip" style="--d:1.3s"><b class="l b" style="width:56px"></b></div>
    <div class="clip" style="--d:2.6s"><b class="l b" style="width:34px"></b></div></div>
    <span class="chip pop">Copied</span>`,

  recognition: `<i class="win hi"></i>
    <div class="isl w"><div class="pulse-rings"><i></i><i></i><svg viewBox="0 0 24 24"><path d="M9 18V6l10-2v12" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><circle cx="7" cy="18" r="2.6" fill="#fff"/><circle cx="17" cy="16" r="2.6" fill="#fff"/></svg></div>
    <div class="col grow"><b class="l b seq" style="width:42px;--d:.8s"></b><b class="l seq" style="width:28px;--d:1.2s"></b></div></div>
    <div class="lines"><b class="l" style="width:60px"></b></div>`,

  stats: `<i class="win"></i>
    <div class="isl t"><div class="vbars">
    <div><i style="--s:.9s;--h:.7"></i><em>CPU</em></div><div><i style="--s:1.3s;--h:.5"></i><em>RAM</em></div>
    <div><i style="--s:1.1s;--h:.85"></i><em>GPU</em></div><div><i style="--s:1.6s;--h:.35"></i><em>NET</em></div></div></div>`,

  controls: `<i class="win"></i>
    <div class="isl t"><div class="tiles"><i style="--d:0s"></i><i style="--d:1s"></i><i style="--d:2s"></i><i style="--d:3s"></i></div></div>`,

  volume: `<i class="win hi"></i>
    <div class="isl w"><svg class="spk" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z" fill="#fff"/><path d="M16 9a4 4 0 010 6" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>
    <div class="slide grow"><i class="fill"></i><i class="knob"></i></div></div>
    <div class="lines"><b class="l" style="width:46px"></b></div>`,

  wallpaper: `<div class="wps"><i></i><i></i><i></i></div>
    <div class="isl w"><i class="dt"></i><b class="l b" style="width:26px"></b><div class="thumbs grow"><i></i><i></i><i></i></div></div>`,

  record: `<i class="win hi"></i>
    <div class="isl w"><i class="rdot"></i><span class="tx">0:12</span><span class="tx m grow">recording</span><i class="stopsq"></i></div>
    <div class="lines"><b class="l" style="width:58px"></b><b class="l" style="width:40px"></b></div>`,

  camera: `<i class="win"></i>
    <div class="isl t cam"><div class="lens"><i class="head"></i><i class="body"></i><i class="flash"></i></div><i class="rec dt pulse"></i></div>`,

  notifications: `<i class="win hi"></i>
    <div class="isl w notif"><i class="av"></i><div class="col grow"><b class="l b" style="width:36px"></b><b class="l" style="width:48px"></b></div><i class="badge">1</i></div>`,

  whatsapp: `<i class="win hi"></i>
    <div class="isl w notif"><i class="av wa"><svg viewBox="0 0 24 24"><path d="M12 4a8 8 0 0 0-6.9 12L4 20l4.1-1.1A8 8 0 1 0 12 4z" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg></i>
    <div class="col grow"><b class="l b" style="width:34px"></b><b class="l" style="width:46px"></b></div><i class="badge">2</i></div>
    <div class="lines"><b class="l" style="width:60px"></b></div>`,

  downloads: `<i class="win hi"></i>
    <div class="isl w"><svg class="dl" viewBox="0 0 24 24"><path d="M12 4v11m0 0l-4-4m4 4l4-4M5 20h14" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <div class="col grow"><b class="l b" style="width:38px"></b><div class="bar"><i></i></div></div></div>`,

  status: `<i class="win hi"></i>
    <div class="isl w"><div class="batt"><i></i></div><svg class="bolt" viewBox="0 0 24 24"><path d="M13 2L5 14h6l-1 8 8-12h-6z" fill="#ffd45a"/></svg>
    <div class="col grow"><b class="l b" style="width:30px"></b><b class="l" style="width:20px"></b></div></div>
    <div class="lines"><b class="l" style="width:50px"></b></div>`,

  circle_ask: `<div class="scr"><i class="img"></i><b class="l" style="width:64px"></b><b class="l" style="width:44px"></b><b class="l" style="width:54px"></b></div>
    <svg class="circ" viewBox="0 0 80 50"><ellipse cx="40" cy="25" rx="30" ry="17" pathLength="1"/></svg>
    <div class="bub ly seq bubble" style="width:50px;--d:1.1s"><b class="l b" style="width:34px"></b><b class="l" style="width:24px"></b></div>`,

  proactive: `<i class="win hi"></i>
    <div class="isl w"><svg class="bell" viewBox="0 0 24 24"><path d="M6 17V11a6 6 0 0112 0v6l2 2H4zM10 21h4" fill="#fff"/></svg>
    <div class="col grow"><b class="l b" style="width:44px"></b><b class="l" style="width:30px"></b></div><i class="dt pulse"></i></div>
    <div class="lines"><b class="l" style="width:58px"></b></div>`,

  ollama: `<svg class="chip-svg" viewBox="0 0 60 60"><rect x="16" y="16" width="28" height="28" rx="7" class="cb2"/><rect x="24" y="24" width="12" height="12" rx="3" class="cc"/>
    <path d="M22 10v6M30 10v6M38 10v6M22 44v6M30 44v6M38 44v6M10 22h6M10 30h6M10 38h6M44 22h6M44 30h6M44 38h6" class="pin"/></svg>
    <span class="keys">on this PC</span>`,

  plugins: `<div class="puz"><i class="pa"></i><i class="pb"></i><i class="spark"></i></div>`,

  proactive_extra: `<i class="win hi"></i>
    <div class="isl w"><i class="dt pulse"></i><b class="l b" style="width:40px"></b></div>
    <div class="chips"><span class="chip seq" style="--d:.2s">Break</span><span class="chip seq" style="--d:.9s">Water</span><span class="chip seq" style="--d:1.6s">Battery</span></div>`,

  file_search: `<div class="files"><div class="fr1"><i class="doc"></i><b class="l" style="width:56px"></b></div>
    <div class="fr1 hit"><i class="doc"></i><b class="l b" style="width:44px"></b></div>
    <div class="fr1"><i class="doc"></i><b class="l" style="width:62px"></b></div></div>
    <svg class="mag" viewBox="0 0 24 24"><circle cx="10" cy="10" r="6" fill="rgba(255,255,255,.08)" stroke="#fff" stroke-width="2.2"/><path d="M15 15l5 5" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/></svg>`,

  dev: `<div class="code"><i class="dots"><b></b><b></b><b></b></i>
    <div class="cl"><b class="l" style="width:30px;background:var(--a)"></b><b class="l" style="width:26px"></b></div>
    <div class="cl err"><b class="l" style="width:52px"></b></div>
    <div class="cl"><b class="l" style="width:36px"></b></div>
    <span class="fix">fixed</span></div>`,

  translate: `<i class="win hi"></i>
    <div class="isl w"><span class="tx sw1">Hello</span><svg class="arr" viewBox="0 0 24 24"><path d="M5 12h14m-5-5l5 5-5 5" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="tx sw2">Bonjour</span></div>
    <div class="lines"><b class="l" style="width:62px"></b></div>`,

  guide: `<div class="ui"><i></i><i></i><i class="target"></i><i></i></div>
    <svg class="gcur" viewBox="0 0 24 24"><path d="M5 3l14 8-6 2 4 7-3 1-4-7-5 4z" fill="#fff" stroke="#000" stroke-width="1.2" stroke-linejoin="round"/></svg>
    <span class="chip here">Here</span>`,

  planner: `<i class="win"></i>
    <div class="isl t"><div class="step" style="--d:0s"><i class="ck"></i><b class="l b" style="width:46px"></b></div>
    <div class="step" style="--d:1.2s"><i class="ck"></i><b class="l b" style="width:34px"></b></div>
    <div class="step" style="--d:2.4s"><i class="ck"></i><b class="l b" style="width:54px"></b></div></div>`,

  google: `<i class="win hi"></i>
    <div class="isl w"><i class="g cal"><em>31</em></i><i class="g mail"><svg viewBox="0 0 24 24"><path d="M3 7l9 6 9-6M3 7v10h18V7z" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg></i>
    <div class="col grow"><b class="l b seq" style="width:38px;--d:.4s"></b><b class="l seq" style="width:26px;--d:.9s"></b></div></div>
    <div class="lines"><b class="l" style="width:60px"></b></div>`,

};

/** A generic island with the feature's own colour: only used for a feature that has no scene yet. */
function generic(f: FeatureDef): string {
  return `<i class="win hi"></i><div class="isl w"><i class="sq"></i><div class="col grow"><b class="l b" style="width:36px"></b><b class="l" style="width:24px"></b></div></div><span class="keys">${f.name}</span>`;
}

/** The two Mochi features draw the real character (same engine as the notch and the screen Mochi). */
function mochiScene(el: HTMLElement, id: "mascot" | "mascot_stay"): void {
  const stay = id === "mascot_stay";
  const host = h("span", { class: stay ? "mo stay" : "mo" });
  el.append(h("i", { class: "win hi" }));
  if (stay) {
    el.append(h("div", { class: "isl w mini" }, h("b", { class: "l" })), host, h("span", { class: "subs" }, h("b", { class: "l b" }), h("b", { class: "l" })));
  } else {
    el.append(h("div", { class: "isl w mini" }, h("b", { class: "l" })), host, h("i", { class: "curs" }));
  }

  const ch = createCharacter(host, { tight: true, sharp: 2 });
  ch.setShape?.("cloud");
  ch.setEmote("idle");

  // Only animate while it is on screen; stop for good once the row has been removed.
  let seen = false, visible = true;
  new IntersectionObserver((entries) => { visible = entries.some((e) => e.isIntersecting); ch.setActive?.(visible); }).observe(el);
  const t0 = performance.now();
  let lastWave = 0;
  const timer = window.setInterval(() => {
    if (el.isConnected) seen = true;
    else if (seen) { window.clearInterval(timer); ch.destroy(); return; }
    if (!visible || !isReallyVisible()) return;
    const t = (performance.now() - t0) / 1000;
    if (stay) {
      ch.look(0.8, 0.1);
      if (t - lastWave > 4) { lastWave = t; ch.setEmote("wave"); window.setTimeout(() => ch.setEmote("idle"), 1500); }
    } else {
      ch.look(-Math.cos((t / 8) * Math.PI * 2), 0.35);   // follows the cursor dot, which sweeps left and right (CSS, 8 s)
    }
  }, 90);
}


export function featurePreview(f: FeatureDef): HTMLElement {
  const stage = h("div", { class: `fo fo-${f.id}`, style: `--a:${f.from};--b:${f.to}` });
  if (f.id === "mascot" || f.id === "mascot_stay") mochiScene(stage, f.id);
  else stage.innerHTML = SCENES[f.id] ?? generic(f);
  const screen = screenPreview({ island: null, els: [stage], cursor: false });
  // The scenes are drawn for 136 px; show them about 62 % as wide as the desktop.
  const fit = () => { const w = screen.clientWidth; if (w) stage.style.setProperty("--k", String((w * 0.62) / 136)); };
  requestAnimationFrame(fit);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(fit).observe(screen);
  return h("div", { class: `fp fp-${f.id}`, "aria-hidden": "true" }, screen);
}
