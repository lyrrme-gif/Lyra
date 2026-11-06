/** IslandHud — collaborator extracted from island.ts */
import type { Island } from "./island";

import { Sound } from "../../shared/core/sound";
import { State } from "../../shared/core/state";
import { type Fx, type FxKind } from "../../shared/activityFx/activityFx";
import { clear, h } from "../../shared/views/dom";
import { VOLUME_HUD_MS } from "./islandConstants";
import { volumeIconSvg, batteryIconSvg } from "./islandIcons";
import { traceLimited } from "../../shared/core/trace";

export class IslandHud {
  constructor(private readonly i: Island) {}

  showHudForced(iconHtml: string, label: string, durationMs?: number, spotifyUrl = "", subtitle = "", activityKind = "") {
    traceLimited(`hudf-${label}`, 1500, "hud", `activity card on the notch: '${label.slice(0, 40)}' kind=${activityKind || (subtitle ? "rich" : "simple")}${durationMs ? ` for ${durationMs}ms` : ""}`);
    this.i.suspendToast();
    this.i.activitySpotifyUrl = spotifyUrl;
    this.i.volumeHud.classList.toggle("recognition-result", !!spotifyUrl);
    this.i.volumeSpotify.classList.toggle("on", !!spotifyUrl);
    // Only touch the icon when it actually changed: re-creating it on every progress
    // tick is what made the spinner restart before it could finish a turn.
    if (this.i.volumeIcon.dataset.sig !== iconHtml) {
      this.i.volumeIcon.innerHTML = iconHtml;
      this.i.volumeIcon.dataset.sig = iconHtml;
    }
    this.i.volumeLabel.textContent = label;
    this.i.volumeSubtitle.textContent = subtitle || "";
    this.i.volumeHud.dataset.activityKind = activityKind || (subtitle ? "rich" : "simple");
    if (activityKind === "download") {
      // A download is a secondary activity when another compact activity is
      // already owning the island (Spotify, timer, app launch, voice, etc.).
      // In that case keep the primary activity visible and reduce the download
      // HUD to a small right-side progress circle.
      // The compact notch can only spare the little right-side circle when
      // another activity already owns it. In an expanded island there is room
      // for the complete download title/subtitle, even if Spotify is playing.
      const secondary =
        State.mode === "compact" && !this.i.dlMain && (
          this.i.isSpotifyMediaActive() ||
          this.i.timerChip.active() ||
          this.i.appLaunchHudOn ||
          this.i.lyraListening ||
          this.i.lyraSpeaking
        );
      this.i.volumeHud.dataset.downloadCompact = secondary ? "1" : "0";
    } else {
      delete this.i.volumeHud.dataset.downloadPhase;
      delete this.i.volumeHud.dataset.downloadCompact;
    }
    this.i.volumeHud.classList.add("nobar", "activity");
    this.i.volumeFill.style.width = "0%";
    this.i.volumeThumb.style.left = "0%";

    const wasOn = this.i.volumeHudOn;
    if (State.mode === "hidden") {
      this.i.volumeRevealed = !this.i.fsm.keepCompact;
      this.i.fsm.reveal();
    }
    this.i.volumeHudOn = true;
    // Speaker HUD owns the compact notch while it is visible. Hide the Spotify
    // compact layer immediately so it cannot bleed through the volume slider.
    this.i.syncSpotifyCompact();
    this.i.volumeHud.style.removeProperty("opacity");
    this.i.volumeHud.classList.add("on");
    if (activityKind === "download") this.i.startDownloadSpinner();
    if (!wasOn) {
      this.i.animateGeometry(false);
    }
    this.i.dirty = true;
    this.i.ensureRunning();

    // Downloads own the HUD for the whole transfer. Repeated progress events keep
    // it alive, and the completion/failure event is the only thing that releases it.
    const holdForDownload = activityKind === "download" && !["success", "fail"].includes((this.i.volumeHud.dataset.downloadPhase || "").toLowerCase());
    const ms = holdForDownload ? 0 : (durationMs && durationMs > 0 ? durationMs : VOLUME_HUD_MS);
    if (this.i.volumeTimer != null) window.clearTimeout(this.i.volumeTimer);
    this.i.volumeTimer = null;
    if (ms > 0) this.i.volumeTimer = window.setTimeout(() => {
      this.i.volumeHud.classList.remove("activity");
      this.i.volumeHud.removeAttribute("data-activity-kind");
      this.i.volumeSubtitle.textContent = "";
      this.i.endVolumeHud();
    }, ms);
  }


  /** Music recognition result card in the compact notch. */
  onVolume(level: number, muted: boolean) {
    if (!State.activityOn("volume")) return;
    const shown = muted ? 0 : Math.max(0, Math.min(1, level));
    this.i.showHud(volumeIconSvg(level, muted), muted ? "Muted" : "Speakers", shown, () => {
      this.i.hudFx("volume", { level: muted ? 0.3 : 0.35 + shown * 0.65 });
      this.i.sfx("volume", { level: shown, muted });
    });
  }

  /** Display brightness changed (keys / slider): same Apple-style HUD with a sun. */
  onBrightness(percent: number, ts?: number) {
    if (!State.activityOn("brightness")) return;
    if (ts && Date.now() - ts > 8000) return;
    const p = Math.max(0, Math.min(100, Math.round(percent)));
    const sun = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="currentColor"/><g stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5.3 5.3L7 7M17 17l1.7 1.7M5.3 18.7L7 17M17 7l1.7-1.7"/></g></svg>';
    this.i.showHud(sun, "Brightness", p / 100, () => {
      this.i.hudFx("brightness", { level: 0.25 + (p / 100) * 0.75 });
      this.i.sfx("brightness", { level: p / 100 });
    });
  }

  /** One app's own volume changed (Volume Mixer, Settings slider): same HUD, with the app's name. */
  onAppVolume(s: { app: string; level: number; muted: boolean; ts?: number }) {
    if (!State.activityOn("volume")) return;
    if (s.ts && Date.now() - s.ts > 8000) return;
    const level = Math.max(0, Math.min(1, s.level));
    const name = s.app.slice(0, 16);
    const label = s.muted ? `${name} muted` : `${name} ${Math.round(level * 100)}%`;
    this.i.showHud(volumeIconSvg(level, s.muted), label, s.muted ? 0 : level, () => {
      this.i.hudFx("volume", { level: s.muted ? 0.3 : 0.35 + level * 0.65 });
      this.i.sfx("volume", { level, muted: s.muted });
    });
  }

  /** Battery / charging pop-up: same HUD as the volume, with the charge as the bar. */
  onStatus(s: { text: string; percent: number; charging: boolean; ts?: number }) {
    if (s.ts && Date.now() - s.ts > 8000) return; // a buffered replay, not news
    const pct = Math.max(0, Math.min(100, Math.round(s.percent)));
    this.i.showHud(batteryIconSvg(pct, s.charging), `${s.text} ${pct}%`, pct / 100);
  }

  /** Bluetooth audio device connected / disconnected: icon + name, no bar. */
  onBluetooth(s: { text: string; ts?: number }) {
    if (!State.activityOn("devices")) return;
    if (s.ts && Date.now() - s.ts > 8000) return;
    const icon =
      '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M5 12a7 7 0 0 1 14 0v3.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><rect x="3" y="12.5" width="4" height="7" rx="1.6" fill="currentColor"/><rect x="17" y="12.5" width="4" height="7" rx="1.6" fill="currentColor"/></svg>';
    const gone = /disconnect|removed|lost|unplug/i.test(s.text);
    this.i.showHud(icon, s.text, null, () => {
      this.i.fx.show(gone ? "disconnect" : "devices", { originX: 24 });
      Sound.activity(gone ? "disconnect" : "connect");
    });
  }

  /** Reusable activity feedback (app launch, recognition, …). */
  onPrivacy(mic: boolean) {
    clear(this.i.privacyDots);
    if (mic) this.i.privacyDots.append(h("i", { class: "mic", title: "Microphone in use" }));
    this.i.privacyDots.classList.toggle("on", mic);
  }


  /** Shared by the volume and battery pop-ups: icon + label + bar in the compact notch. */
  showHud(iconHtml: string, label: string, fill: number | null, cue?: () => void) {
    // An open panel is being used; the HUD only replaces the compact notch.
    if (State.mode === "expanded") {
      traceLimited(`hud-skip-${label}`, 3000, "hud", `HUD '${label.slice(0, 30)}' NOT shown because the panel is open`);
      return;
    }
    traceLimited(`hud-${label}`, 1000, "hud", `HUD shown on the compact notch: '${label.slice(0, 30)}'${fill != null ? ` (${Math.round(fill * 100)}%)` : ""}`);

    this.i.suspendToast(); // the HUD wins; the notification comes back after it
    cue?.();             // its sound and its light (only now that it is really being shown)
    this.i.volumeIcon.innerHTML = iconHtml;
    this.i.volumeLabel.textContent = label;
    this.i.volumeHud.classList.toggle("nobar", fill == null);
    this.i.volumeFill.style.width = `${(fill ?? 0) * 100}%`;
    this.i.volumeThumb.style.left = `${(fill ?? 0) * 100}%`;

    const wasOn = this.i.volumeHudOn;
    if (State.mode === "hidden") {
      // Wake the compact notch for the HUD, and remember to put it back to sleep.
      this.i.volumeRevealed = !this.i.fsm.keepCompact;
      this.i.fsm.reveal();
    }
    this.i.volumeHudOn = true;
    this.i.volumeHud.style.removeProperty("opacity");
    this.i.volumeHud.classList.add("on");
    if (!wasOn) {
      this.i.animateGeometry(false);
    }
    this.i.dirty = true;
    this.i.ensureRunning();

    if (this.i.volumeTimer != null) window.clearTimeout(this.i.volumeTimer);
    this.i.volumeTimer = window.setTimeout(() => this.i.endVolumeHud(), VOLUME_HUD_MS);
  }

  endVolumeHud() {
    this.i.volumeTimer = null;
    if (this.i.dlWatchdog != null) { window.clearTimeout(this.i.dlWatchdog); this.i.dlWatchdog = null; }
    const endedKind = this.i.volumeHud.dataset.activityKind || "";
    const fxk = this.i.fx.kind();
    if (fxk && ["volume", "brightness", "devices", "disconnect", "download", "success", "fail", "recognition", "found"].includes(fxk)) this.i.fx.hide();
    this.i.stopDownloadSpinner();
    this.i.volumeHudOn = false;
    this.i.appLaunchHudOn = false;

    // A download reuses the volume HUD, so merely removing `on` is not enough:
    // the old activity class/data could leave the spinner painted/animated after
    // the transfer has already ended. Tear the download presentation down
    // completely before Spotify/other activities are re-synced.
    if (endedKind === "download") {
      const ring = this.i.volumeHud.querySelector<SVGElement>(".download-spinner-ring");
      if (ring) {
        ring.style.animation = "none";
        ring.style.strokeDashoffset = "0";
      }
      this.i.volumeHud.classList.remove("activity");
      delete this.i.volumeHud.dataset.activityKind;
      delete this.i.volumeHud.dataset.downloadPhase;
      delete this.i.volumeHud.dataset.downloadCompact;
      if (this.i.dlMain) { this.i.dlMain = false; this.i.activities.set("download", false); }
      this.i.volumeIcon.innerHTML = "";
      delete this.i.volumeIcon.dataset.sig;
      this.i.volumeLabel.textContent = "";
      this.i.volumeSubtitle.textContent = "";
      this.i.volumePct.textContent = "";
      this.i.volumeHud.removeAttribute("title");
      (this.i.volumeDlBar.firstElementChild as HTMLElement | null)?.style.setProperty("width", "0");
      // Hide immediately; do not wait for a geometry/frame update.
      this.i.volumeHud.style.opacity = "0";
    } else {
      this.i.volumeHud.classList.remove("activity");
      this.i.volumeHud.removeAttribute("data-activity-kind");
      delete this.i.volumeHud.dataset.downloadPhase;
      delete this.i.volumeHud.dataset.downloadCompact;
    }
    this.i.volumeHud.classList.remove("on", "recognition-result");
    this.i.volumeHud.style.opacity = "0";
    this.i.volumeSpotify.classList.remove("on");
    this.i.activitySpotifyUrl = "";
    this.i.animateGeometry(true);
    this.i.dirty = true;
    this.i.ensureRunning();
    this.i.syncAmbientFx();
    // The HUD was the only reason the notch was up: let it go again.
    if (this.i.volumeRevealed) {
      this.i.volumeRevealed = false;
      if (!this.i.wasInIsland && State.mode === "compact" && !this.i.toastQueue.length) this.i.fsm.forceHidden();
    }
    // Re-read what is really alive and redraw the small islands, so nothing left over from this HUD (a download that
    // is no longer there, a second music pill) stays on the notch.
    this.i.refreshActivities();
    this.i.paintSecondary();
    this.i.syncSpotifyCompact();
    this.i.pumpToast();
  }

  sfx(name: Parameters<typeof Sound.activity>[0], o: Parameters<typeof Sound.activity>[1] = {}) {
    const now = performance.now();
    if ((name === "volume" || name === "brightness") && now - this.i.sfxAt < 85) return;
    this.i.sfxAt = now;
    Sound.activity(name, o);
  }

  /** The light of a HUD. A held key must not strobe it: the same kind restarts at most every ~450 ms. */
  hudFx(kind: FxKind, o: Parameters<Fx["show"]>[1] = {}) {
    const now = performance.now();
    if (this.i.fx.kind() === kind && now - this.i.fxAt < 450) return;
    this.i.fxAt = now;
    this.i.fx.show(kind, o);
  }

}
