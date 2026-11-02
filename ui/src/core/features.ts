// Feature registry: the single list of everything in Lyra that can be turned on or off.
// Used by the first-run tour, the Settings → Features tab, the island dock and the Home tiles.
//
// Two kinds of switch:
//   • island features (view): hide/show a tab. Stored in localStorage (UiExtras.features),
//     which the island already watches, so it applies instantly.
//   • helpers (backend): background watchers. Stored in the backend (settings group
//     "features"); they take effect when Lyra's backend restarts.

import type { IslandViewName } from "./layout";

export interface FeatureDef {
  id: string;
  name: string;
  group: "island" | "daily" | "pc" | "helpers";
  /** One line: what it is. */
  blurb: string;
  /** How to use it: shown as "Try this" in the tour and the Features tab. */
  tip: string;
  /** Island tab this switch hides (the dock button, the Home tile and the view itself). */
  view?: IslandViewName;
  /** Backend setting that must follow the switch (applied on backend restart). */
  backend?: string;
  /** Not built yet: shown as "Coming soon", switch disabled, left out of the tour and the counts. */
  soon?: boolean;
  /** Tile colours, same as the Home tray. */
  from: string;
  to: string;
}

export const FEATURE_GROUPS: Array<{ id: FeatureDef["group"]; title: string; sub: string }> = [
  { id: "island", title: "Your island", sub: "The tabs you'll use all day." },
  { id: "daily", title: "Everyday tools", sub: "Planning and quick info." },
  { id: "pc", title: "PC tools", sub: "Look after your computer." },
  { id: "helpers", title: "Helpers in the background", sub: "Things Lyra watches for, so she can tell you at the right moment." },
];

export const FEATURES: FeatureDef[] = [
  { id: "music", name: "Music", group: "island", view: "music", from: "#ff5f7e", to: "#e0245e",
    blurb: "Now playing, with play, pause and skip right in the island.",
    tip: "Play anything in Spotify, then open the island. Or hold Right Ctrl and say “play some lo-fi”." },
  { id: "apps", name: "Apps", group: "island", view: "apps", from: "#61c9a8", to: "#27896d",
    blurb: "Your WhatsApp chats in the notch's own style.",
    tip: "Open the Apps tab on the wheel and press Connect. Or say “open apps”."},
  { id: "chat", name: "Chat", group: "island", view: "prompt", from: "#8f7bff", to: "#5a45e0",
    blurb: "Type to Lyra instead of talking.",
    tip: "Open the Chat tab and ask anything, or press Ctrl + Win for typing mode." },
  { id: "timer", name: "Timer", group: "island", view: "timer", from: "#ffb340", to: "#ff8a00",
    blurb: "Countdowns and Pomodoro that stay visible in the notch.",
    tip: "Say “set a timer for 10 minutes”. The countdown stays in the small island." },

  { id: "tab_wheel", name: "Tab wheel", group: "island", backend: "feature_tab_wheel", from: "#46d4ee", to: "#1595b8",
    blurb: "One ring of groups (Talk, Media, Time, Info, Capture, Desk); pick a group and its tabs slide out beside it.",
    tip: "Hold Ctrl+CapsLock: the ring appears at your cursor. Point at a group, then at a tab, and let go. Or right-click the island, or say “show the tab wheel”." },

  { id: "weather", name: "Weather", group: "daily", view: "weather", from: "#4aa3ff", to: "#0a64ff",
    blurb: "Today's weather for your city.",
    tip: "Open the Mochi tab, or ask “what's the weather tomorrow?”." },
  { id: "calendar", name: "Calendar", group: "daily", view: "calendar", from: "#ff6b6b", to: "#e5322d",
    blurb: "Your week and what's next.",
    tip: "Say “remind me at 6 pm to call mum” and it shows up here." },
  { id: "todo", name: "To-do", group: "daily", view: "todo", from: "#34c759", to: "#1f9d43",
    blurb: "A checklist you can tick off from the Mochi tab.",
    tip: "Say “add milk to my to-do list”, then tick it off on the Mochi tab." },
  { id: "clipboard", name: "Clipboard", group: "daily", view: "clipboard", backend: "feature_clipboard", from: "#ffcf5a", to: "#f2a20a",
    blurb: "Recent copies, ready to paste again. Drop files on it to hold them.",
    tip: "Copy a few things, then open Clipboard and click one to copy it back." },
  { id: "recognition", name: "Recognize", group: "daily", view: "recognition", from: "#4aa3ff", to: "#0a64ff",
    blurb: "Names the song that's playing on your PC.",
    tip: "Open Recognize while a song plays, or say “what song is this?”." },

  { id: "stats", name: "Stats", group: "pc", view: "stats", from: "#8f7bff", to: "#5a45e0",
    blurb: "CPU, GPU, RAM, disk, network and your busiest apps.",
    tip: "Open Stats. Tap “CPU ⇄” to switch the app list between RAM and CPU." },
  { id: "wallpaper", name: "Wallpaper", group: "pc", view: "wallpaper", from: "#4cc3a8", to: "#1f8f78",
    blurb: "Change your wallpaper from the island.",
    tip: "Open Wallpaper and pick one." },
  { id: "camera", name: "Camera", group: "pc", view: "camera", from: "#9aa1ad", to: "#4b505a",
    blurb: "A quick mirror and camera check.",
    tip: "Open Camera to check how you look before a call." },
  { id: "record", name: "Screen recording", group: "pc", view: "record", from: "#ff6b61", to: "#e0245e",
    blurb: "Record your screen through OBS Studio, with one tap. Pause and resume any time.",
    tip: "Open Record and tap the red button, or say “start recording”, “pause recording” and “stop recording”." },

  { id: "notifications", name: "Message pop-ups", group: "helpers", backend: "feature_notifications", from: "#ff6b5f", to: "#ea3b30",
    blurb: "Discord, WhatsApp and Telegram messages appear in the notch.",
    tip: "Send yourself a message on Discord, WhatsApp or Telegram and watch the notch." },
  { id: "downloads", name: "Download progress", group: "helpers", backend: "feature_downloads", from: "#5ab0ff", to: "#2a7de8",
    blurb: "Watches your Downloads folder and browser downloads.",
    tip: "Start a download. A small progress island appears." },
  { id: "status", name: "Device status", group: "helpers", backend: "feature_status", from: "#52d6c4", to: "#16a99a",
    blurb: "Battery, Wi-Fi and device changes.",
    tip: "Plug in a charger or a headset and the island reacts." },
  { id: "light_mode", name: "Light mode", group: "helpers", backend: "feature_light_mode", from: "#8bd17c", to: "#3f9d4a",
    blurb: "Uses less CPU and GPU: heavy voice and speech models load only when needed, and idle watchers check less often. Animations stay at 60 fps.",
    tip: "Leave it on. Turn it off if you want everything preloaded at start-up. Needs a restart of Lyra's backend." },
  { id: "circle_ask", name: "Circle to ask", group: "helpers", backend: "feature_circle_ask", from: "#b983ff", to: "#7b4dea",
    blurb: "Circle anything on screen and ask Lyra about it.",
    tip: "Tap Ctrl, then press it again and hold it while you draw a circle around what you're curious about. Let go to finish." },
  { id: "proactive", name: "Heads-ups", group: "helpers", backend: "proactive_enabled", from: "#ffb340", to: "#ff8a00",
    blurb: "Low battery and “you've been gaming for 2 hours” reminders.",
    tip: "Nothing to do. Lyra speaks up on her own, quietly." },
  { id: "ollama", name: "Local AI (Ollama)", group: "helpers", backend: "feature_ollama", from: "#7c8cff", to: "#4a58e0", blurb: "Use an Ollama model on this PC.", tip: "Choose Ollama under Settings → AI." },
  { id: "proactive_extra", name: "Smart heads-ups", group: "helpers", backend: "feature_proactive_extra", from: "#ffd166", to: "#f4a000", blurb: "Deadlines, breaks and resource alerts.", tip: "Configure reminder types in Settings." },
  { id: "file_search", name: "File search", group: "helpers", backend: "feature_file_search", from: "#4cc3a8", to: "#1f8f78", blurb: "Search chosen folders by file contents.", tip: "Say find my notes about a topic." },
  { id: "dev", name: "Coding helper", group: "pc", soon: true, backend: "feature_dev", from: "#7ee0a1", to: "#2fa863", blurb: "Explain errors and help with projects and Git.", tip: "Say explain this error." },
  { id: "translate", name: "Translation", group: "daily", view: "translate", backend: "feature_translate", from: "#4aa3ff", to: "#5a45e0", blurb: "Translate selected text or conversations.", tip: "Say “translate hello to French”. The result shows on the island for a moment." },
  { id: "guide", name: "Screen guide", group: "helpers", backend: "feature_guide", from: "#50c8dc", to: "#257a9b", blurb: "Walk through on-screen tasks with a pointer, and, if you allow it, click for you.", tip: "Say show me where a button is. Choose how much she may do under Screen guide below." },
  { id: "mascot", name: "Lyra mascot", group: "helpers", backend: "feature_mascot", from: "#8f7bff", to: "#5a45e0", blurb: "A little Lyra who follows your cursor and reacts while you use your PC.", tip: "Move your mouse and watch Lyra look around. Right-click her for options." },
  { id: "mascot_stay", name: "Mochi stays on screen", group: "helpers", backend: "feature_mascot_stay", from: "#a58bff", to: "#6b52e8", blurb: "The Mochi stays out on your screen (parked by the subtitles) instead of going back to the island. Needs the Lyra mascot to be on.", tip: "Turn it off and the Mochi returns to the island by itself. You can also right-click the Mochi." },
  { id: "planner", name: "Plans", group: "daily", soon: true, backend: "feature_planner", from: "#34c759", to: "#0a84ff", blurb: "Break larger requests into visible steps.", tip: "Say research a topic and save a note." },
  { id: "google", name: "Google Calendar & Gmail", group: "pc", backend: "feature_google", from: "#4c8bf5", to: "#1a56c4", blurb: "Read calendar events and emails on demand (read-only).", tip: "Connect it under Google Calendar & Gmail below, then say what's my next event." },
];

export const FEATURE_BY_ID = new Map(FEATURES.map((f) => [f.id, f]));

/** Island view → the feature that owns it. Home is always available. */
export const FEATURE_FOR_VIEW: Partial<Record<IslandViewName, string>> = Object.fromEntries(
  FEATURES.filter((f) => f.view).map((f) => [f.view as IslandViewName, f.id]),
);

/** Live Activity → the feature that owns it (switch the feature off and its compact island goes too). */
export const FEATURE_FOR_ACTIVITY: Record<string, string> = {
  music: "music", timer: "timer", clipboard: "clipboard", recognition: "recognition", volume: "volume",
};

/** `map` is UiExtras.features. Missing key = on. */
export function featureEnabled(map: Partial<Record<string, boolean>> | undefined, id: string): boolean {
  return !map || map[id] !== false;
}
