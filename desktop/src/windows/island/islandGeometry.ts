/** IslandGeometry — collaborator extracted from island.ts */
import type { Island } from "./island";
import { ATTACHED_ROOM, ATTACHED_ROOM_VIEWS } from "./islandConstants";

import { Bridge } from "../../shared/core/bridge";
import { State } from "../../shared/core/state";
import { PULSE_H, PULSE_W } from "./pulse";
import { deckHeight } from "./views/searchDeck";
import { translatePillActive, translatePillSize } from "./views/translate";
import { APPS_CHAT_H, APPS_CHAT_W, appsChatActive } from "./views/apps";
import { EXPANDED_CORNER, EXPANDED_W, PANEL_W, ROUNDED_CORNER, VOLUME_HUD_H, VOLUME_HUD_W, islandSize, PEEK_SIZES } from "../../shared/core/layout";
import { traceLimited } from "../../shared/core/trace";

export class IslandGeometry {
  constructor(private readonly i: Island) {}

  targetSize(): { w: number; h: number; r: number } {
    const s = this.i.targetSizeBase();
    if (State.islandPosition === "attached" && State.mode === "expanded" && ATTACHED_ROOM_VIEWS.has(State.view) && s.h <= 270) {
      return { ...s, h: s.h + ATTACHED_ROOM };
    }
    return s;
  }

  targetSizeBase(): { w: number; h: number; r: number } {
    // Lyra listening / thinking / speaking owns the compact notch: a slightly wider pill (Mochi + text or wave).
    if (this.i.lyraStatusKind !== null && State.mode === "compact") {
      // The same look as the setup previews: a roomy pill with the Mochi, the words / bars and the round button.
      return { w: 392, h: 58, r: 29 };
    }
    // The volume HUD only ever lives in the compact notch (see onVolume).
    if (this.i.volumeHudOn && State.mode === "compact" && !this.i.downloadIsSecondary()) {
      if (this.i.volumeHud.dataset.activityKind === "download" && this.i.volumeHud.dataset.downloadCompact !== "1") {
        return { w: 380, h: 58, r: 29 }; // roomy download card: tile, name, stats, progress bar
      }
      return { w: VOLUME_HUD_W, h: VOLUME_HUD_H, r: VOLUME_HUD_H / 2 };
    }
    // An "added" pulse swells the compact notch into a wide pill (the spring gives it the bounce).
    const base = islandSize(State.mode, State.view, State.chatVisible().length);
    if (this.i.pulseOn && State.mode === "compact") {
      // A mode switch only swells the notch a little (like a notification); "added" opens the wide pill.
      return this.i.pulseCompact ? { w: base.w + 64, h: base.h + 12, r: (base.h + 12) / 2 } : { w: PULSE_W, h: PULSE_H, r: PULSE_H / 2 };
    }
    // A notification (reminder, WhatsApp, Discord...) swells the notch a bit while it is showing.
    if (this.i.toastOn && State.mode === "compact") return { w: base.w + 56, h: base.h + 12, r: (base.h + 12) / 2 };
    // Pointer resting on the compact notch: it swells a little, as if asking "open me?" (see invite.ts).
    const asking = State.mode === "compact" && this.i.wasInIsland && !this.i.toastOn;
    if (asking) return { w: base.w + 40, h: base.h + 10, r: (base.h + 10) / 2 };
    // Chat has three deliberate sizes: a comfortable empty composer, a larger
    // everyday conversation surface, and the full-height research/search surface.
    // Normal conversations should feel substantial without becoming as tall as
    // a results-heavy web search.
    if (State.mode === "expanded" && State.view === "prompt") {
      // Rest: just a composer pill. It grows when you ask, shows the answer, then shrinks back.
      if (State.chatVisible().length === 0) {
        if (State.chatExtra > 0) return { w: 460, h: 54 + State.chatExtra, r: 28 };
        return { w: 420, h: 54, r: 27 };
      }
      if (this.i.searchResultOpen) {
        // Exact height, so nothing ever scrolls: every part of the result deck has a fixed height (views/searchDeck.ts).
        if (State.searchResult) return { w: EXPANDED_W, h: deckHeight(State.searchResult), r: EXPANDED_CORNER };
      }
      return { w: EXPANDED_W, h: 360, r: EXPANDED_CORNER };
    }
    if (State.mode === "expanded" && State.view === "translate") return translatePillActive() ? translatePillSize() : { w: 440, h: 176, r: 34 };
    if (State.mode === "expanded" && State.view === "record") return { w: 340, h: 84, r: 34 };
    const pk = PEEK_SIZES[State.view];
    if (State.mode === "expanded" && State.peek && pk) return { ...pk };
    // Music-sized panel: compact card used by every utility tab (not the full 600px shell).
    const MUSIC_W = 400;
    const MUSIC_H = 176;
    const MUSIC_R = 40;
    if (State.mode === "expanded" && State.view === "music") {
      return { w: MUSIC_W, h: MUSIC_H, r: MUSIC_R };
    }
    // Same footprint as Music for timer / volume / clipboard / recognition / etc.
    // Home (overview) + Chat (prompt) keep the wider shell so Mochi + content fit.
    const compactViews = new Set([
      "volume", "recognition", "clipboard", "timer", "weather", "calendar", "todo", "camera",
      "screen", "result", "searching", "settings", "note", "empty",
      "choose", "mail",
    ]);
    if (State.mode === "expanded" && compactViews.has(State.view)) {
      const tall = State.view === "recognition" || State.view === "camera" || State.view === "todo";
      // Weather is a little taller than the other compact tabs: the big temperature, the hourly row and the
      // degrees under it need room (they were squeezed against the bottom edge).
      const wxExtra = State.view === "weather" ? 30 : 0;
      return { w: MUSIC_W, h: tall ? Math.min(base.h, 220) : MUSIC_H + wxExtra, r: MUSIC_R };
    }
    // Apps: an open conversation (WhatsApp) is a big chat; the chat list keeps the normal Apps size.
    if (State.mode === "expanded" && State.view === "apps" && appsChatActive()) {
      return { w: APPS_CHAT_W, h: APPS_CHAT_H, r: EXPANDED_CORNER };
    }
    const h = base.h;
    // Compact: a full pill (radius = half the height), the same shape as the Settings preview. Hidden keeps the small corner.
    const r = State.mode === "expanded" ? EXPANDED_CORNER : State.mode === "compact" ? h / 2 : ROUNDED_CORNER;
    return { w: base.w, h, r };
  }

  animateGeometry(shrinking: boolean) {
    const { w, h, r } = this.i.targetSize();
    traceLimited("geometry", 120, "size", `island ${shrinking ? "shrinking" : "growing"} ${Math.round(this.i.width.value)}x${Math.round(this.i.height.value)} -> ${w}x${h} (corner ${r}) for ${State.mode}/${State.view}`);
    if (shrinking) {
      this.i.width.curveTowards(w);
      this.i.height.curveTowards(h);
      this.i.radius.curveTowards(r);
    } else {
      this.i.width.springTo(w);
      this.i.height.springTo(h);
      this.i.radius.springTo(r);
    }
    this.i.ensureRunning();
  }

  applyGeometry() {
    const w = this.i.width.value;
    const hh = this.i.height.value;
    const r = this.i.radius.value;
    this.i.islandEl.style.width = `${w}px`;
    this.i.islandEl.style.height = `${hh}px`;

    const attached = State.islandPosition === "attached";

    // Attached mode restores the old Lyra look: square top edge flush with the
    // screen, only the bottom corners rounded (classic notch / drop-zone style).
    // Floating keeps the fully-rounded Dynamic Island silhouette.
    if (attached) {
      this.i.islandEl.style.borderRadius = `0 0 ${r}px ${r}px`;
    } else {
      this.i.islandEl.style.borderRadius = `${r}px`;
    }

    // Attached sits flush at y=0 (old behaviour). Floating has a small breathing gap.
    const islandTop = attached ? 0 : 18;
    this.i.islandEl.style.transform = `translateX(-50%) translateY(${islandTop}px)`;

    const dockTop = islandTop + hh + 6;
    this.i.tabDock.el.style.top = `${dockTop}px`;
    this.i.tabDock.el.style.zIndex = "1000";
    // Whether the dock (it holds the scroller) shows is decided in views/dock.ts sync(): hidden for "Tab wheel", shown while expanded for "Scroller" / "Both".
    // These follow the island as it resizes, so they belong here rather than in
    // the state-driven DOM sync.

    // Position the secondary island, then tell the native layer what is clickable.
    for (const side of ["left", "right"] as const) {
      const slot = this.i.slots[side];
      if (!slot.shown) continue;
      const sr = this.i.secondaryRect(slot, side, (PANEL_W - w) / 2, w, islandTop, hh);
      slot.el.style.left = `${sr.x}px`;
      slot.el.style.top = `${sr.y}px`;
    }
    this.i.pushHitShapes({ x: (PANEL_W - w) / 2, y: islandTop, w, h: hh });
  }

  /**
   * Native click-through. The clickable shapes are measured from the real elements
   * (island, dock, secondary island), so what you see is exactly what takes the click;
   * everything else behaves like the desktop behind it.
   */
  pushHitShapes(fallback?: { x: number; y: number; w: number; h: number }) {
    const box = (el: HTMLElement) => {
      const r = el.getBoundingClientRect();
      return { x: r.left, y: r.top, w: r.width, h: r.height };
    };
    const ib = box(this.i.islandEl);
    const rect = State.mode === "hidden" || ib.w < 2 ? (fallback ?? this.i.islandRect()) : ib;
    const extra: { x: number; y: number; w: number; h: number }[] = [];
    for (const side of ["left", "right"] as const) {
      if (!this.i.slots[side].shown) continue;
      const sb = box(this.i.slots[side].el);
      if (sb.w > 2) extra.push(sb);
    }
    if (State.mode === "expanded") {
      const db = box(this.i.tabDock.el);
      if (db.w > 2) extra.push(db);
    }
    const key = [rect.x, rect.y, rect.w, rect.h, ...extra.flatMap((e) => [e.x, e.y, e.w, e.h])];
    const p = this.i.pushedKey;
    if (p.length !== key.length || key.some((v, k) => Math.abs(v - p[k]) > 0.5)) {
      this.i.pushedKey = key;
      void Bridge.setIslandRect(rect.x, rect.y, rect.w, rect.h, extra);
    }
  }

  /** Island rect in window coordinates (origin top-left of the 720×320 window). */
  islandRect(): { x: number; y: number; w: number; h: number } {
    const w = this.i.width.value;
    const hh = this.i.height.value;
    return { x: (PANEL_W - w) / 2, y: 0, w, h: hh };
  }

  // ── Window collapse (hidden → tiny wake strip, zero polling) ────────────────

  /**
   * Tall native window for Chat / search results. Growing happens at once (the island needs the room before it
   * opens). Releasing it must wait for the shrink animation: shrinking the window first cut the still-tall island
   * off with a flat edge (search results -> Mochi tab looked "cut" and only then got small).
   */
  /** Views that need the tall native window: Chat, and the Apps tab while a conversation is open. */
  wantsTall(mode: string, view: string): boolean {
    return mode === "expanded" && (view === "prompt" || (view === "apps" && appsChatActive()));
  }

  chatPanel(tall: boolean) {
    this.i.chatPanelWanted = tall;
    if (this.i.chatPanelTimer != null) {
      window.clearTimeout(this.i.chatPanelTimer);
      this.i.chatPanelTimer = null;
    }
    if (tall) {
      void Bridge.setChatPanel(true);
      return;
    }
    // curveTowards() runs 340 ms; a little extra so the last frames are never clipped. It must stay BELOW the 420 ms
    // collapse timer (updateWindowCollapsed): releasing after the wake-strip resize would undo it.
    this.i.chatPanelTimer = window.setTimeout(() => {
      this.i.chatPanelTimer = null;
      if (!this.i.chatPanelWanted && !this.i.collapsed) void Bridge.setChatPanel(false);
    }, 380);
  }

  updateWindowCollapsed() {
    if (this.i.collapseTimer != null) {
      window.clearTimeout(this.i.collapseTimer);
      this.i.collapseTimer = null;
    }
    if (State.mode === "hidden") {
      // Let the island finish retracting, then drop the window to the wake strip:
      // from there the OS delivers no cursor events, so nothing polls at all.
      this.i.collapseTimer = window.setTimeout(() => {
        this.i.collapseTimer = null;
        if (State.mode !== "hidden") return;
        this.i.collapsed = true;
        void Bridge.setCollapsed(true);
      }, 420);
    } else if (this.i.collapsed) {
      // Grow the window back before the island animates open.
      this.i.collapsed = false;
      void Bridge.setCollapsed(false);
    }
  }

  // ── Input ───────────────────────────────────────────────────────────────────

}
