// App state — mirror of AppState.swift (the parts the island needs).

import type { BotEmoteName, BotStateName, IslandMode, IslandViewName } from "./layout";
import { FEATURE_FOR_ACTIVITY, FEATURE_FOR_VIEW, featureEnabled } from "./features";
import type { AutoHideMode } from "./autohide";

/** Live Activities the island can show. Missing key = enabled. */
export const LIVE_ACTIVITIES = ["music", "timer", "volume", "clipboard", "recognition", "notifications", "listening", "brightness", "devices", "calls", "reminders", "downloads"] as const;
export type LiveActivity = (typeof LIVE_ACTIVITIES)[number];

/** Appearance extras written by the Settings window (same localStorage trick as theme/position). */
export interface UiExtras {
  shapeNotched: "notch" | "island";
  shapeNotchless: "notch" | "island";
  activities: Partial<Record<LiveActivity, boolean>>;
  /** Per-activity light show (edge glow, shockwaves, sparks, notch buzz). Missing key = on. Sounds are separate. */
  glow: Partial<Record<LiveActivity, boolean>>;
  compactStyle: CompactStyle;
  /** Keep the island hidden even while music plays. */
  hideCompact: boolean;
  keepChat: boolean;
  keepClipboard: boolean;
  /** How you move between tabs: the tab wheel, a scroller (a capsule showing three tabs at a time), or both. */
  navStyle: "wheel" | "scroller" | "both";
  /** Feature switches (Settings → Features). Missing key = on. See core/features.ts. */
  features: Partial<Record<string, boolean>>;
}
export const EXTRAS_KEY = "lyra-ui-extras";

export const DEFAULT_EXTRAS: UiExtras = { shapeNotched: "notch", shapeNotchless: "island", activities: {}, glow: {}, compactStyle: "black", hideCompact: false, keepChat: false, keepClipboard: false, navStyle: "wheel", features: {} };

export function readExtras(): UiExtras {
  try {
    const raw = window.localStorage.getItem(EXTRAS_KEY);
    if (raw) {
      const saved = JSON.parse(raw) as Record<string, unknown>;
      // Prefs removed from Settings (outline, idle circle, swipe, tab-bar mode) are dropped, not carried around.
      for (const k of ["outline", "outlineRest", "idleStyle", "swipeNav", "dockMode", "cleanIsland"]) delete saved[k];
      // "Scroller" used to come with a separate "keep the wheel" switch (on unless turned off): that pair is now one choice.
      if (saved.navStyle === "scroller" && saved.keepWheel === true) saved.navStyle = "both";   // only an old explicit "keep the wheel" pair; a plain "scroller" saved now stays "scroller"
      delete saved.keepWheel;
      if (saved.navStyle !== "wheel" && saved.navStyle !== "scroller" && saved.navStyle !== "both") delete saved.navStyle;
      return { ...DEFAULT_EXTRAS, ...saved };
    }
  } catch { /* hardened WebView */ }
  return { ...DEFAULT_EXTRAS };
}
export function writeExtras(patch: Partial<UiExtras>): UiExtras {
  const next = { ...readExtras(), ...patch };
  try { window.localStorage.setItem(EXTRAS_KEY, JSON.stringify(next)); } catch { /* ignore */ }
  return next;
}

/** The only five themes. "pure-black" is shown as "Black" in Settings. */
export type IslandTheme = "pure-black" | "midnight" | "aurora" | "dynamic" | "frost";
/** Compact island material: independent from the expanded theme. */
export type CompactStyle = "black" | "frost" | "glass";
export type IslandPosition = "floating" | "attached";
export type PillBadge = "approval" | "finished" | "error";

export interface AgentTask {
  id: string;
  name: string;
  color: string;
  state: BotStateName;
  stepIndex: number;
  steps: string[];
  emote?: BotEmoteName | null;
  pillBadge?: PillBadge | null;
  /** The built-in white "Lyra" pill: normal mode, always first and always on. */
  isNormal?: boolean;
}

/** Id of the white normal-mode pill (displayed as "Lyra"). */
const NORMAL_ID = "lyra";

const RESULTS_ROW: ChatMessage[] = [{ id: 0, role: "assistant", content: "" }];

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  /** Rich web results belong to the assistant message that triggered them. */
  search?: SearchResult;
}

export type PromptContext =
  | { kind: "window"; appName: string; title: string; url?: string }
  | { kind: "file"; name: string; path?: string };

export interface ResultItem {
  label: string;
  detail: string;
  url?: string;
  source?: string;
  thumbnail?: string;
  published?: string;
  channel?: string;
  duration?: string;
}

/** Picture, short description and official links shown above web results (from Wikipedia/Wikidata). */
export interface KnowledgeCard {
  title: string;
  subtitle?: string;
  extract?: string;
  image?: string;
  url?: string;
  /** Name of the main link under the card ("Wikipedia", "TMDB", "Website"). */
  urlLabel?: string;
  socials: Array<{ label: string; url: string }>;
  /** Where to watch it in your country (Netflix, Crunchyroll ...): films, series and anime only. */
  watch?: Array<{ label: string; url: string; logo?: string }>;
}

export interface SearchResult {
  title: string;
  items: ResultItem[];
  note?: string;
  card?: KnowledgeCard;
  /** Who answered: "Google", "DuckDuckGo", "Tavily", "YouTube" ... */
  provider?: string;
  /** Google's related searches: each one runs a new search when tapped. */
  related?: string[];
  /** Spotify: more songs by the same artist, shown under the list ("More by The Weeknd"). */
  more?: ResultItem[];
  moreTitle?: string;
}

export interface Settings {
  soundEnabled: boolean;
  soundVolume: number;
  autoCloseInterval: number;
  compactAutoHideInterval: number;
  absenceInterval: number;
  screen: "primary" | "cursor" | `display:${number}`;
  autostart: boolean;
  /** Claude model used by the chat. */
  model: string;
  /** Keep the compact notch visible even when nothing is playing or running. */
  alwaysShowNotch: boolean;
  /** How the hidden island is called back from the top edge. */
  wakeMode: "dwell" | "instant";
  /** Settings → Island → Auto-hide. Screen sharing hides the notch in every mode. */
  islandAutoHide: AutoHideMode;
}

export const DEFAULT_SETTINGS: Settings = {
  soundEnabled: true,
  soundVolume: 0.12,
  autoCloseInterval: 15,
  compactAutoHideInterval: 8,
  absenceInterval: 180,
  screen: "primary",
  autostart: false,
  model: "claude-opus-5",
  alwaysShowNotch: true,
  wakeMode: "dwell",
  islandAutoHide: "always",
};

type Listener = () => void;

class AppState {
  mode: IslandMode = "hidden";
  /** Visual skin for the Dynamic Island. Stored locally so it does not depend on backend schema. */
  theme: IslandTheme = this.loadTheme();
  /** Floating keeps a small breathing gap above the Island; attached glues it to the top edge. */
  islandPosition: IslandPosition = this.loadIslandPosition();
  extras: UiExtras = readExtras();
  /** Latest battery reading from status_watch. null = desktop / unknown. */
  battery: { percent: number; charging: boolean } | null = null;
  view: IslandViewName = "overview";

  tasks: AgentTask[] = [];
  focusId: string | null = null;
  /** Mods last received from Lyra (undefined until the first fetch). */

  stateOverride: BotStateName | null = null;

  /** Cursor in logical screen pixels, origin top-left (like AppState.mousePosition). */
  mouse = { x: 0, y: 0 };
  /** Cursor relative to the island's top-left corner. */
  mouseInIsland = { x: 0, y: 0 };

  isPinned = false;
  /** The Music tab's lyrics are open: the panel must not auto-close. */
  lyricsOpen = false;
  /** Recognize tab is listening: the island must not auto-close mid-capture. */
  recognitionBusy = false;
  paused = false;

  uploadProgress = 0;
  uploadDuration = 2.4;
  fileDragOver = false;

  promptContext: PromptContext | null = null;
  droppedFile: { name: string; path: string } | null = null;
  noteMessage: string | null = null;
  /** Peek-capable tabs (see PEEK_SIZES) open small; false once the user grew one to the full tab. */
  peek = true;
  /** performance.now() of the last time the panel opened: the click that opened it must not also grow the small card. */
  openedAt = 0;
  /** Extra height (px) the Chat tab needs under the typing pill for the last answer / history; 0 = just the pill. */
  chatExtra = 0;
  /** The Chat tab keeps no conversation any more: it is a small typing box that only
   *  grows while rich search results are on screen. One placeholder row stands for those. */
  chatVisible(): ChatMessage[] {
    return this.searchResult && (this.searchResult.items.length || this.searchResult.card) ? RESULTS_ROW : [];
  }
  searchResult: SearchResult | null = null;
  /** Search event can arrive just before or just after the assistant reply. */
  pendingSearchResult: SearchResult | null = null;
  chatHistory: ChatMessage[] = [];

  lastActivity = performance.now();

  settings: Settings = { ...DEFAULT_SETTINGS };

  private listeners = new Set<Listener>();

  private loadTheme(): IslandTheme {
    try {
      const value = window.localStorage.getItem("lyra-island-theme");
      const migrated: Record<string, IslandTheme> = {
        midnight: "midnight", graphite: "midnight", "space-black": "pure-black",
        aurora: "aurora", liquid: "frost", glass: "frost", classic: "midnight",
        "pure-black": "pure-black", oled: "pure-black", titanium: "midnight",
        "macos-dark": "midnight", "macos-light": "midnight", dynamic: "dynamic", frost: "frost",
      };
      if (value && migrated[value]) {
        const theme = migrated[value];
        if (theme !== value) window.localStorage.setItem("lyra-island-theme", theme);
        return theme;
      }
    } catch { /* localStorage can be unavailable in hardened WebViews */ }
    return "midnight";
  }

  private loadIslandPosition(): IslandPosition {
    try {
      return window.localStorage.getItem("lyra-island-position") === "attached" ? "attached" : "floating";
    } catch { return "floating"; }
  }

  setIslandPosition(position: IslandPosition) {
    this.islandPosition = position;
    try { window.localStorage.setItem("lyra-island-position", position); } catch { /* ignore */ }
    this.notify();
  }

  setTheme(theme: IslandTheme) {
    this.theme = theme;
    try { window.localStorage.setItem("lyra-island-theme", theme); } catch { /* ignore */ }
    this.notify();
  }

  /** Re-read theme/position from localStorage (Settings window writes the same keys). */
  reloadAppearance() {
    const theme = this.loadTheme();
    const position = this.loadIslandPosition();
    const extras = readExtras();
    const extrasChanged = JSON.stringify(extras) !== JSON.stringify(this.extras);
    if (theme !== this.theme || position !== this.islandPosition || extrasChanged) {
      this.theme = theme;
      this.islandPosition = position;
      this.extras = extras;
      this.notify();
    }
  }

  /** True unless the user switched this feature off in Settings → Features. */
  featureOn(id: string): boolean {
    return featureEnabled(this.extras.features, id);
  }

  /** Home is always allowed; every other view follows its feature switch. */
  viewAllowed(view: IslandViewName): boolean {
    const id = FEATURE_FOR_VIEW[view];
    return !id || this.featureOn(id);
  }

  /** True unless the user switched this Live Activity (or the feature behind it) off in Settings. */
  activityOn(a: LiveActivity): boolean {
    const owner = FEATURE_FOR_ACTIVITY[a];
    if (owner && !this.featureOn(owner)) return false;
    if (a === "music" && this.extras.hideCompact) return false;
    return this.extras.activities[a] !== false;
  }

  /** True unless the user switched the glow of this Live Activity off in Settings → Live Activities. */
  glowOn(a: LiveActivity): boolean {
    return this.extras.glow?.[a] !== false;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Marks the UI dirty; the island re-renders on the next frame. */
  notify() {
    for (const fn of this.listeners) fn();
  }

  get focusTask(): AgentTask | null {
    return this.tasks.find((t) => t.id === this.focusId) ?? this.tasks[0] ?? null;
  }

  get effectiveState(): BotStateName {
    return this.stateOverride ?? this.focusTask?.state ?? "idle";
  }

  get otherTasks(): AgentTask[] {
    return this.tasks.filter((t) => t.id !== this.focusId);
  }

  setFocus(id: string) {
    const t = this.tasks.find((x) => x.id === id);
    if (!t) return;
    this.focusId = id;
    t.pillBadge = null;
    this.notify();
  }

  updateTask(id: string, state: BotStateName) {
    const t = this.tasks.find((x) => x.id === id);
    if (!t) return;
    t.state = state;
    this.notify();
  }

  setPillBadge(id: string, badge: PillBadge | null) {
    const t = this.tasks.find((x) => x.id === id);
    if (!t) return;
    t.pillBadge = badge;
    this.notify();
  }

  /** The single Lyra task every view hangs off. */
  loadIntegrationTasks() {
    this.tasks = this.tasks.filter((t) => t.id === NORMAL_ID);
    let normal = this.tasks.find((t) => t.id === NORMAL_ID);
    if (!normal) {
      normal = {
        id: NORMAL_ID, name: "Lyra", color: "#F5F6F8", state: "idle", stepIndex: 0,
        steps: [],
      };
      this.tasks.unshift(normal);
    }
    normal.isNormal = true;
    if (!this.focusId || !this.tasks.some((t) => t.id === this.focusId)) {
      this.focusId = this.tasks[0]?.id ?? null;
    }
    this.notify();
  }

  defaultView(): IslandViewName {
    // Lyra behaviour: overview if tasks exist, otherwise empty — not chat
    return this.tasks.length === 0 ? "empty" : "overview";
  }
}

export const State = new AppState();
