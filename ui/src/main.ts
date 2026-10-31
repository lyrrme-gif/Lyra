// Entry point: full Coucou UI shell + Lyra brain connection.

import "./style.css";
import "./apple.css";
import "./tokens.css";
import "./home.css";
import "./stats.css";
import "./controls.css";
import "./wallpaper.css";
import "./study.css";
import "./homex.css";
import "./home-apple.css";
import "./messages-apple.css";
import { Bridge, IS_TAURI, onEvent } from "./core/bridge";
import { Sound } from "./core/sound";
import { State, type Settings } from "./core/state";
import { Island } from "./island/island";
import { mountNotchMochi } from "./island/notchMochi";
import { onMochiSound } from "./mascot/dock";
import { isMochiKind, playMochi } from "./core/mochiSound";
import { connectLyra } from "./lyra/client";
import { installTracer, trace } from "./core/trace";
import { installIslandTrace } from "./core/traceIsland";

installTracer();

async function main() {
  const root = document.getElementById("root");
  if (!root) return;

  void Sound.preload();

  const island = new Island(root);
  installIslandTrace(island);
  mountNotchMochi();
  // Sounds the floating Mochi asks for (it only has the animation): the island owns the audio.
  void onMochiSound((k) => { if (isMochiKind(k)) playMochi(k); });

  const boot = await Bridge.boot();
  trace("boot", boot ? `Lyra UI ${boot.version} booted, screen ${boot.screen.width}x${boot.screen.height} @${boot.screen.scale}x, cursorPoll=${boot.cursorPoll}` : "boot info not available (plain browser?)");
  if (boot) {
    State.settings = { ...State.settings, ...boot.settings };
  }
  island.applySettings();
  State.loadIntegrationTasks();
  if (boot && !boot.cursorPoll) island.followPageCursor();

  await onEvent<{ x: number; y: number }>("cursor", ({ x, y }) => island.onCursor(x, y));

  const setPaused = (on: boolean) => {
    if (State.paused === on) return;
    State.paused = on;
    void Bridge.setPaused(on);
  };

  await onEvent<string>("tray", (what) => {
    switch (what) {
      case "settings":
        setPaused(false);
        void Bridge.openSettingsWindow();
        break;
      case "open":
        setPaused(false);
        island.alert(State.defaultView());
        break;
      case "chat":
        setPaused(false);
        island.alert("prompt");
        break;
      case "quit": {
        // Play the closing animation, then really quit (Rust also force-quits after 1.5 s).
        document.getElementById("island")?.classList.add("quitting");
        document.getElementById("lyra-dock")?.classList.add("quitting");
        window.setTimeout(() => void Bridge.quit(), 520);
        break;
      }
      case "pause":
        setPaused(!State.paused);
        if (State.paused) island.fsm.forceHidden();
        else island.reveal();
        break;
    }
  });

  await onEvent<null>("screen-changed", () => void Bridge.reposition());
  // The tab wheel window (src/wheel) reports a choice, or that it went away without one.
  await onEvent<string>("wheel-pick", (view) => island.onWheelPick(view));
  await onEvent<null>("wheel-closed", () => island.onWheelClosed());

  // System speaker volume changed: show the volume HUD in the notch.
  await onEvent<{ level: number; muted: boolean }>("volume", (v) =>
    island.onVolume(v.level, v.muted),
  );

  await onEvent<Settings>("settings-changed", (s) => {
    State.settings = { ...State.settings, ...s };
    island.applySettings();
    State.loadIntegrationTasks();
  });

  island.launch();

  // First launch opens the mandatory setup wizard in the Settings window.
  try {
    if (window.localStorage.getItem("lyra-onboarding-complete") !== "1") {
      window.setTimeout(() => void Bridge.openSettingsWindow(), 350);
    }
  } catch { /* localStorage may be unavailable in hardened WebViews */ }

  // Only addition: Lyra brain over WebSocket (does not change Coucou UI)
  // "Keep chat": restore the last conversation and save it as it changes.
  const CHAT_KEY = "lyra-chat-keep";
  if (State.extras.keepChat) {
    try {
      const saved = JSON.parse(window.localStorage.getItem(CHAT_KEY) || "[]");
      if (Array.isArray(saved) && saved.length) State.chatHistory = saved;
    } catch { /* ignore */ }
  }
  let chatSaveTimer = 0;
  State.subscribe(() => {
    if (!State.extras.keepChat) return;
    window.clearTimeout(chatSaveTimer);
    chatSaveTimer = window.setTimeout(() => {
      try { window.localStorage.setItem(CHAT_KEY, JSON.stringify(State.chatHistory.slice(-60).map((c) => ({ id: c.id, role: c.role, content: c.content })))); } catch { /* ignore */ }
    }, 400);
  });
  connectLyra(island);

  if (!IS_TAURI) {
    island.followPageCursor();
    document.addEventListener("click", () => Sound.resume(), { once: true });
  }
}

void main();
