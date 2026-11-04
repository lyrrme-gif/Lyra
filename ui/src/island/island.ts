// The island: DOM shell, sizing animation, Mochi placement, mouse handling.
// Mirrors IslandRootView.swift + IslandWindowController.swift.

import { Tracked, clamp } from "../core/anim";
import { Bridge, IS_TAURI, onDragDrop, onEvent } from "../core/bridge";
import { FileDropFx } from "../core/fileDropFx";
import {
  EXPANDED_CORNER, EXPANDED_W, NOTCH_W, PANEL_H, PANEL_W,
  ROUNDED_CORNER, VIEW_LAYOUTS, VOLUME_HUD_H, VOLUME_HUD_W, chatPromptHeight,
  islandSize,
  type IslandMode, type IslandViewName,
} from "../core/layout";
import { Sound } from "../core/sound";
import { carry as mochiCarry } from "../mascot/carry";
import { State } from "../core/state";
import type { KnowledgeCard, SearchResult } from "../core/state";
import { deckHeight } from "../views/searchDeck";
import { USC, UploadSeq } from "../upload/sequence";
import { buildTabDock, buildViews, type ViewActions, type ViewHost } from "../views/views";
import { clear, h } from "../views/dom";
import { IslandStateMachine } from "./fsm";
import { setTimerItems } from "../views/timer";
import { buildTimerChip, clockText, colorFor, getTimers, pickPrimary, valueMs, type TimerChip, type TimersPayload } from "./timerChip";
import { ActivityManager } from "../core/activities";
import { setNowPlaying } from "../core/nowPlaying";
import { emitMusic } from "../mascot/dock";
import { dueNow } from "../core/calendarStore";
import { asFxKind, fxAdded } from "../views/addedFx";
import { translatePillActive, translatePillSize } from "../views/translate";
import { STUDY_HOME_H, studyStageActive } from "../views/study";
import { APPS_CHAT_H, APPS_CHAT_W, appsChatActive, openAppsChat } from "../views/apps";
import { appUrl, buildToast, type NotifyEvent, type Toast } from "./toast";
import { APP_FX_COLOR, buildFx, type Fx, type FxKind } from "./activityFx";
import { PULSE_H, PULSE_W, buildPulse, artColor, describeMode, describePulse, describeSong, type Pulse, type PulseInfo } from "./pulse";
import { buildInvite, type Invite } from "./invite";
import { currentPosition, isSpotifySource, type SpotifyState } from "../spotify/model";
import { LyricsLine } from "./lyricsLine";
import { LyraStatus, type LyraStatusKind } from "./lyraStatus";
import { WheelHomeGesture } from "../core/wheelHome";
import { DESTINATIONS, destinationFor, openableFor } from "../core/destinations";
import { trace, traceLimited } from "../core/trace";
import { tourSignal } from "../core/tour";
import { visiblePoll } from "../core/visiblePoll";
import { isReallyVisible } from "../core/visibility";
import { PEEK_SIZES } from "../core/layout";

/** Tabs that get ATTACHED_ROOM extra height in the attached position (keep in sync with the :is() list in style.css, v30). */
const ATTACHED_ROOM = 10;
const ATTACHED_ROOM_VIEWS = new Set<string>(["overview", "music", "recognition", "timer", "weather", "calendar", "todo", "stats", "controls", "wallpaper", "record", "volume", "clipboard"]);
import { DEFAULT_CONTEXT, autoHidden, normalizeAutoHide, watchContext, type AutoHideContext } from "../core/autohide";
import { dlMiddleEllipsis, dlTileHtml } from "./downloadUi";
import { volumeIconSvg, activityIconSvg, spotifyIconSvg, batteryIconSvg } from "./islandIcons";
import { cleanCard } from "./islandSearch";

/** Same margins as the Rust hit test (src-tauri/src/island.rs). Kept tight so the
 * area around the notch stays clickable (tabs, window buttons, links). */
const HIT_MARGIN = 6;
/** Slightly more forgiving once the full panel is open. */
const PANEL_MARGIN = 12;

/** Wake zone while hidden: only the middle of the top edge, not the full width. */
const WAKE_HALF_W = 160;
/** Pointer must be this close to the very top pixel row to count as touching the edge. */
const WAKE_EDGE_Y = 3;
/** Double-touch: second touch must come within this long after leaving the edge. */
/** Dwell: how long the pointer has to rest on the edge. */
const WAKE_DWELL_MS = 350;
/** Auto-hidden: how long the pointer rests at the top-centre before the notch comes back. */
const AUTOHIDE_DWELL_MS = 450;

/** The three views the drop sequence owns; leaving them stops the engine. */
const UPLOAD_VIEWS: ReadonlySet<IslandViewName> = new Set(["upload", "uploading", "choose"]);

/** Seconds between the drop and the moment the progress bar starts filling. */
const PRE_PROGRESS = USC.T_PROG_START - USC.T_DROP;

const modeOrder = (m: IslandMode) => (m === "hidden" ? 0 : m === "compact" ? 1 : 2);

// ── Download card helpers ─────────────────────────────────────────────────────

/** Shorten in the middle so the extension (the useful part) stays visible. */


/** Pure white, Apple-style progress glyph: ring + bold arrow (check when done). */

/** One of the two small islands beside the main one. */
interface SecSlot { el: HTMLElement; id: string | null; shown: boolean; w: number; key: string }

export class Island {
  readonly fsm = new IslandStateMachine();

  private root: HTMLElement;
  private islandEl!: HTMLElement;
  private clipEl!: HTMLElement;
  private contentEl!: HTMLElement;
  private viewsEl!: HTMLElement;
  private countdown!: HTMLElement;
  private wakeStrip!: HTMLElement;
  private spotifyCompact!: HTMLElement;
  private spotifyArt!: HTMLImageElement;
  private spotifyTitle!: HTMLElement;
  private spotifyArtist!: HTMLElement;
  private spotifyEq!: HTMLElement;
  private volumeHud!: HTMLElement;
  private volumeIcon!: HTMLElement;
  private volumeLabel!: HTMLElement;
  private volumeSubtitle!: HTMLElement;
  private volumePct!: HTMLElement;
  private lastDlSuccessAt = -1e9;
  private dlWatchdog: number | null = null;
  /** Latest download update that arrived while the island was expanded (replayed on collapse). */
  private pendingDownload: { s: any; at: number } | null = null;
  private volumeDlBar!: HTMLElement;
  private volumeFill!: HTMLElement;
  private volumeThumb!: HTMLElement;
  private volumeSpotify!: HTMLElement;
  private activitySpotifyUrl = "";
  private privacyDots!: HTMLElement;
  private modeLight!: HTMLElement;   // glowing bar under the notch while game mode is on
  private gameOn = false;
  private timerChip!: TimerChip;
  private timerTicker: number | null = null;
  private downloadSpinnerRaf: number | null = null;
  private toast!: Toast;
  /** The light show around the notch (activityFx.ts): edge light, shockwaves, sparks. Same layer the Settings previews use. */
  private fx!: Fx;
  private fxAt = 0;
  private sfxAt = 0;
  private lastAlarmAt = 0;
  private timerSeen = new Map<string, { kind: string; state: string; end: number | null }>();
  private timerTickSec = -1;
  /** "Added" pulse on the compact notch (to-do green, calendar / reminder red, timer orange); see pulse.ts. */
  private pulse!: Pulse;
  private invite!: Invite;
  private pulseOn = false;
  private pulseView = "";
  private pulseKey = "";
  private pulseCompact = false;
  private pulseUrl = "";
  /** The island is open: the pulse drops in as a floating banner over the tab instead of resizing the notch. */
  private pulseBanner = false;
  private pulseCount = 0;
  private pulseTimer: number | null = null;
  private pulseOffTimer: number | null = null;
  /** The pulse woke the island from hidden, so it puts it back to sleep when it ends. */
  private pulseRevealed = false;
  private lastHoverSound = 0;

  private tabDock!: ViewHost;
  /** Clean island: true while the tab bar is showing (just switched tabs, or the pointer is over the island / the bar). */
  /** The tab wheel window is up (it lives in its own window: src/wheel, src-tauri/src/wheel.rs). */
  private wheelUp = false;
  private views!: Map<IslandViewName, ViewHost>;

  private width = new Tracked(NOTCH_W);
  private height = new Tracked(0);
  private radius = new Tracked(ROUNDED_CORNER);


  private running = false;
  private lastFrame = 0;
  private dirty = true;

  // Rust starts the window at full size so the launch greeting has room.
  private collapsed = false;
  /** launch() has run: the notch may be raised straight away by a settings change. */
  private launched = false;
  private collapseTimer: number | null = null;
  /** The tall native panel (Chat / search results) is released only AFTER the island has finished shrinking. */
  private chatPanelTimer: number | null = null;
  private chatPanelWanted = false;
  /** What Rust last said about the desktop / apps / screen share (Settings → Island → Auto-hide). */
  private autoCtx: AutoHideContext = DEFAULT_CONTEXT;
  private wasInIsland = false;
  // Wake gesture state (hidden island only).
  private wakeDwellStart = 0;
  /** Last shapes handed to Rust for the click-through test. */
  private pushedKey: number[] = [];
  // ── Activities: main island + secondary islands ──────────────────────────
  private activities = new ActivityManager();
  /** Up to two small islands beside the main one: left and right. Each side has its own element. */
  private slots: Record<"left" | "right", SecSlot> = {
    left: { el: null as unknown as HTMLElement, id: null, shown: false, w: 0, key: "" },
    right: { el: null as unknown as HTMLElement, id: null, shown: false, w: 0, key: "" },
  };
  /** A screen recording is running (since this wall-clock time), reported by the backend. */
  private recordingSince: number | null = null;
  /** The two things that can be recording: the screen (Record tab) and a call (Notetaker). recordingSince is the earlier of the two. */
  private screenSince: number | null = null;
  private callSince: number | null = null;
  private recordTicker: number | null = null;
  /** true = the red pill shows a live m:ss clock (h:mm:ss past an hour) instead of the static word "REC".
   *  The clock only touches one text node once a second. Set false to go back to the fully static pill if recording ever hurts fps. */
  private static readonly LIVE_REC_CLOCK = true;
  private static fmtClock(secs: number): string {
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), sec = secs % 60;
    const ss = String(sec).padStart(2, "0");
    return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
  }
  /** Set while the SCREEN recording is paused: the clock shows this many ms, frozen (the backend does not count pauses). */
  private screenPausedMs: number | null = null;
  private promoting = false;
  /** A download shown as its own floating island while something else owns the notch. */
  private dlSec: { phase: string; pct: number | null } = { phase: "", pct: null };
  private dlSecTimer: number | null = null;
  private dlLast: Parameters<Island["onActivity"]>[0] | null = null;
  private dlSecDoneSound = false;
  /** The user clicked the download island: it owns the notch and the other activity is the small one. */
  private dlMain = false;
  private srcIcons = new Map<string, string>();
  private srcIconAt = 0;
  /** Clipboard was opened by an incoming file drag (and closes again afterwards). */
  private dragOpenedClipboard = false;
  /** A file drag is currently over the page (browser drag events: what Windows delivers, see wireHtmlFileDrag). */
  private htmlDragLive = false;
  private htmlDragTimer = 0;
  private homeCollapseAt: number | null = null;

  // Bot hover → love (IslandWindowController.botHoverIn)

  private lastSyncedView: IslandViewName | null = null;
  private viewTransitionTimer: number | null = null;
  private viewTransitionSeq = 0;
  /** Views that were told `setActive(true)` and haven't been told it ended. */
  private activeViews = new Set<IslandViewName>();
  /** View to open when the compact notch is clicked. Normally the home view,
   * but the Spotify notch should jump straight into Music. */
  private pendingCompactClickView: IslandViewName | null = null;
  private searchResultOpen = false;

  // Spotify compact mode lives inside the same notch as Mochi.
  private spotifyState: SpotifyState | null = null;
  /** Synced lyrics for the notch (LRCLIB via the backend's /lyrics). */
  private lyrics = new LyricsLine();
  /** When the last spotify-state arrived: the page advances the position from there. */
  private spotifyReceivedAt = 0;
  /** What the second notch line currently shows (artist or a lyric). */
  private artistShown: string | null = null;
  private lyricTimer: number | null = null;
  private spotifyArtUrl: string | null = null;
  private lyraSpeaking = false;
  /** True while Lyra is actively listening; this owns the compact notch exclusively. */
  private lyraListening = false;
  /** Tracks the thinking phase so the island can stay available while Lyra responds. */
  private lyraThinking = false;
  /**
   * A command typed in the Chat tab: the island folds to the small notch (Mochi + "Thinking", then the voice wave) and
   * comes back to the Chat tab once Lyra is done. `typedHold` bridges the moment before the backend says "thinking".
   */
  private typedTurn = false;
  private typedHold = false;
  private typedSpoke = false;
  private typedGraceMs = 2600;
  private typedReturnTimer = 0;
  private typedHoldTimer = 0;
  /** Chat tab left alone (nothing typed, nothing moved) for CHAT_IDLE_MS: the island goes back to its small state. */
  private static readonly CHAT_IDLE_MS = 8_000;
  private chatIdleTimer: number | null = null;
  /** Lyra's turn drawn in the notch: Mochi on the left + "Listening" / "Thinking" / the white voice wave (lyraStatus.ts). */
  private readonly lyraStatus = new LyraStatus();
  private lyraStatusKind: LyraStatusKind | null = null;
  /** Dictation (hold Left Alt): listening -> processing (the accurate text is being made) -> done (it was pasted). */
  private dictActive = false;
  private dictProcessing = false;
  private dictLinger = false;
  private dictApp = "";
  private dictLingerTimer = 0;
  /** Circle to ask (hold Ctrl and circle something): "drawing" while you circle, "looking" while she looks and explains. */
  private circleState: "" | "drawing" | "looking" = "";

  /** Volume HUD: shown in the compact notch for a moment after the speaker volume changes. */
  private volumeHudOn = false;
  /** True while an explicit app-open notification owns the compact notch. */
  private appLaunchHudOn = false;
  private volumeTimer: number | null = null;
  /** The HUD woke the island from hidden, so it puts it back to sleep when it ends. */
  private volumeRevealed = false;

  /** Notification toast (Discord / WhatsApp / Telegram / calls / reminders), one at a time. */
  private toastOn = false;
  private toastCurrent: NotifyEvent | null = null;
  private toastQueue: NotifyEvent[] = [];
  private toastTimer: number | null = null;
  /** Theme changes from the Settings window get a short visual preview in the island. */
  private themePreviewToken = "";
  private readonly bootAt = Date.now();
  private themePreviewTimer: number | null = null;
  /** The toast woke the island from hidden, so it puts it back to sleep when it ends. */
  private toastRevealed = false;

  /** Drop sequence bookkeeping: last tick played, and whether the ✓ has fired. */
  private uploadTens = 0;
  private uploadDone = false;

  constructor(root: HTMLElement) {
    this.root = root;
    this.build();
    this.wireFsm();
    this.wireInput();
    State.subscribe(() => {
      this.dirty = true;
      this.ensureRunning();
      const key = `${State.chatVisible().length}:${State.searchResult?.title ?? ""}`;
      if (key !== this.lastChatKey) { this.lastChatKey = key; this.armChatAutoClose(); }
      if (this.chatIdleTimer == null) this.armChatIdle();   // the Chat tab just opened (a timer already running is never pushed back here)
    });
    // Any interaction inside the island restarts the countdown.
    for (const ev of ["pointermove", "pointerdown", "keydown", "wheel"]) {
      this.islandEl.addEventListener(ev, () => { if (this.chatCloseTimer != null) this.armChatAutoClose(); }, { passive: true });
    }
    this.islandEl.addEventListener("pointerleave", () => this.armChatAutoClose());
    // Chat tab idle limit. Capture phase: the chat box stops its own keydown from bubbling.
    for (const ev of ["pointermove", "pointerdown", "keydown", "wheel", "input", "focusin"]) {
      this.islandEl.addEventListener(ev, () => this.armChatIdle(), { passive: true, capture: true });
    }
    this.islandEl.addEventListener("pointerleave", () => this.armChatIdle());
    // A command typed in the Chat tab (chat.ts) / the backend could not be reached (client.ts).
    window.addEventListener("lyra:chat-typed", () => this.onTypedCommand());
    window.addEventListener("lyra:typed-failed", () => this.endTypedTurn(true));
    // Scrolling down on any tab other than Home takes you back to Home.
    this.islandEl.addEventListener("wheel", (e) => this.onWheelHome(e), { passive: true });
    // Settings window writes theme/position to the same localStorage keys.
    // storage events fire across Tauri webviews that share origin.
    window.addEventListener("storage", (e) => {
      if (e.key === "lyra-island-theme" || e.key === "lyra-island-position" || e.key === "lyra-ui-extras") {
        State.reloadAppearance();
        this.animateGeometry(false);
      }
    });
    // Fallback poll in case storage events do not cross webviews.
    // Rate follows what the island is doing: full speed expanded, slow while compact/idle, very slow hidden.
    const every = (fast: number, compact: number, hidden: number, fn: () => void): void => {
      const loop = (): void => {
        if (isReallyVisible()) { try { fn(); } catch { /* keep the loop alive */ } }
        window.setTimeout(loop, State.mode === "expanded" ? fast : State.mode === "compact" ? compact : hidden);
      };
      window.setTimeout(loop, fast);
    };
    every(250, 750, 1500, () => {
      const before = `${State.theme}|${State.islandPosition}`;
      State.reloadAppearance();
      if (`${State.theme}|${State.islandPosition}` !== before) this.animateGeometry(false);
      this.checkThemePreview();
    });
    // Safety net: the clickable shapes are re-measured regularly, whatever happened in between.
    every(300, 1000, 2000, () => this.pushHitShapes());
    // Reminders are time-based, not visual: they keep their own steady clock (created once, not per timer).
    window.setInterval(() => this.checkReminders(), 15000);
  }

  // ── DOM ─────────────────────────────────────────────────────────────────────

  /**
   * Settings lives in a separate WebView. A localStorage token lets that window
   * ask the main island to show the newly selected theme for a clear 5-second
   * preview, even when the island was hidden.
   */
  private checkThemePreview() {
    let token = "";
    try { token = window.localStorage.getItem("lyra-theme-preview") || ""; } catch { return; }
    if (!token || token === this.themePreviewToken) return;
    this.themePreviewToken = token;
    // Boot / start-up animation (1.5 s) owns the island: a preview token written while Lyra is starting
    // (or left over from the last session) must not force it open and cut the animation short.
    if (Date.now() - this.bootAt < 3000) return;

    if (this.themePreviewTimer != null) {
      window.clearTimeout(this.themePreviewTimer);
      this.themePreviewTimer = null;
    }

    // Give the island the clearest canvas for the preview when it is not already open.
    if (State.mode !== "expanded") {
      State.view = "overview";
      this.fsm.forceHome("Theme preview");
    } else {
      this.animateGeometry(false);
    }

    // A theme preview owns the open state for exactly five seconds.
    this.themePreviewTimer = window.setTimeout(() => {
      this.themePreviewTimer = null;
      if (State.mode === "expanded" && !State.isPinned && !this.searchResultOpen) {
        this.collapse("Theme preview finished");
      }
    }, 5000);
  }

  private build() {
    const actions: ViewActions = {
      setView: (v) => this.setView(v),
      collapse: () => this.collapse(),
      setFocus: (id) => {
        State.setFocus(id);
        Sound.play("blip");
      },
      openUrl: (url) => {
        if (url) void Bridge.openUrl(url);
      },
      decide: (d) => {
        const req = State.pendingApproval;
        void Bridge.log(`decide ${d} req=${req?.requestId ?? "none"}`);
        if (!req) return;
        Sound.play(d === "deny" ? "blip" : "approve");
        void Bridge.approvalDecision(req.requestId, d);
        State.pendingApproval = null;
        State.isPinned = false;
        this.fsm.pinned = false;
        State.updateTask("integration_claude", "working");
        State.setPillBadge("integration_claude", null);
        this.setView(State.defaultView());
      },
      toggleSound: () => {
        State.settings.soundEnabled = !State.settings.soundEnabled;
        Sound.setEnabled(State.settings.soundEnabled);
        void Bridge.saveSettings(State.settings);
        State.notify();
      },
      setVolume: (v) => {
        State.settings.soundVolume = v;
        Sound.setVolume(v);
        void Bridge.saveSettings(State.settings);
        State.notify();
      },
      setAutoClose: (s) => {
        State.settings.autoCloseInterval = s;
        this.applyAutoClose();
        void Bridge.saveSettings(State.settings);
        State.notify();
      },
      setTheme: (theme) => State.setTheme(theme),
      setIslandPosition: (position) => State.setIslandPosition(position),
      openSettingsWindow: () => void Bridge.openSettingsWindow(),
      setPeek: (on: boolean) => {
        if (State.peek === on) return;
        trace("island", `${State.view}: user switched to the ${on ? "SMALL peek card" : "FULL tab"}`);
        State.peek = on;
        State.lastActivity = performance.now();
        this.armAutoClose();
        this.animateGeometry(on);
        State.notify();
      },
      blip: () => Sound.play("blip"),
    };

    this.wakeStrip = h("div", { id: "wake-strip" });
    this.countdown = h("div", { id: "countdown" });

    this.spotifyArt = h("img", { id: "spotify-compact-art", alt: "", draggable: "false" }) as HTMLImageElement;
    this.spotifyTitle = h("div", { id: "spotify-compact-title" });
    this.spotifyArtist = h("div", { id: "spotify-compact-artist" });
    this.spotifyEq = h("div", { id: "spotify-compact-eq" }, h("i"), h("i"), h("i"), h("i"));
    const spotifyText = h("div", { id: "spotify-compact-text" }, this.spotifyTitle, this.spotifyArtist);
    this.spotifyCompact = h("div", { id: "spotify-compact" }, this.spotifyArt, spotifyText, this.spotifyEq);

    this.volumeIcon = h("span", { id: "volume-hud-icon" });
    this.volumeLabel = h("span", { id: "volume-hud-label", text: "Speakers" });
    this.volumeSubtitle = h("span", { id: "volume-hud-subtitle" });
    this.volumeFill = h("div", { id: "volume-hud-fill" });
    this.volumeThumb = h("div", { id: "volume-hud-thumb" });
    const volumeTrack = h("div", { id: "volume-hud-track" }, this.volumeFill, this.volumeThumb);
    this.volumeSpotify = h("button", {
      id: "volume-hud-spotify",
      type: "button",
      title: "Open in Spotify",
      "aria-label": "Open recognized song in Spotify",
    }) as HTMLButtonElement;
    this.volumeSpotify.innerHTML = spotifyIconSvg(18);
    const volumeText = h("div", { id: "volume-hud-text" }, this.volumeLabel, this.volumeSubtitle);
    this.volumePct = h("span", { id: "volume-hud-pct" });
    this.volumeDlBar = h("div", { id: "volume-hud-dlbar" }, h("i"));
    this.volumeHud = h("div", { id: "volume-hud" }, this.volumeIcon, volumeText, this.volumePct, volumeTrack, this.volumeSpotify, this.volumeDlBar);
    this.volumeSpotify.addEventListener("mousedown", (e) => {
      e.stopPropagation();
      if (this.activitySpotifyUrl) void Bridge.openUrl(this.activitySpotifyUrl);
    });
    this.volumeSpotify.addEventListener("click", (e) => {
      e.stopPropagation();
    });
    this.privacyDots = h("div", { id: "privacy-dots" });
    this.modeLight = h("div", { id: "island-modelight", "aria-hidden": "true" });
    this.toast = buildToast();
    this.toast.onAnswer = () => this.clickToast();
    this.toast.onDecline = () => this.declineToast();
    this.pulse = buildPulse();
    this.pulse.el.addEventListener("mousedown", (e) => {
      if (!this.pulseBanner) return;                       // (the compact pulse is handled by the island's own click)
      e.stopPropagation();
      const url = this.pulseUrl;
      const onReco = State.view === "recognition";
      this.endPulse(false);
      if (url) void Bridge.openUrl(url);
      // Same as when it ends by itself: close the island rather than uncover the Recognition tab underneath.
      if (State.mode === "expanded" && onReco) this.collapse("song pulse clicked");
    });
    for (const t of ["click", "mouseup"] as const) {
      this.pulse.el.addEventListener(t, (e) => { if (this.pulseBanner) e.stopPropagation(); });
    }
    this.invite = buildInvite();
    this.timerChip = buildTimerChip();

    this.tabDock = buildTabDock(actions);
    this.views = buildViews(actions, () => {
      // The Apps tab grows into a big chat when a conversation opens: the native window must be tall first.
      if (State.view === "apps") this.chatPanel(this.wantsTall(State.mode, State.view));
      this.animateGeometry(false);
    });
    this.viewsEl = h("div", { id: "views" });
    for (const v of this.views.values()) this.viewsEl.append(v.el);
    this.contentEl = h("div", { id: "content" }, this.viewsEl);

    this.clipEl = h(
      "div",
      { id: "island-clip" },
      this.contentEl,
    );
    this.islandEl = h(
      "div",
      { id: "island" },
      this.clipEl,
      this.countdown,
      this.spotifyCompact,
      this.timerChip.el,
      this.volumeHud,
      this.privacyDots,
      this.modeLight,
      this.toast.el,
      this.lyraStatus.el,
      this.pulse.aura,
      this.pulse.el,
      ...this.invite.els,
    );
    this.fx = buildFx(this.islandEl);
    this.fx.onHide = () => { this.ambientKey = ""; this.syncAmbientFx(); };   // a one-shot ended: bring the quiet light back


    this.slots.right.el = h("div", { id: "island-secondary", class: "isl-sec", "data-side": "right" });
    this.slots.left.el = h("div", { id: "island-secondary-left", class: "isl-sec", "data-side": "left" });
    // The dock grows and shrinks as temporary tabs come and go: keep its hit shape exact.
    new ResizeObserver(() => { this.dirty = true; this.ensureRunning(); }).observe(this.tabDock.el);
    window.addEventListener("lyra-collapse", () => this.collapse("Closed"));
    // A search card in the conversation was tapped: show that deck again.
    window.addEventListener("lyra:reopen-search", (e) => { const r = (e as CustomEvent<SearchResult>).detail; if (r) this.showSearchResult(r); });
    for (const side of ["left", "right"] as const) {
      this.slots[side].el.addEventListener("click", (e) => {
        e.stopPropagation();
        if (this.slots[side].id === "record") {
          // The red pill opens the Record tab (stop button). Go through the FSM, like every other way of opening a tab,
          // so the auto-close timer is armed; calling expand() directly left the FSM in "petit" and the panel never closed.
          this.fsm.forceHome("record pill");
          this.setView("record");
          return;
        }
        this.promoteSecondary(side);
      });
    }
    this.root.append(this.wakeStrip, this.islandEl, this.tabDock.el, this.slots.left.el, this.slots.right.el);

    if (IS_TAURI) {
      void onEvent<SpotifyState>("spotify-state", (next) => {
        const wasActive = this.isSpotifyMediaActive();
        const prevS = this.spotifyState;
        this.spotifyState = next;
        if (!prevS || prevS.trackKey !== next.trackKey || prevS.status !== next.status || prevS.running !== next.running) {
          trace("music", `${next.source || "player"}: ${next.running ? next.status : "not running"}${next.title ? ` - "${String(next.title).slice(0, 40)}"` : ""}` +
            `${prevS && prevS.trackKey !== next.trackKey ? " (track changed)" : ""}; compact music pill ${this.isSpotifyCompactActive() ? "SHOWN" : "not shown"} (island ${State.mode})`);
        }
        this.spotifyReceivedAt = performance.now();
        setNowPlaying({ running: next.running, status: next.status, title: next.title, artist: next.artist });
        emitMusic(next.running && next.status === "playing");   // the floating Mochi + subtitles live in other windows
        this.lyrics.track(next, () => this.paintArtistLine());
        this.syncLyricTicker();
        if (!next.running) this.spotifyArtUrl = null;
        else if (next.artChanged) this.spotifyArtUrl = next.art;
        setNowPlaying({ artUrl: this.spotifyArtUrl });

        // Spotify owns the compact notch for the whole lifetime of a track.
        // This deliberately includes PAUSED: pausing should freeze the notch,
        // not hand it back to the Mochi/hover auto-hide system.
        this.fsm.keepCompact = this.keepNotch();
        if (this.fsm.keepCompact && !wasActive && this.fsm.state === "hidden") {
          this.fsm.forcePetit();
        } else if (!this.fsm.keepCompact && wasActive && !this.wasInIsland) {
          this.fsm.forceHidden();
        }

        this.syncSpotifyCompact();
        // At launch the notch can come up as the small idle circle before the first Spotify state
        // arrives (Spotify was already open). The size is only recomputed on mode changes, so it
        // stayed circle-sized with the cover squeezed inside: resize it now that music is known.
        if (State.mode === "compact" && wasActive !== this.isSpotifyMediaActive()) {
          this.animateGeometry(false);
          window.setTimeout(() => { if (State.mode === "compact") this.animateGeometry(false); }, 60);
        }
        this.dirty = true;
        this.ensureRunning();
      }).then(() => Bridge.musicView(true));
    }

    this.applyGeometry();
  }

  // ── FSM ─────────────────────────────────────────────────────────────────────

  private wireFsm() {
    this.applyAutoClose();
    this.fsm.petitToHiddenDelay = State.settings.compactAutoHideInterval;
    void watchContext((c) => { this.autoCtx = c; this.syncAutoHide(); });
    // Chat is an active workspace, not a transient notification. Keep it open while
    // the user is composing/reading, including the first empty Chat launch.
    this.fsm.holdOpen = () => State.lyricsOpen || State.view === "prompt" || State.view === "camera" || State.recognitionBusy || this.pulseBanner || mochiCarry.active;   // mochiCarry: the Mochi is being dragged out of its tab
    this.fsm.onTransition = (from, to) => {
      // One line per open / close, with what caused it: lyra.log is where to look
      // when the island does something unexpected.
      void Bridge.log(`island: ${from} -> ${to} (${this.fsm.lastReason})`);
      trace("island", `FSM ${from} -> ${to}  because: ${this.fsm.lastReason}  | pointer ${this.wasInIsland ? "IS" : "is not"} on the island, pinned=${State.isPinned}, holdOpen=${this.fsm.holdOpen()}`);
      switch (to) {
        case "hidden":
          if (this.keepNotch()) {
            // window to the wake strip while Spotify owns the notch.
            this.fsm.keepCompact = true;
            this.fsm.forcePetit();
          } else {
            this.setMode("hidden");
          }
          break;
        case "petit":
          if (from === "hidden") Sound.play("peek");
          this.setMode("compact");
          if (!this.wasInIsland) this.fsm.mouseLeft();
          break;
        case "home": {
          const view = this.pendingCompactClickView ?? State.defaultView();
          this.pendingCompactClickView = null;
          this.expand(view);
          if (!this.wasInIsland) this.fsm.mouseLeft();
          break;
        }
      }
      State.notify();
    };
  }

  launch() {
    // Screen-recording state is pushed by the backend; a Notetaker call is not, so this catches both a missed
    // event and a call that started elsewhere. Runs at boot, then at most once per 30 s, never while the window
    // is hidden (the timer is cleared, see core/visiblePoll.ts). Hovering the island asks again if 30 s have passed.
    const recPoll = visiblePoll(() => this.syncRecordingFromBackend(), { ms: 30_000, floorMs: 30_000 });
    this.islandEl.addEventListener("pointerenter", () => recPoll.poke(), { passive: true });
    this.launched = true;
    this.fsm.launch();
  }

  // ── Mode / view ─────────────────────────────────────────────────────────────

  private setMode(mode: IslandMode) {
    const prev = State.mode;
    if (mode === prev) return;
    State.mode = mode;
    if (mode !== "expanded" && this.pulseBanner) this.endPulse(true);   // closed by hand: the banner goes with it
    if (mode === "expanded") State.openedAt = performance.now();
    // Opening the panel (e.g. a click on the notch while a HUD is up) ends the HUD at once;
    // otherwise it would stay stretched across the panel until its timer runs out.
    if (mode === "expanded" && this.volumeHudOn) {
      if (this.volumeTimer != null) window.clearTimeout(this.volumeTimer);
      this.volumeRevealed = false; // the user opened the notch: it must not go back to sleep
      this.endVolumeHud();
    }
    // Recompute the download's secondary/primary presentation whenever the
    // notch changes mode. A download that started on Home must become a tiny
    // circle if Spotify is now owning the compact notch.
    if (this.volumeHudOn && this.volumeHud.dataset.activityKind === "download") {
      if (mode === "expanded" && State.view !== "overview") {
        this.endVolumeHud();
      } else {
        const secondary = mode === "compact" && !this.dlMain && (
          this.isSpotifyMediaActive() || this.timerChip.active() ||
          this.appLaunchHudOn || this.lyraListening || this.lyraSpeaking
        );
        this.volumeHud.dataset.downloadCompact = secondary ? "1" : "0";
      }
    }
    if (mode !== "expanded" && this.pendingDownload) {
      const pend = this.pendingDownload;
      this.pendingDownload = null;
      if (performance.now() - pend.at < 20_000) {
        window.setTimeout(() => {
          if (State.mode !== "expanded") this.onActivity({ ...pend.s, ts: Date.now() });
        }, 450);
      }
    }
    // The Spotify compact content lives in this same notch, so re-evaluate it
    // whenever Lyra changes mode. This matters when Spotify is paused: its media
    // state may not emit another event just because the notch wakes up.
    this.syncSpotifyCompact();
    if (mode === "expanded") {
      Sound.play("open");
    }
    // Restore the normal native window first, then grow it for an expanded Chat.
    // updateWindowCollapsed() can call setCollapsed(false), which reapplies the
    // normal panel geometry. Doing Chat sizing before that call made the native
    // window shrink back and clip the expanded Chat/dock.
    const tallChat = this.wantsTall(mode, State.view);
    // A toast shows in the open panel too, as a small card at the top (see .in-panel).
    this.toast.el.classList.toggle("in-panel", mode === "expanded");
    // Notifications never show on the open island: a showing one goes back to the queue, and
    // the waiting ones come out again (if still fresh) once the island has closed.
    if (mode === "expanded") { this.endPulse(true); this.islandEl.classList.remove("hovering"); this.suspendToast(); }
    else if (prev === "expanded") window.setTimeout(() => this.pumpToast(), 350);
    if (prev === "expanded") {
      Sound.play("close");
      State.isPinned = false;
      void Bridge.focusWindow(false);
    }
    if (mode !== "expanded") {
      // Nothing can be seen of the sequence once the island is shut, and leaving
      // it running would keep the frame loop awake — the island must cost
      // nothing while hidden.
      UploadSeq.deactivate();
    }
    this.updateWindowCollapsed();
    // Apply Chat's native height after collapsed-window restoration so it wins
    // over the normal 320px geometry.
    this.chatPanel(tallChat);
    if (mode !== "expanded" && this.searchResultOpen) {
      this.searchResultOpen = false;
      this.fsm.unlockSearch();
      State.searchResult = null;
      this.chatPanel(false);
    }
    this.animateGeometry(modeOrder(mode) < modeOrder(prev));
    if (mode === "expanded" && State.view === "prompt") {
      window.setTimeout(() => this.animateGeometry(false), 60);
    }
    State.notify();
    if (prev === "expanded") window.setTimeout(() => this.pumpToast(), 500);
  }

  /** True while the drop sequence owns the island body. */
  /** Navigating out of the drop flow ends the sequence, as on macOS. */
  private stopSequenceIfLeaving(view: IslandViewName) {
    if (UploadSeq.isActive && !UPLOAD_VIEWS.has(view)) UploadSeq.deactivate();
  }

  /**
   * Apple-style "close when you're done": every tab has its own idle time, counted
   * from the moment the pointer leaves (or from opening it by voice/shortcut when the
   * pointer was never there). The user's Auto-close setting is still the upper limit.
   */
  private applyAutoClose() {
    const base = State.settings.autoCloseInterval || 15;
    const perView: Partial<Record<IslandViewName, number>> = {
      overview: 6,      // glance, then get out of the way
      volume: 3,        // like the system volume HUD
      recognition: 8,   // long enough to read/tap the result (never while listening)
      clipboard: 6,     // copy and go
      music: 9,         // controls you may come back to
      prompt: 25,       // chat is a workspace (also held open while you type)
      settings: 12,
    };
    // Floor of 4 s: a smaller Auto-close value (or a wheel-scrolled number box in Settings) made the panel
    // vanish right after the pointer left it.
    this.fsm.homeToPetitDelay = Math.max(4, Math.min(base, perView[State.view] ?? base));
  }

  /** Start the idle countdown for a tab that was opened without the pointer on it. */
  private armAutoClose() {
    this.applyAutoClose();
    if (!this.wasInIsland) this.fsm.activity();
  }

  /** `onIsland`: show the island's own Chat view (used for search results) instead of the chat bar. */
  expand(view: IslandViewName, onIsland = false) {
    trace("island", `expand('${view}')${onIsland ? " [on island]" : ""} requested; island is ${State.mode}/${State.view}`);
    this.stopSequenceIfLeaving(view);
    // Every fresh open (or a switch to another tab) starts at the small peek size, never at the last full size.
    if (State.mode !== "expanded" || view !== State.view) State.peek = true;
    State.view = view;
    if (State.mode !== "expanded") this.setMode("expanded");
    else {
      this.animateGeometry(false);
      window.setTimeout(() => this.animateGeometry(false), 60);
    }
    this.armAutoClose();
    State.lastActivity = performance.now();
    this.homeCollapseAt = null;
    State.notify();
  }

  // ── Scroll down → Home ─────────────────────────────────────────────────────
  // Content that can still scroll (chat log, lists) scrolls first. Views that use the wheel
  // themselves (timer tape, volume sliders, calendar strip) call preventDefault: left alone.
  private wheelHome = new WheelHomeGesture();

  private canScrollDown(from: EventTarget | null): boolean {
    let el = from instanceof HTMLElement ? from : null;
    while (el && el !== this.islandEl) {
      const oy = getComputedStyle(el).overflowY;
      if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight + 1
          && el.scrollTop + el.clientHeight < el.scrollHeight - 1) return true;
      el = el.parentElement;
    }
    return false;
  }

  private onWheelHome(e: WheelEvent) {
    // Chat keeps its own scroll behavior; wheel gestures there must never switch tabs.
    if (State.mode !== "expanded" || State.view === "overview" || State.view === "empty" || State.view === "prompt") return;
    if (State.view === "apps" && appsChatActive()) return;   // an open WhatsApp chat scrolls: reaching its end must not leave the tab
    if (e.defaultPrevented || e.ctrlKey) return;
    if (this.wheelHome.feed(performance.now(), e.deltaX, e.deltaY, () => this.canScrollDown(e.target))) {
      this.setView("overview");
    }
  }

  setView(view: IslandViewName) {
    // A tab the user switched off in Settings → Features can't be opened (voice, tray or events included).
    const asked = view;
    if (!State.viewAllowed(view)) view = "overview";
    trace("island", `setView('${asked}')${asked !== view ? ` -> BLOCKED (tab switched off in Settings -> Features), showing '${view}' instead` : ""}; island is ${State.mode}/${State.view}${State.mode !== "expanded" ? " (closed: it will be opened)" : ""}`);
    if (view !== State.view) State.peek = true;   // Music / Timer / Weather / Recognize always open small
    // Leaving Chat: just drop the results. Collapsing here fought the tab switch below
    // (island closed, reopened with the tall chat window still on = black island).
    if (this.searchResultOpen && view !== "prompt") this.dismissSearchResults(false);
    this.stopSequenceIfLeaving(view);
    if (State.mode !== "expanded") {
      this.fsm.forceHome();
      State.view = view;
      this.armAutoClose();
      this.animateGeometry(false);
      State.notify();
      return;
    }
    const grew = VIEW_LAYOUTS[view].height >= VIEW_LAYOUTS[State.view].height;
    State.view = view;
    this.armAutoClose();
    State.lastActivity = performance.now();
    if (view !== "overview" && this.volumeHudOn && this.volumeHud.dataset.activityKind === "download") {
      this.endVolumeHud();
    }
    // Clipboard is the one tab that must accept native Windows Explorer drops.
    // Arm the native drop target before the user starts dragging; otherwise the
    // island's normal click-through layer can swallow the OLE drag.
    void Bridge.setDropTarget(view === "clipboard");
    // Switching away from Chat / Study releases the tall native window (after the shrink, see chatPanel());
    // switching into one restores it after any collapsed-window resize. This happens BEFORE the island starts
    // growing, so a growing island never meets the edge of a window that has not grown yet.
    const tallChat = this.wantsTall(State.mode, view);
    this.updateWindowCollapsed();
    this.chatPanel(tallChat);
    this.animateGeometry(!grew);
    State.notify();
  }

  // ── Chat smart auto-close ──────────────────────────────────────────────────
  // After Lyra answers, the chat closes by itself once you stop using it:
  //   • plain answer          → closes ~10s after she finishes
  //   • answer with results   → stays ~30s so you can read / click a result
  // It never closes while you hover it, type in it, or while she is thinking,
  // listening or speaking, and every interaction restarts the countdown.
  // Pinned panels, Escape and click-outside behave exactly as before.
  private static readonly CHAT_CLOSE_MS = 10_000;
  private static readonly CHAT_CLOSE_RESULTS_MS = 30_000;
  private chatCloseTimer: number | null = null;
  private lastChatKey = "";

  private chatBusy(): boolean {
    const input = this.islandEl.querySelector<HTMLInputElement>(".chat-input");
    const typing = !!input && (input.value.trim().length > 0);
    return (
      this.islandEl.matches(":hover") ||
      typing ||
      State.stateOverride === "thinking" ||
      this.lyraListening ||
      this.lyraSpeaking
    );
  }

  private armChatAutoClose() {
    if (this.chatCloseTimer != null) {
      window.clearTimeout(this.chatCloseTimer);
      this.chatCloseTimer = null;
    }
    if (State.mode !== "expanded" || State.view !== "prompt" || State.isPinned) return;
    if (State.chatVisible().length === 0) return; // resting composer: nothing to close
    const delay = this.searchResultOpen ? Island.CHAT_CLOSE_RESULTS_MS : Island.CHAT_CLOSE_MS;
    this.chatCloseTimer = window.setTimeout(() => {
      this.chatCloseTimer = null;
      if (State.mode !== "expanded" || State.view !== "prompt" || State.isPinned) return;
      if (this.chatBusy()) { this.armChatAutoClose(); return; } // still in use: look again later
      if (this.searchResultOpen) this.dismissSearchResults();
      else this.collapse("Chat finished");
    }, delay);
  }

  collapse(why = "collapse()") {
    trace("island", `collapse requested: ${why} (island is ${State.mode}/${State.view}, fsm=${this.fsm.state})`);
    // Closing an open panel must always close it, even if the state machine lost track of it
    // (it only reports a transition when its own state changes).
    if (State.mode === "expanded" && this.fsm.state !== "home") this.fsm.resync("home");
    void Bridge.setDropTarget(false);
    State.isPinned = false;
    this.fsm.pinned = false;
    // Drive the state machine rather than the mode: setting the mode behind its
    // back left it thinking the island was still open, and a click on the compact
    // island then did nothing — the island could never be reopened.
    this.fsm.forcePetit(why);
  }

  /** Alert from the hook server: open on this view. Pinned alerts never auto-close. */
  alert(view: IslandViewName) {
    this.fsm.pinned = State.isPinned;
    this.fsm.forceHome();
    this.expand(view);
  }

  /** Track the thinking phase for island behavior without showing an island indicator. */
  setThinking(on: boolean) {
    if (on === this.lyraThinking) return; // state polling repeats itself: stay idempotent
    this.lyraThinking = on;
    if (on && this.typedHold) { this.typedHold = false; window.clearTimeout(this.typedHoldTimer); }   // the backend took over from the bridge
    trace("lyra", `thinking ${on ? "STARTED" : "ended"} (island ${State.mode}/${State.view})`);
    this.fsm.keepCompact = on || this.lyraListening || this.lyraSpeaking || this.keepNotch();
    if (on) {
      try { if (State.mode === "hidden") this.fsm.forcePetit("Lyra thinking"); } catch { /* */ }
      this.syncTimerChip();
    } else {
      this.syncKeepCompact();
      this.syncTimerChip();
    }
    if (this.spotifyState) this.syncSpotifyCompact();
    this.syncLyraStatus();
    this.dirty = true;
    this.ensureRunning();
  }

  // ── Typed command: small notch while Lyra works, then back to the Chat tab ────────────────────────────

  /** A command was typed in the Chat tab: fold to the small notch and show Thinking right away. */
  private onTypedCommand() {
    if (State.mode !== "expanded" || State.view !== "prompt" || this.searchResultOpen) return;   // a deck on screen stays as it is
    this.typedTurn = true;
    this.typedSpoke = false;
    this.typedGraceMs = 2600;
    this.typedHold = true;
    window.clearTimeout(this.typedReturnTimer);
    window.clearTimeout(this.typedHoldTimer);
    // If the backend never says "thinking" (offline, or a command that answers instantly) the bridge must not hang.
    this.typedHoldTimer = window.setTimeout(() => {
      this.typedHold = false;
      this.syncLyraStatus();
    }, 6_000);
    this.fsm.keepCompact = true;
    this.collapse("Typed command");
    this.syncLyraStatus();
  }

  /** client.ts: Lyra's answer arrived. A Mochi sound is not spoken, so there is no voice to wait for. */
  onTypedReply(mochi: boolean) {
    if (!this.typedTurn) return;
    const bridged = this.typedHold;
    this.typedHold = false;
    window.clearTimeout(this.typedHoldTimer);
    if (mochi) this.typedGraceMs = 600;
    if (bridged) this.syncLyraStatus();   // the reply beat the backend's "thinking": let go of the bridge now
  }

  /** Stop the typed turn. `redraw`: the notch should drop its Thinking right away (e.g. the backend is unreachable). */
  private endTypedTurn(redraw: boolean) {
    if (!this.typedTurn && !this.typedHold) return;
    this.typedTurn = false;
    this.typedHold = false;
    this.typedSpoke = false;
    window.clearTimeout(this.typedReturnTimer);
    window.clearTimeout(this.typedHoldTimer);
    if (redraw) {
      this.syncKeepCompact();
      this.syncLyraStatus();
      this.returnToChat();
    }
  }

  /** Called whenever the notch's status changes: once Lyra is completely done, the Chat tab comes back. */
  private syncTypedReturn(kind: LyraStatusKind | null) {
    if (!this.typedTurn) return;
    window.clearTimeout(this.typedReturnTimer);
    if (kind !== null) return;   // thinking / speaking: still her turn
    // After the voice has finished there is nothing left to wait for; before it, give the voice time to start.
    const wait = this.typedSpoke ? 450 : this.typedGraceMs;
    this.typedReturnTimer = window.setTimeout(() => {
      if (!this.typedTurn || this.lyraStatusKind !== null) return;
      this.typedTurn = false;
      this.typedSpoke = false;
      this.syncKeepCompact();
      this.returnToChat();
    }, wait);
  }

  /** Back to the empty Chat tab, but only if the island is still the small notch (you may have opened something else). */
  private returnToChat() {
    if (State.mode !== "compact") return;
    this.pendingCompactClickView = "prompt";
    this.fsm.forceHome("typed command finished");
  }

  // ── Chat tab idle limit ─────────────────────────────────────────────────────────────────────────────

  /** (Re)start the idle countdown of the Chat tab. Nothing happening on it for CHAT_IDLE_MS folds the island to the small notch. */
  private armChatIdle() {
    if (this.chatIdleTimer != null) {
      window.clearTimeout(this.chatIdleTimer);
      this.chatIdleTimer = null;
    }
    if (State.mode !== "expanded" || State.view !== "prompt") return;
    this.chatIdleTimer = window.setTimeout(() => {
      this.chatIdleTimer = null;
      if (State.mode !== "expanded" || State.view !== "prompt") return;
      // Someone is (or is about to be) using it: look again later instead of closing.
      if (State.isPinned || this.searchResultOpen || State.searchResult || State.droppedFile || this.chatBusy()) { this.armChatIdle(); return; }
      this.collapse("Chat idle");
    }, Island.CHAT_IDLE_MS);
  }

  private lyraStatusQueued = false;

  /**
   * One owner for the compact notch while Lyra has a turn: listening, then thinking, then speaking.
   * The notch shows her face (the Mochi, on the left) with "Listening" / "Thinking" / the white wave.
   */
  private syncLyraStatus() {
    // applyState() calls setListening / setSpeaking / setThinking one after the other: decide once they have all run,
    // so listening -> thinking never passes through an empty notch (and the Mochi is not rebuilt in between).
    if (this.lyraStatusQueued) return;
    this.lyraStatusQueued = true;
    queueMicrotask(() => { this.lyraStatusQueued = false; this.applyLyraStatus(); });
  }

  private applyLyraStatus() {
    const kind: LyraStatusKind | null = this.lyraListening ? "listening" : this.lyraSpeaking ? "speaking"
      : (this.lyraThinking || this.typedHold || this.dictProcessing || this.circleState === "looking") ? "thinking"
      : this.dictLinger ? "done" : this.circleState === "drawing" ? "circle" : null;
    const was = this.lyraStatusKind;
    this.lyraStatusKind = kind;
    this.islandEl.classList.toggle("lyra-status", kind !== null);
    this.islandEl.classList.toggle("lyra-thinking", this.lyraThinking);
    this.lyraStatus.setDictation(kind !== null && (this.dictActive || this.dictProcessing || this.dictLinger), this.dictApp);
    this.lyraStatus.setCircle(this.circleState !== "");
    this.lyraStatus.set(kind);
    // The notch swells a little when her turn starts and settles when it ends (not between her own phases).
    if ((was === null) !== (kind === null)) this.animateGeometry(kind === null);
    this.syncTypedReturn(kind);
  }

  /** The words you are saying, as you say them (backend "live_text"); `final` is the accurate text that replaces the guess. */
  onLiveText(text: string, final: boolean, source: string) {
    this.lyraStatus.setLive(text, final);
    // Dictation: the accurate text is pasted right after this, so the notch keeps it on screen for a moment.
    if (final && source === "dictation" && text.trim()) this.startDictLinger();
  }

  /** Dictation phases from the backend ("dictation_pill"): listening, processing, done, cancelled, idle. */
  onDictationPill(state: string, app: string) {
    if (state === "listening") { this.dictActive = true; this.dictProcessing = false; this.dictLinger = false; this.dictApp = app; }
    else if (state === "processing") { this.dictActive = false; this.dictProcessing = true; }
    else if (state === "done") { this.dictActive = false; this.dictProcessing = false; this.startDictLinger(); return; }
    else { this.dictActive = false; this.dictProcessing = false; this.dictLinger = false; window.clearTimeout(this.dictLingerTimer); }   // cancelled / idle
    this.syncLyraStatus();
  }

  /** Circle to ask phases from the backend ("circle_pill"): drawing, looking, idle. */
  onCirclePill(state: string) {
    this.circleState = state === "drawing" || state === "looking" ? state : "";
    // A hidden (auto-hidden) island comes up for it, like it does when Lyra listens or thinks.
    if (this.circleState !== "") { try { if (State.mode === "hidden") this.fsm.forcePetit("Circle to ask"); } catch { /* */ } }
    this.syncLyraStatus();
  }

  private startDictLinger() {
    this.dictLinger = true;
    this.dictProcessing = false;
    window.clearTimeout(this.dictLingerTimer);
    this.dictLingerTimer = window.setTimeout(() => { this.dictLinger = false; this.syncLyraStatus(); }, 1700);
    this.syncLyraStatus();
  }

  /** The loudness envelope of the sentence Lyra is saying (backend "wave" event): drives the white wave. */
  feedWave(levels: number[], hz: number, startMs: number) {
    this.lyraStatus.feedWave(levels, hz, startMs);
  }

  /** Lyra started / stopped listening: the listening HUD owns the compact notch exclusively. */
  setListening(on: boolean) {
    const wasListening = this.lyraListening;
    this.lyraListening = on;
    if (on) this.endTypedTurn(false);   // the voice took over: nothing to come back to
    if (on !== wasListening) trace("lyra", `listening ${on ? "STARTED" : "ended"} (island ${State.mode}/${State.view})`);
    if (on !== wasListening && State.activityOn("listening")) {
      if (on) { this.fx.show("listening"); Sound.activity("listenOn"); }
      else { if (this.fx.kind() === "listening") this.fx.hide(); Sound.activity("listenOff"); }
    }
    this.fsm.keepCompact = on || this.lyraThinking || this.keepNotch();
    this.islandEl.classList.toggle("lyra-listening", on);
    if (on) {
      try {
        if (State.mode === "hidden") this.fsm.forcePetit("Lyra listening");
        else if (State.mode === "expanded" && !this.searchResultOpen) this.collapse("Lyra listening");
      } catch { /* */ }
      // Listening owns the notch just like speaking does. The existing PTT
      // activity HUD is the sole visible "Listening…" indicator.
      this.syncTimerChip();
    }
    if (!on && wasListening && this.volumeHudOn) {
      // The listening HUD is temporary. As soon as Lyra leaves the listening
      // state, release it so thinking/speaking/Spotify can take over cleanly.
      this.endVolumeHud();
    }
    if (on) {
      // Listening must never share the notch with Spotify, Mochi, or another
      // compact presentation. The PTT activity event will fill the HUD with
      // the existing PTT activity HUD provides the actual "Listening…" label.
      this.dirty = true;
      this.ensureRunning();
    }
    this.syncSpotifyCompact();
    if (!on) this.syncKeepCompact();
    this.syncLyraStatus();
    this.dirty = true;
    this.ensureRunning();
  }

  /** Lyra started / stopped talking; keep the island available during speech. */
  setSpeaking(on: boolean) {
    if (on !== this.lyraSpeaking) trace("lyra", `speaking ${on ? "STARTED" : "ended"} (island ${State.mode}/${State.view})`);
    this.lyraSpeaking = on;
    if (on && this.typedTurn) { this.typedSpoke = true; this.typedHold = false; window.clearTimeout(this.typedHoldTimer); }
    this.fsm.keepCompact = on || this.lyraListening || this.lyraThinking || this.keepNotch();
    // The caption window shows the speaking indicator. Keep other island tabs intact.
    this.islandEl.classList.toggle("lyra-speaking", on && (State.mode !== "expanded" || State.view === "prompt"));
    // While she talks, keep the small notch available and clear competing chrome.
    if (on) {
      try {
        if (State.mode === "hidden") this.fsm.forcePetit("Lyra speaking");
      } catch { /* */ }
      // Speaking owns the compact notch. Hide the timer chip and all right-side
      // mini bots until speech has completely finished.
      this.syncTimerChip();
    } else {
      // The timer may have been running underneath the speech. Reveal it only
      // after the speaking state has ended.
      this.syncTimerChip();
    }
    // While she talks the notch shows her (Mochi + white wave, see syncLyraStatus); the music pill comes back when she is done.
    if (this.spotifyState) this.syncSpotifyCompact();
    if (!on) { this.syncKeepCompact(); this.armChatAutoClose(); }
    this.syncLyraStatus();
    this.dirty = true;
    this.ensureRunning();
  }

  reveal() {
    this.fsm.reveal();
  }

  /** An alert stopped waiting for an answer: let the island auto-close again. */
  dropPin() {
    this.fsm.pinned = false;
  }

  // ── Volume HUD ──────────────────────────────────────────────────────────────

  private static readonly VOLUME_HUD_MS = 1600;


  /** System speaker volume changed (keys, slider, mixer): show it in the notch. */
  onVolume(level: number, muted: boolean) {
    if (!State.activityOn("volume")) return;
    const shown = muted ? 0 : Math.max(0, Math.min(1, level));
    this.showHud(volumeIconSvg(level, muted), muted ? "Muted" : "Speakers", shown, () => {
      this.hudFx("volume", { level: muted ? 0.3 : 0.35 + shown * 0.65 });
      this.sfx("volume", { level: shown, muted });
    });
  }

  /** Display brightness changed (keys / slider): same Apple-style HUD with a sun. */
  onBrightness(percent: number, ts?: number) {
    if (!State.activityOn("brightness")) return;
    if (ts && Date.now() - ts > 8000) return;
    const p = Math.max(0, Math.min(100, Math.round(percent)));
    const sun = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="currentColor"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5.3 5.3L7 7M17 17l1.7 1.7M5.3 18.7L7 17M17 7l1.7-1.7"/></g></svg>';
    this.showHud(sun, "Brightness", p / 100, () => {
      this.hudFx("brightness", { level: 0.25 + (p / 100) * 0.75 });
      this.sfx("brightness", { level: p / 100 });
    });
  }

  /** One app's own volume changed (Volume Mixer, Settings slider): same HUD, with the app's name. */
  onAppVolume(s: { app: string; level: number; muted: boolean; ts?: number }) {
    if (!State.activityOn("volume")) return;
    if (s.ts && Date.now() - s.ts > 8000) return;
    const level = Math.max(0, Math.min(1, s.level));
    const name = s.app.slice(0, 16);
    const label = s.muted ? `${name} muted` : `${name} ${Math.round(level * 100)}%`;
    this.showHud(volumeIconSvg(level, s.muted), label, s.muted ? 0 : level, () => {
      this.hudFx("volume", { level: s.muted ? 0.3 : 0.35 + level * 0.65 });
      this.sfx("volume", { level, muted: s.muted });
    });
  }

  /** Battery / charging pop-up: same HUD as the volume, with the charge as the bar. */
  onStatus(s: { text: string; percent: number; charging: boolean; ts?: number }) {
    if (s.ts && Date.now() - s.ts > 8000) return; // a buffered replay, not news
    const pct = Math.max(0, Math.min(100, Math.round(s.percent)));
    this.showHud(batteryIconSvg(pct, s.charging), `${s.text} ${pct}%`, pct / 100);
  }

  /** Bluetooth audio device connected / disconnected: icon + name, no bar. */
  onBluetooth(s: { text: string; ts?: number }) {
    if (!State.activityOn("devices")) return;
    if (s.ts && Date.now() - s.ts > 8000) return;
    const icon =
      '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M5 12a7 7 0 0 1 14 0v3.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><rect x="3" y="12.5" width="4" height="7" rx="1.6" fill="currentColor"/><rect x="17" y="12.5" width="4" height="7" rx="1.6" fill="currentColor"/></svg>';
    const gone = /disconnect|removed|lost|unplug/i.test(s.text);
    this.showHud(icon, s.text, null, () => {
      this.fx.show(gone ? "disconnect" : "devices", { originX: 24 });
      Sound.activity(gone ? "disconnect" : "connect");
    });
  }

  /** Reusable activity feedback (app launch, recognition, …). */
  onActivity(s: {
    kind: string;
    phase: string;
    title: string;
    subtitle?: string;
    app?: string;
    iconUrl?: string;
    url?: string;
    durationMs?: number;
    progress?: number;
    ts?: number;
  }) {
    if (s.ts && Date.now() - s.ts > 12_000) return;
    const title = (s.title || "").trim();
    if (!title) return;
    // While PTT is listening, the notch belongs exclusively to the listening
    // indicator. Ignore unrelated activity/Spotify-style HUDs until listening
    // ends, otherwise two presentations can briefly coexist.
    if (this.lyraListening && s.kind !== "ptt") return;
    // A song picked on the island is now playing: the same swelling banner as a recognised song
    // (cover left, name, Spotify icon right), then the normal music island comes back.
    if (s.kind === "song_play") {
      if (State.mode === "expanded") {
        State.view = State.defaultView();
        this.setMode("compact");
      }
      this.playPulse(describeSong({ title, artist: s.subtitle || "", artworkUrl: s.iconUrl || "", spotifyUrl: s.url || "spotify:" }), "", Island.PLAY_MS, "ok");
      Sound.activity("music");
      return;
    }
    const appKey = (s.app || "").toLowerCase().trim();
    if (s.kind === "download" && !State.activityOn("downloads")) return;
    const icon = activityIconSvg(s.kind, s.phase, appKey, title, s.iconUrl, s.progress);
    if (s.kind === "download") {
      // Downloads are compact-only activity. Never paint the download HUD over
      // an expanded page (including Home): expanded content owns the whole
      // panel. In compact mode it can be full-width, or reduce to a circle
      // when Spotify/another compact activity is already visible.
      if (State.mode === "expanded") {
        if (this.volumeHudOn && this.volumeHud.dataset.activityKind === "download") {
          this.endVolumeHud();
        }
        // Don't lose it: show the download as soon as the island shrinks again.
        this.pendingDownload = s.phase === "fail" ? null : { s, at: performance.now() };
        return;
      }
      this.pendingDownload = null;
      // Another activity (timer, music) owns the notch: the download becomes its own island.
      this.refreshActivities();
      if (this.dlMain) this.dlLast = s;
      if (this.activities.main() && !this.dlMain && this.takeDownloadAsIsland(s)) return;
      // The folder watcher and the browser watcher can both report the same download.
      if (s.phase === "success") {
        if (performance.now() - this.lastDlSuccessAt < 3500) return;
        this.lastDlSuccessAt = performance.now();
      }
      if (this.dlWatchdog != null) { window.clearTimeout(this.dlWatchdog); this.dlWatchdog = null; }
      const wasDl = this.volumeHudOn && this.volumeHud.dataset.activityKind === "download";
      this.volumeHud.dataset.downloadPhase = s.phase;
      const pctNum = Number.isFinite(s.progress) ? Math.max(0, Math.min(100, Number(s.progress))) : null;
      // Chromium's temporary "Unconfirmed 123456" name means nothing to a person.
      const dlName = /^unconfirmed\s+\d+/i.test(title) ? "Downloading a file" : title;
      this.showHudForced(dlTileHtml(s.phase, pctNum), dlMiddleEllipsis(dlName, 30), s.durationMs, "", s.subtitle, "download");
      this.volumeHud.title = dlName; // full file name on hover
      this.volumePct.textContent = s.phase === "success" ? "100%" : s.phase === "fail" ? "" : (pctNum != null ? `${Math.round(pctNum)}%` : "");
      const bar = this.volumeDlBar.firstElementChild as HTMLElement | null;
      if (bar) bar.style.width = s.phase === "success" ? "100%" : (pctNum != null ? `${pctNum}%` : "100%");
      this.volumeDlBar.classList.toggle("indeterminate", pctNum == null && s.phase !== "success");
      this.animateGeometry(false);
      if (s.phase !== "success" && s.phase !== "fail") {
        // Safety net: progress reports arrive every ~1.5s. If they stop (download cancelled,
        // browser closed, report lost) the card must not sit on the island forever.
        this.dlWatchdog = window.setTimeout(() => {
          this.dlWatchdog = null;
          if (this.volumeHudOn && this.volumeHud.dataset.activityKind === "download"
              && !["success", "fail"].includes(this.volumeHud.dataset.downloadPhase || "")) {
            delete this.volumeHud.dataset.downloadPhase;
            this.endVolumeHud();
          }
        }, 6500);
      }
      if (s.phase === "fail") {
        // A cancelled/interrupted download must disappear immediately. Do not
        // leave the old spinner parked on the island for the normal HUD timeout.
        delete this.volumeHud.dataset.downloadPhase;
        this.endVolumeHud();
        this.fx.show("fail");
        Sound.activity("downloadFail");
      } else if (s.phase === "success") {
        delete this.volumeHud.dataset.downloadPhase;
        this.fx.show("success", { originX: 26 });
        Sound.activity("downloadDone");
      } else if (!wasDl) {
        // the first progress report of a transfer: the island draws it in
        this.fx.show("download");
        Sound.activity("downloadStart");
      }
      return;
    }

    // Music recognition is intentionally ambient: when the user asks Lyra to
    // recognize music without opening the Recognition tab, never turn the
    // whole Island into a page. Show only the compact status pill.
    if (s.kind === "app_launch") {
      // App launches are compact Island notifications. If a full panel is
      // open, close it first so the app icon + "Opening …" is the thing the
      // user actually sees rather than a HUD floating over the page.
      if (State.mode === "expanded") {
        State.view = State.defaultView();
        this.setMode("compact");
      }
      this.appLaunchHudOn = true;
      this.showHudForced(icon, title, s.durationMs, "", s.subtitle, s.kind);
      this.fx.show("devices", { originX: 24 });
      Sound.activity("connect");
      return;
    }

    if (s.kind === "music_recognition") {
      if (State.view !== "recognition" && State.mode === "expanded") {
        State.view = State.defaultView();
        this.setMode("compact");
        this.showHudForced(icon, title, s.durationMs, "", s.subtitle, s.kind);
        this.recognitionCue();
      } else if (State.view === "recognition" && State.mode === "expanded") {
        // The dedicated Recognition page owns its own listening state. Do not
        // put a second activity HUD over the page (and do not hide Mochi).
        window.dispatchEvent(new CustomEvent("lyra:music-recognition-activity", { detail: s }));
      } else {
        this.showHudForced(icon, title, s.durationMs, "", s.subtitle);
        this.recognitionCue();
      }
      return;
    }
    this.showHudForced(icon, title, s.durationMs, "", s.subtitle);
  }

  /** Song recognition is listening: a prism of light round the notch until it has an answer. */
  private recognitionCue() {
    if (!State.activityOn("recognition") || this.fx.kind() === "recognition") return;
    this.fx.show("recognition");
    Sound.activity("listenOn");
  }

  onWebSearch(s: { query: string; provider: string; results: any[]; card?: any; related?: string[]; more?: any[]; moreTitle?: string; ts?: number }) {
    if (s.ts && Date.now() - s.ts > 20_000) return;
    const toItem = (r: any) => ({
      label: String(r?.title || "Result"),
      detail: String(r?.description || r?.content || r?.snippet || ""),
      url: String(r?.url || r?.href || "") || undefined,
      source: String(r?.source || "") || undefined,
      thumbnail: String(r?.thumbnail || "") || undefined,
      published: String(r?.published || "") || undefined,
      channel: String(r?.channel || "") || undefined,
      duration: String(r?.metadata?.duration || r?.duration || "") || undefined,
    });

    const result: SearchResult = {
      title: String(s.query || "Web search"),
      provider: String(s.provider || "") || undefined,
      related: (Array.isArray(s.related) ? s.related : []).map((x) => String(x || "").trim()).filter(Boolean).slice(0, 4),
      items: (Array.isArray(s.results) ? s.results : []).slice(0, 4).map(toItem),
      card: cleanCard(s.card),
      more: (Array.isArray(s.more) ? s.more : []).slice(0, 3).map(toItem),
      moreTitle: String(s.moreTitle || "").trim() || undefined,
    };
    if (!result.items.length && !result.card) return; // nothing to show: no empty panel
    this.showSearchResult(result);
    // The Chat tab keeps it in this session's conversation (a small card that reopens it).
    window.dispatchEvent(new CustomEvent("lyra:search-turn", { detail: result }));
  }

  /** Open the result deck on the island (a new search, or a tap on its card in the conversation). */
  showSearchResult(result: SearchResult) {
    this.endTypedTurn(false);   // the results open the Chat tab on their own
    State.searchResult = result;
    State.notify();

    this.searchResultOpen = true;
    this.fsm.lockSearch();
    this.fsm.pinned = false;
    // The panel is opened below without going through the state machine; if it still thought
    // the island was closed, nothing could ever close it again (black island, no dismiss).
    if (this.fsm.state !== "home") this.fsm.resync("home");
    // The rich-search presentation is the only time Chat gets a genuinely tall
    // native panel. Other tabs keep the normal 720×320 window.
    this.chatPanel(true);
    this.expand("prompt", true); // the results themselves live on the island
  }

  /** Validate the knowledge card sent by the backend; anything malformed is ignored. */

  hasSearchResultsOpen(): boolean {
    return this.searchResultOpen;
  }

  dismissSearchResults(collapse = true) {
    if (!this.searchResultOpen) return;
    this.searchResultOpen = false;
    this.fsm.unlockSearch();
    State.searchResult = null;
    State.notify();
    this.chatPanel(false);
    void Bridge.log(`island: search dismissed (collapse=${collapse}, mode=${State.mode}, view=${State.view}, fsm=${this.fsm.state})`);
    if (collapse && State.mode === "expanded") this.collapse("Search dismissed");
  }

  private stopDownloadSpinner() {
    if (this.downloadSpinnerRaf != null) {
      cancelAnimationFrame(this.downloadSpinnerRaf);
      this.downloadSpinnerRaf = null;
    }
    const ring = this.volumeHud.querySelector<SVGCircleElement>(".download-spinner-ring");
    if (ring) {
      ring.style.animation = "none";
      ring.style.strokeDashoffset = "0";
    }
  }

  private startDownloadSpinner() {
    // One loop for the whole download. Its angle comes from the clock, not from "when this
    // event arrived", so a re-rendered icon keeps turning smoothly and always completes its turn.
    if (this.downloadSpinnerRaf != null) { this.paintDownloadSpinner(performance.now()); return; }
    const tick = (now: number) => {
      if (!this.volumeHudOn || this.volumeHud.dataset.activityKind !== "download" || !this.volumeHud.classList.contains("on")) {
        this.downloadSpinnerRaf = null;
        return;
      }
      this.paintDownloadSpinner(now);
      this.downloadSpinnerRaf = requestAnimationFrame(tick);
    };
    this.paintDownloadSpinner(performance.now());
    this.downloadSpinnerRaf = requestAnimationFrame(tick);
  }

  private paintDownloadSpinner(now: number) {
    const ring = this.volumeHud.querySelector<SVGCircleElement>(".download-spinner-ring");
    if (!ring) return;
    ring.style.strokeDashoffset = `${-((now % 950) / 950) * DL_C}`;
  }

  /** Monochrome app / phase icons for the activity HUD. */


  /**
   * Activity HUD that always surfaces the compact notch (from hidden or expanded).
   * Layout: [icon] label  — matches volume HUD (icon left, bold white text).
   */
  private showHudForced(iconHtml: string, label: string, durationMs?: number, spotifyUrl = "", subtitle = "", activityKind = "") {
    traceLimited(`hudf-${label}`, 1500, "hud", `activity card on the notch: '${label.slice(0, 40)}' kind=${activityKind || (subtitle ? "rich" : "simple")}${durationMs ? ` for ${durationMs}ms` : ""}`);
    this.suspendToast();
    this.activitySpotifyUrl = spotifyUrl;
    this.volumeHud.classList.toggle("recognition-result", !!spotifyUrl);
    this.volumeSpotify.classList.toggle("on", !!spotifyUrl);
    // Only touch the icon when it actually changed: re-creating it on every progress
    // tick is what made the spinner restart before it could finish a turn.
    if (this.volumeIcon.dataset.sig !== iconHtml) {
      this.volumeIcon.innerHTML = iconHtml;
      this.volumeIcon.dataset.sig = iconHtml;
    }
    this.volumeLabel.textContent = label;
    this.volumeSubtitle.textContent = subtitle || "";
    this.volumeHud.dataset.activityKind = activityKind || (subtitle ? "rich" : "simple");
    if (activityKind === "download") {
      // A download is a secondary activity when another compact activity is
      // already owning the island (Spotify, timer, app launch, voice, etc.).
      // In that case keep the primary activity visible and reduce the download
      // HUD to a small right-side progress circle.
      // The compact notch can only spare the little right-side circle when
      // another activity already owns it. In an expanded island there is room
      // for the complete download title/subtitle, even if Spotify is playing.
      const secondary =
        State.mode === "compact" && !this.dlMain && (
          this.isSpotifyMediaActive() ||
          this.timerChip.active() ||
          this.appLaunchHudOn ||
          this.lyraListening ||
          this.lyraSpeaking
        );
      this.volumeHud.dataset.downloadCompact = secondary ? "1" : "0";
    } else {
      delete this.volumeHud.dataset.downloadPhase;
      delete this.volumeHud.dataset.downloadCompact;
    }
    this.volumeHud.classList.add("nobar", "activity");
    this.volumeFill.style.width = "0%";
    this.volumeThumb.style.left = "0%";

    const wasOn = this.volumeHudOn;
    if (State.mode === "hidden") {
      this.volumeRevealed = !this.fsm.keepCompact;
      this.fsm.reveal();
    }
    this.volumeHudOn = true;
    // Speaker HUD owns the compact notch while it is visible. Hide the Spotify
    // compact layer immediately so it cannot bleed through the volume slider.
    this.syncSpotifyCompact();
    this.volumeHud.style.removeProperty("opacity");
    this.volumeHud.classList.add("on");
    if (activityKind === "download") this.startDownloadSpinner();
    if (!wasOn) {
      this.animateGeometry(false);
    }
    this.dirty = true;
    this.ensureRunning();

    // Downloads own the HUD for the whole transfer. Repeated progress events keep
    // it alive, and the completion/failure event is the only thing that releases it.
    const holdForDownload = activityKind === "download" && !["success", "fail"].includes((this.volumeHud.dataset.downloadPhase || "").toLowerCase());
    const ms = holdForDownload ? 0 : (durationMs && durationMs > 0 ? durationMs : Island.VOLUME_HUD_MS);
    if (this.volumeTimer != null) window.clearTimeout(this.volumeTimer);
    this.volumeTimer = null;
    if (ms > 0) this.volumeTimer = window.setTimeout(() => {
      this.volumeHud.classList.remove("activity");
      this.volumeHud.removeAttribute("data-activity-kind");
      this.volumeSubtitle.textContent = "";
      this.endVolumeHud();
    }, ms);
  }


  /** Music recognition result card in the compact notch. */
  onMusicRecognition(s: {
    ok: boolean;
    title: string;
    artist: string;
    album?: string;
    artworkUrl?: string;
    spotifyUrl?: string;
    youtubeUrl?: string;
    message?: string;
    source?: string;
    ts?: number;
  }) {
    if (s.ts && Date.now() - s.ts > 20_000) return;

    // Keep the Recognition history synchronized even when the Recognition tab
    // is not the active view. The history view will merge this with the backend
    // copy when opened, while the local event makes Ctrl+Win recognition
    // immediately visible in Previous recognitions.
    window.dispatchEvent(new CustomEvent("lyra:music-recognition-history", {
      detail: {
        ok: s.ok,
        title: s.title,
        artist: s.artist,
        album: s.album || "",
        artwork_url: s.artworkUrl || "",
        spotify_url: s.spotifyUrl || "",
        youtube_url: s.youtubeUrl || "",
        message: s.message || "",
        source: s.source || "",
        ts: s.ts || Date.now(),
      },
    }));

    // The answer: a harp run and a prism of light when it is found, a small shrug when it is not.
    if (State.activityOn("recognition")) {
      if (s.ok) {
        if (State.mode !== "expanded") this.fx.show("found", { originX: 26 });
        else if (this.fx.kind() === "recognition") this.fx.hide();
        Sound.activity("found");
      } else {
        if (this.fx.kind() === "recognition") this.fx.hide();
        Sound.activity("notFound");
      }
    }

    // The Recognition tab is the only place allowed to remain expanded.
    // Voice/agent recognition stays as a compact Island notification.
    if (State.view !== "recognition" && State.mode === "expanded") {
      State.view = State.defaultView();
      this.setMode("compact");
    }

    if (State.view === "recognition" && State.mode === "expanded") {
      // Let the expanded Recognition page render the result directly instead of
      // covering it with the compact HUD.
      // The Recognition view uses the backend's snake_case field names while
      // the Island controller uses camelCase. Normalize the event here so the
      // expanded card receives artwork + Spotify instead of silently dropping
      // both fields.
      window.dispatchEvent(new CustomEvent("lyra:music-recognition-result", {
        detail: {
          ok: s.ok,
          title: s.title,
          artist: s.artist,
          album: s.album || "",
          artwork_url: s.artworkUrl || "",
          spotify_url: s.spotifyUrl || "",
          youtube_url: s.youtubeUrl || "",
          message: s.message || "",
          source: s.source || "",
          ts: s.ts,
        },
      }));
      // The tab shows the result, and the song also drops in over it as a banner (cover, name, Spotify).
      if (s.ok) this.playPulse(describeSong(s), "", Island.SONG_MS, "ok");
      return;
    }

    if (!s.ok) {
      const msg = s.source === "capture"
        ? "Couldn't capture system audio"
        : (s.message || "No match found");
      this.showHudForced(
        '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" d="M4 12h4l2-5 4 10 2-5h4"/></svg>',
        msg.slice(0, 42),
        3200,
      );
      return;
    }
    // Found it: the notch swells and shows the cover, the song and the Spotify button.
    if (State.mode !== "expanded") {
      const wasRevealed = this.volumeRevealed;
      if (this.volumeHudOn) {
        // Replace the "Listening…" card (it must not put the island back to sleep in between).
        if (this.volumeTimer != null) window.clearTimeout(this.volumeTimer);
        this.volumeRevealed = false;
        this.endVolumeHud();
      }
      this.playPulse(describeSong(s), "", Island.SONG_MS, "ok");
      if (wasRevealed) this.pulseRevealed = true;
      return;
    }
    const label = [s.title, s.artist].filter(Boolean).join(" — ").slice(0, 48) || "Track identified";
    let icon =
      '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M9 17.5V6.6l10-2v10.9a2.7 2.7 0 1 1-1.6-2.5V7.6L10.6 9v8.5A2.7 2.7 0 1 1 9 17.5z"/></svg>';
    if (s.artworkUrl) {
      icon = `<img src="${s.artworkUrl.replace(/"/g, "")}" width="22" height="22" style="border-radius:4px;object-fit:cover" alt=""/>`;
    }
    this.showHudForced(icon, label, 8000, s.spotifyUrl || "");
  }

  /** Microphone in use: small privacy dot beside the notch. */
  onPrivacy(mic: boolean) {
    clear(this.privacyDots);
    if (mic) this.privacyDots.append(h("i", { class: "mic", title: "Microphone in use" }));
    this.privacyDots.classList.toggle("on", mic);
  }


  /** Shared by the volume and battery pop-ups: icon + label + bar in the compact notch. */
  private showHud(iconHtml: string, label: string, fill: number | null, cue?: () => void) {
    // An open panel is being used; the HUD only replaces the compact notch.
    if (State.mode === "expanded") {
      traceLimited(`hud-skip-${label}`, 3000, "hud", `HUD '${label.slice(0, 30)}' NOT shown because the panel is open`);
      return;
    }
    traceLimited(`hud-${label}`, 1000, "hud", `HUD shown on the compact notch: '${label.slice(0, 30)}'${fill != null ? ` (${Math.round(fill * 100)}%)` : ""}`);

    this.suspendToast(); // the HUD wins; the notification comes back after it
    cue?.();             // its sound and its light (only now that it is really being shown)
    this.volumeIcon.innerHTML = iconHtml;
    this.volumeLabel.textContent = label;
    this.volumeHud.classList.toggle("nobar", fill == null);
    this.volumeFill.style.width = `${(fill ?? 0) * 100}%`;
    this.volumeThumb.style.left = `${(fill ?? 0) * 100}%`;

    const wasOn = this.volumeHudOn;
    if (State.mode === "hidden") {
      // Wake the compact notch for the HUD, and remember to put it back to sleep.
      this.volumeRevealed = !this.fsm.keepCompact;
      this.fsm.reveal();
    }
    this.volumeHudOn = true;
    this.volumeHud.style.removeProperty("opacity");
    this.volumeHud.classList.add("on");
    if (!wasOn) {
      this.animateGeometry(false);
    }
    this.dirty = true;
    this.ensureRunning();

    if (this.volumeTimer != null) window.clearTimeout(this.volumeTimer);
    this.volumeTimer = window.setTimeout(() => this.endVolumeHud(), Island.VOLUME_HUD_MS);
  }

  private endVolumeHud() {
    this.volumeTimer = null;
    if (this.dlWatchdog != null) { window.clearTimeout(this.dlWatchdog); this.dlWatchdog = null; }
    const endedKind = this.volumeHud.dataset.activityKind || "";
    const fxk = this.fx.kind();
    if (fxk && ["volume", "brightness", "devices", "disconnect", "download", "success", "fail", "recognition", "found"].includes(fxk)) this.fx.hide();
    this.stopDownloadSpinner();
    this.volumeHudOn = false;
    this.appLaunchHudOn = false;

    // A download reuses the volume HUD, so merely removing `on` is not enough:
    // the old activity class/data could leave the spinner painted/animated after
    // the transfer has already ended. Tear the download presentation down
    // completely before Spotify/other activities are re-synced.
    if (endedKind === "download") {
      const ring = this.volumeHud.querySelector<SVGElement>(".download-spinner-ring");
      if (ring) {
        ring.style.animation = "none";
        ring.style.strokeDashoffset = "0";
      }
      this.volumeHud.classList.remove("activity");
      delete this.volumeHud.dataset.activityKind;
      delete this.volumeHud.dataset.downloadPhase;
      delete this.volumeHud.dataset.downloadCompact;
      if (this.dlMain) { this.dlMain = false; this.activities.set("download", false); }
      this.volumeIcon.innerHTML = "";
      delete this.volumeIcon.dataset.sig;
      this.volumeLabel.textContent = "";
      this.volumeSubtitle.textContent = "";
      this.volumePct.textContent = "";
      this.volumeHud.removeAttribute("title");
      (this.volumeDlBar.firstElementChild as HTMLElement | null)?.style.setProperty("width", "0");
      // Hide immediately; do not wait for a geometry/frame update.
      this.volumeHud.style.opacity = "0";
    } else {
      this.volumeHud.classList.remove("activity");
      this.volumeHud.removeAttribute("data-activity-kind");
      delete this.volumeHud.dataset.downloadPhase;
      delete this.volumeHud.dataset.downloadCompact;
    }
    this.volumeHud.classList.remove("on", "recognition-result");
    this.volumeHud.style.opacity = "0";
    this.volumeSpotify.classList.remove("on");
    this.activitySpotifyUrl = "";
    this.animateGeometry(true);
    this.dirty = true;
    this.ensureRunning();
    this.syncAmbientFx();
    // The HUD was the only reason the notch was up: let it go again.
    if (this.volumeRevealed) {
      this.volumeRevealed = false;
      if (!this.wasInIsland && State.mode === "compact" && !this.toastQueue.length) this.fsm.forceHidden();
    }
    this.pumpToast();
  }

  // ── Notification toast ──────────────────────────────────────────────────────

  private static readonly TOAST_MS = 4000;
  /** Events older than this when they arrive are bridge-buffer replays, not news. */
  private static readonly TOAST_STALE_MS = 15_000;
  /** A queued notification that waited longer than this (panel open) is dropped. */
  private static readonly TOAST_MAX_WAIT_MS = 60_000;

  /** Toast length: the backend sends the Settings value with each event (clamped here). */
  private toastMs(ev: NotifyEvent | null): number {
    const ms = ev?.durationMs;
    return ms && ms > 0 ? Math.min(15_000, Math.max(1500, ms)) : Island.TOAST_MS;
  }

  /** A message, call or reminder arrived (from the backend's "notify" event). */
  onNotify(ev: NotifyEvent) {
    // The tray's Pause is silent: nothing shown, nothing kept.
    void Bridge.log(`toast: got ${ev.app}/${ev.kind} (mode=${State.mode} paused=${State.paused} age=${Date.now() - ev.ts}ms)`);
    trace("toast", `notification from ${ev.app} (${ev.kind}) arrived: island=${State.mode}, paused=${State.paused}, age=${Date.now() - ev.ts}ms, toastShowing=${this.toastOn}, queued=${this.toastQueue.length}`);
    if (State.paused) return;
    if (ev.kind === "call" && !State.activityOn("calls")) return;
    if (ev.kind === "reminder" && !State.activityOn("reminders")) return;
    if (ev.kind === "message" && !State.activityOn("notifications")) return;
    if (Date.now() - ev.ts > Island.TOAST_STALE_MS) {
      void Bridge.log("toast: dropped, event too old");
      trace("toast", `notification from ${ev.app} DROPPED: too old (${Date.now() - ev.ts}ms)`);
      return;
    }

    // Another message from the same chat while its toast is up: update in place.
    const cur = this.toastCurrent;
    if (this.toastOn && cur && cur.app === ev.app && cur.kind === ev.kind && cur.sender === ev.sender) {
      this.toastCurrent = ev;
      this.toast.set(ev);
      this.armToastTimer(this.toastMs(ev));
      if (ev.kind === "message") this.toastCue(ev);       // another message in the same chat: the light and the sound again
      return;
    }
    this.toastQueue.push(ev);
    if (this.toastQueue.length > 5) this.toastQueue.shift();
    this.pumpToast();
  }

  /** Shows the next waiting notification, if the notch is free for it. */
  private pumpToast() {
    if (State.paused || this.toastOn || this.pulseOn) return;
    // The open island is never covered by a notification: it waits in the queue.
    if (State.mode === "expanded") return;
    // The panel and the volume HUD own the notch while they are up.
    if (this.volumeHudOn) {
      void Bridge.log("toast: waiting (volume HUD up)");
      return;
    }
    const now = Date.now();
    let next = this.toastQueue.shift();
    while (next && now - next.ts > Island.TOAST_MAX_WAIT_MS) next = this.toastQueue.shift();
    if (!next) return;

    this.toastOn = true;
    this.toastCurrent = next;
    this.toast.set(next);
    if (State.mode === "hidden") {
      // Wake the compact notch for it, and remember to put it back to sleep.
      this.toastRevealed = !this.fsm.keepCompact;
      this.fsm.reveal();
    }
    this.toast.el.classList.add("on");
    this.animateGeometry(false);                           // a little bigger while it shows
    void Bridge.log(`toast: showing ${next.app}/${next.kind} (mode=${State.mode})`);
    this.toastCue(next);
    this.armToastTimer(this.toastMs(next));
    this.dirty = true;
    this.ensureRunning();
  }

  /** The sound and the light of a notification: each app, call and reminder has its own. */
  private toastCue(ev: NotifyEvent) {
    if (ev.kind === "call") {
      this.fx.show("call");
      Sound.ringStart();
    } else if (ev.kind === "reminder") {
      this.fx.show("reminder", { color: APP_FX_COLOR.reminder });
      // checkReminders() already rang the alarm for calendar reminders; any other reminder rings here
      if (Date.now() - this.lastAlarmAt > 4000) Sound.activity("reminder");
    } else if (ev.kind === "message") {
      this.fx.show("message", { color: APP_FX_COLOR[ev.app] });
      Sound.activity("message", { app: ev.app });
    } else {
      Sound.play("blip");
    }
  }

  /** Stops the light and the ringing of whatever toast was up. */
  private toastCueEnd() {
    Sound.ringStop();
    const k = this.fx.kind();
    if (k === "call" || k === "reminder" || k === "message") this.fx.hide();
  }

  /** A sound for a HUD that can fire many times a second (volume keys): at most one every ~85 ms. */
  private sfx(name: Parameters<typeof Sound.activity>[0], o: Parameters<typeof Sound.activity>[1] = {}) {
    const now = performance.now();
    if ((name === "volume" || name === "brightness") && now - this.sfxAt < 85) return;
    this.sfxAt = now;
    Sound.activity(name, o);
  }

  /** The light of a HUD. A held key must not strobe it: the same kind restarts at most every ~450 ms. */
  private hudFx(kind: FxKind, o: Parameters<Fx["show"]>[1] = {}) {
    const now = performance.now();
    if (this.fx.kind() === kind && now - this.fxAt < 450) return;
    this.fxAt = now;
    this.fx.show(kind, o);
  }

  private armToastTimer(ms: number) {
    if (this.toastTimer != null) window.clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => {
      this.toastTimer = null;
      // Don't pull it out from under the cursor: wait until the mouse has left.
      if (this.wasInIsland && State.mode !== "expanded") this.armToastTimer(1500);
      else this.endToast();
    }, ms);
  }

  /** Puts the toast back at the head of the queue (panel opened / volume HUD took over). */
  private suspendToast() {
    if (!this.toastOn) return;
    if (this.toastTimer != null) window.clearTimeout(this.toastTimer);
    this.toastTimer = null;
    if (this.toastCurrent) this.toastQueue.unshift(this.toastCurrent);
    this.toastOn = false;
    this.toastCurrent = null;
    this.toast.el.classList.remove("on");
    this.toastCueEnd();
  }

  private endToast() {
    if (!this.toastOn) return;
    if (this.toastTimer != null) window.clearTimeout(this.toastTimer);
    this.toastTimer = null;
    this.toastOn = false;
    this.toastCurrent = null;
    this.toast.el.classList.remove("on");
    this.toastCueEnd();
    this.syncAmbientFx();
    if (State.mode === "compact") this.animateGeometry(!this.wasInIsland);
    this.dirty = true;
    this.ensureRunning();
    if (this.toastQueue.length) {
      window.setTimeout(() => this.pumpToast(), 260); // let the fade-out read before the next one
      return;
    }
    if (this.toastRevealed) {
      this.toastRevealed = false;
      if (!this.wasInIsland && State.mode === "compact") this.fsm.forceHidden();
    }
  }

  /** The X on a toast. For a dictation offer it also tells the backend to forget it and drops any others waiting. */
  private declineToast() {
    if (this.toastCurrent?.kind === "call") Sound.activity("decline");
    if (this.toastCurrent?.kind === "dictation") {
      this.toastQueue = this.toastQueue.filter((e) => e.kind !== "dictation");
      void fetch("http://127.0.0.1:8765/dictation/dismiss", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => undefined);
    }
    this.endToast();
  }

  /** Click on the toast: open the app it came from, then dismiss it. */
  private clickToast() {
    if (this.toastCurrent?.kind === "call") Sound.activity("answer");
    // "Apply polished version" (dictation): the backend puts the text in its window and reports on the island.
    if (this.toastCurrent?.kind === "dictation") {
      this.toastQueue = this.toastQueue.filter((e) => e.kind !== "dictation");
      void fetch("http://127.0.0.1:8765/dictation/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => undefined);
      this.endToast();
      return;
    }
    // A WhatsApp message that came through Lyra's own WhatsApp link opens that chat on the notch (big chat);
    // otherwise (the Windows toast of the desktop app) it opens the app as before.
    const cur = this.toastCurrent;
    if (cur?.app === "whatsapp" && cur.chat && State.viewAllowed("apps")) {
      const chat = cur.chat;
      this.endToast();
      openAppsChat("whatsapp", chat);
      this.onOpenView("apps");
      return;
    }
    const url = cur ? appUrl(cur.app) : null;
    if (url) void Bridge.openUrl(url);
    this.endToast();
  }

  // ── File drop ───────────────────────────────────────────────────────────────

  private async captureClipboardFiles(paths: string[]) {
    try {
      const r = await fetch("http://127.0.0.1:8765/clipboard/capture-files", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths }),
      });
      const d = await r.json();
      void Bridge.log(`clipboard saved ${paths.length} file(s): ${d.ok ? "ok" : "failed"}`);
      FileDropFx.done(!!d.ok, d.ok ? undefined : "Couldn't save that");
      State.notify();
    } catch (err) {
      void Bridge.log(`clipboard file capture failed: ${String(err)}`);
      FileDropFx.done(false, "Lyra is unavailable");
    }
  }

  /** Close the tab an incoming drag opened, once the drag is over. Waits for Mochi to finish reacting to the drop. */
  private closeDragOpenedClipboard(waits = 0) {
    if (!this.dragOpenedClipboard || State.fileDragOver || this.htmlDragLive) return;
    if (FileDropFx.busy && waits < 24) {   // bounded (6 s): a stuck save must not pin the island open
      window.setTimeout(() => this.closeDragOpenedClipboard(waits + 1), 250);
      return;
    }
    this.dragOpenedClipboard = false;
    if (State.view === "clipboard") this.collapse("drag finished");
  }

  /** A file is approaching: open the Clipboard tab right away so it can be dropped there. Idempotent. */
  private openClipboardForFileDrag() {
    if (State.paused) return;
    if (State.mode === "expanded" && State.view === "clipboard") return;
    if (this.dragOpenedClipboard) return;
    this.dragOpenedClipboard = State.mode !== "expanded";   // only close it again if the drag is what opened it
    this.fsm.forceHome("file drag");
    this.setView("clipboard");
  }

  /**
   * Browser drag events. On Windows the island window runs with Tauri's native file-drop hook off
   * (dragDropEnabled: false), so Explorer drags arrive as plain HTML5 events and onDragDrop() below
   * never fires. This is the same "open Clipboard for an incoming file" behaviour for that path;
   * the Clipboard tab's own listeners (views/clipboard.ts) then take the hover and the drop.
   */
  private wireHtmlFileDrag() {
    const hasFiles = (e: DragEvent) => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
    const incoming = (e: DragEvent) => {
      if (!hasFiles(e) || State.paused) return;
      this.htmlDragLive = true;
      // dragleave is unreliable when the cursor leaves the window, so dragover keeps re-arming this.
      window.clearTimeout(this.htmlDragTimer);
      this.htmlDragTimer = window.setTimeout(() => {
        this.htmlDragLive = false;
        window.setTimeout(() => this.closeDragOpenedClipboard(), 350);
      }, 450);
      this.openClipboardForFileDrag();
    };
    window.addEventListener("dragenter", incoming);
    window.addEventListener("dragover", incoming);
    window.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      this.htmlDragLive = false;
      window.clearTimeout(this.htmlDragTimer);
      // Hold the tab open while Mochi eats the file and shows the result (closeDragOpenedClipboard waits for it).
      if (this.dragOpenedClipboard) window.setTimeout(() => this.closeDragOpenedClipboard(), 2200);
    });
  }

  private onDragDrop(e: { type: string; paths?: string[] }) {
    // Clipboard uses the same native Tauri drag/drop stream as the main Drop flow.
    // Keep it here so there is only one native listener and files reliably reach
    // the clipboard instead of being swallowed by the normal upload sequence.
    // A file approaching the island opens Clipboard right away: drop it to save it there.
    if (e.type === "enter" || e.type === "over") this.openClipboardForFileDrag();
    if (this.dragOpenedClipboard || (State.mode === "expanded" && State.view === "clipboard")) {
      if (State.paused) return;
      if (this.dragOpenedClipboard && (e.type === "leave" || e.type === "drop")) {
        const hold = e.type === "drop" ? 2200 : 350;
        window.setTimeout(() => this.closeDragOpenedClipboard(), hold);
      }
      if (e.type !== "over") void Bridge.log(`clipboard drag ${e.type} ${e.paths?.length ?? 0} file(s)`);
      switch (e.type) {
        case "enter":
        case "over":
          if (!State.fileDragOver) {
            State.fileDragOver = true;
            State.notify();
          }
          break;
        case "leave":
          if (State.fileDragOver) {
            State.fileDragOver = false;
            State.notify();
          }
          break;
        case "drop": {
          State.fileDragOver = false;
          const paths = Array.isArray(e.paths) ? e.paths.filter(Boolean) : [];
          if (paths.length) FileDropFx.start(paths.map((p) => ({ name: p.split(/[\\/]/).pop() || "file" })));   // Mochi eats first, then the hover is released
          State.notify();
          if (paths.length) void this.captureClipboardFiles(paths);
          else void Bridge.log("clipboard drop received with no file paths");
          break;
        }
      }
      return;
    }
    if (e.type !== "over") void Bridge.log(`drag ${e.type} ${e.paths?.length ?? 0} file(s)`);
    if (State.paused) return;
    switch (e.type) {
      case "enter":
      case "over": {
        if (State.fileDragOver) return;
        State.fileDragOver = true;
        // enterZone must run before the island expands, so the sequence is
        // already active by the time the view becomes `upload`.
        UploadSeq.enterZone(State.mouseInIsland.x, State.mouseInIsland.y);
        this.alert("upload");
        break;
      }
      case "leave": {
        if (!State.fileDragOver) return;
        State.fileDragOver = false;
        // The island deliberately stays open: the drag session is still alive.
        UploadSeq.exitZone();
        State.notify();
        break;
      }
      case "drop": {
        State.fileDragOver = false;
        const path = e.paths?.[0];
        if (!path) {
          this.setView(State.defaultView());
          return;
        }
        this.swallow(path);
        break;
      }
    }
  }

  /**
   * Mochi eats the file. Nothing here waits on the file system: the copy into
   * the inbox runs in the background and swaps the path in when it lands, so a
   * slow disk can never stall the animation — same as FileDropHandler on macOS.
   */
  private swallow(path: string) {
    const name = path.split(/[\\/]/).pop() || "file";
    State.droppedFile = { name, path };
    State.promptContext = { kind: "file", name, path };
    State.chatHistory = [];
    void Bridge.chatReset();

    UploadSeq.performDrop(State.uploadDuration);
    this.uploadTens = 0;
    this.uploadDone = false;

    Sound.play("approve");

    State.uploadProgress = 0;
    this.setView("uploading");
    this.ensureRunning();

    void Bridge.ingestFile(path)
      .then((file) => {
        State.droppedFile = { name: file.name, path: file.path };
        State.promptContext = { kind: "file", name: file.name, path: file.path };
        State.notify();
      })
      .catch((err) => {
        UploadSeq.deactivate();
        State.noteMessage = String(err).replace(/^Error:\s*/, "");
        this.setView("note");
        Sound.play("error");
        window.setTimeout(() => this.setView(State.defaultView()), 2400);
      });
  }

  /**
   * Sounds and view changes hung off the canvas timeline: a `tick` every 10 %,
   * the ✓ chime when the bar completes, then `choose` once Mochi has grown back.
   */
  private stepSequence() {
    const since = UploadSeq.sinceDrop();
    if (since == null) return;
    const dur = State.uploadDuration;
    const p = Math.max(0, Math.min(1, (since - PRE_PROGRESS) / dur));

    const tens = Math.floor(p * 10);
    if (tens > this.uploadTens && tens < 10) {
      this.uploadTens = tens;
      Sound.play("tick");
    }

    if (!this.uploadDone && since >= PRE_PROGRESS + dur) {
      this.uploadDone = true;
      Sound.play("approve");
    }
    // The extra second is the grow-back, after which the choose card is up.
    if (since >= PRE_PROGRESS + dur + 1 && State.view === "uploading") {
      this.setView("choose");
    }
  }

  // ── Geometry ────────────────────────────────────────────────────────────────

  /**
   * ATTACHED island only (the floating island is never touched): style.css pushes a tab's content 10px down so the
   * title is not swallowed by the clipped top edge. These tabs are sized for their content, so they get those 10px
   * back at the bottom, otherwise their last row ends up squashed against / under the rounded bottom corners.
   */
  private targetSize(): { w: number; h: number; r: number } {
    const s = this.targetSizeBase();
    if (State.islandPosition === "attached" && State.mode === "expanded" && ATTACHED_ROOM_VIEWS.has(State.view) && s.h <= 270) {
      return { ...s, h: s.h + ATTACHED_ROOM };
    }
    return s;
  }

  private targetSizeBase(): { w: number; h: number; r: number } {
    // Lyra listening / thinking / speaking owns the compact notch: a slightly wider pill (Mochi + text or wave).
    if (this.lyraStatusKind !== null && State.mode === "compact") {
      // The same look as the setup previews: a roomy pill with the Mochi, the words / bars and the round button.
      return { w: 392, h: 58, r: 29 };
    }
    // The volume HUD only ever lives in the compact notch (see onVolume).
    if (this.volumeHudOn && State.mode === "compact" && !this.downloadIsSecondary()) {
      if (this.volumeHud.dataset.activityKind === "download" && this.volumeHud.dataset.downloadCompact !== "1") {
        return { w: 380, h: 58, r: 29 }; // roomy download card: tile, name, stats, progress bar
      }
      return { w: VOLUME_HUD_W, h: VOLUME_HUD_H, r: VOLUME_HUD_H / 2 };
    }
    // An "added" pulse swells the compact notch into a wide pill (the spring gives it the bounce).
    const base = islandSize(State.mode, State.view, State.chatVisible().length);
    if (this.pulseOn && State.mode === "compact") {
      // A mode switch only swells the notch a little (like a notification); "added" opens the wide pill.
      return this.pulseCompact ? { w: base.w + 64, h: base.h + 12, r: (base.h + 12) / 2 } : { w: PULSE_W, h: PULSE_H, r: PULSE_H / 2 };
    }
    // A notification (reminder, WhatsApp, Discord...) swells the notch a bit while it is showing.
    if (this.toastOn && State.mode === "compact") return { w: base.w + 56, h: base.h + 12, r: (base.h + 12) / 2 };
    // Pointer resting on the compact notch: it swells a little, as if asking "open me?" (see invite.ts).
    const asking = State.mode === "compact" && this.wasInIsland && !this.toastOn;
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
      if (this.searchResultOpen) {
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
      "approval", "question", "error", "finished", "confused", "choose", "mail",
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
    // Study: the quiz and scratchpad use the tall single-column stage; the home screen is shorter.
    if (State.mode === "expanded" && State.view === "study" && !studyStageActive()) {
      return { w: base.w, h: Math.min(base.h, STUDY_HOME_H), r: EXPANDED_CORNER };
    }
    const h = base.h;
    // Compact: a full pill (radius = half the height), the same shape as the Settings preview. Hidden keeps the small corner.
    const r = State.mode === "expanded" ? EXPANDED_CORNER : State.mode === "compact" ? h / 2 : ROUNDED_CORNER;
    return { w: base.w, h, r };
  }

  private animateGeometry(shrinking: boolean) {
    const { w, h, r } = this.targetSize();
    traceLimited("geometry", 120, "size", `island ${shrinking ? "shrinking" : "growing"} ${Math.round(this.width.value)}x${Math.round(this.height.value)} -> ${w}x${h} (corner ${r}) for ${State.mode}/${State.view}`);
    if (shrinking) {
      this.width.curveTowards(w);
      this.height.curveTowards(h);
      this.radius.curveTowards(r);
    } else {
      this.width.springTo(w);
      this.height.springTo(h);
      this.radius.springTo(r);
    }
    this.ensureRunning();
  }

  private applyGeometry() {
    const w = this.width.value;
    const hh = this.height.value;
    const r = this.radius.value;
    this.islandEl.style.width = `${w}px`;
    this.islandEl.style.height = `${hh}px`;

    const attached = State.islandPosition === "attached";

    // Attached mode restores the old Lyra look: square top edge flush with the
    // screen, only the bottom corners rounded (classic notch / drop-zone style).
    // Floating keeps the fully-rounded Dynamic Island silhouette.
    if (attached) {
      this.islandEl.style.borderRadius = `0 0 ${r}px ${r}px`;
    } else {
      this.islandEl.style.borderRadius = `${r}px`;
    }

    // Attached sits flush at y=0 (old behaviour). Floating has a small breathing gap.
    const islandTop = attached ? 0 : 18;
    this.islandEl.style.transform = `translateX(-50%) translateY(${islandTop}px)`;

    const dockTop = islandTop + hh + 6;
    this.tabDock.el.style.top = `${dockTop}px`;
    this.tabDock.el.style.zIndex = "1000";
    // Whether the dock (it holds the scroller) shows is decided in views/dock.ts sync(): hidden for "Tab wheel", shown while expanded for "Scroller" / "Both".
    // These follow the island as it resizes, so they belong here rather than in
    // the state-driven DOM sync.

    // Position the secondary island, then tell the native layer what is clickable.
    for (const side of ["left", "right"] as const) {
      const slot = this.slots[side];
      if (!slot.shown) continue;
      const sr = this.secondaryRect(slot, side, (PANEL_W - w) / 2, w, islandTop, hh);
      slot.el.style.left = `${sr.x}px`;
      slot.el.style.top = `${sr.y}px`;
    }
    this.pushHitShapes({ x: (PANEL_W - w) / 2, y: islandTop, w, h: hh });
  }

  /**
   * Native click-through. The clickable shapes are measured from the real elements
   * (island, dock, secondary island), so what you see is exactly what takes the click;
   * everything else behaves like the desktop behind it.
   */
  private pushHitShapes(fallback?: { x: number; y: number; w: number; h: number }) {
    const box = (el: HTMLElement) => {
      const r = el.getBoundingClientRect();
      return { x: r.left, y: r.top, w: r.width, h: r.height };
    };
    const ib = box(this.islandEl);
    const rect = State.mode === "hidden" || ib.w < 2 ? (fallback ?? this.islandRect()) : ib;
    const extra: { x: number; y: number; w: number; h: number }[] = [];
    for (const side of ["left", "right"] as const) {
      if (!this.slots[side].shown) continue;
      const sb = box(this.slots[side].el);
      if (sb.w > 2) extra.push(sb);
    }
    if (State.mode === "expanded") {
      const db = box(this.tabDock.el);
      if (db.w > 2) extra.push(db);
    }
    const key = [rect.x, rect.y, rect.w, rect.h, ...extra.flatMap((e) => [e.x, e.y, e.w, e.h])];
    const p = this.pushedKey;
    if (p.length !== key.length || key.some((v, k) => Math.abs(v - p[k]) > 0.5)) {
      this.pushedKey = key;
      void Bridge.setIslandRect(rect.x, rect.y, rect.w, rect.h, extra);
    }
  }

  /** Island rect in window coordinates (origin top-left of the 720×320 window). */
  private islandRect(): { x: number; y: number; w: number; h: number } {
    const w = this.width.value;
    const hh = this.height.value;
    return { x: (PANEL_W - w) / 2, y: 0, w, h: hh };
  }

  // ── Window collapse (hidden → tiny wake strip, zero polling) ────────────────

  /**
   * Tall native window for Chat / search results. Growing happens at once (the island needs the room before it
   * opens). Releasing it must wait for the shrink animation: shrinking the window first cut the still-tall island
   * off with a flat edge (search results -> Mochi tab looked "cut" and only then got small).
   */
  /** Views that need the tall native window: Chat, Study, and the Apps tab while a conversation is open. */
  private wantsTall(mode: string, view: string): boolean {
    return mode === "expanded" && (view === "prompt" || view === "study" || (view === "apps" && appsChatActive()));
  }

  private chatPanel(tall: boolean) {
    this.chatPanelWanted = tall;
    if (this.chatPanelTimer != null) {
      window.clearTimeout(this.chatPanelTimer);
      this.chatPanelTimer = null;
    }
    if (tall) {
      void Bridge.setChatPanel(true);
      return;
    }
    // curveTowards() runs 340 ms; a little extra so the last frames are never clipped. It must stay BELOW the 420 ms
    // collapse timer (updateWindowCollapsed): releasing after the wake-strip resize would undo it.
    this.chatPanelTimer = window.setTimeout(() => {
      this.chatPanelTimer = null;
      if (!this.chatPanelWanted && !this.collapsed) void Bridge.setChatPanel(false);
    }, 380);
  }

  private updateWindowCollapsed() {
    if (this.collapseTimer != null) {
      window.clearTimeout(this.collapseTimer);
      this.collapseTimer = null;
    }
    if (State.mode === "hidden") {
      // Let the island finish retracting, then drop the window to the wake strip:
      // from there the OS delivers no cursor events, so nothing polls at all.
      this.collapseTimer = window.setTimeout(() => {
        this.collapseTimer = null;
        if (State.mode !== "hidden") return;
        this.collapsed = true;
        void Bridge.setCollapsed(true);
      }, 420);
    } else if (this.collapsed) {
      // Grow the window back before the island animates open.
      this.collapsed = false;
      void Bridge.setCollapsed(false);
    }
  }

  // ── Input ───────────────────────────────────────────────────────────────────

  private wireInput() {
    // The wake strip is the only thing the OS can hit while the island is hidden.
    // mouseenter + mousemove: if the cursor was already at the top when the
    // island retracted, mouseenter alone never fires again — mousemove still does.
    const wakeFromStrip = () => {
      Sound.resume();
      if (State.mode !== "hidden") return;
      // Only the legacy instant mode wakes from a plain hover on the strip.
      if (State.settings.wakeMode !== "instant") return;
      // Auto-hidden: only the dwell on the notch area brings it back (see wakeGesture).
      if (this.fsm.suppressed) return;
      // Expand the OS window immediately so the compact notch can paint.
      if (this.collapsed) {
        this.collapsed = false;
        void Bridge.setCollapsed(false);
      }
      this.fsm.mouseEntered();
    };
    // Right-click the open island: the tab wheel, at the cursor. Text fields keep their own right-click menu.
    this.islandEl.addEventListener("contextmenu", (e) => {
      if ((e.target as HTMLElement | null)?.closest("input, textarea, [contenteditable]")) return;
      e.preventDefault();
      if (State.mode === "expanded") this.toggleWheel(true);
    });
    this.wakeStrip.addEventListener("mouseenter", wakeFromStrip);
    this.wakeStrip.addEventListener("mousemove", wakeFromStrip);

    // The toast card inside the open panel: click opens the app it came from.
    this.toast.el.addEventListener("mousedown", (e) => {
      if (State.mode !== "expanded" || !this.toastOn) return;
      e.stopPropagation();
      this.clickToast();
    });

    this.islandEl.addEventListener("mousedown", () => {
      Sound.resume();
      State.lastActivity = performance.now();
      if (this.pulseOn && State.mode !== "expanded") {
        // The pulse is a shortcut to what was just added: a click opens that tab.
        const view = openableFor(this.pulseView)?.view;
        const url = this.pulseUrl;
        this.endPulse(true);
        if (url) { void Bridge.openUrl(url); return; }   // a found song: a click opens it in Spotify
        if (view && State.viewAllowed(view)) {
          trace("click", `'added' pulse clicked -> opens '${view}'`);
          this.wasInIsland = true;
          this.fsm.forceHome(`pulse ${view}`);
          this.setView(view);
          return;
        }
      }
      if (this.toastOn && State.mode !== "expanded") {
        trace("click", `notification toast clicked (${this.toastCurrent?.app ?? "?"}/${this.toastCurrent?.kind ?? "?"}) -> opens that app`);
        this.clickToast();
        return;
      }
      if (State.mode !== "expanded") {
        // The compact Spotify presentation is the same notch as Mochi. Clicking
        // it should open Lyra directly on Music; with no active track it keeps
        // the normal Home behaviour.
        const main = this.activities.main();
        // A plain island click expands the island on its overview. Only live
        // media and timers take the user straight to their dedicated page.
        this.pendingCompactClickView = main === "timer"
          ? "timer"
          : this.isSpotifyMediaActive()
            ? "music"
            : "overview";
        const fsmBefore = this.fsm.state;
        // The press itself proves the pointer is on the island. The native cursor feed can lag a moment
        // behind (the window has just taken focus), which made the panel look "left already" and start
        // its auto-close timer the instant it opened.
        this.wasInIsland = true;
        trace("click", `compact island clicked -> wants to open '${this.pendingCompactClickView}' ` +
          `(${this.pendingCompactClickView === "music" ? "a track is on the compact notch -> opens the Music card in its SMALL form" : this.pendingCompactClickView === "timer" ? "a timer owns the notch" : "nothing live on the notch -> Home overview"}; ` +
          `main activity=${main ?? "none"}, spotify=${this.isSpotifyMediaActive() ? this.spotifyState?.status : "off"}, fsm=${fsmBefore})`);
        this.fsm.click();
        if (this.fsm.state === fsmBefore) {
          trace("click", `!! the click was IGNORED: the state machine was '${fsmBefore}' (it only opens from 'petit'), so the island did NOT open`);
        }
        this.pendingCompactClickView = null;   // the transition above already used it; never leave a stale target behind
        return;
      }
    });

    // Rich search stays open until an explicit click dismisses it. Leaving the
    // island with the pointer must not collapse the result while the user reads.
    window.addEventListener("pointerdown", (e) => {
      if (!this.searchResultOpen || State.mode !== "expanded") return;
      const target = e.target as Node | null;
      if (target && (this.islandEl.contains(target) || this.tabDock.el.contains(target))) return; // the tab dock sits outside the island but is part of it
      this.dismissSearchResults();
    }, true);

    window.addEventListener("lyra:search-link-clicked", () => this.dismissSearchResults());

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && State.mode === "expanded" && !State.isPinned) this.collapse("Escape key");
      State.lastActivity = performance.now();
    });

    void onDragDrop((e) => this.onDragDrop(e));
    this.wireHtmlFileDrag();

    // Outside Tauri (plain browser) drive the cursor from DOM events so the
    // island can be inspected with `npm run dev`.
    if (!IS_TAURI) this.followPageCursor();
  }

  /**
   * Takes the cursor from the page's own mouse events instead of Rust's poll.
   * Used where the OS has no global cursor position (Wayland): the events only
   * fire while the pointer is over the island, so leaving the window is
   * reported as a cursor far away, which is what the poll would have said.
   */
  followPageCursor() {
    window.addEventListener("mousemove", (e) => this.onCursor(e.clientX, e.clientY));
    window.addEventListener("mouseout", (e) => {
      if (e.relatedTarget == null) this.onCursor(-10_000, -10_000);
    });
  }

  /** Cursor in window-logical coordinates. */
  onCursor(x: number, y: number) {
    State.mouse = { x, y };
    const rect = this.islandRect();
    State.mouseInIsland = { x: x - rect.x, y: y - rect.y };

    // Windows sends no cursor position with an OLE drag, so the drop sequence is
    // fed from the Win32 cursor poll instead — it runs throughout the drag.
    if (UploadSeq.isActive && !UploadSeq.dropped) {
      UploadSeq.updateCursor(State.mouseInIsland.x, State.mouseInIsland.y);
    }

    // While hidden, the window is the native wake strip. Rust keeps emitting the
    // cursor so we can recognise a deliberate "call Lyra" gesture instead of
    // waking on every hover (which stole clicks on tabs / window buttons).
    const margin = State.mode === "expanded" ? PANEL_MARGIN : HIT_MARGIN;
    let collapsedWake = false;
    if (this.collapsed && State.mode === "hidden") {
      collapsedWake = this.wakeGesture(x, y);
    } else {
      this.resetWakeGesture();
    }

    const inIsland = collapsedWake || (
      x >= rect.x - margin && x <= rect.x + rect.w + margin &&
      y >= rect.y - margin && y <= rect.y + rect.h + margin
    );

    if (collapsedWake && State.mode === "hidden") {
      this.fsm.peek(); // no-op unless auto-hide is on
      this.homeCollapseAt = null;
      this.collapsed = false;
      void Bridge.setCollapsed(false);
    }

    if (inIsland && !this.wasInIsland) {
      this.fsm.mouseEntered();
    }
    if (!inIsland && this.wasInIsland) {
      this.fsm.mouseLeft();
    }
    const hoverChanged = inIsland !== this.wasInIsland;
    this.wasInIsland = inIsland;
    if (State.mode === "compact") {
      if (hoverChanged || (inIsland && !this.pulseOn && !this.islandEl.classList.contains("hovering"))) this.syncHover(inIsland);
      if (inIsland) this.invite.track(State.mouseInIsland.x, State.mouseInIsland.y, rect.w, rect.h);
    } else if (hoverChanged || this.islandEl.classList.contains("hovering")) {
      this.islandEl.classList.remove("hovering");
    }

    // A bare cursor event changes nothing on screen: only run the frame loop when hover really changed,
    // or while the island is open / mid-drop.
    if (hoverChanged || UploadSeq.isActive) this.ensureRunning();
    else if (inIsland || State.mode === "expanded") {
      // Pointer moving inside the island: 100+ events/s used to restart the frame loop each time. 10/s is plenty.
      const now = performance.now();
      if (now - this.lastCursorKick >= 100) { this.lastCursorKick = now; this.ensureRunning(); }
    }
  }
  private lastCursorKick = 0;

  private resetWakeGesture() {
    this.wakeDwellStart = 0;
  }

  /**
   * Decides whether the pointer is "calling" the hidden island.
   * - instant: pointer anywhere in the top strip (legacy behaviour).
   * - dwell:   rest on the top edge for WAKE_DWELL_MS.
   * Only the centre of the top edge counts, so tabs and window buttons in the
   * corners never trigger it.
   */
  private wakeGesture(x: number, y: number): boolean {
    // Auto-hidden: the pointer has to rest on the notch area, whatever wake gesture is set.
    const mode = this.fsm.suppressed ? "dwell" : State.settings.wakeMode;
    const dwellMs = this.fsm.suppressed ? AUTOHIDE_DWELL_MS : WAKE_DWELL_MS;
    // The collapsed window spans the whole display, so the centre is its own midpoint.
    const cx = window.innerWidth / 2;
    const inCentre = Math.abs(x - cx) <= WAKE_HALF_W;

    if (mode === "instant") {
      return x >= -HIT_MARGIN && y >= -HIT_MARGIN && y <= 20 + HIT_MARGIN;
    }

    const onEdge = inCentre && y >= -HIT_MARGIN && y <= WAKE_EDGE_Y;
    const now = performance.now();

    // dwell (the only gesture besides "instant")
    if (!onEdge) { this.wakeDwellStart = 0; return false; }
    if (this.wakeDwellStart === 0) this.wakeDwellStart = now;
    return now - this.wakeDwellStart >= dwellMs;
  }






  // ── Frame loop ──────────────────────────────────────────────────────────────

  ensureRunning() {
    if (this.running) return;
    this.running = true;
    this.lastFrame = performance.now();
    requestAnimationFrame(this.frame);
  }

  private frame = (nowMs: number) => {
    const dt = Math.min(0.05, (nowMs - this.lastFrame) / 1000);
    this.lastFrame = nowMs;

    const wasAnimating = this.width.animating || this.height.animating || this.radius.animating;
    this.width.step(dt, nowMs);
    this.height.step(dt, nowMs);
    this.radius.step(dt, nowMs);
    // Geometry writes style and measures layout: only when it can have changed.
    if (wasAnimating || this.dirty || UploadSeq.isActive || State.mode === "expanded") this.applyGeometry();

    if (this.dirty) {
      this.dirty = false;
      this.syncDom();
    }



    this.views.get(State.view)?.tick?.(nowMs);
    if (UploadSeq.isActive) this.stepSequence();
    this.updateCountdown(nowMs);

    // Nothing is drawn while the island is hidden, so nothing may keep the loop
    // alive either. This used to read `... || this.engine.busy || State.mode !==
    // "hidden"`, and engine.busy is permanently true for any state with a
    // looping animation — breathing, ratelimit sweat, sleeping z's, the search
    // sweep — so a hidden island went on burning frames in exactly the states it
    // spends most of its life in. Geometry still has to finish retracting.
    const settling =
      this.width.animating || this.height.animating || this.radius.animating;
    const busy = State.mode === "hidden"
      ? settling
      : settling || UploadSeq.isActive;

    if (busy) {
      requestAnimationFrame(this.frame);
    } else {
      this.running = false;
      Sound.idle();
    }
  };





  private isSpotifyMediaActive(): boolean {
    if (!State.activityOn("music")) return false;
    const s = this.spotifyState;
    return !!s?.running && s.status !== "idle";
  }

  /** True when the download is only the small right-hand circle (another activity owns the notch). */
  private downloadIsSecondary(): boolean {
    return this.volumeHudOn && this.volumeHud.dataset.activityKind === "download" && this.volumeHud.dataset.downloadCompact === "1";
  }

  private isSpotifyCompactActive(): boolean {
    return State.mode === "compact" && this.isSpotifyMediaActive() && this.activities.main() !== "timer" && (!this.volumeHudOn || this.downloadIsSecondary()) && !this.appLaunchHudOn;
    // Not decided here: while Lyra listens / thinks / speaks, lyraStatus.ts draws over the notch and its CSS hides this
    // pill (#island.lyra-status), so the notch is never blank and never shows two things at once.
  }

  /** "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App" -> "WhatsApp"; plain names pass through. */
  private sourceDisplayName(src: string, title: string): string {
    const raw = (src || "").trim();
    if (!raw) return (title || "").trim();
    if (raw.includes("!") || /^[0-9A-Za-z]{6,}\.[A-Za-z]/.test(raw)) {
      let n = raw.split("!")[0].split("_")[0];
      if (n.includes(".")) n = n.slice(n.indexOf(".") + 1);
      n = n.replace(/Desktop$/i, "");
      if (n) return n;
    }
    return raw.replace(/\.exe$/i, "");
  }

  /** Icon for the app that is making sound when the media has no artwork (WhatsApp, a browser...). */
  private sourceIconFor(name: string): string {
    const key = (name || "").trim().toLowerCase();
    if (!key) return "";
    const hit = this.srcIcons.get(key);
    if (hit) return hit;
    if (performance.now() - this.srcIconAt > 5000) {
      this.srcIconAt = performance.now();
      void this.loadSourceIcons(name.trim());
    }
    // Until the real icon arrives (or if the app has none): a letter tile, never an empty gap.
    const letter = (name.trim().charAt(0) || "?").toUpperCase();
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40" rx="10" fill="#33363c"/><text x="20" y="27.5" font-family="Segoe UI, Arial, sans-serif" font-size="21" font-weight="700" text-anchor="middle" fill="#fff">${letter.replace(/[<>&]/g, "")}</text></svg>`;
    return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
  }

  private async loadSourceIcons(name: string) {
    const key = name.toLowerCase();
    const BASE = "http://127.0.0.1:8765";
    try {
      // 1) the installed app of that name (also finds Microsoft Store apps)
      const r1 = await fetch(`${BASE}/app/icon?name=${encodeURIComponent(name)}`);
      if (r1.ok) {
        const d1 = await r1.json();
        if (d1?.icon) {
          this.srcIcons.set(key, d1.icon);
          this.syncSpotifyCompact();
          return;
        }
      }
      // 2) an app that is making sound right now
      const res = await fetch(`${BASE}/volume/apps`);
      if (!res.ok) return;
      const data = await res.json();
      const apps: Array<{ name?: string; exe?: string; icon?: string | null }> = Array.isArray(data.apps) ? data.apps : [];
      const stem = (e: string) => e.toLowerCase().replace(/\.exe$/, "");
      const app = apps.find((a) => {
        if (!a.icon) return false;
        const n = (a.name || "").toLowerCase();
        const e = stem(a.exe || "");
        return (n && (n.includes(key) || key.includes(n))) || (e && (e.includes(key) || key.includes(e)));
      });
      if (app?.icon) {
        this.srcIcons.set(key, app.icon);
        this.syncSpotifyCompact();
      }
    } catch { /* backend not reachable: keep the letter tile */ }
  }

  private syncSpotifyCompact() {
    const s = this.spotifyState;
    const active = this.isSpotifyCompactActive();
    const downloadActive = this.volumeHudOn && this.volumeHud.dataset.activityKind === "download";
    this.spotifyCompact.classList.toggle("on", active);
    this.syncAmbientFx();
    this.spotifyCompact.classList.toggle("paused", active && s?.status === "paused");
    this.islandEl.classList.toggle("download-active", downloadActive);
    const spotifyOwnsRight = State.mode === "compact" && this.isSpotifyMediaActive();
    if (spotifyOwnsRight || this.timerChip.active() || this.volumeHudOn || this.appLaunchHudOn || this.lyraSpeaking || this.lyraListening) {
    }
    if (!s) return;
    const title = s.title || s.source || "Spotify";
    this.spotifyTitle.textContent = title;
    this.spotifyTitle.title = title;
    this.paintArtistLine();
    const artSrc = this.spotifyArtUrl || this.sourceIconFor(this.sourceDisplayName(s.source || "", title));
    if (artSrc) {
      if (this.spotifyArt.src !== artSrc) this.spotifyArt.src = artSrc;
      this.spotifyCompact.classList.add("has-art");
    } else {
      this.spotifyArt.removeAttribute("src");
      this.spotifyCompact.classList.remove("has-art");
    }
  }

  private currentLyric(): string | null {
    const s = this.spotifyState;
    if (!s || s.status === "idle" || !s.trackKey) return null;
    const pos = currentPosition(s, this.spotifyReceivedAt, performance.now());
    return this.lyrics.at(s.trackKey, pos)?.text ?? null;
  }

  private paintArtistLine() {
    const s = this.spotifyState;
    if (!s) return;
    const lyric = this.currentLyric();
    const text = lyric ?? (s.artist || s.album || "");
    if (text === this.artistShown) return;
    this.artistShown = text;
    const el = this.spotifyArtist;
    el.textContent = text;
    el.title = text;
    el.classList.toggle("lyric", lyric != null);
    if (lyric != null) {
      el.classList.remove("swap");
      void el.offsetWidth;
      el.classList.add("swap");
    }
  }

  private syncLyricTicker() {
    if (this.isSpotifyMediaActive() && isSpotifySource(this.spotifyState)) {   // only Spotify has lyrics to paint
      if (this.lyricTimer == null) {
        this.lyricTimer = window.setInterval(() => {
          if (this.isSpotifyCompactActive()) this.paintArtistLine();
        }, 250);
      }
    } else if (this.lyricTimer != null) {
      window.clearInterval(this.lyricTimer);
      this.lyricTimer = null;
    }
  }

  private updateCountdown(nowMs: number) {
    if (State.mode !== "expanded" || State.isPinned || State.lyricsOpen || this.homeCollapseAt == null) {
      this.countdown.style.width = "0px";
      return;
    }
    const autoClose = State.settings.autoCloseInterval;
    const windowS = Math.min(10, autoClose * 0.6);
    const remaining = (this.homeCollapseAt - nowMs) / 1000;
    this.countdown.style.width =
      remaining < windowS ? `${Math.max(0, clamp(remaining / windowS, 0, 1) * 160)}px` : "0px";
  }

  // ── DOM sync ────────────────────────────────────────────────────────────────

  private syncDom() {
    const expanded = State.mode === "expanded";

    this.contentEl.style.opacity = expanded ? "1" : "0";
    this.contentEl.style.pointerEvents = expanded ? "auto" : "none";
    this.islandEl.classList.toggle("recognition-open", expanded && State.view === "recognition");
    this.islandEl.dataset.theme = State.theme;
    this.islandEl.dataset.compact = State.extras.compactStyle;
    this.slots.left.el.dataset.theme = State.theme;
    this.slots.right.el.dataset.theme = State.theme;
    // The resting chat pill styling must never outlive the Chat tab.
    if (State.view !== "prompt" || State.mode !== "expanded") this.islandEl.classList.remove("chat-small");
    this.tabDock.el.dataset.theme = State.theme;
    this.slots.left.el.dataset.compact = State.extras.compactStyle;
    this.slots.right.el.dataset.compact = State.extras.compactStyle;
    this.islandEl.dataset.position = State.islandPosition;
    this.islandEl.dataset.mode = State.mode;
    this.islandEl.dataset.view = State.view;
    this.islandEl.classList.toggle("search-results-open", this.searchResultOpen && expanded && State.view === "prompt");

    const viewChanged = this.lastSyncedView != null && this.lastSyncedView !== State.view;
    const previousView = this.lastSyncedView;
    const transitionSeq = ++this.viewTransitionSeq;

    this.tabDock.sync();

    // Cross-fade/slide the actual view content instead of hard-swapping it.
    // The island geometry is already spring-driven; this adds a small Apple-like
    // content transition on top without changing any view's layout or lifecycle.
    if (viewChanged && expanded && previousView) {
      // Clear any stale transition classes first. This matters when the user
      // changes tabs quickly: an old Music view must never get a second frame
      // to paint over the destination tab.
      for (const view of this.views.values()) {
        view.el.classList.remove("is-entering", "is-leaving");
      }
      const outgoing = this.views.get(previousView)?.el;
      const incoming = this.views.get(State.view)?.el;
      // Direction-aware motion: moving right along the dock slides content in from the right.
      const ORDER = ["timer", "overview", "music", "prompt", "controls", "wallpaper", "stats", "volume", "recognition", "camera", "clipboard", "weather", "calendar", "todo", "study", "translate", "record"];
      const a = ORDER.indexOf(previousView), b = ORDER.indexOf(State.view);
      this.islandEl.dataset.nav = a >= 0 && b >= 0 && b < a ? "back" : "fwd";
      outgoing?.classList.add("is-leaving");
      incoming?.classList.add("is-entering");
      this.tabDock.el.classList.add("switching");

      requestAnimationFrame(() => {
        if (transitionSeq !== this.viewTransitionSeq) return;
        incoming?.classList.remove("is-entering");
      });

      if (this.viewTransitionTimer != null) window.clearTimeout(this.viewTransitionTimer);
      this.viewTransitionTimer = window.setTimeout(() => {
        if (transitionSeq !== this.viewTransitionSeq) return;
        outgoing?.classList.remove("is-leaving");
        this.tabDock.el.classList.remove("switching");
        this.viewTransitionTimer = null;
      }, 300);
    } else if (!expanded) {
      for (const view of this.views.values()) view.el.classList.remove("is-entering", "is-leaving");
      this.tabDock.el.classList.remove("switching");
    }

    for (const [name, view] of this.views) {
      const on = name === State.view;
      view.el.classList.toggle("on", on);
      if (on) view.sync();
      // "On screen" = selected and the island open (it keeps State.view while collapsed).
      const active = on && expanded;
      if (view.setActive && active !== this.activeViews.has(name)) {
        if (active) this.activeViews.add(name);
        else this.activeViews.delete(name);
        view.setActive(active);
      }
    }

    // The chat is the only view with a text field, so it is the only time the
    // island is allowed to take keyboard focus.
    if (this.lastSyncedView !== State.view) {
      const wasChat = this.lastSyncedView === "prompt";
      this.lastSyncedView = State.view;
      if (State.view === "prompt") {
        // The island's Chat tab has the text field: give it the keyboard.
        void Bridge.focusWindow(true);
        window.setTimeout(() => this.views.get("prompt")?.focus?.(), 120);
      } else if (wasChat) {
        void Bridge.focusWindow(false);
      }
    }

    // Compact mini grid: keep the right-side mini bots out of the way whenever

    this.refreshActivities();
    this.syncSpotifyCompact();
    this.syncTimerChip();
    this.paintSecondary();
  }

  // ── Timers ──────────────────────────────────────────────────────────────────



  /** Live timer state from the backend. The chip owns its own 250ms countdown. */
  /** The backend changed the to-do list (usually by voice); `open` also shows the To-do view. */
  onTodos(open: boolean) {
    window.dispatchEvent(new CustomEvent("lyra:todos-changed"));
    if (!open || State.paused) return;
    this.fsm.forceHome("to-do requested");
    this.setView("todo");
  }

  onStudy(open: boolean) {
    if (!open) return;
    this.fsm.forceHome("study requested");
    this.setView("study");
    this.reveal();
  }

  /** Game mode: a game is running, so Lyra's keyboard shortcuts are removed. The notch announces it (and how to wake her)
   *  with the pulse, then a small glowing bar under it stays as the reminder. */
  onGameMode(on: boolean, wake: string) {
    const changed = on !== this.gameOn;
    this.gameOn = on;
    this.modeLight.classList.toggle("on", on);
    if (!changed || State.paused) return;
    // Open island: the person is already looking at it, the light is enough.
    if (State.mode === "expanded") return;
    this.playPulse(describeMode(on, wake), "", Island.PULSE_MS + 400, "ok");
  }

  /** The backend changed the scheduled actions / routines; `open` also shows them (Calendar view). */
  onSchedule(open: boolean) {
    window.dispatchEvent(new CustomEvent("lyra:schedule-changed"));
    if (!open || State.paused) return;
    this.fsm.forceHome("schedule requested");
    this.setView("calendar");
  }

  /** Quick controls changed by voice ("turn off bluetooth"); `open` also shows the Controls tab. */
  onControls(open: boolean) {
    if (!open || State.paused) return;
    this.fsm.forceHome("controls requested");
    this.setView("controls");
  }

  /** "Open the wallpapers" by voice (or the wallpaper changed); `open` also shows the Wallpaper tab. */
  onWallpaper(open: boolean) {
    if (!open || State.paused) return;
    this.fsm.forceHome("wallpaper requested");
    this.setView("wallpaper");
  }

  // ── Tab wheel ───────────────────────────────────────────────────────────────
  // The wheel is its own transparent window, shown by Rust at the cursor or in the middle of the
  // screen, so it does not need the island to be open. The island only asks for it and then goes
  // to the tab that was picked.

  /** Shows the wheel. `hold` = opened by the held shortcut: letting go opens the tab you point at. */
  openWheel(atCursor: boolean, hold = false) {
    if (State.paused || this.wheelUp) return;
    if (State.extras.navStyle === "scroller") {
      trace("wheel", "tab wheel ignored: switched off while the scroller is the navigation (Settings > Island)");
      return;
    }
    this.wheelUp = true;
    trace("wheel", `tab wheel opened (${hold ? "keyboard shortcut" : atCursor ? "right-click" : "by voice"})`);
    tourSignal("wheel-open", hold ? "shortcut" : atCursor ? "rightclick" : "voice");
    void Bridge.wheelOpen(atCursor, {
      views: DESTINATIONS.filter((d) => State.viewAllowed(d.view)).map((d) => d.view),
      current: State.view === "empty" ? "overview" : State.view,
      hold,
      theme: State.theme,
    });
  }

  closeWheel() {
    if (!this.wheelUp) return;
    this.wheelUp = false;
    void Bridge.wheelClose();
  }

  toggleWheel(atCursor: boolean) {
    if (this.wheelUp) this.closeWheel();
    else this.openWheel(atCursor);
  }

  /** The wheel window chose a tab (Rust has already hidden it). */
  onWheelPick(view: string) {
    this.wheelUp = false;
    if (view === "settings") {            // not an island tab: the wheel's Settings icon opens the Settings window
      trace("wheel", "wheel pick: 'settings' -> opening the Settings window");
      Sound.play("blip");
      void Bridge.openSettingsWindow();
      return;
    }
    trace("wheel", `wheel pick: '${view}'${destinationFor(view) && State.viewAllowed(destinationFor(view)!.view) ? "" : "  (ignored: unknown or switched-off tab)"}`);
    const dest = destinationFor(view);
    if (!dest || !State.viewAllowed(dest.view)) return;
    Sound.play("blip");
    tourSignal("wheel-pick", dest.view);
    this.fsm.forceHome(`wheel ${dest.view}`);
    this.setView(dest.view);
  }

  /** The wheel window went away without a choice (Esc, a click outside it). */
  onWheelClosed() {
    this.wheelUp = false;
    trace("wheel", "wheel closed without a choice");
  }

  /** Lyra (voice or a backend feature) asks for a tab. */
  onOpenView(view: string) {
    const dest = openableFor(view);        // a tab, or a view that is not a tab any more (translate, volume, controls, study)
    trace("island", `Lyra (voice/backend) asked to open '${view}'${!dest ? " -> unknown tab, ignored" : State.paused ? " -> ignored, Lyra is paused" : !State.viewAllowed(dest.view) ? " -> ignored, tab switched off in Settings" : ""}`);
    if (!dest || State.paused || !State.viewAllowed(dest.view)) return;
    tourSignal("open-view", dest.view);
    this.fsm.forceHome(`open ${dest.view}`);
    this.setView(dest.view);
  }

  /** Lyra added something by voice (to-do, reminder, alarm, timer, event) - or could not: show that tab and
   *  play the matching animation in it (success = icon acts out the action; failure = red pill + shake). */
  onAdded(view: string, text: string, kind = "", status: "ok" | "fail" = "ok") {
    const dest = openableFor(view);
    trace("island", `Lyra ${status === "fail" ? "could not add" : "added"} something -> '${view}' (${kind || "default"})${!dest ? " (unknown tab, ignored)" : State.paused ? " (ignored, Lyra is paused)" : !State.viewAllowed(dest.view) ? " (ignored, tab switched off in Settings)" : ""}`);
    if (!dest || State.paused || !State.viewAllowed(dest.view)) return;
    // The island is closed (compact / hidden): do not throw a whole tab at the person, let the notch itself
    // react (colour flood, icon, sparks, rings). A click on it opens the tab.
    if (State.mode !== "expanded") {
      this.showPulse(dest.view, text, kind, status);
      return;
    }
    this.fsm.forceHome(`added ${dest.view}`);
    this.setView(dest.view);
    const host = this.views.get(dest.view);
    window.setTimeout(() => {
      fxAdded(host?.el, text, dest.view, { kind: asFxKind(kind), status, specific: true });   // after the tab has grown
      Sound.play(status === "fail" ? "error" : "pop");
    }, 300);
  }

  // ── "Added" pulse on the compact notch ─────────────────────────────────────

  private static readonly PULSE_MS = 3200;
  /** A found song stays up longer: long enough to read it and reach for the Spotify button. */
  private static readonly SONG_MS = 11000;
  private static readonly PLAY_MS = 5500;   // "Playing <song>" banner after a Spotify card was tapped

  private showPulse(view: string, text: string, kind: string, status: "ok" | "fail", retries = 0) {
    // The volume HUD owns the notch for a moment: wait for it (a couple of tries at most).
    if (this.volumeHudOn && retries < 4) {
      window.setTimeout(() => this.showPulse(view, text, kind, status, retries + 1), 1000);
      return;
    }
    this.playPulse(describePulse(view, kind, status, text), view, status === "fail" ? Island.PULSE_MS + 700 : Island.PULSE_MS, status);
  }

  private playPulse(info: PulseInfo, view: string, ms: number, status: "ok" | "fail", retries = 0) {
    if (this.volumeHudOn && retries < 4) {
      window.setTimeout(() => this.playPulse(info, view, ms, status, retries + 1), 1000);
      return;
    }
    const key = `${view}|${info.kind}|${info.kind === "song" ? info.title : ""}`;
    const banner = State.mode === "expanded";
    trace("island", `'added' pulse: ${info.kind} on the compact notch (island ${State.mode})`);

    // Same kind again while it is up: bump the counter and flash again instead of stacking.
    if (this.pulseOn && this.pulseKey === key) {
      this.pulseCount++;
      this.pulse.bump(info, ms, this.pulseCount);
      this.armPulseTimer(ms);
      if (info.kind !== "song") Sound.play(status === "fail" ? "error" : "pop");
      return;
    }

    if (this.pulseOffTimer != null) { window.clearTimeout(this.pulseOffTimer); this.pulseOffTimer = null; }
    this.suspendToast();                                   // a notification waits its turn, it comes back after
    if (!banner && State.mode === "hidden") {
      this.pulseRevealed = !this.fsm.keepCompact;
      this.fsm.reveal();
    }
    this.pulseOn = true;
    this.pulseView = view;
    this.pulseKey = key;
    this.pulseCompact = !!info.compact;
    this.pulseUrl = info.url || "";
    this.pulseBanner = banner;
    this.pulseCount = 1;
    const el = this.islandEl;
    this.setPulseColor(info.rgb);
    this.pulse.el.classList.toggle("banner", banner);
    el.classList.remove("pulse-off", "hovering");
    el.classList.toggle("pulse-fail", status === "fail" && !banner);
    // Restart the island's own glow animation even when a previous pulse is still showing.
    el.classList.remove("pulse-on");
    if (!banner) { void el.offsetWidth; el.classList.add("pulse-on"); }
    this.pulse.play(info, ms, 1);
    if (info.kind === "song" && info.art) {
      // The whole notch takes the colour of the album cover as soon as it can be read.
      void artColor(info.art).then((rgb) => { if (rgb && this.pulseOn && this.pulseKey === key) this.setPulseColor(rgb); });
    }
    if (info.kind !== "song") Sound.play(status === "fail" ? "error" : "pop");   // a song brings its own sound (recognition "found" / music)
    this.armPulseTimer(ms);
    if (!banner) this.animateGeometry(false);              // springs wide: the overshoot is the "bounce"
    this.dirty = true;
    this.ensureRunning();
  }

  private setPulseColor(rgb: string) {
    this.islandEl.style.setProperty("--pl-rgb", rgb);
    this.islandEl.querySelector<HTMLElement>("#island-aura")?.style.setProperty("--pl-rgb", rgb);
    this.pulse.recolor(rgb);
  }

  private armPulseTimer(ms: number) {
    if (this.pulseTimer != null) window.clearTimeout(this.pulseTimer);
    this.pulseTimer = window.setTimeout(() => {
      this.pulseTimer = null;
      // Do not pull it away from under the cursor: the person may be about to click it.
      if (this.wasInIsland && !this.pulseBanner && State.mode !== "expanded") this.armPulseTimer(1200);
      else {
        const wasBanner = this.pulseBanner;
        this.endPulse(false);
        // The banner covered the Recognition tab: when it is done, close the island instead of uncovering the tab again.
        if (wasBanner && State.mode === "expanded" && State.view === "recognition") this.collapse("song pulse done");
      }
    }, ms);
  }

  /** `now`: the island is opening (or the pulse was clicked), so no slow fade-out. */
  private endPulse(now: boolean) {
    if (!this.pulseOn) return;
    if (this.pulseTimer != null) window.clearTimeout(this.pulseTimer);
    this.pulseTimer = null;
    this.pulseOn = false;
    this.pulseKey = "";
    this.pulseCount = 0;
    this.pulse.stop();
    const el = this.islandEl;
    const wasBanner = this.pulseBanner;
    this.pulseBanner = false;
    el.classList.remove("pulse-on", "pulse-fail");
    if (!now && !wasBanner) {
      el.classList.add("pulse-off");                       // the glow fades out
      this.pulseOffTimer = window.setTimeout(() => { this.pulseOffTimer = null; el.classList.remove("pulse-off"); }, 520);
    } else {
      el.classList.remove("pulse-off");
    }
    if (State.mode === "compact") {
      if (this.wasInIsland) el.classList.add("hovering");  // the pointer is still on it: back to "open me?"
      this.animateGeometry(!this.wasInIsland);
    }
    this.dirty = true;
    this.ensureRunning();
    if (this.toastQueue.length) window.setTimeout(() => this.pumpToast(), 320);
    if (this.pulseRevealed && !now) {
      this.pulseRevealed = false;
      if (!this.wasInIsland && State.mode === "compact" && !this.toastQueue.length) this.fsm.forceHidden();
    } else if (now) {
      this.pulseRevealed = false;
    }
  }

  /** The pointer arrived on / left the compact notch: swell and invite, or settle back. */
  private syncHover(on: boolean) {
    this.islandEl.classList.toggle("hovering", on && !this.pulseOn);
    if (on && !this.pulseOn && !this.toastOn) {
      const t = performance.now();
      if (t - this.lastHoverSound > 900) { this.lastHoverSound = t; Sound.play("hover"); }
    }
    if (!this.pulseOn) this.animateGeometry(!on);
  }

  /** The global shortcut (hold Ctrl+CapsLock) and the voice command, from the backend. */
  onWheel(action: string) {
    switch (action) {
      case "open":                       // shortcut pressed: the wheel appears at the cursor; a second press closes it
        if (this.wheelUp) this.closeWheel();
        else this.openWheel(true, true);
        break;
      case "stay": if (this.wheelUp) void Bridge.wheelCmd("stay"); break;       // it was only tapped: stay up
      case "commit": if (this.wheelUp) void Bridge.wheelCmd("commit"); break;   // it was held and let go
      case "show": this.openWheel(false); break;                                // by voice: middle of the screen
      case "close": this.closeWheel(); break;
    }
  }

  /** "Show my stats" by voice: open the Stats tab. */
  onStats(open: boolean) {
    if (!open || State.paused) return;
    this.fsm.forceHome("stats requested");
    this.setView("stats");
  }

  onTimers(p: TimersPayload) {
    if (!State.activityOn("timer")) p = { ...p, items: [] };
    setTimerItems(p);
    // A countdown that just ran out (not one the user cancelled): a small fanfare and a burst of light.
    const nowMs = Date.now();
    const seen = new Map<string, { kind: string; state: string; end: number | null }>();
    for (const it of p.items) seen.set(String(it.id), { kind: it.kind, state: it.state, end: it.end_ms ?? null });
    let finished = false;
    for (const [id, prev] of this.timerSeen) {
      const cur = seen.get(id);
      if (prev.kind !== "stopwatch" && prev.state === "running" && prev.end != null && Math.abs(nowMs - prev.end) < 4000 && (!cur || cur.state !== "running")) finished = true;
    }
    this.timerSeen = seen;
    if (finished && State.activityOn("timer") && !State.paused) {
      Sound.activity("timerDone");
      if (State.mode !== "expanded") this.fx.show("success", { color: "#ff9f0a", originX: 26, act: "timer" });
    }
    const had = this.timerChip.active();
    this.timerChip.setItems(p);
    const has = this.timerChip.active();
    this.tabDock.sync();

    if (has && this.timerTicker == null) {
      this.timerTicker = window.setInterval(() => { this.timerChip.tick(); this.paintSecondary(); this.timerCountdownTick(); this.syncAmbientFx(); }, 250);
    } else if (!has && this.timerTicker != null) {
      window.clearInterval(this.timerTicker);
      this.timerTicker = null;
    }

    if (has && !had) {
      // A timer just started: open Lyra on the Timer experience, unless the user
      // is busy in another expanded view.
      const busy = State.mode === "expanded" && State.view !== "overview" && State.view !== "timer";
      if (!busy) {
        this.fsm.forceHome("timer started");
        this.setView("timer");
      }
    }

    if (has !== had) {
      this.fsm.keepCompact = this.keepNotch();
      if (has && this.fsm.state === "hidden") {
        this.fsm.forcePetit();
      } else if (!has && !this.keepNotch() && State.mode === "compact" && !this.wasInIsland) {
        if (this.toastOn || this.toastQueue.length) this.toastRevealed = true;
        else this.fsm.forceHidden();
      }
    }
    this.dirty = true;
    this.ensureRunning();
  }

  /** Calendar reminders that just came due surface as a notification. */
  private checkReminders() {
    for (const it of dueNow()) {
      this.lastAlarmAt = Date.now();
      Sound.alarm();
      this.onNotify({
        app: "reminder", kind: "reminder", sender: it.title,
        text: `${it.time}${it.repeat === "daily" ? " · every day" : ""}`, ts: Date.now(), durationMs: 8000,
      });
    }
  }

  // ── Activities & secondary islands ──────────────────────────────────────────

  /** Feeds the ActivityManager the current state of every activity. */
  private refreshActivities() {
    this.activities.set("music", this.isSpotifyMediaActive());
    this.activities.set("timer", this.timerChip.active());
    this.activities.set("record", this.recordingSince !== null);
  }

  /** The backend says a screen recording started / stopped (core: ui_bridge "record" event, or the boot check). */
  onRecord(recording: boolean, startedAt?: number, pausedMs?: number | null) {
    this.screenSince = recording ? (startedAt && startedAt > 0 ? startedAt : Date.now()) : null;
    this.screenPausedMs = recording && pausedMs != null ? pausedMs : null;
    this.applyRecording();
  }

  /** Recorded time shown on the red pill, in ms: frozen while only the screen recording is paused. */
  private recElapsedMs(): number {
    if (this.screenPausedMs !== null && this.callSince === null) return this.screenPausedMs;
    return Date.now() - (this.recordingSince as number);
  }

  private recPaused(): boolean {
    return this.screenPausedMs !== null && this.callSince === null;
  }

  /** The Notetaker started / stopped recording a call (found by polling /p7/notetaker/status). */
  onCallRecord(recording: boolean, startedAt?: number) {
    this.callSince = recording ? (startedAt && startedAt > 0 ? startedAt : Date.now()) : null;
    this.applyRecording();
  }

  private applyRecording() {
    const was = this.recordingSince !== null;
    const starts = [this.screenSince, this.callSince].filter((v): v is number => v !== null);
    this.recordingSince = starts.length ? Math.min(...starts) : null;
    const recording = this.recordingSince !== null;
    const ticking = Island.LIVE_REC_CLOCK && recording && !this.recPaused();       // a paused clock does not move: no timer, no repaints
    if (ticking && this.recordTicker == null) {
      this.recordTicker = window.setInterval(() => this.paintSecondary(), 1000);   // keeps the clock moving (a seconds clock needs no more)
    } else if (!ticking && this.recordTicker != null) {
      window.clearInterval(this.recordTicker);
      this.recordTicker = null;
    }
    if (was !== recording) {
      this.fsm.keepCompact = this.keepNotch();
      this.dirty = true;
      this.ensureRunning();
    }
    this.paintSecondary();
  }

  /** Matches the red pill to the backend's real state: at boot, then at most every 30 s, so a missed "stopped" event
   *  (ffmpeg crashed …) can never leave it stuck on, and a Notetaker call recording shows up too. */
  private syncRecordingFromBackend(): Promise<unknown> {
    const get = (path: string) => fetch(`http://127.0.0.1:8765${path}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    const screen = get("/record/status").then((d: { recording?: boolean; paused?: boolean; seconds?: number } | null) => {
      if (!d) return;                                         // backend not reachable: leave things as they are
      const rec = !!d.recording;
      const paused = rec && !!d.paused;
      if (rec !== (this.screenSince !== null) || paused !== (this.screenPausedMs !== null)) {
        this.onRecord(rec, rec ? Date.now() - (d.seconds ?? 0) * 1000 : 0, paused ? (d.seconds ?? 0) * 1000 : null);
      }
    });
    const call = get("/p7/notetaker/status").then((d: { recorder?: { recording?: boolean; elapsed?: number } } | null) => {
      if (!d || !d.recorder) return;
      const rec = !!d.recorder.recording;
      if (rec !== (this.callSince !== null)) this.onCallRecord(rec, rec ? Date.now() - (d.recorder.elapsed ?? 0) * 1000 : 0);
    });
    return Promise.all([screen, call]);
  }

  /** Tracks a download for its floating island. Returns true when it is handled there. */
  private takeDownloadAsIsland(s: Parameters<Island["onActivity"]>[0]): boolean {
    if (s.phase === "fail") { this.endDownloadIsland(); Sound.activity("downloadFail"); return true; }
    if (s.phase === "success" && !this.dlSecDoneSound) { this.dlSecDoneSound = true; Sound.activity("downloadDone"); window.setTimeout(() => { this.dlSecDoneSound = false; }, 3500); }
    const pct = Number.isFinite(s.progress) ? Math.max(0, Math.min(100, Number(s.progress))) : null;
    this.dlSec = { phase: s.phase, pct: s.phase === "success" ? 100 : pct };
    this.dlLast = s;
    this.activities.set("download", true);
    if (this.dlSecTimer != null) window.clearTimeout(this.dlSecTimer);
    // Progress reports keep it alive; if they stop (or it just finished) the island leaves.
    this.dlSecTimer = window.setTimeout(() => this.endDownloadIsland(), s.phase === "success" ? 2600 : 6500);
    this.paintSecondary();
    return true;
  }

  private endDownloadIsland() {
    if (this.dlSecTimer != null) { window.clearTimeout(this.dlSecTimer); this.dlSecTimer = null; }
    this.dlLast = null;
    this.activities.set("download", false);
    this.paintSecondary();
  }

  /** Draws the secondary island for the activity that is not main. */
  private paintSecondary() {
    this.refreshActivities();
    const lay = this.activities.layout();
    // The thing that owned the notch is gone but the download goes on: let it take over as the main HUD.
    if (!lay.main && this.activities.isActive("download") && this.dlLast) {
      const last = { ...this.dlLast, ts: Date.now() };
      this.endDownloadIsland();
      this.onActivity(last);
      return;
    }
    // While the download owns the notch (after a click) the other activity may still sit beside it.
    const dlIsMain = this.dlMain && this.volumeHudOn && this.volumeHud.dataset.activityKind === "download";
    const busy = this.appLaunchHudOn || this.lyraSpeaking || this.lyraListening || this.lyraStatusKind !== null || (this.volumeHudOn && !dlIsMain);
    const canShow = State.mode === "compact" && !busy && !this.promoting;
    // One island per side (the layout hands out at most two). A recording that has no island of its own
    // rides along inside the first one (timer | red dot + clock).
    const recHasOwn = lay.secondary.some((x) => x.id === "record");
    const carrier = lay.secondary[0]?.side ?? null;
    for (const side of ["left", "right"] as const) {
      const sec = lay.secondary.find((x) => x.side === side) ?? null;
      this.paintSlot(this.slots[side], sec, canShow && !!sec, !recHasOwn && this.recordingSince !== null && carrier === side);
    }
  }

  /** Draws one small island (left or right of the main one). */
  private paintSlot(slot: SecSlot, sec: { id: string; side: "left" | "right" } | null, show: boolean, withRec: boolean) {
    const el = slot.el;
    if (show && sec) {
      let key = "";
      if (sec.id === "music") {
        const s = this.spotifyState;
        key = `music|${this.spotifyArtUrl}|${s?.status}|${s?.title ?? ""}`;
        if (key !== slot.key.replace(/\|rec\d+$/, "") || slot.id !== "music") {
          el.replaceChildren(
            h("img", { class: "sec-art", src: this.spotifyArtUrl || "", alt: "" }),
            h("span", { class: "sec-eq" }, h("i"), h("i"), h("i"), h("i")),
          );
        }
        el.classList.toggle("paused", s?.status === "paused");
      } else if (sec.id === "download") {
        const d = this.dlSec;
        const p = d.pct == null ? null : Math.round(d.pct);
        key = `dl|${d.phase}|${p ?? "x"}`;
        if (slot.id !== "download" || !el.querySelector(".sec-ring")) {
          el.replaceChildren(h("i", { class: "sec-ring" }), h("span", { class: "sec-text sec-dl-text" }));
        }
        const ring = el.querySelector<HTMLElement>(".sec-ring");
        const txt = el.querySelector(".sec-dl-text");
        if (ring) {
          ring.style.setProperty("--p", String(p ?? 28));
          ring.classList.toggle("indet", p == null && d.phase !== "success");
          ring.classList.toggle("done", d.phase === "success");
        }
        if (txt) txt.textContent = d.phase === "success" ? "Done" : p == null ? "" : `${p}%`;
        el.classList.remove("paused");
      } else if (sec.id === "record" && this.recordingSince !== null) {
        const secs = Math.max(0, Math.floor(this.recElapsedMs() / 1000));
        const text = Island.LIVE_REC_CLOCK ? Island.fmtClock(secs) : (this.recPaused() ? "Paused" : "REC");
        key = `rec|${text.length}|${this.recPaused() ? "p" : "r"}`;
        if (slot.id !== "record" || !el.querySelector(".sec-text")) {
          el.replaceChildren(h("i", { class: "sec-dot sec-rec" }), h("span", { class: "sec-text" }));
        }
        el.style.setProperty("--sec-accent", "#ff453a");
        const t = el.querySelector(".sec-text");
        if (t && t.textContent !== text) t.textContent = text;
        el.classList.toggle("paused", this.recPaused());
      } else if (sec.id === "timer") {
        const it = pickPrimary(getTimers().items, Date.now());
        if (it) {
          const text = clockText(it, Date.now());
          key = `timer|${it.id}|${it.state}|${text.length}`;
          if (slot.id !== "timer" || !el.querySelector(".sec-text")) {
            el.replaceChildren(h("i", { class: "sec-dot" }), h("span", { class: "sec-text" }));
          }
          el.style.setProperty("--sec-accent", colorFor(it));
          const t = el.querySelector(".sec-text");
          if (t && t.textContent !== text) t.textContent = text;
          el.classList.toggle("paused", it.state === "paused");
        }
      }
      let chip = el.querySelector<HTMLElement>(".sec-extra");
      if (withRec) {
        const rs = Math.max(0, Math.floor(this.recElapsedMs() / 1000));
        const rt = Island.LIVE_REC_CLOCK ? Island.fmtClock(rs) : (this.recPaused() ? "Paused" : "REC");
        if (!chip) {
          chip = h("span", { class: "sec-extra", title: "Recording" }, h("i", { class: "sec-dot sec-rec" }), h("span", { class: "sec-text" }));
          el.append(chip);
        }
        const rtEl = chip.querySelector(".sec-text");
        if (rtEl && rtEl.textContent !== rt) rtEl.textContent = rt;
        key += `|rec${rt.length}`;
      } else if (chip) chip.remove();
      if (key !== slot.key || slot.id !== sec.id) {
        slot.key = key;
        slot.id = sec.id;
        el.dataset.activity = sec.id;
        slot.w = el.offsetWidth; // content-aware width, measured once per content change
        this.dirty = true;
        this.ensureRunning();
      }
    }
    if (show !== slot.shown) {
      slot.shown = show;
      el.classList.toggle("show", show);
      this.dirty = true;
      this.ensureRunning();
    }
  }

  /** Small island rect in window coordinates (also its click-through hit shape). */
  private secondaryRect(slot: SecSlot, side: "left" | "right", islandX: number, islandW: number, islandTop: number, islandH: number) {
    const h2 = 34;
    const gap = 10;
    const w = Math.max(h2, slot.w || h2);
    const x = side === "right" ? islandX + islandW + gap : islandX - gap - w;
    return { x, y: islandTop + (islandH - h2) / 2, w, h: h2 };
  }

  /** Click on a small island: it becomes main, the old main shrinks to a small island. */
  private promoteSecondary(side: "left" | "right") {
    const slot = this.slots[side];
    const id = slot.id;
    if (!id || this.promoting) return;
    this.promoting = true;
    const el = slot.el;
    el.classList.add("morph-out");              // travels toward the main island
    this.islandEl.classList.add("activity-swap"); // main content yields
    window.setTimeout(() => {
      // try/finally: if anything in here throws, the flags and classes must still be released,
      // otherwise the small islands stay hidden and the notch looks frozen.
      try {
        if (id === "download") {
          // The download takes the notch; whatever owned it (Spotify, timer) steps aside as the small island.
          if (this.dlSecTimer != null) { window.clearTimeout(this.dlSecTimer); this.dlSecTimer = null; }
          const last = this.dlLast;
          this.activities.promote("download");
          this.dlMain = true;
          if (last) this.onActivity({ ...last, ts: Date.now() });
        } else {
          const dlWasMain = this.dlMain && this.volumeHudOn && this.volumeHud.dataset.activityKind === "download";
          this.activities.promote(id);
          if (dlWasMain) {
            // Clicking the small island while the download owns the notch swaps them back.
            const last = this.dlLast;
            this.dlMain = false;
            this.endVolumeHud();
            if (last) this.takeDownloadAsIsland(last);
          }
        }
        this.promoting = false;
        this.refreshActivities();
        this.syncSpotifyCompact();
        this.syncTimerChip();
        // The demoted activity enters on its own side, growing out of the main island.
        el.classList.remove("show");
        el.classList.add("enter");
        el.classList.remove("morph-out");
        slot.shown = false;
        slot.key = "";
        this.paintSecondary();
        void el.offsetWidth;
        el.classList.remove("enter");
      } finally {
        this.promoting = false;
        el.classList.remove("morph-out", "enter");
        this.islandEl.classList.remove("activity-swap");
        this.dirty = true;
        this.ensureRunning();
      }
    }, 200);
  }

  /** The last three seconds of a countdown tick, one soft tick per second. */
  private timerCountdownTick() {
    if (State.mode !== "compact" || !this.timerChip.el.classList.contains("show")) { this.timerTickSec = -1; return; }
    const it = pickPrimary(getTimers().items, Date.now());
    if (!it || it.kind === "stopwatch" || it.state !== "running") { this.timerTickSec = -1; return; }
    const left = valueMs(it, Date.now());
    if (left <= 0 || left > 3000) { this.timerTickSec = -1; return; }
    const sec = Math.ceil(left / 1000);
    if (sec !== this.timerTickSec) { this.timerTickSec = sec; Sound.activity("timerTick"); }
  }

  /** The two kinds of light that stay on all the time (a running timer, music): quiet, never in the way of a notification. */
  private syncAmbientFx() {
    const k = this.fx.kind();
    if (k !== null && k !== "timer" && k !== "music") return;
    const timerOn = this.timerChip.el.classList.contains("show");
    const musicOn = !timerOn && this.isSpotifyCompactActive() && !this.toastOn && !this.pulseOn && !this.lyraListening && !this.lyraSpeaking;
    const want: FxKind | null = timerOn ? "timer" : musicOn ? "music" : null;
    const urgent = timerOn && this.timerChip.el.classList.contains("urgent");
    const glow = !want || State.glowOn(want === "timer" ? "timer" : "music");
    const key = want ? `${want}${urgent ? "!" : ""}${glow ? "" : "~"}` : "";
    if (key === this.ambientKey) return;
    this.ambientKey = key;
    if (!want) { if (k) this.fx.hide(); return; }
    const color = urgent ? "#ff453a" : want === "timer" ? (this.timerChip.el.style.getPropertyValue("--timer-accent") || undefined) : undefined;
    this.fx.show(want, { color });
  }
  private ambientKey = "";

  /** Shows the live timer only in the compact notch, alongside/replacing music EQ. */
  private syncTimerChip() {
    // A timer can keep running while Lyra speaks, but its compact countdown must
    // wait until speech is completely finished so it never competes with the
    // voice wave. The same rule applies while the listening HUD owns the notch.
    const show =
      State.mode === "compact" &&
      !this.volumeHudOn &&
      !this.lyraSpeaking &&
      !this.lyraListening &&
      !this.lyraThinking &&
      this.timerChip.active() &&
      this.activities.main() === "timer";
    this.timerChip.el.classList.toggle("show", show);
    this.islandEl.classList.toggle("timer-on", show);
    this.syncAmbientFx();
    // The timer owns the main island here, so it always has room for its label.
    this.timerChip.el.classList.toggle("with-music", false);
    if (show) this.timerChip.tick();
  }

  /** Applies settings coming from Rust at boot. */
  /** One line saying what the island shows right now. Only for the trace log, never for logic. */
  traceSnapshot(): string {
    const p: string[] = [`mode=${State.mode}`, `tab=${State.view}`];
    if (State.mode === "expanded" && PEEK_SIZES[State.view]) p.push(State.peek ? "SMALL peek card" : "FULL tab");
    if (State.mode === "compact") {
      const what = this.volumeHudOn ? `HUD(${this.volumeLabel.textContent})`
        : this.toastOn ? "notification toast"
        : this.lyraListening ? "listening HUD"
        : this.lyraSpeaking ? "speaking"
        : this.isSpotifyCompactActive() ? `music pill${this.spotifyState?.status === "paused" ? " (paused)" : ""}`
        : this.timerChip.active() ? "timer chip"
        : "Mochi";
      p.push(`compact shows: ${what}`);
    }
    p.push(`fsm=${this.fsm.state}`, `size=${Math.round(this.width.value)}x${Math.round(this.height.value)}`);
    const flags: string[] = [];
    if (State.isPinned) flags.push("pinned");
    if (State.lyricsOpen) flags.push("lyrics open");
    if (this.searchResultOpen) flags.push("search results");
    if (this.wheelUp) flags.push("wheel up");
    if (this.lyraThinking) flags.push("thinking");
    if (State.recognitionBusy) flags.push("recognizing");
    if (State.paused) flags.push("PAUSED");
    if (this.fsm.suppressed) flags.push("auto-hide on");
    if (flags.length) p.push(flags.join(","));
    return p.join(" ");
  }

  applySettings() {
    Sound.setEnabled(State.settings.soundEnabled);
    Sound.setVolume(State.settings.soundVolume);
    this.applyAutoClose();
    this.fsm.petitToHiddenDelay = State.settings.compactAutoHideInterval;
    this.syncAutoHide();
    this.syncKeepCompact();
    State.notify();
  }

  /** Settings → Island → Auto-hide (or a screen share) decides whether the compact notch may be on screen. */
  private syncAutoHide() {
    const on = autoHidden(normalizeAutoHide(State.settings.islandAutoHide), this.autoCtx);
    if (on === this.fsm.suppressed) return;
    this.fsm.setSuppressed(on, on ? "auto-hide on" : "auto-hide off");
    this.syncKeepCompact();
    void Bridge.log(`island: auto-hide ${on ? "on" : "off"} (mode=${State.settings.islandAutoHide}, desktop=${this.autoCtx.desktop}, covering=${this.autoCtx.covering}, sharing=${this.autoCtx.sharing})`);
  }

  private keepNotch(): boolean {
    // The compact notch always stays up; hiding is the Auto-hide setting's job (Settings → Island).
    return !this.fsm.suppressed;
  }

  /** Re-evaluates keepCompact after a settings change (and once at boot). */
  private syncKeepCompact() {
    const keep = this.keepNotch();
    this.fsm.keepCompact = keep;
    if (!this.launched) return; // boot: launch() brings the notch up with the greeting
    if (keep && this.fsm.state === "hidden") this.fsm.forcePetit();
    else if (!keep && this.fsm.state === "petit" && !this.wasInIsland) this.fsm.mouseLeft(); // arms the idle hide
  }

  get panelSize() {
    return { w: PANEL_W, h: PANEL_H };
  }

  get chatHeight() {
    return chatPromptHeight(State.chatVisible().length);
  }
}
