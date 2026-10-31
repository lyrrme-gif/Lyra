// Weather: shared data store (Home widget + Weather experience read the same thing).

import { h, svg } from "./dom";
import { ICONS } from "./icons";
import type { ViewHost } from "./views";

const CITY_KEY = "lyra-weather-city";

export interface WeatherData {
  ok: boolean;
  city?: string;
  temp?: number;
  condition?: string;
  kind?: string;
  night?: boolean;
  hi?: number;
  lo?: number;
  hourly?: Array<{ hour: number; temp: number; kind: string }>;
}

let current: WeatherData = { ok: false };
let lastFetch = 0;
const listeners = new Set<() => void>();

export const getWeather = () => current;
export const onWeather = (fn: () => void) => { listeners.add(fn); return () => listeners.delete(fn); };

const CODES: Record<number, [string, string]> = {
  0: ["Clear", "clear"], 1: ["Mostly clear", "clear"], 2: ["Partly cloudy", "partly"], 3: ["Cloudy", "cloud"],
  45: ["Fog", "fog"], 48: ["Fog", "fog"], 51: ["Drizzle", "rain"], 53: ["Drizzle", "rain"], 55: ["Drizzle", "rain"],
  56: ["Freezing drizzle", "rain"], 57: ["Freezing drizzle", "rain"], 61: ["Light rain", "rain"], 63: ["Rain", "rain"],
  65: ["Heavy rain", "rain"], 66: ["Freezing rain", "rain"], 67: ["Freezing rain", "rain"], 71: ["Light snow", "snow"],
  73: ["Snow", "snow"], 75: ["Heavy snow", "snow"], 77: ["Snow grains", "snow"], 80: ["Showers", "rain"],
  81: ["Showers", "rain"], 82: ["Heavy showers", "rain"], 85: ["Snow showers", "snow"], 86: ["Snow showers", "snow"],
  95: ["Thunderstorm", "storm"], 96: ["Thunderstorm", "storm"], 99: ["Thunderstorm", "storm"],
};

async function jget<T>(url: string): Promise<T> {
  const ctl = new AbortController();
  const t = window.setTimeout(() => ctl.abort(), 7000);
  try { return (await (await fetch(url, { signal: ctl.signal })).json()) as T; } finally { window.clearTimeout(t); }
}

/** Open-Meteo needs no key. The city comes from localStorage, otherwise a one-time IP lookup. */
async function loadWeather(city: string): Promise<WeatherData> {
  let lat: number, lon: number, name: string;
  if (city) {
    const g = await jget<{ results?: Array<{ latitude: number; longitude: number; name: string }> }>(
      `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(city)}&count=1`);
    if (!g.results?.length) throw new Error("city not found");
    ({ latitude: lat, longitude: lon, name } = g.results[0]);
  } else {
    const g = await jget<{ latitude: number; longitude: number; city?: string }>("https://ipwho.is/");
    lat = g.latitude; lon = g.longitude; name = g.city || "";
  }
  const d = await jget<{
    current: { temperature_2m: number; weather_code: number; is_day: number; time: string };
    hourly: { time: string[]; temperature_2m: number[]; weather_code: number[] };
    daily: { temperature_2m_max: number[]; temperature_2m_min: number[] };
  }>(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&timezone=auto&forecast_days=2&current=temperature_2m,weather_code,is_day&hourly=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min`);
  const [label, kind] = CODES[d.current.weather_code] ?? ["", "cloud"];
  const nowKey = d.current.time.slice(0, 13);
  const start = Math.max(0, d.hourly.time.findIndex((t) => t.slice(0, 13) >= nowKey)) + 1;
  const hourly = d.hourly.time.slice(start, start + 6).map((t, i) => ({
    hour: Number(t.slice(11, 13)), temp: Math.round(d.hourly.temperature_2m[start + i]),
    kind: (CODES[d.hourly.weather_code[start + i]] ?? ["", "cloud"])[1],
  }));
  return {
    ok: true, city: name, temp: Math.round(d.current.temperature_2m), condition: label, kind, night: !d.current.is_day,
    hi: Math.round(d.daily.temperature_2m_max[0]), lo: Math.round(d.daily.temperature_2m_min[0]), hourly,
  };
}

export async function refreshWeather(force = false): Promise<void> {
  if (!force && current.ok && Date.now() - lastFetch < 10 * 60_000) return;
  if (Date.now() - lastFetch < 20_000 && !force) return;
  lastFetch = Date.now();
  let city = "";
  try { city = window.localStorage.getItem(CITY_KEY) || ""; } catch { /* ignore */ }
  try { current = await loadWeather(city.trim()); } catch { /* offline: keep what we have */ }
  for (const fn of listeners) fn();
}

/** Small SF-style weather glyphs, drawn inline so they follow no external assets. */
export function weatherGlyph(kind: string, night: boolean, size: number): SVGSVGElement {
  const NS = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(NS, "svg");
  el.setAttribute("viewBox", "0 0 32 32"); el.setAttribute("width", String(size)); el.setAttribute("height", String(size));
  el.setAttribute("aria-hidden", "true");
  const sun = night
    ? `<path d="M21 5.5a9 9 0 1 0 5.5 15.4A9.5 9.5 0 0 1 21 5.5z" fill="#e8e3c8"/>`
    : `<circle cx="16" cy="16" r="6" fill="#ffd60a"/><g stroke="#ffd60a" stroke-width="2" stroke-linecap="round"><path d="M16 3v3M16 26v3M3 16h3M26 16h3M6.8 6.8l2.1 2.1M23.1 23.1l2.1 2.1M6.8 25.2l2.1-2.1M23.1 8.9l2.1-2.1"/></g>`;
  const cloud = (dy = 0, fill = "#dfe6f2") => `<path transform="translate(0 ${dy})" d="M9 25a5.5 5.5 0 0 1-.8-10.9A7.5 7.5 0 0 1 22.6 15 4.6 4.6 0 0 1 22 25H9z" fill="${fill}"/>`;
  const parts: Record<string, string> = {
    clear: sun,
    partly: `<g transform="translate(-4 -5) scale(.8)">${sun}</g>${cloud(1)}`,
    cloud: cloud(-1, "#cfd8e6"),
    fog: `${cloud(-4, "#cfd8e6")}<g stroke="#cfd8e6" stroke-width="2" stroke-linecap="round"><path d="M7 25h18M10 29h12"/></g>`,
    rain: `${cloud(-4, "#cfd8e6")}<g stroke="#5ab4ff" stroke-width="2.2" stroke-linecap="round"><path d="M11 24l-1.5 4M17 24l-1.5 4M23 24l-1.5 4"/></g>`,
    snow: `${cloud(-4, "#cfd8e6")}<g fill="#fff"><circle cx="11" cy="26" r="1.5"/><circle cx="17" cy="28" r="1.5"/><circle cx="23" cy="26" r="1.5"/></g>`,
    storm: `${cloud(-4, "#aab4c6")}<path d="M17.5 21l-4 6h3.5l-1.5 5 6-7.5h-3.5l1.5-3.5z" fill="#ffd60a"/>`,
  };
  el.innerHTML = parts[kind] ?? parts.cloud;
  return el;
}

function hourLabel(hr: number): string {
  const h12 = hr % 12 === 0 ? 12 : hr % 12;
  return `${h12}${hr < 12 ? "a" : "p"}`;
}

export function buildWeather(): ViewHost {
  const city = h("div", { class: "wx-city" });
  const temp = h("div", { class: "wx-temp" });
  const cond = h("div", { class: "wx-cond" });
  const glyphBox = h("div", { class: "wx-glyph" });
  const hilo = h("div", { class: "wx-hilo" });
  const hours = h("div", { class: "wx-hours" });
  const empty = h("div", { class: "wx-empty" }, svg(ICONS.cloudFill, 22), h("span", { text: "Weather unavailable" }));
  const body = h("div", { class: "wx-body" },
    h("div", { class: "wx-top" },
      h("div", { class: "wx-left" }, city, temp),
      h("div", { class: "wx-right" }, glyphBox, cond, hilo),
    ),
    hours,
  );
  const el = h("div", { class: "view weather-view" }, body, empty);

  const paint = () => {
    const w = current;
    el.dataset.state = w.ok ? "ok" : "empty";
    if (!w.ok) return;
    city.textContent = w.city ?? "";
    temp.textContent = `${w.temp}°`;
    cond.textContent = w.condition ?? "";
    hilo.textContent = `H:${w.hi}°  L:${w.lo}°`;
    glyphBox.replaceChildren(weatherGlyph(w.kind ?? "cloud", !!w.night, 30));
    hours.replaceChildren(...(w.hourly ?? []).map((x) =>
      h("div", { class: "wx-hour" }, h("span", { class: "wx-h", text: hourLabel(x.hour) }), weatherGlyph(x.kind, x.hour < 6 || x.hour >= 20, 22), h("span", { class: "wx-t", text: `${x.temp}°` }))));
  };
  onWeather(paint);
  return { el, sync: paint, setActive(on) { if (on) void refreshWeather(); } };
}
