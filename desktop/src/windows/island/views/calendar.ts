// Calendar: focused date view with today's events and named reminders (daily or one-off).

import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import type { ViewHost } from "./views";
import { addItem, dayKey, itemsOn, removeItem } from "../../../shared/core/calendarStore";
import { fxAdded } from "./addedFx";
import { Bridge } from "../../../shared/core/bridge";
import { refreshSchedule, removeScheduled, scheduleOn } from "../../../shared/core/scheduleStore";
import { googleOn, refreshGoogle } from "../../../shared/core/googleCalStore";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function todayParts(d = new Date()) {
  return { weekday: DAYS[d.getDay()], day: d.getDate(), month: MONTHS[d.getMonth()], year: d.getFullYear() };
}

export function buildCalendar(): ViewHost {
  const wd = h("div", { class: "cal-wd" });
  const num = h("div", { class: "cal-num" });
  const mon = h("div", { class: "cal-mon" });
  const strip = h("div", { class: "cal-strip" });
  const list = h("div", { class: "cal-list" });
  const addBtn = h("button", { class: "cal-add", type: "button", "aria-label": "Add", title: "Add event or reminder" }, svg(ICONS.plus, 14));

  let selected = new Date();
  selected.setHours(12, 0, 0, 0);
  const selKey = () => dayKey(selected);

  // Add form
  const title = h("input", { class: "cal-in", type: "text", placeholder: "Name", maxlength: "60" }) as HTMLInputElement;
  const time = h("input", { class: "cal-in cal-time", type: "time", value: "09:00" }) as HTMLInputElement;
  const kindSeg = seg(["Reminder", "Event"], 0);
  const repSeg = seg(["Once", "Daily"], 0);
  const formDay = h("div", { class: "cal-form-day" });
  const save = h("button", { class: "cal-save", type: "button", text: "Add" }) as HTMLButtonElement;
  const cancel = h("button", { class: "cal-cancel", type: "button", text: "Cancel" }) as HTMLButtonElement;
  const form = h("div", { class: "cal-form" },
    formDay,
    title,
    h("div", { class: "cal-row" }, time, kindSeg.el, repSeg.el),
    h("div", { class: "cal-row end" }, cancel, save),
  );

  const left = h("div", { class: "cal-left" }, wd, num, mon);
  const right = h("div", { class: "cal-right" }, strip, h("div", { class: "cal-listwrap" }, list, addBtn));
  const el = h("div", { class: "view calendar-view" }, left, right, form);

  function seg(labels: string[], start: number) {
    let value = start;
    const btns = labels.map((l, i) => h("button", { type: "button", class: i === start ? "on" : "", text: l, onclick: (e: Event) => {
      e.stopPropagation(); value = i; btns.forEach((b, j) => b.classList.toggle("on", j === i));
    } }) as HTMLButtonElement);
    return { el: h("div", { class: "cal-seg" }, ...btns), get value() { return value; } };
  }

  // Rows that were not in the list the last time it was drawn (same day, tab already open) slide in with a pulse.
  let lastDay = "", lastKeys = new Set<string>(), openedAt = 0;
  const paintList = () => {
    // Calendar items you added here, plus what Lyra has scheduled (reminders, actions, routines).
    const lines = [
      ...itemsOn(selKey()).map((it) => ({ time: it.time, title: it.title, kind: it.kind, tag: it.repeat === "daily" ? "Daily" : "",
        remove: () => removeItem(it.id) as unknown })),
      ...scheduleOn(selKey()).map((l) => ({ time: l.time, title: l.title, kind: l.kind, tag: l.tag,
        remove: l.id ? () => removeScheduled(l.id as string) as unknown : null })),
      ...googleOn(selKey()).map((l) => ({ time: l.time, title: l.title, kind: l.kind, tag: l.tag, remove: null })),
    ].sort((a, b) => (a.time || "99:99").localeCompare(b.time || "99:99"));
    const keyOf = (it: { time: string; title: string }) => `${it.time}|${it.title}`;
    const settled = Date.now() - openedAt > 1500;       // a list that is still loading is not "new"
    const fresh = new Set<string>();
    if (settled && lastDay === selKey()) for (const it of lines) if (!lastKeys.has(keyOf(it))) fresh.add(keyOf(it));
    lastDay = selKey(); lastKeys = new Set(lines.map(keyOf));
    if (fresh.size) fxAdded(el, "Added to your calendar", "calendar");
    list.replaceChildren(...(lines.length ? lines.slice(0, 3).map((it) => {
      const del = it.remove ? h("button", { class: "cal-del", type: "button", "aria-label": "Delete", title: "Delete", text: "×",
        onclick: (e: Event) => { e.stopPropagation(); it.remove?.(); paintList(); paintStrip(false); } }) : null;
      return h("div", { class: `cal-item ${it.kind}${fresh.has(keyOf(it)) ? " fx-new" : ""}` },
        h("i", { class: "cal-dot" }),
        h("span", { class: "cal-t", text: it.time || "All day" }),
        h("span", { class: "cal-n", text: it.title }),
        it.tag ? h("span", { class: "cal-rep", text: it.tag }) : null,
        del);
    }) : [h("span", { class: "cal-none", text: "Nothing planned" })]));
  };

  const paintLeft = () => {
    const t = todayParts(selected);
    wd.textContent = t.weekday.toUpperCase();
    num.textContent = String(t.day);
    mon.textContent = `${t.month} ${t.year}`;
  };

  // A strip of days you can swipe, drag or scroll; tap a day to select it.
  const SPAN_BACK = 14, SPAN_FWD = 75;
  const paintStrip = (recenter: boolean) => {
    const keep = strip.scrollLeft;
    const today = new Date(); today.setHours(12, 0, 0, 0);
    const selStr = selKey(), todayStr = dayKey(today);
    const days: HTMLElement[] = [];
    for (let o = -SPAN_BACK; o <= SPAN_FWD; o++) {
      const d = new Date(today); d.setDate(today.getDate() + o);
      const k = dayKey(d);
      const has = itemsOn(k).length > 0 || scheduleOn(k).length > 0 || googleOn(k).length > 0;
      const cls = `cal-day${k === selStr ? " today" : ""}${k === todayStr ? " is-today" : ""}${has ? " has" : ""}`;
      const cell = h("button", { class: cls, type: "button", "data-key": k },
        h("span", { class: "cal-d-w", text: DAYS[d.getDay()].slice(0, 1) }),
        h("span", { class: "cal-d-n", text: String(d.getDate()) }),
        h("i", { class: "cal-d-dot" })) as HTMLButtonElement;
      cell.addEventListener("click", (e) => {
        e.stopPropagation();
        if (dragMoved) return;
        selected = d; selected.setHours(12, 0, 0, 0);
        paintLeft(); paintList(); paintStrip(false);
      });
      days.push(cell);
    }
    strip.replaceChildren(...days);
    if (recenter) {
      const sel = strip.querySelector<HTMLElement>(".today");
      if (sel) strip.scrollLeft = sel.offsetLeft - strip.clientWidth / 2 + sel.offsetWidth / 2;
    } else strip.scrollLeft = keep;
  };

  // Drag to swipe (mouse or touch) and wheel to scroll sideways.
  let dragging = false, dragMoved = false, startX = 0, startScroll = 0;
  strip.addEventListener("pointerdown", (e: PointerEvent) => { dragging = true; dragMoved = false; startX = e.clientX; startScroll = strip.scrollLeft; });
  strip.addEventListener("pointermove", (e: PointerEvent) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    if (Math.abs(dx) > 4) { dragMoved = true; strip.setPointerCapture(e.pointerId); }
    if (dragMoved) strip.scrollLeft = startScroll - dx;
  });
  const endDrag = () => { dragging = false; window.setTimeout(() => { dragMoved = false; }, 0); };
  strip.addEventListener("pointerup", endDrag); strip.addEventListener("pointercancel", endDrag);
  strip.addEventListener("wheel", (e: WheelEvent) => { e.preventDefault(); strip.scrollLeft += e.deltaX + e.deltaY; }, { passive: false });

  const paint = () => { paintLeft(); paintList(); };

  const openForm = (on: boolean) => {
    el.classList.toggle("adding", on);
    const isl = el.closest<HTMLElement>("#island");
    if (on && isl) { isl.append(form); void form.offsetWidth; }
    form.classList.toggle("open", on);
    if (!on) window.setTimeout(() => { if (!form.classList.contains("open")) el.append(form); }, 320);
    void Bridge.focusWindow(on);
    if (on) {
      const t = todayParts(selected);
      formDay.textContent = `${t.weekday}, ${t.month} ${t.day}`;
      title.value = "";
      window.setTimeout(() => title.focus(), 60);
    }
  };
  for (const ev of ["pointerdown", "mousedown", "click"]) form.addEventListener(ev, (e) => e.stopPropagation());
  for (const inp of [title, time]) {
    inp.addEventListener("pointerdown", () => { void Bridge.focusWindow(true); window.setTimeout(() => inp.focus(), 0); });
  }
  addBtn.addEventListener("click", (e) => { e.stopPropagation(); openForm(true); });
  cancel.addEventListener("click", (e) => { e.stopPropagation(); openForm(false); });
  const commit = () => {
    const name = title.value.trim();
    if (!name) { title.focus(); return; }
    addItem({ title: name, date: selKey(), time: time.value || "", kind: kindSeg.value === 1 ? "event" : "reminder", repeat: repSeg.value === 1 ? "daily" : "none" });
    openForm(false);
    paintList(); paintStrip(false);
  };
  save.addEventListener("click", (e) => { e.stopPropagation(); commit(); });
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") openForm(false); e.stopPropagation(); });
  window.addEventListener("lyra-calendar-changed", () => { paintList(); paintStrip(false); });
  window.addEventListener("lyra-schedule-updated", () => { paintList(); paintStrip(false); });
  window.addEventListener("lyra-google-updated", () => { paintList(); paintStrip(false); });

  return {
    el,
    sync: paint,
    setActive(on) {
      if (on) { openedAt = Date.now(); selected = new Date(); selected.setHours(12, 0, 0, 0); paint(); paintStrip(true); void refreshSchedule(); void refreshGoogle(); }
      else if (el.classList.contains("adding")) openForm(false);
    },
  };
}
