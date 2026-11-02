// Island geometry — ported from IslandTypes.swift + IslandWindowController.islandSize
// + IslandRootView.botPosition. All values are logical pixels, identical to the
// macOS app's points.

export type IslandMode = "hidden" | "compact" | "expanded";

export type IslandViewName =
  | "overview"
  | "empty"
  | "approval"
  | "question"
  | "error"
  | "finished"
  | "confused"
  | "upload"
  | "uploading"
  | "choose"
  | "mail"
  | "prompt"
  | "searching"
  | "result"
  | "note"
  | "settings"
  | "music"
  | "volume"
  | "recognition"
  | "clipboard"
  | "timer"
  | "weather"
  | "calendar"
  | "todo"
  | "stats"
  | "controls"
  | "wallpaper"
  | "camera"
  | "screen"
  | "study"
  | "translate"
  | "record"
  | "apps";

export type BotStateName =
  | "idle"
  | "working"
  | "thinking"
  | "searching"
  | "approval"
  | "question"
  | "error"
  | "finished"
  | "ratelimit"
  | "sleeping"
  | "dizzy"
  | "focus"
  | "hot"
  | "thirsty"
  | "ringing"
  | "lowbat";

export type BotEmoteName = "love" | "surprised" | "proud" | "wink" | "yawn" | "happy" | "annoyed";

export type AgentLayoutMode = "none" | "grid" | "pills" | "column";

export interface ViewLayout {
  height: number;
  botX: number;
  botY: number | null; // null = auto-centred
  botDiameter: number;
  agentMode: AgentLayoutMode;
}

// The window is a fixed 720×320 (largest view) like the macOS panel; the island is
// drawn inside it, glued to the top edge and horizontally centred.
export const PANEL_W = 720;
export const PANEL_H = 320;

// No notch on a PC: these are the hidden/compact sizes from docs/SPEC.md.
export const NOTCH_W = 150;
export const NOTCH_H = 44;   // was 38: the resting notch now has the size it used to have under the pointer
export const COMPACT_W = 274; // resting notch (was 248; 274 x 44 is the old "pointer on it" size). Pointer / notifications / activities grow from here
/** Width of the notch while the volume HUD is showing (speaker icon, label, slider). */
export const VOLUME_HUD_W = 340;
/** Height of the notch while the volume HUD is showing. */
export const VOLUME_HUD_H = 48;
export const EXPANDED_W = 600;


export const ROUNDED_CORNER = 16; // hidden / compact
export const EXPANDED_CORNER = 24;

/** Invisible hover strip that wakes the island when hidden. */
export const WAKE_STRIP_W = 240;
export const WAKE_STRIP_H = 8;

export const VIEW_LAYOUTS: Record<IslandViewName, ViewLayout> = {
  // Home + Chat keep a smaller Mochi. Everything else is Mochi-free and compact.
  overview: { height: 258, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  empty: { height: 120, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  approval: { height: 130, botX: 0, botY: null, botDiameter: 0, agentMode: "column" },
  question: { height: 130, botX: 0, botY: null, botDiameter: 0, agentMode: "column" },
  error: { height: 130, botX: 0, botY: null, botDiameter: 0, agentMode: "column" },
  finished: { height: 130, botX: 0, botY: null, botDiameter: 0, agentMode: "column" },
  // The rate-limit / "Too many hits" view keeps Mochi visible, like Home.
  confused: { height: 130, botX: 522, botY: null, botDiameter: 62, agentMode: "pills" },
  // Upload sequence draws its own Mochi on the canvas.
  upload: { height: 176, botX: 140, botY: 104, botDiameter: 0, agentMode: "column" },
  uploading: { height: 176, botX: 46, botY: 103, botDiameter: 0, agentMode: "none" },
  choose: { height: 140, botX: 0, botY: null, botDiameter: 0, agentMode: "column" },
  mail: { height: 160, botX: 0, botY: null, botDiameter: 0, agentMode: "column" },
  prompt: { height: 140, botX: 44, botY: null, botDiameter: 36, agentMode: "column" },
  searching: { height: 120, botX: 0, botY: null, botDiameter: 0, agentMode: "column" },
  result: { height: 160, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  note: { height: 120, botX: 0, botY: null, botDiameter: 0, agentMode: "column" },
  settings: { height: 160, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  // Music reference size — no Mochi.
  music: { height: 186, botX: 0, botY: 0, botDiameter: 0, agentMode: "none" },
  volume: { height: 176, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  recognition: { height: 250, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  clipboard: { height: 176, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  weather: { height: 214, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  todo: { height: 220, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  wallpaper: { height: 210, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  controls: { height: 250, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  stats: { height: 250, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  calendar: { height: 200, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  timer: { height: 176, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  camera: { height: 220, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  screen: { height: 176, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  // Study: the maths stage (problem, worked steps, answer workspace, handwriting pad) is a tall single column.
  // It runs in the tall native window (island.ts asks for it, like Chat) so the tab dock below stays visible.
  study: { height: 440, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  record: { height: 84, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  translate: { height: 176, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  apps: { height: 250, botX: 0, botY: null, botDiameter: 0, agentMode: "none" },
  // Compact intro island — smaller footprint, Mochi dead-centre.
};

// The upload views above are only the fallback geometry. Once a file is actually
// dropped the whole sequence — Mochi included — is drawn by src/upload, which
// owns its own constants (USC) straight from UploadSequenceEngine.swift.

/**
 * Peek size: these tabs first open as a slightly bigger small island (like the previews in Settings →
 * Features) showing the one or two things you glance at. A tap grows it to the full tab; the small arrow
 * in the full tab shrinks it again. Tabs that are all content (Stats, Controls, Calendar…) have no peek.
 */
export const PEEK_SIZES: Partial<Record<IslandViewName, { w: number; h: number; r: number }>> = {
  music: { w: 380, h: 84, r: 30 },
  timer: { w: 300, h: 64, r: 32 },
  weather: { w: 340, h: 72, r: 30 },
  recognition: { w: 320, h: 72, r: 32 },
  stats: { w: 340, h: 128, r: 30 },
  calendar: { w: 400, h: 128, r: 30 },
};

/**
 * Chat is a real conversation surface, not a small card.  Once expanded,
 * give it essentially the full available desktop height so the message log
 * never gets squeezed into a tiny fading/overflowing viewport.  Keep a small
 * margin from the screen edges for the floating island shadow.
 */
export function chatPromptHeight(messageCount: number): number {
  // Keep Chat at its original Dynamic-Island size. The expanded chat uses the
  // existing message viewport as the scroll area; do not turn the whole native
  // window into a full-screen chat surface.
  return Math.min(280, 200 + messageCount * 36);
}

export function islandSize(
  mode: IslandMode,
  view: IslandViewName,
  chatCount = 0,
): { w: number; h: number } {
  switch (mode) {
    case "hidden":
      // No notch to hide inside on a PC: the island retracts to zero height and
      // slides into the top edge of the screen instead of sitting there as a bar.
      return { w: NOTCH_W, h: 0 };
    case "compact":
      return { w: COMPACT_W, h: NOTCH_H };
    case "expanded": {
      const h = view === "prompt" ? chatPromptHeight(chatCount) : VIEW_LAYOUTS[view].height;
      return { w: EXPANDED_W, h };
    }
  }
}

export interface BotPlacement {
  cx: number;
  cy: number;
  diameter: number;
  opacity: number;
}


// Project colours (IslandConst.projectColors)
const PROJECT_COLORS: Record<string, string> = {
  korus: "#FF5A4E",
  "sbe hub": "#2EC4A0",
  "morning ai brief": "#F29B38",
  "publication ig": "#7C5CFF",
  "ig post": "#7C5CFF",
  "louisraille.fr": "#38BDF8",
  louisraille: "#38BDF8",
  "notch buddy": "#EC4899",
  "notch-buddy": "#EC4899",
  notchbuddy: "#EC4899",
};

const FALLBACK_COLORS = ["#22C55E", "#EAB308", "#60A5FA", "#E879F9"];

export function colorForProject(name: string): string {
  const key = name.toLowerCase().trim();
  const exact = PROJECT_COLORS[key];
  if (exact) return exact;
  for (const [k, c] of Object.entries(PROJECT_COLORS)) {
    if (key.startsWith(k) || key.includes(k)) return c;
  }
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return FALLBACK_COLORS[Math.abs(hash) % FALLBACK_COLORS.length];
}

// Card wash colours (CardBackground.washColor)
export type Wash = "red" | "green" | "pink" | "amber" | "cyan" | "indigo" | "soft" | null;

export function washRGBA(wash: Wash): string {
  switch (wash) {
    case "red":
      return "rgba(244,80,94,0.55)";
    case "green":
      return "rgba(52,211,153,0.5)";
    case "pink":
      return "rgba(244,114,182,0.55)";
    case "amber":
      return "rgba(245,165,36,0.42)";
    case "cyan":
      return "rgba(34,211,238,0.38)";
    case "indigo":
      return "rgba(99,102,241,0.5)";
    case "soft":
      return "rgba(255,255,255,0.08)";
    default:
      return "rgba(0,0,0,0)";
  }
}
