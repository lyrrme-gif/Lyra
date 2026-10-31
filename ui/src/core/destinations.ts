// Tab registry: every place the island can go, in one list.
//
// The launcher (its own window, see src/wheel) draws itself from this list, and so does the
// "open this tab" path used by the wheel and by Lyra (voice / backend). This file is pure data
// on purpose: the wheel window loads it without loading the island. The `view` id is the shared contract: the
// backend sends `{type: "open_view", view: "<id>"}` and backend/actions/tabs.py maps spoken
// names to the same ids. To add tab number 20, add one entry here (and its spoken names there).
//
// The launcher shows PAGES as its groups (a ring of them); clicking one keeps the ring and slides that
// group's tabs out beside it (to its right or left). `page` is an index into PAGES. Keep a group to 4 tabs
// or fewer, and the ring to 6 groups or fewer (the tabs sit in one short row next to the group, see
// wheelGeometry.tabsBeside; scripts/check-constellation.mjs checks the layout).
//
// Study, Sounds, Controls and Translate are NOT tabs any more: they have no entry here, so they are not on the
// wheel, the dock or the scroller. Their commands still work ("set Spotify volume to 50", "shut down the PC",
// "translate hello to French", "quiz me"): the backend/island still opens those views on their own to show the result.

import type { ICONS } from "../views/icons";
import type { IslandViewName } from "./layout";

export const PAGES = [
  { id: "talk", label: "Talk" },
  { id: "media", label: "Media" },
  { id: "time", label: "Time" },
  { id: "info", label: "Info" },
  { id: "capture", label: "Capture" },
  { id: "desk", label: "Desk" },
] as const;

export interface Destination {
  view: IslandViewName;
  label: string;
  icon: keyof typeof ICONS;
  /** Which wheel group it lives in: an index into PAGES. */
  page: number;
  /** Tile colours, same as the Features list (the wheel itself is white; these are for other lists). */
  from: string;
  to: string;
  stroke?: number;
  evenodd?: boolean;
}

export const DESTINATIONS: Destination[] = [
  // Talk
  { view: "overview", label: "Mochi", icon: "mochi", page: 0, from: "#a58bff", to: "#6b52e8", evenodd: true },
  { view: "prompt", label: "Chat", icon: "messageFill", page: 0, from: "#8f7bff", to: "#5a45e0" },
  // Media
  { view: "music", label: "Music", icon: "noteFill", page: 1, from: "#ff5f7e", to: "#e0245e" },
  { view: "recognition", label: "Recognize", icon: "waveform", page: 1, from: "#4aa3ff", to: "#0a64ff", stroke: 2.4 },
  // Time
  { view: "calendar", label: "Calendar", icon: "calendarFill", page: 2, from: "#ff6b6b", to: "#e5322d", evenodd: true },
  { view: "timer", label: "Timer", icon: "timerFill", page: 2, from: "#ffb340", to: "#ff8a00", evenodd: true },
  { view: "todo", label: "To-do", icon: "listBullet", page: 2, from: "#34c759", to: "#1f9d43" },
  // Info
  { view: "weather", label: "Weather", icon: "cloudFill", page: 3, from: "#4aa3ff", to: "#0a64ff" },
  { view: "stats", label: "Stats", icon: "chartFill", page: 3, from: "#8f7bff", to: "#5a45e0" },
  // Capture
  { view: "camera", label: "Camera", icon: "cameraFill", page: 4, from: "#9aa1ad", to: "#4b505a", evenodd: true },
  { view: "record", label: "Record", icon: "record", page: 4, from: "#ff6b61", to: "#e0245e", evenodd: true },
  { view: "clipboard", label: "Clipboard", icon: "clipboardFill", page: 4, from: "#ffcf5a", to: "#f2a20a", evenodd: true },
  // Desk (the apps, WhatsApp, Spotify... are picked inside the Apps tab)
  { view: "wallpaper", label: "Wallpaper", icon: "photo", page: 5, from: "#4cc3a8", to: "#1f8f78", stroke: 2.2 },
  { view: "apps", label: "Apps", icon: "apps", page: 5, from: "#61c9a8", to: "#27896d" },
];

export function destinationFor(view: string): Destination | undefined {
  return DESTINATIONS.find((d) => d.view === view);
}

/**
 * Views that are NOT tabs (not on the wheel, the dock or the scroller) but that Lyra still opens by herself to
 * show the result of a command: "translate hello to French", "set Spotify volume to 50", "turn off bluetooth",
 * "quiz me". `page` is -1: they belong to no group.
 */
export const HIDDEN_DESTINATIONS: Destination[] = [
  { view: "translate", label: "Translate", icon: "translate", page: -1, from: "#4aa3ff", to: "#5a45e0", evenodd: true },
  { view: "volume", label: "Sounds", icon: "speakerFill", page: -1, from: "#ff6b8a", to: "#e0245e" },
  { view: "controls", label: "Controls", icon: "sliders", page: -1, from: "#46d4ee", to: "#1595b8", stroke: 2.2 },
  { view: "study", label: "Study", icon: "listBullet", page: -1, from: "#8f7bff", to: "#5a45e0" },
];

/** Where Lyra (voice / backend) may send the island: a tab or one of the hidden views. The wheel and dock use `destinationFor` (tabs only). */
export function openableFor(view: string): Destination | undefined {
  return destinationFor(view) ?? HIDDEN_DESTINATIONS.find((d) => d.view === view);
}
