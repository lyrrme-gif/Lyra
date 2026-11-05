// Mochi sounds: a short acknowledgement ("Done", "Opening Spotify") is a Mochi sound + animation +
// subtitle instead of speech. The backend (mochi_sounds.py) decides which replies qualify and tells the
// windows; this module is what the windows share: the sound / emote / subtitle of each kind, and the
// "Speak / Mochi sounds / Both" setting (Settings -> Voice, pref `mochi_sound_mode`).

import { Sound } from "./sound";
import type { EmoteName } from "../mascot/character";

export type MochiKind =
  "done" | "opened" | "error" | "thinking" | "question" | "happy" | "dizzy" | "tired" | "overheating" | "info" | "rage";
export type MochiMode = "speak" | "mochi" | "both";

interface Look { sound: string; emote: EmoteName; subtitle: string; ms: number }

// `ms` is how long the animation / subtitle stay (the same numbers as KINDS in backend/mochi_sounds.py).
// Every character has its own WAV for each sound name (public/sounds/<character>/<name>.wav).
export const MOCHI_KINDS: Record<MochiKind, Look> = {
  done:        { sound: "finish",   emote: "celebrate", subtitle: "Done", ms: 2200 },
  opened:      { sound: "open",     emote: "wink",      subtitle: "Opened", ms: 1800 },
  error:       { sound: "error",    emote: "error",     subtitle: "Something went wrong", ms: 2800 },
  thinking:    { sound: "think",    emote: "think",     subtitle: "Thinking…", ms: 2500 },
  question:    { sound: "question", emote: "listen",    subtitle: "Hmm?", ms: 3000 },
  happy:       { sound: "love",     emote: "happy",     subtitle: "Happy!", ms: 2200 },
  dizzy:       { sound: "dizzy",    emote: "dizzy",     subtitle: "Whoa… I'm dizzy!", ms: 2800 },
  tired:       { sound: "yawn",     emote: "tired",     subtitle: "So tired…", ms: 3600 },
  overheating: { sound: "rate",     emote: "hot",       subtitle: "I'm overheating!", ms: 5000 },
  info:        { sound: "approval", emote: "happy",     subtitle: "", ms: 3000 },     // a task result with some content: sound + subtitle
  rage:        { sound: "slap",     emote: "grumpy",    subtitle: "", ms: 1800 },     // the Knight being poked: the nail swing (the subtitle depends on how angry he is)
};

export function isMochiKind(k: unknown): k is MochiKind {
  return typeof k === "string" && k in MOCHI_KINDS;
}

const BASE = "http://127.0.0.1:8765";
let mode: MochiMode = "mochi";
let readAt = 0;

/** The saved mode, re-read at most every few seconds (each window keeps its own copy). */
export async function mochiMode(): Promise<MochiMode> {
  if (Date.now() - readAt > 4000) {
    readAt = Date.now();
    try {
      const res = await fetch(`${BASE}/settings/voice`);
      if (res.ok) {
        const m = ((await res.json()) as { mochi_sound_mode?: string }).mochi_sound_mode;
        if (m === "speak" || m === "mochi" || m === "both") mode = m;
      }
    } catch { /* backend not up yet: keep the last value */ }
  }
  return mode;
}

/** Play the sound of `kind`. It is audible even with the island's UI sounds switched off: it stands in for speech. */
export function playMochi(kind: MochiKind): void {
  Sound.play(MOCHI_KINDS[kind].sound, true);
}

/** For sounds a window makes by itself (dizzy, thinking): only when the setting allows Mochi sounds. */
export async function playMochiIfOn(kind: MochiKind): Promise<boolean> {
  if ((await mochiMode()) === "speak") return false;
  playMochi(kind);
  return true;
}
