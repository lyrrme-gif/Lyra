// Controls: quick switches (Wi-Fi, Bluetooth, Night light, Dark mode), master volume and
// the power row (lock, sleep, restart, shut down) in one tab. Data and actions come from
// the backend (controls.py); it is polled only while the tab is on screen.

import { visiblePoll, type VisiblePoll } from "../../../shared/core/visiblePoll";
import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import type { ViewHost } from "./views";

const BASE = "http://127.0.0.1:8765";

type Key = "wifi" | "bluetooth" | "night" | "dark";
interface Snap {
  ok?: boolean; error?: string;
  wifi?: boolean | null; bluetooth?: boolean | null; night?: boolean | null; dark?: boolean | null;
  volume?: number | null; muted?: boolean | null; volume_error?: string;
}
interface PowerReply { ok?: boolean; message?: string; delay?: number }

const TILES: { key: Key; label: string; icon: keyof typeof ICONS }[] = [
  { key: "wifi", label: "Wi-Fi", icon: "wifi" },
  { key: "bluetooth", label: "Bluetooth", icon: "bluetooth" },
  { key: "night", label: "Night light", icon: "sunLow" },
  { key: "dark", label: "Dark mode", icon: "contrast" },
];
const POWER: { id: string; label: string; icon: keyof typeof ICONS; confirm: boolean }[] = [
  { id: "lock", label: "Lock", icon: "lock", confirm: false },
  { id: "sleep", label: "Sleep", icon: "moon", confirm: false },
  { id: "restart", label: "Restart", icon: "restart", confirm: false },
  { id: "shutdown", label: "Shut down", icon: "power", confirm: true },
];

async function call<T>(path: string, body?: object): Promise<T | null> {
  try {
    const res = await fetch(`${BASE}${path}`, body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined);
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export function buildControls(): ViewHost {
  let snap: Snap = {};
  let dragging = false;
  let poll: VisiblePoll | null = null;
  let confirmTimer: number | null = null;
  let confirming: string | null = null;
  let countdown: number | null = null;

  // ── switches ────────────────────────────────────────────────────────────────
  const tiles = new Map<Key, HTMLButtonElement>();
  const tileEls = TILES.map((t) => {
    const sub = h("span", { class: "ct-sub" });
    const btn = h("button", { class: "ct-tile na", type: "button", "data-key": t.key, "aria-pressed": "false" },
      h("span", { class: "ct-ico" }, svg(ICONS[t.icon], 20, { stroke: 1.9 })),
      h("span", { class: "ct-lbl", text: t.label }),
      sub,
    ) as HTMLButtonElement;
    btn.addEventListener("click", (e) => { e.stopPropagation(); void flip(t.key); });
    tiles.set(t.key, btn);
    return btn;
  });

  // ── volume ──────────────────────────────────────────────────────────────────
  const muteBtn = h("button", { class: "ct-mute", type: "button", "aria-label": "Mute" }) as HTMLButtonElement;
  const range = h("input", { class: "ct-range", type: "range", min: 0, max: 100, step: 1, value: 0, "aria-label": "Volume" }) as HTMLInputElement;
  const pct = h("span", { class: "ct-pct", text: "--" });
  const volRow = h("div", { class: "ct-vol na" }, muteBtn, range, pct);

  // ── power ───────────────────────────────────────────────────────────────────
  const powerBtns = new Map<string, HTMLButtonElement>();
  const powerEls = POWER.map((p) => {
    const lbl = h("span", { class: "ct-lbl", text: p.label });
    const btn = h("button", { class: `ct-pw ${p.confirm ? "danger" : ""}`, type: "button", "data-id": p.id },
      svg(ICONS[p.icon], 20, { stroke: 1.9 }), lbl) as HTMLButtonElement;
    btn.addEventListener("click", (e) => { e.stopPropagation(); void press(p.id, p.confirm); });
    powerBtns.set(p.id, btn);
    return btn;
  });
  const bannerText = h("span", { class: "ct-banner-text" });
  const cancelBtn = h("button", { class: "ct-cancel", type: "button", text: "Cancel" }) as HTMLButtonElement;
  const banner = h("div", { class: "ct-banner" }, bannerText, cancelBtn);
  const powerRow = h("div", { class: "ct-power" }, ...powerEls);

  const el = h("div", { class: "view controls-view" },
    h("div", { class: "ct-tiles" }, ...tileEls),
    volRow,
    h("div", { class: "ct-powerwrap" }, powerRow, banner),
  );

  // ── painting ────────────────────────────────────────────────────────────────
  const paint = () => {
    for (const t of TILES) {
      const btn = tiles.get(t.key)!;
      const v = snap[t.key];
      btn.classList.toggle("na", v == null);
      btn.classList.toggle("on", v === true);
      btn.setAttribute("aria-pressed", v === true ? "true" : "false");
      (btn.querySelector(".ct-sub") as HTMLElement).textContent = v == null ? "Unavailable" : v ? "On" : "Off";
    }
    const vol = snap.volume;
    volRow.classList.toggle("na", vol == null);
    volRow.title = vol == null ? (snap.volume_error || "Volume isn't available on this PC") : "";
    if (!dragging) {
      range.value = String(vol ?? 0);
      range.style.setProperty("--pct", `${vol ?? 0}%`);
      pct.textContent = vol == null ? "--" : snap.muted ? "Muted" : `${vol}%`;
    }
    muteBtn.replaceChildren(svg(snap.muted ? ICONS.speakerOff : ICONS.speakerOn, 18));
    muteBtn.classList.toggle("muted", !!snap.muted);
  };

  const flash = (btn: HTMLElement, msg: string) => {
    btn.classList.add("err");
    btn.title = msg;
    window.setTimeout(() => { btn.classList.remove("err"); btn.title = ""; }, 2200);
  };

  const refresh = async () => {
    const s = await call<Snap>("/controls");
    if (s) { snap = s; paint(); }
  };

  // ── actions ─────────────────────────────────────────────────────────────────
  async function flip(key: Key) {
    const btn = tiles.get(key)!;
    const cur = snap[key];
    if (cur == null || btn.classList.contains("busy")) return;
    snap = { ...snap, [key]: !cur };                 // show the change straight away
    paint();
    btn.classList.add("busy");
    const res = await call<Snap>("/controls/toggle", { id: key, on: !cur });
    btn.classList.remove("busy");
    if (res) {
      snap = res;
      if (res.ok === false) flash(btn, res.error || "Couldn't change that");
    } else {
      snap = { ...snap, [key]: cur };
      flash(btn, "Lyra's backend isn't answering");
    }
    paint();
  }

  const resetConfirm = () => {
    if (confirmTimer != null) { window.clearTimeout(confirmTimer); confirmTimer = null; }
    if (confirming) {
      const b = powerBtns.get(confirming);
      if (b) { b.classList.remove("confirm"); (b.querySelector(".ct-lbl") as HTMLElement).textContent = POWER.find((p) => p.id === confirming)!.label; }
    }
    confirming = null;
  };

  const showBanner = (text: string, withCancel: boolean) => {
    bannerText.textContent = text;
    cancelBtn.style.display = withCancel ? "" : "none";
    el.classList.add("pending");
  };
  const hideBanner = () => {
    if (countdown != null) { window.clearInterval(countdown); countdown = null; }
    el.classList.remove("pending");
  };

  async function press(id: string, needsConfirm: boolean) {
    const btn = powerBtns.get(id)!;
    if (needsConfirm && confirming !== id) {         // first tap arms it, second tap does it
      resetConfirm();
      confirming = id;
      btn.classList.add("confirm");
      (btn.querySelector(".ct-lbl") as HTMLElement).textContent = "Tap to confirm";
      confirmTimer = window.setTimeout(resetConfirm, 3200);
      return;
    }
    resetConfirm();
    const res = await call<PowerReply>("/controls/power", { action: id });
    if (!res || res.ok === false) { flash(btn, res?.message || "Lyra's backend isn't answering"); return; }
    if (needsConfirm) {
      let left = res.delay ?? 8;
      const verb = id === "restart" ? "Restarting" : "Shutting down";
      showBanner(`${verb} in ${left}s`, true);
      if (countdown != null) window.clearInterval(countdown);
      countdown = window.setInterval(() => {
        left -= 1;
        if (left <= 0) { hideBanner(); return; }
        bannerText.textContent = `${verb} in ${left}s`;
      }, 1000);
    }
  }

  cancelBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    hideBanner();
    await call<PowerReply>("/controls/power", { action: "cancel" });
  });

  muteBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (snap.volume == null) return;
    snap = { ...snap, muted: !snap.muted };
    paint();
    await call("/controls/mute", { on: snap.muted });
  });

  let sendTimer: number | null = null;
  range.addEventListener("pointerdown", (e) => { e.stopPropagation(); dragging = true; });
  range.addEventListener("click", (e) => e.stopPropagation());
  range.addEventListener("input", () => {
    const v = Number(range.value);
    range.style.setProperty("--pct", `${v}%`);
    pct.textContent = `${v}%`;
    snap = { ...snap, volume: v, muted: false };
    if (sendTimer == null) {                          // at most ~8 updates a second while dragging
      sendTimer = window.setTimeout(() => { sendTimer = null; void call("/controls/volume", { level: Number(range.value) }); }, 120);
    }
  });
  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    void call("/controls/volume", { level: Number(range.value) });
  };
  range.addEventListener("change", endDrag);
  window.addEventListener("pointerup", endDrag);

  return {
    el,
    sync() { paint(); },
    setActive(on) {
      poll?.stop(); poll = null;
      if (on) {
        poll = visiblePoll(() => { if (!dragging) return refresh(); }, { ms: 3000 });   // runs at once; cleared while hidden
      } else {
        resetConfirm();
      }
    },
  };
}
