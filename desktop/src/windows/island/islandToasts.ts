/** IslandToasts — collaborator extracted from island.ts */
import type { Island } from "./island";

import { Sound } from "../../shared/core/sound";
import { Bridge } from "../../shared/core/bridge";
import { State } from "../../shared/core/state";
import { APP_FX_COLOR } from "../../shared/activityFx/activityFx";
import { appUrl, type NotifyEvent } from "./toast";
import { openAppsChat } from "./views/apps";
import { TOAST_MS, TOAST_STALE_MS, TOAST_MAX_WAIT_MS } from "./islandConstants";
import { trace } from "../../shared/core/trace";

export class IslandToasts {
  constructor(private readonly i: Island) {}

  toastMs(ev: NotifyEvent | null): number {
    const ms = ev?.durationMs;
    return ms && ms > 0 ? Math.min(15_000, Math.max(1500, ms)) : TOAST_MS;
  }

  private lastReminderKey = "";
  private lastReminderAt = 0;

  /** A message, call or reminder arrived (from the backend's "notify" event). */
  onNotify(ev: NotifyEvent) {
    // The tray's Pause is silent: nothing shown, nothing kept.
    void Bridge.log(`toast: got ${ev.app}/${ev.kind} (mode=${State.mode} paused=${State.paused} age=${Date.now() - ev.ts}ms)`);
    trace("toast", `notification from ${ev.app} (${ev.kind}) arrived: island=${State.mode}, paused=${State.paused}, age=${Date.now() - ev.ts}ms, toastShowing=${this.i.toastOn}, queued=${this.i.toastQueue.length}`);
    if (State.paused) return;
    if (ev.kind === "call" && !State.activityOn("calls")) return;
    if (ev.kind === "reminder" && !State.activityOn("reminders")) return;
    if (ev.kind === "message" && !State.activityOn("notifications")) return;
    if (Date.now() - ev.ts > TOAST_STALE_MS) {
      void Bridge.log("toast: dropped, event too old");
      trace("toast", `notification from ${ev.app} DROPPED: too old (${Date.now() - ev.ts}ms)`);
      return;
    }

    // The same reminder twice (a backend that reports one event more than once, or a toast that was suspended by a HUD
    // and re-queued): once is enough. Messages are left alone, a repeated message is a new message.
    if (ev.kind === "reminder") {
      const key = `${ev.app}|${ev.sender}|${ev.text}`;
      const queued = this.i.toastQueue.some((q) => q.kind === "reminder" && `${q.app}|${q.sender}|${q.text}` === key);
      const now = performance.now();
      if (queued || (key === this.lastReminderKey && now - this.lastReminderAt < 20_000)) {
        trace("toast", `reminder DROPPED: same notice already shown / queued (${key.slice(0, 60)})`);
        return;
      }
      this.lastReminderKey = key;
      this.lastReminderAt = now;
    }

    // Another message from the same chat while its toast is up: update in place.
    const cur = this.i.toastCurrent;
    if (this.i.toastOn && cur && cur.app === ev.app && cur.kind === ev.kind && cur.sender === ev.sender) {
      this.i.toastCurrent = ev;
      this.i.toast.set(ev);
      this.i.armToastTimer(this.i.toastMs(ev));
      if (ev.kind === "message") this.i.toastCue(ev);       // another message in the same chat: the light and the sound again
      return;
    }
    this.i.toastQueue.push(ev);
    if (this.i.toastQueue.length > 5) this.i.toastQueue.shift();
    this.i.pumpToast();
  }

  /** Shows the next waiting notification, if the notch is free for it. */
  pumpToast() {
    if (State.paused || this.i.toastOn || this.i.pulseOn) return;
    // The open island is never covered by a notification: it waits in the queue.
    if (State.mode === "expanded") return;
    // The panel and the volume HUD own the notch while they are up.
    if (this.i.volumeHudOn) {
      void Bridge.log("toast: waiting (volume HUD up)");
      return;
    }
    const now = Date.now();
    let next = this.i.toastQueue.shift();
    while (next && now - next.ts > TOAST_MAX_WAIT_MS) next = this.i.toastQueue.shift();
    if (!next) return;

    this.i.toastOn = true;
    this.i.toastCurrent = next;
    this.i.toast.set(next);
    if (State.mode === "hidden") {
      // Wake the compact notch for it, and remember to put it back to sleep.
      this.i.toastRevealed = !this.i.fsm.keepCompact;
      this.i.fsm.reveal();
    }
    this.i.toast.el.classList.add("on");
    this.i.animateGeometry(false);                           // a little bigger while it shows
    void Bridge.log(`toast: showing ${next.app}/${next.kind} (mode=${State.mode})`);
    this.i.toastCue(next);
    this.i.armToastTimer(this.i.toastMs(next));
    this.i.dirty = true;
    this.i.ensureRunning();
  }

  /** The sound and the light of a notification: each app, call and reminder has its own. */
  toastCue(ev: NotifyEvent) {
    if (ev.kind === "call") {
      this.i.fx.show("call");
      Sound.ringStart();
    } else if (ev.kind === "reminder") {
      this.i.fx.show("reminder", { color: APP_FX_COLOR.reminder });
      // checkReminders() already rang the alarm for calendar reminders; any other reminder rings here
      if (Date.now() - this.i.lastAlarmAt > 4000) Sound.activity("reminder");
    } else if (ev.kind === "message") {
      this.i.fx.show("message", { color: APP_FX_COLOR[ev.app] });
      Sound.activity("message", { app: ev.app });
    } else {
      Sound.play("blip");
    }
  }

  /** Stops the light and the ringing of whatever toast was up. */
  toastCueEnd() {
    Sound.ringStop();
    const k = this.i.fx.kind();
    if (k === "call" || k === "reminder" || k === "message") this.i.fx.hide();
  }

  /** A sound for a HUD that can fire many times a second (volume keys): at most one every ~85 ms. */
  armToastTimer(ms: number) {
    if (this.i.toastTimer != null) window.clearTimeout(this.i.toastTimer);
    this.i.toastTimer = window.setTimeout(() => {
      this.i.toastTimer = null;
      // Don't pull it out from under the cursor: wait until the mouse has left.
      if (this.i.wasInIsland && State.mode !== "expanded") this.i.armToastTimer(1500);
      else this.i.endToast();
    }, ms);
  }

  /** Puts the toast back at the head of the queue (panel opened / volume HUD took over). */
  suspendToast() {
    if (!this.i.toastOn) return;
    if (this.i.toastTimer != null) window.clearTimeout(this.i.toastTimer);
    this.i.toastTimer = null;
    if (this.i.toastCurrent) this.i.toastQueue.unshift(this.i.toastCurrent);
    this.i.toastOn = false;
    this.i.toastCurrent = null;
    this.i.toast.el.classList.remove("on");
    this.i.toastCueEnd();
  }

  endToast() {
    if (!this.i.toastOn) return;
    if (this.i.toastTimer != null) window.clearTimeout(this.i.toastTimer);
    this.i.toastTimer = null;
    this.i.toastOn = false;
    this.i.toastCurrent = null;
    this.i.toast.el.classList.remove("on");
    this.i.toastCueEnd();
    this.i.syncAmbientFx();
    if (State.mode === "compact") this.i.animateGeometry(!this.i.wasInIsland);
    this.i.dirty = true;
    this.i.ensureRunning();
    if (this.i.toastQueue.length) {
      window.setTimeout(() => this.i.pumpToast(), 260); // let the fade-out read before the next one
      return;
    }
    if (this.i.toastRevealed) {
      this.i.toastRevealed = false;
      if (!this.i.wasInIsland && State.mode === "compact") this.i.fsm.forceHidden();
    }
  }

  /** The X on a toast. For a dictation offer it also tells the backend to forget it and drops any others waiting. */
  declineToast() {
    if (this.i.toastCurrent?.kind === "call") Sound.activity("decline");
    if (this.i.toastCurrent?.kind === "dictation") {
      this.i.toastQueue = this.i.toastQueue.filter((e) => e.kind !== "dictation");
      void fetch("http://127.0.0.1:8765/dictation/dismiss", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => undefined);
    }
    this.i.endToast();
  }

  /** Click on the toast: open the app it came from, then dismiss it. */
  clickToast() {
    if (this.i.toastCurrent?.kind === "call") Sound.activity("answer");
    // "Apply polished version" (dictation): the backend puts the text in its window and reports on the island.
    if (this.i.toastCurrent?.kind === "dictation") {
      this.i.toastQueue = this.i.toastQueue.filter((e) => e.kind !== "dictation");
      void fetch("http://127.0.0.1:8765/dictation/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => undefined);
      this.i.endToast();
      return;
    }
    // A WhatsApp message that came through Lyra's own WhatsApp link opens that chat on the notch (big chat);
    // otherwise (the Windows toast of the desktop app) it opens the app as before.
    const cur = this.i.toastCurrent;
    if (cur?.app === "whatsapp" && cur.chat && State.viewAllowed("apps")) {
      const chat = cur.chat;
      this.i.endToast();
      openAppsChat("whatsapp", chat);
      this.i.onOpenView("apps");
      return;
    }
    const url = cur ? appUrl(cur.app) : null;
    if (url) void Bridge.openUrl(url);
    this.i.endToast();
  }

  // ── File drop ───────────────────────────────────────────────────────────────

  /** Close the tab an incoming drag opened, once the drag is over. Waits for Mochi to finish reacting to the drop. */
}
