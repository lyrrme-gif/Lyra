// Eco mode: one switch for "use as little CPU/GPU as possible".
// Settings -> Features -> Eco mode flips it; every window (island, settings, mascot, caption, wheel) follows at once
// through localStorage + the "storage" event (same trick as the debug pause in visibility.ts).
// While on: <html data-eco> (see design/eco.css: no shadows, blurs or filters) and the eyes stop following the cursor.
// Animations keep their full 60 fps: Eco mode never lowers the frame rate.

const KEY = "lyra-eco";
type Listener = (on: boolean) => void;
const listeners = new Set<Listener>();

function read(): boolean {
  try { return window.localStorage.getItem(KEY) === "1"; } catch { return false; }
}
let on = read();

function apply(): void {
  const root = document.documentElement;
  if (on) root.setAttribute("data-eco", "1"); else root.removeAttribute("data-eco");
}
function notify(): void {
  for (const cb of [...listeners]) { try { cb(on); } catch { /* one listener must not break the others */ } }
}

export const isEco = (): boolean => on;

export function setEco(v: boolean): void {
  if (v === on) return;
  try { if (v) window.localStorage.setItem(KEY, "1"); else window.localStorage.removeItem(KEY); } catch { /* ignore */ }
  on = v; apply(); notify();
}

export function onEcoChange(cb: Listener): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key !== KEY) return;
    const v = read();
    if (v !== on) { on = v; apply(); notify(); }
  });
  apply();
}
