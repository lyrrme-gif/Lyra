// Island open/close FSM — port of IslandStateMachine.swift.
// No DOM, no Tauri: it only reports transitions.

// >>> phase-6 begin
import * as startup from "./startup";
// <<< phase-6 end

export type FsmState = "hidden" | "petit" | "home";

export class IslandStateMachine {
  state: FsmState = "hidden";

  onTransition: ((from: FsmState, to: FsmState) => void) | null = null;

  /** home → petit delay, seconds. */
  homeToPetitDelay = 15;
  /** petit → hidden delay, seconds. Keep the compact notch visible for 15
   * seconds of inactivity before retracting it. The top wake strip can reveal
   * it again immediately after it has been hidden. */
  petitToHiddenDelay = 8;
  /** An alert waiting for an answer stays open, even when the mouse leaves. */
  pinned = false;
  /** While this returns true the panel does not auto-close (e.g. lyrics are open). */
  holdOpen: () => boolean = () => false;
  /** When Spotify has an active track, the compact notch must stay visible even when the cursor leaves. */
  keepCompact = false;
  /** A rich web-search result stays expanded until the user explicitly dismisses it. */
  searchLocked = false;
  /** Auto-hide (Settings → Island): the compact notch is not allowed. Panels opened on purpose still open. */
  suppressed = false;
  /** While suppressed, the notch was called back by resting the pointer on it: it is allowed until it hides again. */
  private peeking = false;

  /** Why the last transition happened — only for the log, never for logic. */
  lastReason = "";

  private petitHide: number | null = null;
  private homeCollapse: number | null = null;

  /** keepCompact, unless auto-hide is on: then nothing keeps the notch on screen. */
  private get keep(): boolean {
    return this.keepCompact && !this.suppressed;
  }

  // ── Inputs ──────────────────────────────────────────────────────────────────

  launch() {
    this.lastReason = "launch";
    this.cancelTimers();
    this.transition(this.keep ? "petit" : "hidden");
    // >>> phase-6 begin
    // Start-up animation: opens the compact pill (reveal), plays, and leaves the usual idle timer running.
    // It plays whatever is (or is about to be) on the island: music, a timer, auto-hide ...
    startup.run({
      canPlay: () => true,
      reveal: () => this.startupReveal(),
    });
    // <<< phase-6 end
  }

  mouseEntered() {
    this.lastReason = "mouse entered";
    if (this.leaveDebounce != null) {
      window.clearTimeout(this.leaveDebounce);
      this.leaveDebounce = null;
    }
    switch (this.state) {
      case "hidden":
        this.cancelTimers();
        this.transition("petit");
        break;
      case "petit":
        this.clear("petitHide");
        break;
      case "home":
        this.clear("homeCollapse");
        break;
    }
  }

  /** Short debounce so grazing the wake-strip edge does not flicker the notch. */
  private leaveDebounce: number | null = null;
  private static readonly LEAVE_DEBOUNCE_MS = 140;

  mouseLeft() {
    this.lastReason = "mouse left";
    // Debounce leave while in compact so a brief exit from the wake strip / notch
    // does not start the collapse animation immediately (prevents flicker).
    if (this.leaveDebounce != null) {
      window.clearTimeout(this.leaveDebounce);
      this.leaveDebounce = null;
    }
    if (this.state === "petit" && !this.keep && !this.searchLocked) {
      this.leaveDebounce = window.setTimeout(() => {
        this.leaveDebounce = null;
        if (this.state === "petit" && !this.keep && !this.searchLocked) this.schedulePetitHide();
      }, IslandStateMachine.LEAVE_DEBOUNCE_MS);
      return;
    }
    switch (this.state) {
      case "hidden":
        break;
      case "petit":
        // handled above
        break;
      case "home":
        this.scheduleHomeCollapse();
        break;
    }
  }

  click() {
    if (this.state !== "petit") return;
    this.lastReason = "click";
    this.cancelTimers();
    this.transition("home");
  }

  /** User activity while the expanded panel is open restarts its auto-close timer. */
  activity() {
    if (this.state !== "home" || this.pinned) return;
    this.lastReason = "activity";
    this.scheduleHomeCollapse();
  }

  /** Non-alert work event: show compact from hidden. */
  reveal() {
    if (this.state !== "hidden") return;
    this.lastReason = "reveal";
    this.cancelTimers();
    this.transition("petit");
    this.schedulePetitHide();
  }

  /** The start-up sequence needs the compact pill on screen, even while auto-hide would keep it away. */
  startupReveal() {
    if (this.state !== "hidden") return;
    if (this.suppressed) this.peeking = true;
    this.reveal();
  }

  /** Alert or explicit request: open straight to expanded. */
  forceHome(why = "forceHome") {
    this.lastReason = why;
    this.cancelTimers();
    this.transition("home");
  }

  /// Explicit close (OK button, Escape, an alert being answered).
  forcePetit(why = "forcePetit") {
    this.lastReason = why;
    this.cancelTimers();
    this.transition("petit");
  }

  /** Bring the state in line with what is on screen, without firing a transition. */
  resync(state: FsmState) {
    this.state = state;
  }

  lockSearch() {
    this.searchLocked = true;
    this.cancelTimers();
  }

  unlockSearch() {
    this.searchLocked = false;
    this.cancelTimers();
  }

  forceHidden(why = "forceHidden") {
    this.lastReason = why;
    this.cancelTimers();
    if (this.keep) {
      this.transition("petit");
      return;
    }
    this.transition("hidden");
  }

  /** Auto-hide on / off. Turning it on retracts the compact notch; an open panel was asked for, so it stays. */
  setSuppressed(on: boolean, why = "auto-hide") {
    if (this.suppressed === on) return;
    this.suppressed = on;
    this.peeking = false;
    this.lastReason = why;
    if (on) {
      this.cancelTimers();
      if (this.state === "petit") this.transition("hidden");
    }
  }

  /** Auto-hidden, and the pointer rested on the notch area: show the compact notch until it retracts again. */
  peek() {
    if (!this.suppressed || this.state !== "hidden") return;
    this.lastReason = "pointer rested on the notch (auto-hide)";
    this.peeking = true;
    this.cancelTimers();
    this.transition("petit");
    this.schedulePetitHide();
  }

  // ── Timers ──────────────────────────────────────────────────────────────────

  private schedulePetitHide() {
    this.clear("petitHide");
    this.petitHide = window.setTimeout(() => {
      this.petitHide = null;
      this.lastReason = "idle timer (petit -> hidden)";
      if (this.state === "petit" && !this.keep && !this.searchLocked) this.transition("hidden");
    }, this.petitToHiddenDelay * 1000);
  }

  private scheduleHomeCollapse() {
    this.clear("homeCollapse");
    if (this.pinned || this.searchLocked) return;
    this.homeCollapse = window.setTimeout(() => {
      this.homeCollapse = null;
      if (this.state === "home" && (this.holdOpen() || this.searchLocked)) {
        this.scheduleHomeCollapse(); // look again later, stay open meanwhile
        return;
      }
      this.lastReason = `auto-close timer (${this.homeToPetitDelay}s)`;
      if (this.state === "home") this.transition("petit");
    }, this.homeToPetitDelay * 1000);
  }

  private clear(which: "petitHide" | "homeCollapse") {
    const id = this[which];
    if (id != null) window.clearTimeout(id);
    this[which] = null;
  }

  cancelTimers() {
    this.clear("petitHide");
    this.clear("homeCollapse");
    if (this.leaveDebounce != null) {
      window.clearTimeout(this.leaveDebounce);
      this.leaveDebounce = null;
    }
  }

  private transition(next: FsmState) {
    // A playing/paused Spotify track owns the compact notch. Never let an
    // ordinary hover timeout retract it while media is active.
    if (next === "hidden" && this.keep) next = "petit";
    if (next === "petit" && this.suppressed && !this.peeking) next = "hidden";
    if (next === "hidden") this.peeking = false;
    if (next === this.state) return;
    const from = this.state;
    this.state = next;
    this.onTransition?.(from, next);
  }
}
