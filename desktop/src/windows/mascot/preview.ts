// Standalone preview for the mascot art (npm run dev → /mascot-preview.html). No Tauri needed.
import { createCharacter } from "../../shared/mascot/character";
import type { Character, EmoteName } from "../../shared/mascot/character";

const EMOTES: EmoteName[] = [
  "idle", "happy", "think", "talk", "listen", "surprised", "sad", "sleep", "wave", "point", "celebrate", "error", "dizzy",
];
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const stage = $("stage");
const chars: Character[] = [];
let emote: EmoteName = "idle";
let mouth = 0, motion = 0, facing: -1 | 1 = 1, lookX = 0, lookY = 0;
let music = false;

function spawn() {
  const slot = document.createElement("div");
  slot.className = "slot";
  stage.appendChild(slot);
  const c = createCharacter(slot);
  c.setVibing?.(music); c.setEmote(emote); c.setMouth(mouth); c.setMotion(motion); c.setFacing(facing); c.look(lookX, lookY);
  chars.push(c);
}
function each(fn: (c: Character) => void) { chars.forEach(fn); }

spawn();

// Emote buttons
const emoteBox = $("emotes");
const buttons = new Map<EmoteName, HTMLButtonElement>();
for (const name of EMOTES) {
  const b = document.createElement("button");
  b.textContent = name;
  b.onclick = () => {
    emote = name;
    each((c) => c.setEmote(name));
    buttons.forEach((btn, n) => btn.classList.toggle("on", n === name));
  };
  buttons.set(name, b);
  emoteBox.appendChild(b);
}
buttons.get("idle")?.classList.add("on");

// Mouth, motion, facing
$<HTMLInputElement>("mouth").oninput = (e) => { mouth = +(e.target as HTMLInputElement).value; each((c) => c.setMouth(mouth)); };
$<HTMLInputElement>("motion").oninput = (e) => { motion = +(e.target as HTMLInputElement).value; each((c) => c.setMotion(motion)); };
const setFace = (d: -1 | 1) => {
  facing = d; each((c) => c.setFacing(d));
  $("faceL").classList.toggle("on", d < 0); $("faceR").classList.toggle("on", d > 0);
};
$("faceL").onclick = () => setFace(-1);
$("faceR").onclick = () => setFace(1);

// Music: groove + notes (what the island relays while a track plays)
$("music").onclick = () => {
  music = !music;
  each((c) => c.setVibing?.(music));
  $("music").textContent = `Music: ${music ? "on" : "off"}`;
  $("music").classList.toggle("on", music);
};

// Look pad
const pad = $("pad"), dot = $("dot");
function lookFrom(e: PointerEvent) {
  const r = pad.getBoundingClientRect();
  lookX = Math.max(-1, Math.min(1, ((e.clientX - r.left) / r.width) * 2 - 1));
  lookY = Math.max(-1, Math.min(1, ((e.clientY - r.top) / r.height) * 2 - 1));
  dot.style.left = `${(lookX + 1) * 50}%`; dot.style.top = `${(lookY + 1) * 50}%`;
  each((c) => c.look(lookX, lookY));
}
pad.onpointerdown = (e) => { pad.setPointerCapture(e.pointerId); lookFrom(e); };
pad.onpointermove = (e) => { if (pad.hasPointerCapture(e.pointerId)) lookFrom(e); };
pad.ondblclick = () => {
  lookX = lookY = 0; dot.style.left = dot.style.top = "50%"; each((c) => c.look(0, 0));
};

// Background, second character
$<HTMLSelectElement>("bg").onchange = (e) => { stage.dataset.bg = (e.target as HTMLSelectElement).value; };
$<HTMLInputElement>("two").onchange = (e) => {
  if ((e.target as HTMLInputElement).checked) spawn();
  else chars.pop()?.destroy();
  stage.querySelectorAll(".slot:empty").forEach((s) => s.remove());
};

// FPS meter
let frames = 0, since = performance.now();
const fpsEl = $("fps");
(function tick(now: number) {
  frames++;
  if (now - since >= 500) {
    fpsEl.textContent = `fps: ${Math.round((frames * 1000) / (now - since))}`;
    frames = 0; since = now;
  }
  requestAnimationFrame(tick);
})(performance.now());
